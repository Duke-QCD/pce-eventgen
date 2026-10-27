#!/usr/bin/env python3
"""Conditional charged-multiplicity closure of OPTIONAL O-O yield proxies.

This is a diagnostic, not a new calibration input or an O-O PID measurement.
Reference spectral shapes and unmeasured charged species are explicit assumptions.
The original yield files and their assumed covariance are never modified here.
"""
import argparse
from functools import lru_cache
import json
from pathlib import Path

import numpy as np
from scipy.integrate import simpson
from scipy.special import i0e, k1e, kv

ROOT = Path(__file__).resolve().parent / 'data/OO5360'
SPECIES = ('pion', 'kaon', 'proton', 'Sigma_ch', 'Xi_ch', 'Omega_ch')
MASSES = dict(zip(SPECIES, (.13957039, .493677, .93827209, 1.193, 1.32171, 1.67245)))
# ALICE arXiv:1307.6796v4, Table 1 and Table 5, printed pages 4 and 16.
# Increasing multiplicity; beta is the area-averaged radial velocity, not beta_s.
REFERENCE = np.array([
    [4.4, .260, .166, 3.90], [9.8, .360, .169, 2.40],
    [16.1, .428, .164, 1.73], [23.2, .478, .157, 1.41],
    [30.5, .511, .151, 1.24], [36.2, .531, .147, 1.14],
    [45., .547, .143, 1.07],
])


def eta_window_jacobian(pt, mass, half_width=.5):
    """Average dy/deta on |eta|<h for a spectrum flat in true rapidity.

    y(eta,pT) = asinh[pT/mT sinh(eta)]. At eta=0, dy/deta=pT/mT.
    Integrate this against dN/(dpT dy), NOT an eta-binned spectrum.
    """
    pt = np.asarray(pt, dtype=float)
    if mass < 0 or half_width < 0 or np.any(pt < 0):
        raise ValueError('Nonnegative masses, momenta and window widths are required')
    speed = np.divide(pt, np.hypot(pt, mass), out=np.ones_like(pt),
                      where=np.hypot(pt, mass) > 0)
    return speed if half_width == 0 else np.arcsinh(speed*np.sinh(half_width))/half_width


def blast_wave(pt, mass, beta_mean, temperature, exponent, radial_nodes=64):
    """Unnormalized dN/(dpT dy); ALICE Eq.(1), with stable Bessel evaluation."""
    beta_s = beta_mean*(exponent+2)/2
    if not 0 <= beta_s < 1 or temperature <= 0 or exponent < 0:
        raise ValueError('Unphysical blast-wave parameters')
    nodes, weights = np.polynomial.legendre.leggauss(radial_nodes)
    r = (nodes+1)/2
    rho = np.arctanh(beta_s*r**exponent)
    pt = np.asarray(pt)
    mt = np.hypot(pt, mass)
    a = pt[:, None]*np.sinh(rho)/temperature
    b = mt[:, None]*np.cosh(rho)/temperature
    integral = (i0e(a)*k1e(b)*np.exp(a-b)) @ (weights*r/2)
    return pt*mt*integral


def spectrum_moments(pt, density, mass):
    norm = simpson(density, x=pt)
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError('Spectrum must have a positive finite integral')
    jac = eta_window_jacobian(pt, mass)
    eta_norm = simpson(jac*density, x=pt)
    return dict(mean_pt=float(simpson(pt*density, x=pt)/norm),
                mean_pt_eta=float(simpson(pt*jac*density, x=pt)/eta_norm),
                jacobian=float(eta_norm/norm),
                jacobian_eta0=float(simpson(eta_window_jacobian(pt, mass, 0)*density, x=pt)/norm))


@lru_cache(maxsize=32)
def reference_shapes(parameters, grid_points):
    beta, temperature, exponent = parameters
    pt = np.linspace(0, 20, grid_points)
    anchor = blast_wave(pt, .497611, beta, temperature, exponent)
    reference_mean = spectrum_moments(pt, anchor, .497611)['mean_pt']
    densities = {name: blast_wave(pt, mass, beta, temperature, exponent)
                 for name, mass in MASSES.items()}
    return pt, reference_mean, densities


