"""Joint inference contracts using disjoint, unsorted synthetic system grids."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import inference as I
import joint_inference as J


def make_study(root):
    root.mkdir(parents=True)
    design, _, _, _ = I.load_design(J.DEFAULT_DESIGN)
    design = design[:16]
    names = [p for p, _, _ in I.PARAMS_ALL]
    lower = np.array([lo for _, lo, _ in I.PARAMS_ALL])
    width = np.array([hi-lo for _, lo, hi in I.PARAMS_ALL])
    unit = (design-lower)/width
    np.savetxt(root / "design.dat", design, header="Parameter " + " ".join(names))
    systems = []
    for name, indices in [("AuAu200", [11, 8, 4, 2, 14, 3, 6, 10]),
                          ("OO5360", [7, 1, 12, 5, 0, 9, 15])]:
        folder = root / name
        folder.mkdir()
        cfg = I.system_config(name)
        specs = [s for s in cfg['data_spec'] if s['name'] in ('dNch_deta', 'c2_charged')]
        for spec in specs:
            values = [300., 100.] if spec['kind'] == 'yield' else np.sqrt([.005, .008])
            (folder / spec['files'][0]).write_text(''.join(
                f'{lo} {lo+5} {lo+2.5} {value} {value*.05}\n' for lo, value in zip((0, 5), values)))
        labels = [f'charged__{lo}_{lo+5}__{obs}' for obs in ('dNch_deta', 'c2_gap') for lo in (0, 5)]
        factor = .8+.3*unit[:, names.index(cfg['norm'])]+.08*unit[:, names.index('gamma_s_0')]
        flow = .9+.1*unit[:, names.index('eta_min')]-.05*unit[:, names.index('eta_low')]
        values = np.column_stack([300*factor, 100*factor, .005*flow, .008*flow])[indices]
        matrix = folder / 'matrix.npz'
        np.savez(matrix, Y=values, Y_err=.01*values, feature_labels=np.array(labels),
                 design_index=np.array(indices), design_name=np.array([f'design_{i:03d}' for i in indices]))
        meta = dict(system=name, analysis_profile=cfg['profile'], synthetic=True)
        if name == 'OO5360':
            meta['charged_flow_sample_usage'] = [dict(design_index=i, design_name=f'design_{i:03d}',
                bins=[dict(cent_low=lo, cent_high=lo+5, same_full_pair_weight_fraction=0.,
                           same_full_fallback_events=0) for lo in (0, 5)]) for i in indices]
        matrix.with_suffix('.meta.json').write_text(json.dumps(meta))
        systems.append(dict(system=name, matrix=f'{name}/matrix.npz', data_dir=name,
                            drop_specs=[s['name'] for s in cfg['data_spec'] if s not in specs]))
    path = root / 'study.json'
    path.write_text(json.dumps(dict(schema=1, design='design.dat', shear_mode='flat', systems=systems)))
    return path


class JointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='pce-joint-tests-')
        cls.root = Path(cls.tmp.name)
        cls.config = make_study(cls.root / 'inputs')
        cls.study = J.load_study(cls.config)
        cls.fits = {}
        for mode in I.SHEAR_MODES:
            problem = J.prepare_study(replace(cls.study, shear_mode=mode))
            output = cls.root / mode
            with contextlib.redirect_stdout(io.StringIO()):
                J.run(problem, output, nsteps=8, nburn=2, nwalkers=8, nsplits=2, make_plots=False)
            cls.fits[mode] = output

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.previous = I.SYSTEM, I.DESIGN_DAT, dict(I.FIXED_PARAMS)
        self.addCleanup(self.restore_globals)

    def restore_globals(self):
        I.use_system(self.previous[0])
        I.DESIGN_DAT = self.previous[1]
        I.FIXED_PARAMS = self.previous[2]

    def prepared(self, study=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return J.prepare_study(study or self.study)

    def test_independent_contexts_keep_all_disjoint_unsorted_rows_and_priors(self):
        I.use_system('OO5360')
        I.FIXED_PARAMS = {'k': 1.3}
        before = I.SYSTEM, I.MATRIX_NPZ, I.DATA_DIR, dict(I.FIXED_PARAMS), list(I.DATA_SPEC)
        problem = self.prepared()
        after = I.SYSTEM, I.MATRIX_NPZ, I.DATA_DIR, dict(I.FIXED_PARAMS), list(I.DATA_SPEC)
        self.assertEqual(before, after)
        self.assertEqual(len(problem.free_names), 13)
        self.assertEqual(len(problem.pnames), 15)
        self.assertNotIn('norm2760', problem.pnames)
        self.assertNotIn('norm5020', problem.pnames)
        self.assertEqual(problem.fixed_params, {'eta_low': 0., 'eta_high': 0.})
        a, o = problem.components
        self.assertEqual(len(a.inputs.X), 8)
        self.assertEqual(len(o.inputs.X), 7)
        self.assertEqual(set(a.inputs.indices) & set(o.inputs.indices), set())
        full = np.loadtxt(self.study.design)
        for c in problem.components:
            positions = [i for i, p in enumerate(I.PARAMS_ALL) if p[0] in c.inputs.pnames]
            np.testing.assert_array_equal(c.inputs.X, full[c.inputs.indices][:, positions])
        self.assertEqual(len(set(problem.labels)), len(problem.labels))

    def test_likelihood_addition_normalization_isolation_and_one_jacobian(self):
        problem = self.prepared(replace(self.study, shear_mode='temperature-dependent'))
        class LinearEmulator:
            def __init__(self, c):
                self.norm = c.inputs.pnames.index(I.system_config(c.spec.system)['norm'])
                self.w = c.inputs.pnames.index('w')
            def predict(self, x, return_std=False):
                mean = np.repeat((x[:, self.norm]+2*x[:, self.w])[:, None], 4, axis=1)
                return (mean, np.full_like(mean, .2)) if return_std else mean
        emulators = [LinearEmulator(c) for c in problem.components]
        joint = J.JointEmulator(problem, emulators)
        theta = problem.ranges.mean(axis=1)[None, :]
        base = joint.predict(theta)
        changed = theta.copy(); changed[:, problem.pnames.index('norm200')] += 1
        np.testing.assert_allclose(joint.predict(changed)-base, [[1, 1, 1, 1, 0, 0, 0, 0]])
        changed = theta.copy(); changed[:, problem.pnames.index('w')] += .1
        np.testing.assert_allclose(joint.predict(changed)-base, .2)
        expected = np.zeros(1)
        for c, emu in zip(problem.components, emulators):
            mean, std = emu.predict(theta[:, c.projection], return_std=True)
            expected += I.gaussian_measurement_loglike(c.inputs.sigmas)(mean-c.inputs.measured, std)
        physical = I.make_logprob(joint, problem.measured, problem.sigmas, problem.free_ranges)
        logit = I.make_logprob_u(joint, problem.measured, problem.sigmas, problem.free_ranges,
                                problem.free_pos, problem.template)
        np.testing.assert_allclose(physical(theta), expected)
        np.testing.assert_allclose(logit(np.zeros_like(theta)), expected+15*np.log(.25))

    def test_within_system_covariance_is_retained_without_cross_system_terms(self):
        covariance = np.diag([225., 25., .000001, .000001])
        covariance[0, 1] = covariance[1, 0] = 10.
        # Match the actual diagonal errors produced by the adapters.
        problem = self.prepared()
        covariance[np.diag_indices(4)] = problem.components[0].inputs.sigmas**2
        with mock.patch.object(I, 'proxy_measurement_covariance', side_effect=[covariance, None]):
            combined = self.prepared()
        np.testing.assert_array_equal(combined.covariance[:4, :4], covariance)
        np.testing.assert_array_equal(combined.covariance[:4, 4:], 0.)
        np.testing.assert_array_equal(combined.covariance[4:, 4:], np.diag(combined.sigmas[4:]**2))

    def test_saved_fits_restore_both_parameter_modes_and_design_provenance(self):
        for mode, count in [('flat', 13), ('temperature-dependent', 15)]:
            with self.subTest(mode=mode):
                with contextlib.redirect_stdout(io.StringIO()):
                    problem, samples, record = J.restore(self.fits[mode])
                self.assertEqual(samples.shape, (64, count))
                self.assertEqual(problem.study.shear_mode, mode)
                self.assertEqual([len(s['usable_design_indices']) for s in record['system_inputs']], [8, 7])
                self.assertFalse(list(self.fits[mode].rglob('*.pdf')))
                diagnostics = json.loads((self.fits[mode]/'joint_diagnostics.json').read_text())
                self.assertFalse(diagnostics['converged'])
                for c in problem.components:
                    validation = I.read_npz(self.fits[mode]/c.spec.system/'emulator_validation.npz')
                    self.assertEqual(set(validation['design_index']), set(c.inputs.indices))
                    self.assertNotIn('rows', validation)
                    # Each stacked held-out row is the model output of the design ID saved beside it.
                    position = {int(d): i for i, d in enumerate(c.inputs.indices)}
                    np.testing.assert_array_equal(
                        validation['truth'], c.inputs.Y[[position[int(d)] for d in validation['design_index']]])
                    self.assertTrue(np.all(validation['var_inflation'] >= 1))

    def test_plot_run_rejects_a_viscosity_override(self):
        for mode in I.SHEAR_MODES:
            stderr = io.StringIO()
            with self.subTest(mode=mode), contextlib.redirect_stderr(stderr), \
                    mock.patch.object(J, 'replay') as replay, self.assertRaises(SystemExit) as caught:
                J.main(['--plot-run', str(self.fits['flat']), '--shear-mode', mode])
            self.assertEqual(caught.exception.code, 1)
            self.assertIn('--shear-mode', stderr.getvalue())
            replay.assert_not_called()

    def test_replay_checks_contents_and_shear_policy_accepts_identical_relocation(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            copied = Path(directory)/'inputs'
            shutil.copytree(self.config.parent, copied)
            relocated = J.load_study(copied/'study.json')
            with contextlib.redirect_stdout(io.StringIO()):
                J.restore(self.fits['flat'], relocated)
                with self.assertRaisesRegex(ValueError, 'differ'):
                    J.restore(self.fits['flat'], replace(relocated, shear_mode='temperature-dependent'))
                matrix = relocated.systems[0].matrix
                arrays = I.read_npz(matrix); arrays['Y'] *= 1.01
                np.savez(matrix, **arrays)
                with self.assertRaisesRegex(ValueError, 'input contents'):
                    J.restore(self.fits['flat'], relocated)

    def test_replay_draws_are_shared_and_no_sampling_or_closure_is_repeated(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            output = Path(directory)/'fit'
            shutil.copytree(self.fits['flat'], output)
            original = (output/'joint_posterior.npz').read_bytes()
            with contextlib.ExitStack() as stack:
                stack.enter_context(mock.patch.object(I, 'ensemble_sample', side_effect=AssertionError('resampled')))
                stack.enter_context(mock.patch.object(I, 'kfold_closure', side_effect=AssertionError('closure repeated')))
                corners = stack.enter_context(mock.patch.object(I, 'make_corner_plots'))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                J.replay(output, plot_npost=12, plot_nprior=10, corner_font_scale=1.)
            self.assertEqual(len(corners.call_args.args[1]), 13)
            self.assertEqual(corners.call_args.kwargs['param_labels']['eta_min'], r'$\eta/s$')
            self.assertEqual((output/'joint_posterior.npz').read_bytes(), original)
            predictions = []
            for c in self.study.systems:
                prefix = I.system_config(c.system)['prefix']
                arrays = I.read_npz(output/c.system/f'{prefix}_predictive.npz')
                self.assertEqual(arrays['posterior_mean'].shape, (12, 4))
                predictions.append(arrays['posterior_sample_indices'])
                self.assertEqual(len(list((output/c.system).glob('*Summary*.pdf'))), 4)
            np.testing.assert_array_equal(*predictions)

    def test_joint_cli_runs_from_another_directory(self):
        output = self.root/'cli-fit'
        result = subprocess.run([sys.executable, str(ROOT/'joint_inference.py'), '--config', str(self.config),
                                 '--out-dir', str(output), '--no-plots', '--nsteps', '8', '--nburn', '2',
                                 '--nwalkers', '8', '--nsplits', '2'], cwd='/tmp',
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertEqual(I.read_npz(output/'joint_posterior.npz')['samples'].shape, (64, 13))

    def test_profile_fallback_and_system_guards_survive_explicit_contexts(self):
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            copied = Path(directory)/'inputs'
            shutil.copytree(self.config.parent, copied)
            study = J.load_study(copied/'study.json')
            meta_path = study.systems[1].matrix.with_suffix('.meta.json')
            original = json.loads(meta_path.read_text())
            for change, message in [(dict(system='AuAu200'), 'active system'),
                                    (dict(analysis_profile='baseline'), 'analysis_profile'),
                                    (dict(charged_flow_sample_usage=[]), 'design_index')]:
                meta_path.write_text(json.dumps(original | change))
                with self.subTest(change=change), self.assertRaisesRegex(ValueError, message):
                    self.prepared(study)

    def test_invalid_studies_fail_closed_and_preflight_writes_nothing(self):
        raw = json.loads(self.config.read_text())
        for change in [dict(shear_mode='typo'), dict(schema=True), dict(unknown_option=1),
                       dict(systems=[raw['systems'][0]]*2), dict(systems=raw['systems'][:1])]:
            with self.subTest(change=change), self.assertRaises((ValueError, KeyError)):
                J.parse_study(raw | change, self.config.parent)
        output = self.root/'preflight-unused'
        result = subprocess.run([sys.executable, str(ROOT/'joint_inference.py'), '--config', str(self.config),
                                 '--check-inputs', '--out-dir', str(output)], cwd='/tmp',
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('13 sampled parameters', result.stdout)
        self.assertFalse(output.exists())

    def test_existing_output_is_refused_and_emulator_only_skips_sampling(self):
        problem = self.prepared()
        with self.assertRaises(FileExistsError):
            J.run(problem, self.fits['flat'], nsteps=8, nburn=2, nwalkers=8, nsplits=2)
        output = self.root/'emulator-only'
        with mock.patch.object(I, 'ensemble_sample', side_effect=AssertionError('sampled')):
            with contextlib.redirect_stdout(io.StringIO()):
                J.run(problem, output, nsteps=8, nburn=2, nwalkers=8, nsplits=2,
                      emulator_only=True, make_plots=False)
        self.assertTrue((output/'joint_run.json').is_file())
        self.assertFalse((output/'joint_posterior.npz').exists())


if __name__ == '__main__':
    unittest.main()
