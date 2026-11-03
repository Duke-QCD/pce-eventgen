"""MAP optimization, calibrated replay and observable plots on small synthetic fits."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault('MPLCONFIGDIR', '/tmp/pce-inference-test-matplotlib')
for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ[name] = '1'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from scipy.stats import multivariate_normal
import inference as I
import joint_inference as J
import plot_map_observables as M
from test_joint_inference import make_study

STATE_KEYS = ('SYSTEM', 'DESIGN_DAT', 'MATRIX_NPZ', 'DATA_DIR', 'OUT_DIR', 'OUT_PREFIX', 'PROFILE',
              'DATA_SPEC', 'DROP_PARAMS', 'FIXED_PARAMS', 'FEEDDOWN_NPZ', 'FEEDDOWN_CORR', 'SYSTEM_DROP')


class MapMathTests(unittest.TestCase):
    def test_physical_map_retains_correlated_measurement_errors(self):
        covariance = np.array([[4., 1.], [1., 9.]])
        emulator = mock.Mock()
        emulator.predict.side_effect = lambda x, return_std: (
            np.tile([1., 2.], (len(x), 1)), np.tile([.2, .3], (len(x), 1)))
        fit = SimpleNamespace(sigmas=np.sqrt(covariance.diagonal()), covariance=covariance,
                              free_ranges=np.array([[0., 1.]]), free_names=['theta'], free_pos=[0],
                              template=np.zeros(1), emulator=emulator, measured=np.zeros(2))
        expected = multivariate_normal.logpdf([1., 2.], cov=covariance+np.diag([.04, .09]))
        self.assertAlmostEqual(M.physical_logprob(fit)([[.5]])[0], expected)

    def test_physical_map_is_refined_beyond_chain_median_without_a_logit_jacobian(self):
        samples = np.array([[11.], [12.], [13.]])
        logprob = lambda x: -((np.atleast_2d(x)[:, 0]-19.)/.2)**2/2
        point, result = M.estimate_map(logprob, samples, np.array([[10., 20.]]), starts=2, maxiter=100)
        self.assertAlmostEqual(point[0], 19., places=5)
        self.assertGreater(result['log_likelihood'], result['best_initial_log_likelihood'])
        self.assertTrue(result['optimizer_converged'])

    def test_gaussian_normalization_emulator_variance_and_prior_boundaries_are_used(self):
        class Emulator:
            def predict(self, x, return_std):
                return np.zeros((len(x), 1)), .2+x
        fit = SimpleNamespace(sigmas=np.array([.1]), covariance=None, free_ranges=np.array([[0., 1.]]),
                              free_names=['theta'], free_pos=[0], template=np.zeros(1),
                              emulator=Emulator(), measured=np.zeros(1))
        logprob = M.physical_logprob(fit)
        points = np.array([[0.], [.5], [1.]])
        np.testing.assert_allclose(logprob(points), -.5*np.log(2*np.pi*(.1**2+(.2+points[:, 0])**2)))
        point, result = M.estimate_map(logprob, points, fit.free_ranges, starts=1, maxiter=40)
        self.assertAlmostEqual(point[0], 0., places=7)
        self.assertEqual(result['boundary_positions'], [0])
        self.assertTrue(np.isneginf(logprob([[-.01], [1.01]])).all())

    def test_invalid_search_options_and_nonfinite_starts_are_rejected(self):
        samples = np.array([[.2], [.8]])
        ranges = np.array([[0., 1.]])
        for options in (dict(max_samples=0), dict(starts=-1), dict(seed=-1), dict(batch_size=0)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                M.estimate_map(lambda x: np.zeros(len(x)), samples, ranges, **options)
        with self.assertRaisesRegex(ValueError, 'No finite'):
            M.estimate_map(lambda x: np.full(len(x), -np.inf), samples, ranges)


class ObservableCoverageTests(unittest.TestCase):
    def test_all_oo_groups_and_bins_appear_in_map_prior_and_posterior_grids(self):
        yields = {'dNch_deta', 'dN_dy_K0S_vs_mult', 'dN_dy_Xi_vs_mult', 'dN_dy_Omega_vs_mult',
                  'proxy_dN_dy_pion_vs_mult', 'proxy_dN_dy_kaon_vs_mult', 'proxy_dN_dy_proton_vs_mult'}
        ptflow = {'mean_pT_charged', 'mean_pT_K0S_vs_mult', 'mean_pT_Xi_vs_mult',
                  'mean_pT_Omega_vs_mult', 'v2_charged', 'v3_charged'}
        names = [spec['name'] for spec in I.DATA_SPEC_OO5360]
        names += ['proxy_dN_dy_pion_vs_mult', 'proxy_dN_dy_kaon_vs_mult', 'proxy_dN_dy_proton_vs_mult']
        labels = [f'{name}__{lo}_{lo+5}' for name in names for lo in (0, 5)]
        measured = np.arange(1., len(labels)+1)
        sigmas = .05*measured
        mean = .95*measured
        std = .02*mean
        predictions = mean[None, :]*np.array([.9, 1., 1.1])[:, None]
        expected = {}
        for name in names:
            indices = [j for j, label in enumerate(labels) if label.split('__')[0] == name]
            shown = name.replace('c2_charged', 'v2_charged').replace('c3_charged', 'v3_charged')
            values = np.sqrt(measured[indices]) if name.startswith(('c2_', 'c3_')) else measured[indices]
            expected[I.OBS_LABELS[shown]] = values
        saved = []

        def inspect_figure(fig, *args, **kwargs):
            axes = [ax for ax in fig.axes if ax.axison]
            panel_labels = {ax.get_ylabel() for ax in axes}
            self.assertIn(panel_labels, [{I.OBS_LABELS[name] for name in yields},
                                         {I.OBS_LABELS[name] for name in ptflow}])
            for ax in axes:
                # Inspect actual plotted data, including both bins in each panel.
                data_line = ax.containers[0].lines[0]
                np.testing.assert_array_equal(data_line.get_xdata(), [2.5, 7.5])
                np.testing.assert_allclose(data_line.get_ydata(), expected[ax.get_ylabel()])
            saved.append(panel_labels)

        with mock.patch('matplotlib.figure.Figure.savefig', autospec=True, side_effect=inspect_figure), I.plt.rc_context():
            M.F.set_pub_style()
            for group in ('yields', 'ptflow'):
                self.assertTrue(M.map_grid(mean, std, labels, measured, sigmas,
                                           Path('unused.png'), 'OO5360', group))
            displayed, shown, data, errors = M.F.to_v_space(predictions, labels, measured, sigmas)
            for kind in ('prior', 'posterior'):
                for group in ('yields', 'ptflow'):
                    M.F.summary_grid(displayed, shown, data, errors, 'unused.pdf', kind, group,
                                     system='OO5360')
        self.assertEqual(len(saved), 6)
        self.assertEqual(set.union(*saved), set(expected))


class MapPlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        state = {k: getattr(I, k) for k in STATE_KEYS}
        cls.tmp = tempfile.TemporaryDirectory(prefix='pce-map-tests-')
        cls.root = Path(cls.tmp.name)
        try:
            cls.config = make_study(cls.root/'inputs')
            cls.study = J.load_study(cls.config)
            cls.joint = cls.root/'joint'
            cls.single = cls.root/'single'
            cls.oo_single = cls.root/'oo-single'
            with contextlib.redirect_stdout(io.StringIO()):
                J.run(J.prepare_study(cls.study), cls.joint, nsteps=8, nburn=2, nwalkers=8,
                      nsplits=2, make_plots=False)
                I.use_system('AuAu200')
                I.configure_parameters(shear_mode='flat')
                spec = cls.study.systems[0]
                I.main(matrix_path=spec.matrix, design_path=cls.study.design, data_dir=spec.data_dir,
                       out_dir=cls.single, drop_specs=spec.drop_specs, feeddown='', nsteps=8, nburn=2,
                       nwalkers=8, n_splits=2, make_plots=False)
                I.use_system('OO5360')
                I.configure_parameters(shear_mode='flat')
                spec = cls.study.systems[1]
                I.main(matrix_path=spec.matrix, design_path=cls.study.design, data_dir=spec.data_dir,
                       out_dir=cls.oo_single, drop_specs=spec.drop_specs, feeddown='', nsteps=8, nburn=2,
                       nwalkers=8, n_splits=2, make_plots=False)
        finally:
            for key, value in state.items():
                setattr(I, key, value)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.state = {key: getattr(I, key) for key in STATE_KEYS}

    def tearDown(self):
        for key, value in self.state.items():
            setattr(I, key, value)

    def test_single_and_joint_outputs_preserve_slopes_and_shared_map_parameters(self):
        for kind, run_dir, prefix, nparams, nsystems in [
            ('single', self.single, 'auau', 14, 1), ('oo-single', self.oo_single, 'oo', 14, 1),
            ('joint', self.joint, 'joint', 15, 2)]:
            with self.subTest(kind=kind):
                output = self.root/f'map-{kind}'
                saved = (run_dir/f'{prefix}_posterior.npz').read_bytes()
                with contextlib.ExitStack() as stack:
                    stack.enter_context(mock.patch.object(I, 'ensemble_sample', side_effect=AssertionError('MCMC repeated')))
                    stack.enter_context(mock.patch.object(I, 'kfold_closure', side_effect=AssertionError('closure repeated')))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    self.assertEqual(M.main(['--run-dir', str(run_dir), '--out-dir', str(output),
                                             '--max-samples', '12', '--starts', '1', '--maxiter', '20',
                                             '--format', 'both' if prefix == 'auau' else 'png']), 0)
                record = json.loads((output/'map_result.json').read_text())
                self.assertEqual(record['fit_kind'], 'joint' if kind == 'joint' else 'single')
                self.assertEqual(len(record['parameters']), nparams)
                self.assertEqual(record['parameters']['eta_low'], 0.)
                self.assertEqual(record['parameters']['eta_high'], 0.)
                self.assertEqual(len(record['systems']), nsystems)
                self.assertAlmostEqual(sum(s['log_likelihood'] for s in record['systems']),
                                       record['search']['log_likelihood'], places=8)
                for system in record['systems']:
                    for key, value in system['parameters'].items():
                        self.assertEqual(value, record['parameters'][key])
                    self.assertEqual(len(system['figures']), 4 if prefix == 'auau' else 2)
                    for path in map(Path, system['figures']):
                        self.assertTrue(path.read_bytes().startswith(b'%PDF' if path.suffix == '.pdf' else b'\x89PNG'))
                self.assertEqual((run_dir/f'{prefix}_posterior.npz').read_bytes(), saved)

    def test_recorded_uncertainty_calibration_is_restored(self):
        with contextlib.redirect_stdout(io.StringIO()):
            fit = M.load_fit(self.single)
        saved = json.loads(str(I.read_npz(self.single/'auau_posterior.npz')['config_json']))
        np.testing.assert_array_equal(fit.emulator.var_inflation, saved['emulator_variance_inflation'])
        self.assertEqual(fit.reconstruction['method'], 'saved')

    def test_older_single_fit_recomputes_uncertainty_with_an_explicit_warning(self):
        output = self.root/'legacy'; output.mkdir()
        arrays = I.read_npz(self.single/'auau_posterior.npz')
        record = json.loads(str(arrays['config_json']))
        record.pop('emulator_settings'); record.pop('emulator_variance_inflation')
        arrays['config_json'] = np.array(I.config_json(record))
        np.savez(output/'auau_posterior.npz', **arrays)
        with self.assertWarnsRegex(UserWarning, 'Older fit'), contextlib.redirect_stdout(io.StringIO()):
            fit = M.load_fit(output)
        self.assertEqual(fit.reconstruction['method'], 'recomputed_legacy')
        self.assertEqual(fit.reconstruction['nsplits'], 2)

    def test_changed_inputs_fail_before_training_or_writes(self):
        matrix = self.root/'changed.npz'
        original = self.study.systems[0].matrix
        arrays = I.read_npz(original); arrays['Y'] *= 1.01
        np.savez(matrix, **arrays)
        matrix.with_suffix('.meta.json').write_bytes(original.with_suffix('.meta.json').read_bytes())
        output = self.root/'bad-output'
        with mock.patch.object(I, 'PCAGPEmulator') as train, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                M.main(['--run-dir', str(self.single), '--matrix', str(matrix), '--out-dir', str(output)])
        train.assert_not_called()
        self.assertFalse(output.exists())

    def test_existing_output_is_refused_before_training(self):
        with mock.patch.object(M, 'load_fit') as load, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                M.main(['--run-dir', str(self.single), '--out-dir', str(self.single)])
        load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