def shape_moments(parameters, target_kaon_mean, grid_points=1601):
    """Scale all pT in a reference shape by lambda to match measured K0S mean.

    This transfers a spectral SHAPE, not its yield or reference multiplicity.
    The 4 MeV K0S/charged-kaon mass difference is retained for the anchor.
    """
    pt, reference_mean, densities = reference_shapes(tuple(parameters), grid_points)
    scale = target_kaon_mean/reference_mean
    out = {}
    for name, mass in MASSES.items():
        out[name] = spectrum_moments(pt*scale, densities[name], mass)
    # Low-pT pions are outside the simultaneous reference fit's 0.5-1 GeV
    # range. A separate soft-component stress test addresses this limitation.
    soft = pt*np.hypot(pt, MASSES['pion'])*np.exp(-np.hypot(pt, MASSES['pion'])/.100)
    out['soft_pion'] = spectrum_moments(pt, soft, MASSES['pion'])
    return out, float(scale)


def sigma_to_proton(temperature=.155):
    """Direct Boltzmann-gas estimate; same spin, two charged Sigma states.

    Includes both antiparticles in numerator and denominator. Strong-resonance
    feed-down is not calculated: use a 100% assumed normalization width.
    """
    mp = MASSES['proton']
    return float(sum(m*m*kv(2, m/temperature) for m in (1.18937, 1.197449)) /
                 (mp*mp*kv(2, mp/temperature)))


def matched_rows(root, stem, x):
    data = np.loadtxt(root/'derived'/f'{stem}.dat')
    # The eight FT0C class points have separately digitized x coordinates.
    # Match their order rather than extrapolating at slightly displaced endpoints.
    if len(data) != len(x) or np.any(np.diff(data[:, 0]) <= 0) or np.max(abs(data[:, 0]/x-1)) > .02:
        raise ValueError(f'Cannot match eight event classes for {stem}')
    return data


