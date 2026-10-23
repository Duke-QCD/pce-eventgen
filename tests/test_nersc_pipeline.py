"""NERSC orchestration integration tests, using temporary campaigns only."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'models'))
import workflow_config as config
import submit_cache_reap_campaign as campaign
import workflow_records


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SharedConfigTests(unittest.TestCase):
    def test_types_overrides_and_action_flags_remain_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fp = root / 'scope.json'
            fp.write_text(json.dumps({key: 1 for key in config.SCOPE_ENV}))
            ini = root / 'campaign.ini'
            ini.write_text(f'[run]\nsystem=AuAu200\nnevents=4\n'
                           f'[pipeline]\nexpected_fingerprint={fp}\nrecord_dir=records\n'
                           '[cache]\nnprocs=2\nmax_dropped_events=3\n')
            parser = argparse.ArgumentParser()
            parser.add_argument('--stage')
            parser.add_argument('--target-events', type=int)
            parser.add_argument('--num-events', type=int)
            parser.add_argument('--max-events', type=int)
            parser.add_argument('--nprocs', type=int)
            parser.add_argument('--max-dropped-events', type=int)
            parser.add_argument('--fingerprint')
            parser.add_argument('--system')
            parser.add_argument('--record-dir', required=True)
            parser.add_argument('--submit', action='store_true')
            with patch.dict(os.environ):
                args = config.parse_args(parser, 'cache', ['--config', str(ini), '--stage',
                    'cache', '--nprocs', '1', '--target-events', '2'])
            self.assertEqual((args.target_events, args.num_events, args.max_events), (2, 2, 2))
            self.assertEqual(args.nprocs, 1)
            self.assertEqual(args.max_dropped_events, 3)
            self.assertFalse(args.submit)
            self.assertEqual(args.record_dir, str(Path('records').resolve()))
            ini.write_text('[cache]\nsubmit=true\n')
            with self.assertRaisesRegex(ValueError, 'Unknown key'):
                config.read_config(ini)

    def test_unset_path_variables_are_rejected_before_writes(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'Unresolved environment'):
                config.expand_path('${PSCRATCH}/campaign')

    def test_feeddown_config_boolean_and_cli_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            ini = Path(directory) / 'campaign.ini'
            ini.write_text('[feeddown]\npids=3212 3122\ncombine_antiparticles=false\n')
            def parser():
                result = argparse.ArgumentParser()
                result.add_argument('--pids', type=int, nargs='+', required=True)
                result.add_argument('--combine-antiparticles', action='store_true')
                return result
            args = config.parse_args(parser(), 'analysis', ['--config', str(ini)])
            self.assertEqual(args.pids, [3212, 3122])
            self.assertIs(args.combine_antiparticles, False)
            args = config.parse_args(parser(), 'analysis',
                                     ['--config', str(ini), '--combine-antiparticles', '--pids', '211'])
            self.assertEqual(args.pids, [211])
            self.assertIs(args.combine_antiparticles, True)

    def test_manifest_duplicates_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / 'rows'
            manifest.write_text('design_001 AuAu200\ndesign_001 AuAu200\n')
            with self.assertRaisesRegex(SystemExit, 'duplicate'):
                campaign.read_manifest(manifest)

    def test_shortfall_selection_never_allows_extra_event_directories(self):
        manifest = load_module('manifest_under_test', 'models/cache/make_cache_reap_manifest.py')
        row = {'terminal_frac': 0.9, 'terminal': 9, 'extra_event_dirs': 1}
        self.assertIn('extra_event_dirs', manifest.ready_error(row, 0.8, 10))

    def test_summary_restage_recovery_and_refusal_to_emit_ambiguous_manifest(self):
        load_module('summary_under_test', 'models/summarize_cache_reap_records.py')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / 'design_000_AuAu200_stage1.json'
            second = root / 'design_000_AuAu200_stage2.json'
            old = {'design': 'design_000', 'system': 'AuAu200', 'verdict': 'quarantined',
                   'timestamps': {'end': '2026-10-19T01:00:00Z'},
                   'quarantine_reason': 'current CFS state incomplete != verified_match'}
            new = {'design': 'design_000', 'system': 'AuAu200', 'verdict': 'clean',
                   'timestamps': {'end': '2026-10-20T01:00:00Z'},
                   'dryrun_buckets': {'would_delete': 4}}
            first.write_text(json.dumps(new))
            second.write_text(json.dumps(old))
            manifest = root / 'reap_ready.manifest'
            result = subprocess.run([sys.executable, str(ROOT / 'models/summarize_cache_reap_records.py'),
                                     '--record-dir', str(root), '--reap-manifest', str(manifest)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(manifest.read_text(), 'design_000 AuAu200\n')
            old['verdict'] = 'applied_clean'
            self.assertFalse(workflow_records.restaged_after_quarantine((new, first), (old, second)))
            # A broken record prevents emission, preserving the prior reviewed file.
            (root / 'broken_stage1.json').write_text('{')
            manifest.write_text('previously reviewed\n')
            result = subprocess.run([sys.executable, str(ROOT / 'models/summarize_cache_reap_records.py'),
                                     '--record-dir', str(root), '--reap-manifest', str(manifest)],
                                    cwd=directory, capture_output=True, text=True)
            self.assertEqual(result.returncode, 4)
            self.assertEqual(manifest.read_text(), 'previously reviewed\n')

    def test_aggregate_does_not_resolve_unused_cache_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            ini = Path(directory) / 'campaign.ini'
            ini.write_text('[pipeline]\ncache_cfs_root=${UNSET_CACHE_ROOT}/campaign\n'
                           'analysis_out_dir=artifacts\n')
            parser = argparse.ArgumentParser()
            sub = parser.add_subparsers(dest='command')
            sub.add_parser('run').add_argument('--cache-root')
            sub.add_parser('aggregate').add_argument('--out-dir', required=True)
            args = config.parse_args(parser, 'analysis', ['aggregate', '--config', str(ini)])
            self.assertEqual(args.out_dir, str(Path('artifacts').resolve()))

    def test_survey_no_surface_conflicts_match_cache_and_repair(self):
        survey = load_module('survey_under_test', 'models/cache/survey_raw_designs.py')
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory)
            (event / 'hydro.no_surface').touch()
            (event / 'surface.dat').write_text('nonempty')
            status, _ = survey.classify_event({'hydro.no_surface', 'surface.dat'}, str(event))
            self.assertEqual(status, 'no_surface_conflict')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="NERSC pipeline's fixture ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'raw'
        self.system = self.raw / 'design_000/AuAu200'
        self.system.mkdir(parents=True)
        self.scope = self.root / 'scope.json'
        shutil.copy(ROOT / 'config/cache_fingerprint_AuAu200_gap015.json', self.scope)
        self.baryon = self.root / 'baryon.dat'
        self.baryon.write_text('2212 1\n3122 1\n211 0\n')
        self.mass = self.root / 'pdg.dat'
        self.mass.write_text('pion 0.139 0 - 211\nproton 0.938 0 + 2212\nlambda 1.116 0 + 3122\n')
        self.ini = self.root / 'campaign.ini'
        self.ini.write_text(f'''[run]
directory = {self.raw}
system = AuAu200
nevents = 4
prefix = design
[pipeline]
record_dir = {self.root / 'records'}
survey_json = {self.root / 'survey.json'}
cache_manifest = {self.root / 'cache.manifest'}
reap_manifest = {self.root / 'reap_ready.manifest'}
cache_cfs_root = {self.root / 'published'}
cache_local_root = {self.root / 'staging'}
expected_fingerprint = {self.scope}
pdg_mass = {self.mass}
baryon_dict = {self.baryon}
tmp_dir = {self.root / 'tmp'}
slurm_log_root = {self.root / 'slurm'}
analysis_out_dir = {self.root / 'analysis'}
[cache]
nprocs = 1
validate_events = 4
events_per_shard = 2
max_dropped_events = 0
allow_malformed_rows = 0
designs_per_task = 2
cores = 2
jobs = 1
[analysis_campaign]
jobs = 1
bin_edges = 0 100
n_boot = 0
harmonics = 2 3
''')
        self.env = {key: val for key, val in os.environ.items()
                    if not key.startswith('HIC_') and key not in ('BASH_ENV', 'SLURM_JOB_ID')}
        self.env.update(SLURM_JOB_ID='fixture-allocation', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        for event_id in range(4):
            event = self.system / str(event_id)
            event.mkdir()
            (event / 'afterburner.done').touch()
            particle = event / 'particle_lists.oscar'
            particle.write_text('''#!OSCAR2013 particle_lists t x y z mass p0 px py pz pdg ID charge
# Units: fm fm fm fm GeV GeV GeV GeV GeV none none e
# SMASH-fixture
# event 0 out 2
1 0 0 0 0.139 0.6 0.5 0 0.3 211 0 1
1 0 0 0 0.139 0.6 0.5 0 -0.3 -211 1 -1
# event 0 end 0
# event 1 out 2
1 0 0 0 0.139 0.6 0.5 0 0.3 211 0 1
1 0 0 0 0.139 0.6 0.5 0 -0.3 -211 1 -1
# event 1 end 0
''')
            os.utime(particle, (time.time() - 120, time.time() - 120))

    def invoke(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py'),
                                 '--config', str(self.ini), *args], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        logs = ''
        if result.returncode != expected:
            for log in (self.root / 'records').glob('*.log'):
                logs += '\n' + log.name + '\n' + log.read_text()[-3000:]
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr + logs)
        return result

    @unittest.skipUnless(shutil.which('zstd'), 'zstd required for real publish/reap round trip')
    def test_real_temporary_campaign_survey_cache_publish_reap_and_analysis(self):
        self.invoke('prepare-cache')
        self.assertEqual(json.loads((self.root / 'survey.json').read_text())['rows'][0]['status'], 'ready_to_cache')
        self.assertEqual((self.root / 'cache.manifest').read_text(), 'design_000 AuAu200\n')
        # Merely selecting the cache command is still a dry-run.
        self.invoke('cache', '--execution', 'local')
        self.assertFalse((self.root / 'published').exists())
        self.invoke('cache', '--execution', 'local', '--submit')
        stage1 = json.loads((self.root / 'records/design_000_AuAu200_stage1.json').read_text())
        self.assertEqual(stage1['verdict'], 'clean')
        self.assertEqual(stage1['dryrun_buckets']['would_delete'], 4)
        self.invoke('status', '--nprocs', '1')
        self.assertEqual(json.loads((self.root / 'survey.json').read_text())['rows'][0]['status'], 'ready_to_reap')
        direct = subprocess.run([sys.executable, str(ROOT / 'models/cache/cache_reap_one_design.py'),
                                 'cache', '--config', str(self.ini), '--design', 'design_000'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(direct.returncode, 0, direct.stdout + direct.stderr)
        self.invoke('review')
        self.assertEqual((self.root / 'reap_ready.manifest').read_text(), 'design_000 AuAu200\n')
        # Reap stays a dry-run until explicitly requested, even inside an allocation.
        self.invoke('reap')
        self.assertTrue((self.system / '0/particle_lists.oscar').exists())
        self.invoke('reap', '--submit')
        self.assertTrue((self.system / 'reaped.done').is_file())
        self.assertFalse((self.system / '0').exists())
        self.invoke('status', '--nprocs', '1')
        self.assertEqual(json.loads((self.root / 'survey.json').read_text())['rows'][0]['status'], 'reaped')
        self.invoke('analysis', 'run', '--submit')
        self.invoke('analysis', 'audit')
        self.invoke('analysis', 'aggregate')
        self.invoke('validate')
        import numpy as np
        with np.load(self.root / 'analysis/model_matrix.npz') as matrix:
            self.assertEqual(matrix['Y'].shape, (1, 110))
        matrix_bytes = (self.root / 'analysis/model_matrix.npz').read_bytes()
        # The auxiliary handoff tools consume the same config and published caches.
        self.ini.write_text(self.ini.read_text() + '\n[feeddown]\npids=211\ncombine_antiparticles=true\n')
        self.invoke('analysis', 'feeddown')
        with np.load(self.root / 'analysis/feeddown_yields.npz') as feeddown:
            self.assertEqual(feeddown['Y'].shape, (1, 1))
            self.assertEqual(feeddown['Y'][0, 0], 2.0)
            self.assertTrue(str(feeddown['feature_labels'][0]).startswith('pidpm211__0.0_100.0'))
        self.invoke('analysis', 'feeddown', '--no-combine-antiparticles', '--out', 'signed-feeddown.npz')
        with np.load(self.root / 'signed-feeddown.npz') as feeddown:
            self.assertEqual(feeddown['Y'][0, 0], 1.0)
        inventory = self.invoke('inventory', '--design', 'design_000', '--pdg', '211', '--abs', '--csv')
        self.assertTrue(inventory.stdout.startswith('pdg,abs,'))
        self.assertEqual(float(inventory.stdout.splitlines()[1].split(',')[4]), 2.0)
        self.invoke('analysis-design', 'validate', '--design', 'design_000')
        self.invoke('analysis-design', 'reduce', '--design', 'design_000', '--json', 'single.json')
        reduction = json.loads((self.root / 'single.json').read_text())
        self.assertEqual([(b['cent_low'], b['cent_high']) for b in reduction['bins']], [(0, 100)])
        # Explicit post-processing still runs when extraction is skipped on resume.
        self.invoke('analysis-design', 'run', '--design', 'design_000', '--reduce-json', 'resumed.json')
        self.assertTrue((self.root / 'resumed.json').is_file())
        self.assertEqual((self.root / 'analysis/model_matrix.npz').read_bytes(), matrix_bytes)
        # Fresh readiness selection excludes the reaped design by its existing records.
        self.invoke('prepare-cache')
        self.assertEqual((self.root / 'cache.manifest').read_text(), '')

    def test_batch_freezes_rows_arguments_and_cuts_without_shell_interpolation(self):
        (self.root / 'cache.manifest').write_text('design_000 AuAu200\n')
        wrapper = self.root / 'wrapper.py'
        wrapper.write_text('''import json, os, sys
from pathlib import Path
Path(os.environ['CAPTURE']).write_text(json.dumps(dict(args=sys.argv[1:], drop=os.getenv('HIC_MAX_DROPPED_EVENTS'))))
''')
        reason = 'literal $(touch SHOULD_NOT_EXIST) "quoted"'
        self.invoke('cache', '--wrapper', str(wrapper), '--accept-shortfall', '--shortfall-reason', reason)
        script = self.root / 'records/_submit_cache.sbatch'
        text = script.read_text()
        self.assertIn('#SBATCH --cpus-per-task=2', text)
        self.assertEqual(subprocess.run(['bash', '-n', str(script)]).returncode, 0)
        # These edits must not alter an already-rendered array task.
        (self.root / 'cache.manifest').write_text('design_999 AuAu200\n')
        self.ini.write_text(self.ini.read_text().replace('max_dropped_events = 0', 'max_dropped_events = 9'))
        env = {**self.env, 'SLURM_ARRAY_TASK_ID': '0', 'CAPTURE': str(self.root / 'captured.json')}
        result = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        captured = json.loads((self.root / 'captured.json').read_text())
        self.assertIn('design_000', captured['args'])
        self.assertNotIn('design_999', captured['args'])
        self.assertIn(reason, captured['args'])
        self.assertEqual(captured['drop'], '0')
        self.assertFalse((self.root / 'SHOULD_NOT_EXIST').exists())
        self.scope.write_text('{}')
        result = subprocess.run(['bash', str(script)], cwd=self.root, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Frozen fingerprint changed', result.stderr)

    def test_parallel_wrapper_output_goes_to_logs_and_quarantines_propagate(self):
        (self.root / 'cache.manifest').write_text('design_000 AuAu200\ndesign_001 AuAu200\n')
        wrapper = self.root / 'noisy_wrapper.py'
        wrapper.write_text('import sys\nprint("FULL_BACKEND_DETAIL" * 1000)\n'
                           'raise SystemExit(2 if "design_001" in sys.argv else 0)\n')
        result = self.invoke('cache', '--execution', 'local', '--submit', '--jobs', '2',
                             '--wrapper', str(wrapper), expected=2)
        self.assertNotIn('FULL_BACKEND_DETAIL', result.stdout)
        self.assertIn('1 successful, 1 quarantined, 0 failed', result.stdout)
        self.assertIn('FULL_BACKEND_DETAIL',
                      (self.root / 'records/design_000_AuAu200_cache_dispatch.log').read_text())

    def test_empty_cache_manifest_is_a_successful_no_work_result(self):
        (self.root / 'cache.manifest').write_text('')
        result = self.invoke('cache')
        self.assertIn('nothing to cache/reap', result.stdout)
        self.assertFalse((self.root / 'records').exists())

    def test_fill_uses_campaign_paths_and_reports_failed_srun_steps(self):
        plan = self.root / 'fill.plan'
        plan.write_text('design_000 1 afterburner false 1 AuAu200\n')
        site = self.root / 'site.sh'
        site.write_text('export HIC_SURFACE_ARCHIVE_ROOT=/site/default\n'
                        'export HIC_SURFACE_ARCHIVE_BASE=/site/source\n'
                        'export HIC_SOFTWARE_ROOT=/personal/software\n')
        srun = self.root / 'srun'
        srun.write_text(f'#!{sys.executable}\n' + """import json, os, sys
