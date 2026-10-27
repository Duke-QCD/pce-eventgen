"""Charge factors, provenance, correlated errors and opt-in likelihood regressions."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault('MPLCONFIGDIR','/tmp/pce-inference-test-matplotlib')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.stats import multivariate_normal
import build_oo_yield_proxies as P
import inference as I


class OOYieldProxyTests(unittest.TestCase):
    def setUp(self):
        self.evidence,self.metadata,self.archive=P.calculate()

    def test_charge_conventions_and_display_factor_are_applied_once(self):
        for row in self.evidence['rows']['proton']:
            self.assertAlmostEqual(row['plotted_value']/row['ratio'],6.)
        self.assertEqual(self.evidence['rows']['kaon'][0]['definition'],'2*K0S/(pi+ + pi-)')
        for row in self.metadata['rows']:
            self.assertAlmostEqual(row['pion']['value']*row['R_S'],2*row['K0S'])
            self.assertAlmostEqual(row['proton']['value']/row['pion']['value'],row['R_p'])
            self.assertAlmostEqual(row['kaon']['value'],2*row['K0S'])
        # Factor-of-two/six mistakes produce physically different scales.
        self.assertAlmostEqual(self.metadata['rows'][-1]['pion']['value'],103.39,delta=.1)
        self.assertAlmostEqual(self.metadata['rows'][-1]['proton']['value'],5.478,delta=.01)

    def test_native_marker_and_axis_readings_hit_source_pixels(self):
        from PIL import Image
        image=np.array(Image.open(P.ROOT/self.evidence['source_image']))[:,:,:3]
        axis=self.evidence['axis']
        for value,pixel in zip(axis['y_values'],axis['y_pixels']):
            self.assertAlmostEqual(axis['y_intercept']+axis['y_slope']*np.log10(value),pixel,delta=.5)
        for series in self.evidence['rows'].values():
            for row in series:
                x,y=round(row['marker_x_pixel']),round(row['marker_y_pixel'])
                self.assertGreater(np.mean(image[y-4:y+5,x-4:x+5].max(2)<80),.9)

    def test_no_extrapolation_or_invalid_grid_is_silent(self):
        for source,targets in (([1,10],[.9]),([1,10],[11]),([10,1],[2]),([1,1],[1]),([1,10],[np.nan])):
            with self.subTest(source=source,targets=targets),self.assertRaises(ValueError):
                P.interpolation_weights(source,targets)

    def test_joint_covariance_retains_anchor_and_common_ratio_correlations(self):
        archive=self.archive; covariance=archive['covariance']; values=archive['values']
        np.testing.assert_allclose(covariance,sum(archive[k] for k in self.metadata['covariance_components']))
        self.assertGreaterEqual(np.linalg.eigvalsh(covariance).min(),-1e-10)
        # Same-bin K0S and pion normalization are shared exactly. Different-bin
        # K0S errors are independent by the explicitly recorded assumption.
        self.assertAlmostEqual(covariance[0,8],.01*values[0]*values[8],places=8)
        self.assertEqual(covariance[0,1],0.)
        self.assertGreater(covariance[8,9],0.)
        self.assertGreater(covariance[8,24],0.)
        # Kaon normalization has only anchor + common charged/neutral relation.
        self.assertAlmostEqual(covariance[16,16]/values[16]**2,.02,places=10)
        self.assertAlmostEqual(covariance[16,17]/(values[16]*values[17]),.01,places=10)

    def test_expected_projection_errors_are_not_used_as_measured_errors(self):
        with mock.patch.dict(P.SERIES,{'kaon':dict(P.SERIES['kaon'],upper=[y-100 for y in P.SERIES['kaon']['ys']])}):
            _,_,changed=P.calculate()
        np.testing.assert_array_equal(changed['covariance'],self.archive['covariance'])
        _,_,changed=P.calculate(transfer_kaon=.30)
        self.assertGreater(changed['covariance'][8,8],self.archive['covariance'][8,8])

    def test_outputs_reproduce_and_corruption_is_rejected(self):
        P.build(check=True)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'OO5360'
            for child in ('sources','derived','uncertainties','optional'):
                shutil.copytree(P.ROOT/child,root/child)
            path=root/'optional/proxy_dN_dy_pion_vs_mult.dat'
            original=np.loadtxt(path); original[0,1]*=2
            np.savetxt(path,original)
            with self.assertRaisesRegex(ValueError,'Proxy outputs differ'):
                P.build(root,check=True)
            (root/'sources/ALI-SIMUL-480342.png').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash differs'):
                P.calculate(root)

    def test_default_selection_is_unchanged_and_opt_in_is_system_limited(self):
        previous=I.SYSTEM
        try:
            I.use_system('OO5360')
            default_names=[s['name'] for s in I.DATA_SPEC]
            self.assertFalse(any(s.get('assumed_proxy') for s in I.DATA_SPEC))
            I.enable_oo_yield_proxies(); I.enable_oo_yield_proxies()
            self.assertEqual(len(I.DATA_SPEC),len(default_names)+3)
            I.use_system('OO5360')
            self.assertEqual([s['name'] for s in I.DATA_SPEC],default_names)
            I.use_system('AuAu200')
            with self.assertRaisesRegex(ValueError,'only for OO5360'):
                I.enable_oo_yield_proxies()
        finally:
            I.use_system(previous)

    def test_covariance_mapping_survives_reordering_and_dropped_points(self):
        specs=[dict(name=name,sys_floor=.05,assumed_proxy=j>0) for j,name in enumerate(P.GROUPS)]
        selection=np.array([31,3,11,19,4,12,20,28])
        a=self.archive; labels=a['labels'][selection].tolist(); y=a['values'][selection]
        sig=np.sqrt(np.diag(a['covariance'])[selection]+(.05*y)**2)
        with mock.patch.object(I,'DATA_SPEC',specs),self.assertWarnsRegex(UserWarning,'OPTIONAL ASSUMED'):
            covariance=I.proxy_measurement_covariance(labels,y,sig,str(P.ROOT/'centrality'))
        expected=a['covariance'][np.ix_(selection,selection)]+np.diag((.05*y)**2)
        np.testing.assert_allclose(covariance,expected)

    def test_proxy_tables_assemble_with_model_charge_sum_columns(self):
        specs=[dict(name=name,species=species,obs='dN_dy',kind='vs_mult',sys_floor=.05,
                    assumed_proxy=j>0,files=[f'../optional/{name}.dat'] if j else
                    ['../derived/dN_dy_K0S_vs_mult.dat'])
               for j,(name,species) in enumerate(zip(P.GROUPS,('K0S','pi_tot','K_tot','p_tot')))]
        labels=[f'{species}__{lo}_{hi}__{obs}'
                for species,obs in [('charged','dNch_deta')]+[(s['species'],'dN_dy') for s in specs]
                for lo,hi in [(0,5),(5,10)]]
        grid=np.tile([150.,5., 9.,.3, 120.,4., 18.,.6, 6.,.2],(3,1))
        with mock.patch.object(I,'DATA_SPEC',specs):
            assembled=I.assemble(grid,.01*np.ones_like(grid),labels,str(P.ROOT/'centrality'))
            _,_,y,sig,selected,_=assembled
            self.assertEqual(len(selected),32)
            np.testing.assert_allclose(y,self.archive['values'],rtol=1e-9)
            with self.assertWarnsRegex(UserWarning,'OPTIONAL ASSUMED'):
                covariance=I.proxy_measurement_covariance(selected,y,sig,str(P.ROOT/'centrality'))
        np.testing.assert_allclose(covariance,self.archive['covariance']+np.diag((.05*y)**2),rtol=1e-8)

    def test_both_sampler_coordinates_use_the_correlated_likelihood(self):
        sig=np.array([2.,3.]); covariance=np.array([[4.,1.],[1.,9.]])
        emulator=mock.Mock()
        emulator.predict.side_effect=lambda x,return_std: (np.tile([1.,2.],(len(x),1)),np.tile([.2,.3],(len(x),1)))
        expected=multivariate_normal.logpdf([1.,2.],cov=covariance+np.diag([.04,.09]))
        ranges=np.array([[0.,1.]])
        plain=I.make_logprob(emulator,np.zeros(2),sig,ranges,cov_data=covariance)
        logit=I.make_logprob_u(emulator,np.zeros(2),sig,ranges,np.array([0]),np.zeros(1),cov_data=covariance)
        self.assertAlmostEqual(plain(np.array([[.5]]))[0],expected)
        self.assertAlmostEqual(logit(np.array([[0.]]))[0],expected+np.log(.25))

    def test_correlated_likelihood_matches_independent_multivariate_reference(self):
        covariance=np.array([[4.,1.,0.],[1.,9.,0.],[0.,0.,.25]])
        sig=np.sqrt(covariance.diagonal())
        residual=np.array([[1.,2.,3.],[-2.,.5,-1.]])
        std=np.array([[.2,.3,.4],[.6,.1,.2]])
        actual=I.gaussian_measurement_loglike(sig,covariance)(residual,std)
        expected=[multivariate_normal.logpdf(r,cov=covariance+np.diag(s*s)) for r,s in zip(residual,std)]
        np.testing.assert_allclose(actual,expected,rtol=1e-12)
        np.testing.assert_allclose(I.gaussian_measurement_loglike(sig)(residual,std),
            I.gaussian_measurement_loglike(sig,np.diag(sig*sig))(residual,std),rtol=1e-12)
        for bad in (covariance.T+np.triu(np.ones((3,3)),1),np.ones((2,2)),
                    np.array([[4.,20.,0.],[20.,9.,0.],[0.,0.,.25]])):
            with self.assertRaises(ValueError):
                I.gaussian_measurement_loglike(sig,bad)


if __name__=='__main__':
    unittest.main()