def calculate(root=ROOT, grid_points=1601):
    root = Path(root)
    metadata = json.loads((root/'optional/yield_proxy_metadata.json').read_text())
    # Validate source/proxy consistency with the recorded (possibly customized)
    # nuisance widths; do not overwrite or silently revert to their defaults.
    import build_oo_yield_proxies as proxies
    widths = metadata['assumed_fractional_widths']
    _, _, archive = proxies.build(root, check=True,
        transfer_kaon=widths['kaon_ratio_transfer'], transfer_proton=widths['proton_ratio_transfer'],
        isospin=widths['charged_neutral_kaon_isospin'])
    x = np.array([r['multiplicity'] for r in metadata['rows']])
    n = len(x)
    yields = archive['values'].reshape(4, n)[1:].T
    mean_k = matched_rows(root, 'mean_pT_K0S_vs_mult', x)
    xi = matched_rows(root, 'dN_dy_Xi_tot_vs_mult', x)
    omega = matched_rows(root, 'dN_dy_Omega_tot_vs_mult', x)
    sigma_ratio = sigma_to_proton()
    rows, variations = [], []
    projection = np.zeros((n, 4*n))
    hyperon_cov = np.zeros((n, n))
    for i, m in enumerate(x):
        parameters = [float(np.interp(np.log(m), np.log(REFERENCE[:, 0]), REFERENCE[:, j]))
                      for j in range(1, 4)]
        moments, scale = shape_moments(parameters, mean_k[i, 1], grid_points)
        js = np.array([moments[s]['jacobian'] for s in SPECIES])
        # Sigma estimate is tied to proxy p+pbar, not to an independent anchor.
        # Tiny nuclei allowance is deliberately a generous estimate, 0.005*p,
        # with J=1 (an upper kinematic choice). No charge-magnitude weighting.
        nuclei_per_proton = .005
        weights = js[:3].copy()
        weights[2] += sigma_ratio*js[3] + nuclei_per_proton
        projection[i, n+np.arange(3)*n+i] = weights
        pion_kaon_proton = float(yields[i] @ js[:3])
        extra = dict(Sigma_ch=float(sigma_ratio*yields[i, 2]*js[3]),
                     Xi_ch=float(xi[i, 1]*js[4]), Omega_ch=float(omega[i, 1]*js[5]),
                     nuclei_allowance=float(nuclei_per_proton*yields[i, 2]), prompt_leptons=0.)
        total = pion_kaon_proton + sum(extra.values())
        # No covariance of measured hyperons is supplied. Assume independent
        # species/bins here; their normalization uncertainties are very small.
        hyperon_cov[i, i] = (xi[i, 2]*js[4])**2 + (omega[i, 2]*js[5])**2
        # Explicit sensitivity scenarios, NOT confidence or credible intervals.
        scenarios = []
        for _, beta, temperature, exponent in REFERENCE:
            alt, _ = shape_moments([beta, temperature, exponent], mean_k[i, 1], grid_points)
            ja = np.array([alt[s]['jacobian'] for s in SPECIES])
            # Account for an extra soft-pion component (0 or 60% of pion yield).
            # Pion mean pT is not measured in O-O; this is an assumption scan.
            # The large range also allows much softer inclusive spectra than
            # a naive common BW fit continued below its pion fit range.
            for soft_fraction in (0., .60):
                ja_soft = ja.copy()
                ja_soft[0] = (1-soft_fraction)*ja[0] + soft_fraction*alt['soft_pion']['jacobian']
                pkk = yields[i] @ ja_soft[:3]
                hyp = sigma_ratio*yields[i, 2]*ja[3] + xi[i, 1]*ja[4] + omega[i, 1]*ja[5]
                scenarios.append(float(pkk+hyp+extra['nuclei_allowance']))
        # K0S mean-pT +/- supplied total error, common-shape response.
        kt = []
        for target in mean_k[i, 1]+np.array([-1, 1])*mean_k[i, 2]:
            alt, _ = shape_moments(parameters, target, grid_points)
            ja = np.array([alt[s]['jacobian'] for s in SPECIES])
            kt.append(float(yields[i] @ ja[:3]+sigma_ratio*yields[i, 2]*ja[3] +
                            xi[i, 1]*ja[4]+omega[i, 1]*ja[5]+extra['nuclei_allowance']))
        no_j = float(sum(yields[i])+sigma_ratio*yields[i, 2]+xi[i, 1]+omega[i, 1]+extra['nuclei_allowance'])
        rows.append(dict(multiplicity=float(m), proxy_rapidity_sum=float(sum(yields[i])),
            kaon_mean_pt=float(mean_k[i, 1]), reference_parameters=parameters, pt_scale=scale,
            spectral_moments=moments, contributions_pi_K_p=(yields[i]*js[:3]).tolist(),
            other_charged=extra, predicted_dNch_deta=float(total), closure_ratio=float(total/m),
            deficit=float(m-total), no_jacobian_upper_value=no_j,
            shape_scan_min=float(min(scenarios)), shape_scan_max=float(max(scenarios)),
            shape_scan_ratio=[float(min(scenarios)/m), float(max(scenarios)/m)],
            sigma_extra_required=float(max(0, m-total+extra['Sigma_ch'])),
            pion_proton_rescale_for_closure=float((m-total)/(yields[i, 0]*js[0] +
                yields[i, 2]*(js[2]+sigma_ratio*js[3]+nuclei_per_proton))+1),
            R_S_for_closure=float(metadata['rows'][i]['R_S']/((m-total)/(yields[i, 0]*js[0] +
                yields[i, 2]*(js[2]+sigma_ratio*js[3]+nuclei_per_proton))+1)),
            kaon_mean_pt_response=float((kt[1]-kt[0])/2)))
        variations.append(scenarios)
    total = np.array([r['predicted_dNch_deta'] for r in rows])
    # Exact linear propagation of stored JOINT proxy covariance, including
    # shared anchor and transfer nuisances. Never add marginal species errors.
    proxy_cov = projection @ archive['covariance'] @ projection.T
    sigma_shift = np.array([r['other_charged']['Sigma_ch'] for r in rows])
    nuclei_shift = np.array([r['other_charged']['nuclei_allowance'] for r in rows])
    kaon_pt_shift = np.array([r['kaon_mean_pt_response'] for r in rows])
    diagnostic_cov = (proxy_cov + hyperon_cov + np.outer(sigma_shift, sigma_shift) +
                      np.outer(nuclei_shift, nuclei_shift) + np.diag(kaon_pt_shift**2))
    # These are CONDITIONAL nuisance-Gaussian distances, not measurement p-values.
    # The multiplicity x errors/correlations and O-O spectral errors are unknown.
    residual = x-total
    stats = dict(conditional_proxy_chi2=float(residual @ np.linalg.solve(proxy_cov, residual)),
        conditional_with_species_assumptions_chi2=float(residual @ np.linalg.solve(diagnostic_cov, residual)),
        n_points=n, interpretation='Conditional Gaussian diagnostics only: no calibrated significance '
        'or p-value. Missing multiplicity-axis covariance and true O-O PID spectra; shape scan is not '
        'a probability distribution. Stored proxy/anchor errors are analyst assumptions.')
    for i, row in enumerate(rows):
        row['proxy_only_error'] = float(np.sqrt(proxy_cov[i, i]))
        row['diagnostic_error'] = float(np.sqrt(diagnostic_cov[i, i]))
        row['conditional_marginal_pull'] = float(residual[i]/np.sqrt(diagnostic_cov[i, i]))
    inputs = ['optional/yield_proxy_metadata.json', 'optional/yield_proxy_covariance.npz',
              'derived/mean_pT_K0S_vs_mult.dat', 'derived/dN_dy_Xi_tot_vs_mult.dat',
              'derived/dN_dy_Omega_tot_vs_mult.dat']
    result = dict(schema=1, status='CONDITIONAL_DIAGNOSTIC_NOT_CALIBRATION_DATA',
        input_sha256={name:proxies.digest(root/name) for name in inputs},
        comparison='ALICE displayed <dNch/deta> at |eta|<0.5 for the SAME eight FT0C classes, '
        'taken from the K0S yield abscissae. These are measured coordinates, not independent CMS '
        'centrality-bin values. Their experimental x errors and covariance were not extracted.',
        conversion='For locally flat dN/(dy dpT), J_s = integral f_s(pT) '
        'asinh[(pT/mT)*sinh(0.5)]/0.5 dpT / integral f_s(pT) dpT. '
        'Finite eta-window average, full pT. Not J evaluated at mean pT.',
        reference=dict(url='https://arxiv.org/pdf/1307.6796', version='v4',
            locations='Eq.(1)-(2), Table 1 and Table 5 (printed pp.4,14,16)',
            columns=['dNch_deta', 'beta_mean', 'Tkin_GeV', 'profile_n'], rows=REFERENCE.tolist()),
        shape_policy='Nominal: interpolate reference BW parameters in log multiplicity, clamp '
        'above M=45 (no measured reference at higher M), then uniformly rescale all pT to the '
        'measured O-O K0S mean pT in the same class. Alternative shapes: all seven reference '
        'parameter sets, each similarly rescaled, with 0 or 60% soft-pion admixture at T=100 MeV. '
        'These are transferred reference fits, NOT measured O-O pion/kaon/proton spectra. '
        'The simultaneous reference pion fit excludes pT<0.5 GeV. Resonance decay distortions '
        'and the true O-O spectral shapes remain uncertain. In particular the nominal pion '
        'mean pT can be too hard: the soft-component scan deliberately permits much softer '
        'inclusive spectra. Neither K0S mean pT nor a common BW prescription determines the '
        'pion spectrum. The scan is illustrative, not guaranteed coverage.',
        other_species_policy=dict(
            primary_definition='Count prompt long-lived charged particles and products of short-lived '
            'decays. Include Sigma+/-, Xi-/anti-Xi+, Omega-/anti-Omega+. Do not add neutral K0S, '
            'K0L, Lambda or their weak-decay daughters; do not separately add strong resonances '
            'whose daughters are already in the inclusive primary pi/K/p/hyperon yields.',
            primary_reference='https://cds.cern.ch/record/2270008',
            cms_reference='https://arxiv.org/pdf/2606.02285',
            Sigma_ch=f'Assumed {sigma_ratio:.6g}*(p+pbar): direct Boltzmann estimate at T=155 MeV, '
            'zero chemical potentials, physical Sigma+/- masses and equal spin degeneracy. '
            '100% normalization width, common across bins. Strong-resonance feed-down not calculated; '
            'this is NOT a measured Sigma yield or a strict bound.',
            Xi_Omega='Measured charge-summed yields, matched by ordered FT0C class (separate x '
            'digitizations agree within 2%). Jacobians from the transferred spectral shapes, '
            'not from fitted hyperon spectra. Other-species effect is small.',
            nuclei='Assumed 0.005*(p+pbar) at J=1, with 100% common normalization width. '
            'A generous tiny-particle allowance, not an O-O measurement. Count particles, not units of charge.',
            leptons='Nominal zero. Prompt leptons are explicitly excluded by CMS; ALICE primary '
            'charged particles can include prompt leptons. A separate +1% of multiplicity stress '
            'test is tabulated below, not assigned a measured uncertainty. Conversion electrons '
            'and weak-decay daughters are not primary.'),
        uncertainty_policy='Propagate the stored full joint proxy covariance, plus measured Xi/Omega '
        'marginal errors (assumed independent), 100% common Sigma/nuclei nuisance widths and '
        'the K0S mean-pT marginal error response (assumed independent bins). No model-discrepancy '
        '5% floor. No fitted closure renormalization. Spectral-shape scan is kept separate. '
        'Missing x/cross-species experimental covariances prevent a formal compatibility test.',
        stats=stats, rows=rows,
        stress_tests=dict(extra_prompt_leptons_fraction_of_M=.01,
            shape_min_ratio=(np.min(variations, axis=1)/x).tolist(),
            shape_max_plus_double_sigma_nuclei_and_leptons_ratio=(
                (np.max(variations, axis=1)+sigma_shift+nuclei_shift)/x+.01).tolist()))
    return result, dict(multiplicity=x, predicted=total, proxy_covariance=proxy_cov,
                        diagnostic_covariance=diagnostic_cov, proxy_projection=projection)


