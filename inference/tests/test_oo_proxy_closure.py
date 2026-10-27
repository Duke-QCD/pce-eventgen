"""Kinematic conversion and correlated charged-yield closure diagnostics."""
import json
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('MPLCONFIGDIR', '/tmp/pce-inference-test-matplotlib')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from scipy.integrate import simpson
import check_oo_proxy_closure as C


class OOProxyClosureTests(unittest.TestCase):
    def test_finite_window_jacobian_matches_integrated_exact_derivative(self):
        eta = np.linspace(-.5, .5, 1001)
        for mass in (.13957, .49368, .93827, 1.67245):
            for pt in (.01, .2, 1., 5.):
                exact = pt*np.cosh(eta)/np.sqrt(mass**2+(pt*np.cosh(eta))**2)
                self.assertAlmostEqual(float(C.eta_window_jacobian(pt, mass)),
                                       float(simpson(exact, x=eta)), places=12)

    def test_massless_and_zero_momentum_limits_and_conversion_direction(self):
        pt = np.array([0., .01, .2, 10.])
        np.testing.assert_array_equal(C.eta_window_jacobian(pt, 0), 1.)
        jac = C.eta_window_jacobian(pt, .938)
        self.assertEqual(jac[0], 0.)
        self.assertTrue(np.all((jac >= 0) & (jac <= 1)))
        self.assertTrue(np.all(jac >= C.eta_window_jacobian(pt, .938, 0)))

    def test_jacobian_at_mean_pt_is_not_used_for_a_broad_spectrum(self):
        pt = np.linspace(0, 8, 2001)
        density = pt*np.exp(-pt/.25)
        moments = C.spectrum_moments(pt, density, .493677)
        self.assertLess(moments['jacobian'],
                        C.eta_window_jacobian(moments['mean_pt'], .493677)-.03)

    def test_reference_blast_wave_integrals_converge(self):
        parameters = C.REFERENCE[-1, 1:]
        coarse, _ = C.shape_moments(parameters, .885648)
        fine, _ = C.shape_moments(parameters, .885648, grid_points=3201)
        for species in C.SPECIES:
            self.assertAlmostEqual(coarse[species]['jacobian'], fine[species]['jacobian'], delta=1e-5)
        self.assertAlmostEqual(coarse['kaon']['mean_pt'], .885648, delta=.004)

    @classmethod
    def setUpClass(cls):
        cls.result, cls.arrays = C.calculate()

    def test_charge_accounting_and_joint_covariance_projection(self):
        with np.load(C.ROOT/'optional/yield_proxy_covariance.npz') as proxy:
            prediction = self.arrays['proxy_projection'] @ proxy['values']
            expected = self.arrays['proxy_projection'] @ proxy['covariance'] @ self.arrays['proxy_projection'].T
        np.testing.assert_allclose(self.arrays['proxy_covariance'], expected)
        for i, row in enumerate(self.result['rows']):
            others = row['other_charged']
            self.assertEqual(set(others), {'Sigma_ch', 'Xi_ch', 'Omega_ch', 'nuclei_allowance', 'prompt_leptons'})
            self.assertAlmostEqual(row['predicted_dNch_deta'], sum(row['contributions_pi_K_p'])+sum(others.values()))
            self.assertAlmostEqual(prediction[i]+others['Xi_ch']+others['Omega_ch'], row['predicted_dNch_deta'])
        # Shared transfer assumptions must survive the sum over species/bins.
        self.assertGreater(self.arrays['proxy_covariance'][0, -1], 0.)
        self.assertGreaterEqual(np.linalg.eigvalsh(self.arrays['diagnostic_covariance']).min(), 0.)

    def test_retained_diagnostic_reproduces_without_modifying_yields(self):
        original = (C.ROOT/'optional/yield_proxy_metadata.json').read_bytes()
        C.write_outputs(check=True)
        self.assertEqual(original, (C.ROOT/'optional/yield_proxy_metadata.json').read_bytes())
        row = self.result['rows'][-1]
        self.assertLess(row['shape_scan_max'], row['multiplicity'])
        self.assertLess(row['deficit'], row['diagnostic_error'])
        self.assertGreater(row['pion_proton_rescale_for_closure'], 1.)
        self.assertIn('not guaranteed coverage', self.result['shape_policy'])
        self.assertFalse(json.loads((C.ROOT/'optional/yield_proxy_metadata.json').read_text())['default_enabled'])


if __name__ == '__main__':
    unittest.main()
