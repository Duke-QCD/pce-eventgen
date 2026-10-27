"""OO v3 physics and workflow checks using synthetic caches, never production data."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'models/analysis'))
import check_cache_profiles as fixtures
import extract_observables as sx
import reduce_observables as sr


class OOAnalysisProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='oo-analysis-test-')
        cls.root = Path(cls.temp.name)
        raw, pdg, baryon = fixtures.make_input(cls.root)
        cls.cache = cls.root / 'cache/design_000/OO5360'
        cls.scope = fixtures.FINGERPRINTS['OO5360']
        cls.env = fixtures.profile_environment('OO5360')
        cls.env.update(HIC_PDG_MASS=str(pdg), HIC_BARYON_DICT=str(baryon),
                       OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        command = [sys.executable, ROOT / 'models/cache/cache_one_design.py',
                   '--input-design', raw, '--output-cache', cls.cache,
                   '--num-events', '24', '--max-events', '24', '--validate-events', '0',
                   '--events-per-shard', '24', '--nprocs', '1']
        result = subprocess.run(list(map(str, command)), env=cls.env, capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        meta = json.loads((cls.cache / 'metadata.json').read_text())
        shard = cls.cache / meta['shards'][0]
        with np.load(shard) as z:
            arrays = {k: z[k] for k in z.files}
        # A controlled flat-spectrum boundary bin, varying between hydro events.
        edges = np.asarray(meta['fingerprint']['scalar_pt_upper_edges'])
        boundary = int(np.flatnonzero(np.isclose(edges, .30))[0])
        for window in sx.CHARGED_PTH_KEYS:
            for i, k in enumerate(arrays['nsubs']):
                count = (i + 1) * k
                arrays[f'pth#{window}#N'][i, boundary] = count
                arrays[f'pth#{window}#sum_pT'][i, boundary] = .275 * count
        # Exercise k=1, M<2 without removing that hydro event from other observables.
        arrays['fl#0#m_full'][0] = 1
        for n in (2, 3):
            arrays[f'fl#0#q{n}_full'][0] = 1 + 0j
        np.savez(shard, **arrays)
        cls.shard = arrays
        (cls.cache / 'cache.verified').write_text(json.dumps({
            'cache_valid': True, 'schema_version': sx.EXPECTED_CACHE_SCHEMA,
            'fingerprint_hash': meta['fingerprint_hash'], 'n_events': 24}))
        cls.baseline, cls.base_meta = sx.extract_design(str(cls.cache))
        cls.out, cls.meta = sx.extract_design(str(cls.cache), profile=sx.OO_ANALYSIS_PROFILE)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def command(self, ini, *args, success=True):
        result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py'),
                                 '--config', str(ini), *args], env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def make_ini(self, directory):
        ini = directory / 'campaign.ini'
        ini.write_text(f'''[run]
system = OO5360
nevents = 24
[pipeline]
cache_cfs_root = {self.root / 'cache'}
expected_fingerprint = {self.scope}
analysis_out_dir = {directory / 'artifacts'}
[analysis_campaign]
analysis_profile = {sx.OO_ANALYSIS_PROFILE}
n_boot = 0
jobs = 1
''')
        return ini

    def test_flat_boundary_and_original_arrays(self):
        for key in self.baseline:
            np.testing.assert_array_equal(self.out[key], self.baseline[key], err_msg=key)
        upper = np.asarray(self.meta['cache_fingerprint']['scalar_pt_upper_edges'])
        lower = np.r_[0., upper[:-1]]
        keep = np.flatnonzero((lower >= .25 - 1e-9) & (upper <= 5 + 1e-9))
        boundary = int(np.flatnonzero(np.isclose(upper, .30))[0])
        for wi, window in enumerate(sx.CHARGED_PTH_KEYS):
            counts = self.shard[f'pth#{window}#N']
            pts = self.shard[f'pth#{window}#sum_pT']
            expected_n = counts[:, keep].sum(axis=1) - .4 * counts[:, boundary]
            expected_pt = pts[:, keep].sum(axis=1) - .104 * counts[:, boundary]
            np.testing.assert_allclose(self.out['charged_mean_pT_N'][:, wi], expected_n)
            np.testing.assert_allclose(self.out['charged_mean_pT_sum_pT'][:, wi], expected_pt)
        n = 10
        self.assertAlmostEqual((.275*n - .104*n) / (.6*n), .285)

    def test_cross_pairs_fallback_and_hydro_normalization(self):
        for i, k in enumerate(self.out['nsub_used']):
            a, b = self.shard['offsets'][i:i+2]
            m = self.shard['fl#0#m_full'][a:b].astype(float)
            for hi, n in enumerate((2, 3)):
                q = self.shard[f'fl#0#q{n}_full'][a:b]
                if k >= 2:
                    den = sum(m[s]*m[t] for s in range(k) for t in range(k) if s != t) / (k-1)
                    num = sum((q[s]*q[t].conjugate()).real for s in range(k)
                              for t in range(k) if s != t) / (k-1)
                elif k == 1 and m[0] >= 2:
                    den, num = m[0]*(m[0]-1), abs(q[0])**2-m[0]
                else:
                    den, num = 0., 0.
                self.assertAlmostEqual(self.out['charged_cross_full_den'][i, hi], den)
                self.assertAlmostEqual(self.out['charged_cross_full_num'][i, hi], num)
        idx = np.arange(24)
        reduced = sr.reduce_class(self.out, self.meta, idx)
        w = np.divide(1., self.out['nsub_used'], out=np.zeros(24), where=self.out['nsub_used'] > 0)
        num, den = w*self.out['charged_cross_full_num'][:, 0], w*self.out['charged_cross_full_den'][:, 0]
        self.assertEqual(reduced['charged']['c2_gap'][0], num.sum()/den.sum())
        bins = sr.reduce_all(self.out, self.meta, edges_pct=[0, 100], n_boot=0)['bins']
        usage = bins[0]['charged_flow_sample_usage']
        self.assertEqual(usage['same_full_fallback_events'], 8)
        self.assertEqual(usage['fallback_events_without_pairs'], 1)
        self.assertEqual(usage['same_full_pair_weight_fraction'], den[self.out['nsub_used'] == 1].sum()/den.sum())

    def test_jackknife_and_unchanged_observables(self):
        idx = np.arange(24)
        base = sr.reduce_class(self.baseline, self.base_meta, idx)
        new = sr.reduce_class(self.out, self.meta, idx)
        changed = {'mean_pT', 'v2_2', 'v3_2', 'c2_gap', 'c3_gap'}
        for group, observables in base.items():
            if group.startswith('_'):
                continue
            for key, value in observables.items():
                if group != 'charged' or key not in changed:
                    np.testing.assert_array_equal(new[group][key], value, err_msg=f'{group}/{key}')
        for key in ('mean_pT', 'c2_gap', 'c3_gap'):
            replicates = np.asarray([sr.reduce_class(self.out, self.meta, np.delete(idx, i))['charged'][key][0]
                                     for i in idx])
            se = np.sqrt((len(idx)-1)/len(idx) * np.sum((replicates-replicates.mean())**2))
            self.assertAlmostEqual(new['charged'][key][1], se)
        self.assertTrue(np.isnan(sr._jack_ratio_se([1., 0.], [1., 0.], strict=True)))
        bad = dict(self.meta, cache_fingerprint=dict(self.meta['cache_fingerprint']))
        bad['cache_fingerprint']['scalar_pt_upper_edges'] = [.25, 5.]
        with self.assertRaisesRegex(ValueError, 'cached bin edge'):
            sx._charged_mean_pt_mask(bad['cache_fingerprint'])

    def test_campaign_selection_resume_and_metadata(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            directory = Path(directory)
            ini = self.make_ini(directory)
            artifacts = directory / 'artifacts'
            prefix = str(artifacts / 'design_000_OO5360_observables')
            self.command(ini, 'analysis', 'run', '--submit', '--validate')
            saved, meta = sr.load_artifact(prefix)
            for key in saved:
                np.testing.assert_array_equal(saved[key], self.out[key])
            self.assertEqual(meta['analysis_profile'], sx.OO_ANALYSIS_PROFILE)
            before = Path(prefix + '.npz').stat().st_mtime_ns
            self.command(ini, 'analysis', 'run', '--submit')
            self.assertEqual(Path(prefix + '.npz').stat().st_mtime_ns, before)
            self.command(ini, 'analysis', 'audit')
            # Enough synthetic hydro events to exercise the full 1%-then-5% layout.
            larger = {k: np.concatenate([v]*20) for k, v in saved.items()}
            sx.write_artifact(larger, dict(meta, n_events=480), prefix)
            self.command(ini, 'analysis', 'aggregate', '--no-csv')
            with np.load(artifacts / 'model_matrix.npz') as matrix:
                self.assertEqual(matrix['Y'].shape, (1, 2200))
                self.assertEqual(set(matrix.files), {'Y', 'Y_err', 'feature_labels', 'design_index', 'design_name'})
            metadata = json.loads((artifacts / 'model_matrix.meta.json').read_text())
            self.assertEqual(metadata['analysis_profile'], sx.OO_ANALYSIS_PROFILE)
            self.assertEqual(metadata['source_analysis_profiles'], [sx.OO_ANALYSIS_PROFILE])
            self.assertEqual(metadata['centrality_edges_pct'], sx.OO_DEFAULT_CENTRALITY_EDGES)
            self.assertEqual(len(metadata['charged_flow_sample_usage'][0]['bins']), 20)
            self.assertEqual(metadata['charged_mean_pt_interpolation']['removed_count_fraction'], .4)
            self.assertEqual(metadata['n_failed'], 0)
            self.command(ini, 'analysis', 'aggregate', '--no-csv', '--designs', 'design_000')
            result = self.command(ini, 'analysis', 'aggregate', '--designs', 'design_999', success=False)
            self.assertIn('no complete analysis artifact', result.stderr)
            for obsolete in ('baseline', *sx.OO_OBSOLETE_ANALYSIS_PROFILES):
                sx.write_artifact(saved, dict(meta, analysis_profile=obsolete), prefix)
                for args in [('analysis', 'run', '--submit'), ('analysis', 'audit'),
                             ('analysis', 'aggregate', '--no-csv'),
                             ('analysis-design', 'reduce', '--design', 'design_000')]:
                    result = self.command(ini, *args, success=False)
                    self.assertIn('profile', result.stdout + result.stderr)
            # Explicit re-extraction replaces incompatible artifacts; CLI overrides INI.
            self.command(ini, 'analysis', 'run', '--submit', '--force')
            self.command(ini, 'analysis', 'run', '--submit', '--force', '--analysis-profile', 'baseline')
            self.assertEqual(json.loads(Path(prefix + '.meta.json').read_text())['analysis_profile'], 'baseline')


if __name__ == '__main__':
    unittest.main()