def write_outputs(root=ROOT, check=False):
    root = Path(root)
    result, arrays = calculate(root)
    lines = ['# CONDITIONAL diagnostic, NOT calibration inputs; assumptions in proxy_multiplicity_closure.json',
             '# M prediction proxy_error diagnostic_error shape_min shape_max closure_ratio']
    lines += [' '.join(f'{r[k]:.10g}' for k in ('multiplicity', 'predicted_dNch_deta',
        'proxy_only_error', 'diagnostic_error', 'shape_scan_min', 'shape_scan_max', 'closure_ratio'))
        for r in result['rows']]
    outputs = {'proxy_multiplicity_closure.json': json.dumps(result, indent=2)+'\n',
               'proxy_multiplicity_closure.dat': '\n'.join(lines)+'\n'}
    for name, content in outputs.items():
        path = root/'optional'/name
        if check:
            if not path.exists() or path.read_text() != content:
                raise ValueError(f'Closure diagnostic differs: {path}')
        else:
            path.write_text(content)
    path = root/'optional/proxy_multiplicity_closure_covariance.npz'
    if check:
        with np.load(path) as old:
            if set(old.files) != set(arrays) or not all(np.array_equal(old[k], v) for k,v in arrays.items()):
                raise ValueError(f'Closure covariance differs: {path}')
    else:
        np.savez(path, **arrays)
        plot(root, result)
    return result