from pathlib import Path
Path(os.environ['CAPTURE_FILL']).write_text(json.dumps(dict(args=sys.argv[1:], archive=os.getenv('HIC_SURFACE_ARCHIVE_ROOT'), base=os.getenv('HIC_SURFACE_ARCHIVE_BASE'))))
raise SystemExit(7)
""")
        srun.chmod(0o755)
        self.env.update(PATH=str(self.root) + os.pathsep + self.env['PATH'],
                        LOG_ROOT=str(self.root / 'fill-logs'), HIC_PATHS_FILE=str(site),
                        HIC_SURFACE_ARCHIVE_ROOT='/chosen/archive', CAPTURE_FILL=str(self.root / 'fill.json'))
        self.env['BASH_FUNC_conda%%'] = '() { return 0; }'
        self.env['BASH_FUNC_module%%'] = '() { return 0; }'
        with self.ini.open('a') as stream:
            stream.write(f'\n[repair]\narchive_base={self.root}\n')
        self.invoke('fill', str(plan), expected=1)
        call = json.loads((self.root / 'fill.json').read_text())
        self.assertEqual(call['archive'], '/chosen/archive')
        self.assertEqual(call['base'], str(self.root))
        self.assertEqual(call['args'][-1], str(self.system))
        index = call['args'].index('--nevents')
        self.assertEqual(call['args'][index + 1], '4')
        plan.write_text('design_000 1 afterburner false 1 OO5360\n')
        result = self.invoke('fill', str(plan), expected=1)
        self.assertIn('plan is for OO5360', result.stderr)

    def test_fill_plan_stdout_contains_only_plan_rows(self):
        event = self.system / '0'
        (event / 'afterburner.done').unlink()
        (event / 'particle_lists.oscar').unlink()
        (event / 'hydro.done').touch()
        (event / 'surface.dat').write_text('surface')
        result = self.invoke('fill-plan')
        fields = result.stdout.split()
        self.assertEqual(len(fields), 6)
        self.assertEqual(fields[0], 'design_000')
        self.assertEqual(fields[-1], 'AuAu200')

    def test_local_work_refuses_execution_without_an_allocation(self):
        (self.root / 'cache.manifest').write_text('design_000 AuAu200\n')
        self.env.pop('SLURM_JOB_ID')
        result = self.invoke('cache', '--execution', 'local', '--submit', expected=2)
        self.assertIn('requires a Slurm allocation', result.stderr)
        self.assertFalse((self.root / 'published').exists())

    def test_missing_requested_cumulant_fails_validation(self):
        import numpy as np
        out = self.root / 'analysis'
        out.mkdir()
        np.savez(out / 'model_matrix.npz', Y=[[1., 1.]], Y_err=[[.1, .1]],
                 feature_labels=['charged__0_100__v2_2', 'charged__0_100__c2_gap'])
        self.invoke('validate', expected=1)

    def test_per_design_system_override_updates_all_derived_paths(self):
        result = subprocess.run([sys.executable, str(ROOT / 'models/cache/cache_reap_one_design.py'),
                                 'reap', '--config', str(self.ini), '--design', 'design_000',
                                 '--system', 'OO5360', '--apply'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        # No stage1 record exists: refusal occurs before any reaper is called.
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        record = json.loads((self.root / 'records/design_000_OO5360_stage2.json').read_text())
        self.assertEqual(record['raw_design'], str(self.raw / 'design_000/OO5360'))
        self.assertEqual(record['cfs_cache'], str(self.root / 'published/design_000/OO5360'))


if __name__ == '__main__':
    unittest.main()
