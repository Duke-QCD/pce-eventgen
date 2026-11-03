"""O-O input/likelihood contracts using synthetic fixtures, without study data."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.stats import multivariate_normal
import inference as I
import build_oo_yield_proxies as P


class OOInputTests(unittest.TestCase):
    def setUp(self):
        self.previous = I.SYSTEM, I.DESIGN_DAT
        self.tmp = tempfile.TemporaryDirectory(prefix="pce-oo-inputs-")
        self.root = Path(self.tmp.name)
        self.addCleanup(self.restore)

    def restore(self):
        I.use_system(self.previous[0])
        I.DESIGN_DAT = self.previous[1]
        self.tmp.cleanup()

    def charged_grid(self, fallback=0.):
        data = self.root / "centrality"; data.mkdir()
        specs = I.DATA_SPEC_OO5360[:4]
        for spec in specs:
            value = .1 if spec["kind"] == "flow" else .6 if spec["kind"] == "meanpt" else 100.
            (data / spec["files"][0]).write_text(f"0 5 2.5 {value} {value*.1}\n5 10 7.5 {value} {value*.1}\n")
        pairs = [("dNch_deta", 100.), ("mean_pT", .6), ("c2_gap", .01), ("c3_gap", .001)]
        labels = [f"charged__{lo}_{lo+5}__{obs}" for obs, _ in pairs for lo in (0, 5)]
        values = np.tile([v for _, v in pairs for _ in (0, 5)], (3, 1))
        matrix = self.root / "matrix.npz"
        np.savez(matrix, Y=values, Y_err=.01*np.abs(values), feature_labels=np.array(labels),
                 design_index=np.array([2, 0, 1]), design_name=np.array(["design_002", "design_000", "design_001"]))
        usage = [dict(design_index=i, design_name=f"design_{i:03d}", bins=[dict(cent_low=lo,
                     cent_high=lo+5, same_full_pair_weight_fraction=fallback,
                     same_full_fallback_events=int(fallback>0)) for lo in (0, 5)]) for i in (2, 0, 1)]
        matrix.with_suffix(".meta.json").write_text(json.dumps(dict(system="OO5360",
                    analysis_profile=I.OO_PROFILE, charged_flow_sample_usage=usage)))
        registry = I.SYSTEMS["OO5360"] | dict(matrix=str(matrix), data_dir=str(data),
                                             data_spec=specs, primary_drop=())
        patch = mock.patch.dict(I.SYSTEMS, {"OO5360": registry});patch.start()
        self.addCleanup(patch.stop)
        I.use_system("OO5360")
        return matrix, data

    def test_oo_profile_and_flow_assembly_without_bundled_measurements(self):
        matrix, data = self.charged_grid()
        inputs = I.prepare_inputs(matrix, data_dir=data, drop_specs=(), feeddown="")
        self.assertEqual(inputs.X.shape, (3, 14))
        self.assertEqual(inputs.Y.shape, (3, 8))
        np.testing.assert_array_equal(inputs.indices, [2, 0, 1])
        np.testing.assert_allclose(inputs.measured[4:], .01)

    def test_same_sample_flow_fallback_is_rejected(self):
        matrix, data = self.charged_grid(.01)
        with self.assertRaisesRegex(ValueError, "fallback share"):
            I.prepare_inputs(matrix, data_dir=data, drop_specs=(), feeddown="")

    def test_wrong_profile_is_rejected_before_training(self):
        matrix, data = self.charged_grid()
        meta = json.loads(matrix.with_suffix('.meta.json').read_text());meta['analysis_profile']='baseline'
        matrix.with_suffix('.meta.json').write_text(json.dumps(meta))
        with self.assertRaisesRegex(ValueError, "analysis_profile"):
            I.prepare_inputs(matrix, data_dir=data, drop_specs=(), feeddown="")

    def test_oo_cli_samples_both_shear_choices_and_replays_fixed_slice(self):
        matrix, data = self.charged_grid()
        original = I.read_npz(matrix)
        indices = np.array([2, 0, 1, 7, 3, 6, 4, 5])
        design, _, _, _ = I.load_design(I.DESIGN_DAT)
        values = original['Y'][:1] * (1 + .1 * design[indices, 4, None])
        np.savez(matrix, **(original | dict(Y=values, Y_err=.01*values,
                 design_index=indices, design_name=np.array([f'design_{i:03d}' for i in indices]))))
        meta = json.loads(matrix.with_suffix('.meta.json').read_text())
        bins = meta['charged_flow_sample_usage'][0]['bins']
        meta['charged_flow_sample_usage'] = [dict(design_index=int(i), bins=bins) for i in indices]
        matrix.with_suffix('.meta.json').write_text(json.dumps(meta))
        for mode, count in [('flat', 12), ('temperature-dependent', 14)]:
            output = self.root / mode
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(I.cli(['--system', 'OO5360', '--shear-mode', mode,
                                       '--out-dir', str(output), '--no-plots', '--nsteps', '8',
                                       '--nburn', '2', '--nwalkers', '4', '--nsplits', '2']), 0)
                post, _, _ = I.posterior_inputs(output, system='OO5360')
            self.assertEqual(post['samples'].shape, (32, count))
            self.assertEqual(I.selected_shear_mode(), mode)
            self.assertEqual(json.loads(str(post['config_json']))['shear_mode'], mode)

    def test_proxy_opt_in_is_system_scoped_and_idempotent(self):
        I.use_system('OO5360');baseline=len(I.DATA_SPEC)
        I.enable_oo_yield_proxies();I.enable_oo_yield_proxies()
        self.assertEqual(len(I.DATA_SPEC), baseline+3)
        I.use_system('AuAu200')
        self.assertFalse(any(s.get('assumed_proxy') for s in I.DATA_SPEC))
        with self.assertRaisesRegex(ValueError, 'only for OO5360'):
            I.enable_oo_yield_proxies()

    def test_proxy_opt_in_for_auau_exits_cleanly_from_both_launchers(self):
        import check_inputs as C
        argv = ['--system', 'AuAu200', '--oo-yield-proxies']
        for launcher in (I.cli, C.main):
            stderr = io.StringIO()
            with self.subTest(launcher=launcher.__module__), mock.patch.object(I, 'main') as fit, \
                    contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                launcher(argv)
            self.assertEqual(caught.exception.code, 1)
            self.assertIn('only for OO5360', stderr.getvalue())
            fit.assert_not_called()

    def test_proxy_interpolation_refuses_extrapolation_and_invalid_coordinates(self):
        for source, target in [([1, 10], [.9]), ([1, 10], [11]), ([10, 1], [2]),
                               ([1, 1], [1]), ([1, 10], [np.nan])]:
            with self.subTest(source=source, target=target), self.assertRaises(ValueError):
                P.interpolation_weights(source, target)

    def test_correlated_likelihood_matches_multivariate_reference(self):
        covariance=np.array([[4.,1.,0.],[1.,9.,0.],[0.,0.,.25]])
        sig=np.sqrt(covariance.diagonal());residual=np.array([[1.,2.,3.],[-2.,.5,-1.]])
        std=np.array([[.2,.3,.4],[.6,.1,.2]])
        actual=I.gaussian_measurement_loglike(sig,covariance)(residual,std)
        expected=[multivariate_normal.logpdf(r,cov=covariance+np.diag(s*s)) for r,s in zip(residual,std)]
        np.testing.assert_allclose(actual,expected,rtol=1e-12)
        np.testing.assert_allclose(I.gaussian_measurement_loglike(sig)(residual,std),
                                  I.gaussian_measurement_loglike(sig,np.diag(sig*sig))(residual,std),rtol=1e-12)

    def test_both_sampler_coordinates_use_the_joint_covariance(self):
        covariance=np.array([[4.,1.],[1.,9.]]);sig=np.sqrt(covariance.diagonal())
        emulator=mock.Mock()
        emulator.predict.side_effect=lambda x,return_std: (np.tile([1.,2.],(len(x),1)),np.tile([.2,.3],(len(x),1)))
        expected=multivariate_normal.logpdf([1.,2.],cov=covariance+np.diag([.04,.09]))
        ranges=np.array([[0.,1.]])
        plain=I.make_logprob(emulator,np.zeros(2),sig,ranges,cov_data=covariance)
        logit=I.make_logprob_u(emulator,np.zeros(2),sig,ranges,np.array([0]),np.zeros(1),cov_data=covariance)
        self.assertAlmostEqual(plain(np.array([[.5]]))[0],expected)
        self.assertAlmostEqual(logit(np.array([[0.]]))[0],expected+np.log(.25))

    def test_proxy_covariance_subselection_preserves_correlations(self):
        labels=np.array([f'{name}__1_1' for name in P.GROUPS])
        values=np.array([1.,2.,3.,4.]);cov=np.diag([.1,.2,.3,.4])+np.full((4,4),.01)
        selected=np.array([3,0,1]);data=self.root/'centrality';data.mkdir()
        optional=self.root/'optional';optional.mkdir()
        widths=dict(kaon_ratio_transfer=.15,proton_ratio_transfer=.2,charged_neutral_kaon_isospin=.1)
        (optional/'yield_proxy_metadata.json').write_text(json.dumps(dict(assumed_fractional_widths=widths)))
        specs=[dict(name=n,sys_floor=.05,assumed_proxy=i>0) for i,n in enumerate(P.GROUPS)]
        sig=np.sqrt(cov.diagonal()[selected]+(.05*values[selected])**2)
        archive=dict(labels=labels,values=values,covariance=cov)
        with mock.patch.object(I,'DATA_SPEC',specs),mock.patch.object(P,'build',return_value=(None,None,archive)):
            with self.assertWarnsRegex(UserWarning,'OPTIONAL ASSUMED'):
                actual=I.proxy_measurement_covariance(labels[selected].tolist(),values[selected],sig,str(data))
        np.testing.assert_allclose(actual,cov[np.ix_(selected,selected)]+np.diag((.05*values[selected])**2))


if __name__ == '__main__':
    unittest.main()