def plot(root, result):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = result['rows']
    x = np.array([r['multiplicity'] for r in rows])
    total = np.array([r['predicted_dNch_deta'] for r in rows])
    err = np.array([r['diagnostic_error'] for r in rows])
    low = np.array([r['shape_scan_min'] for r in rows])
    high = np.array([r['shape_scan_max'] for r in rows])
    fig, axes = plt.subplots(2, 1, figsize=(8, 8), sharex=True,
                             gridspec_kw=dict(height_ratios=[1.45, 1]), layout='constrained')
    ax = axes[0]
    ax.plot(x, x, 'k--', label='ALICE measured multiplicity coordinate')
    ax.fill_between(x, low, high, color='#e2a33b', alpha=.4, label='Illustrative spectral shape scan')
    ax.errorbar(x, total, yerr=err, fmt='o', color='#28649b', capsize=3,
                label='Proxy prediction + other primary charged species')
    ax.set_ylabel(r'$\langle dN_{\rm ch}/d\eta\rangle$, $|\eta|<0.5$')
    ax.set_title('O–O yield proxies: conditional charged-multiplicity closure')
    ax.legend(fontsize=9, loc='upper left')
    ax = axes[1]
    ax.axhline(1, color='black', ls='--')
    ax.fill_between(x, low/x, high/x, color='#e2a33b', alpha=.4)
    ax.errorbar(x, total/x, yerr=err/x, fmt='o', color='#28649b', capsize=3)
    ax.plot(x, [r['proxy_rapidity_sum']/r['multiplicity'] for r in rows], ':',
            color='#777777', label=r'Raw $(Y_\pi+Y_K+Y_p)/M$, before conversion')
    ax.set_ylim(.65, 1.35)
    ax.set_ylabel('Prediction / measured coordinate')
    ax.set_xlabel(r'ALICE $M=\langle dN_{\rm ch}/d\eta\rangle$ of the same FT0C class')
    ax.legend(fontsize=9, loc='upper right')
    for ax in axes:
        ax.grid(alpha=.18)
    fig.supxlabel('Errors: assumed joint proxy + charged-species nuisances; x errors unavailable.\n'
                  'Reference spectra transferred using measured K⁰S mean pT; no O–O PID spectra.', fontsize=9)
    fig.savefig(root/'review/OO_proxy_multiplicity_closure.png', dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check retained diagnostics without writing')
    args = parser.parse_args()
    result = write_outputs(check=args.check)
    print('M       prediction  ratio  diagnostic_error  shape_range')
    for r in result['rows']:
        print(f"{r['multiplicity']:7.2f} {r['predicted_dNch_deta']:9.2f} {r['closure_ratio']:6.3f} "
              f"{r['diagnostic_error']:9.2f} [{r['shape_scan_min']:.2f}, {r['shape_scan_max']:.2f}]")
    print(json.dumps(result['stats'], indent=2))


if __name__ == '__main__':
    main()
