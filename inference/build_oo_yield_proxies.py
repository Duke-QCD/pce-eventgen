#!/usr/bin/env python3
"""Build OPTIONAL charge-summed light-hadron yield proxies from ALI-SIMUL-480342.

These are assumption-based inputs, not measured O-O pion/kaon/proton yields.
The measured O-O K0S central values anchor all three species. Their retained 10%
errors are themselves ASSUMED. Outputs include a joint covariance with that anchor.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent / "data/OO5360"
FIGURE = "ALI-SIMUL-480342"
SOURCE_URL = "https://alice-figure.web.cern.ch/system/files/figures/bianchil/img_ToPionRatiosOOfin_1.pdf"
NOTE_URL = "https://cds.cern.ch/record/2765973/files/ALICE_PN_ProjectionShortOxygenRun.pdf"
PDF_SHA = "c31ddc04c26d7234340b67dd6d1f65b877e94ef2f731e27555ef768b6bf4fdfc"
PNG_SHA = "ea13d594c98a59d470044dc033536b2b99f61a941a9b7321c5438e4c17076f05"
# Reviewed centers of the black O-O projection markers in the native 2362x3012
# image embedded in the original figure PDF. Last six markers are closely spaced.
XS = [835, 1020, 1143, 1245, 1327, 1405, 1473, 1525, 1554, 1592, 1630, 1673]
SERIES = {
    "kaon": dict(display_factor=1., definition="2*K0S/(pi+ + pi-)",
        ys=[503.5,475.5,466.5,465.,465.,461.5,456.5,452.,452.,448.5,442.5,442.5],
        upper=[482.,453.,445.,443.,443.,440.,435.,430.,430.,427.,420.5,420.5],
        lower=[528.,500.,491.,489.,489.,486.,481.,476.,476.,473.,467.,467.]),
    "proton": dict(display_factor=6., definition="(p + pbar)/(pi+ + pi-)",
        ys=[267.5,260.5,254.,250.5,247.,250.5,253.5,257.5,260.5,264.,267.5,275.],
        upper=[246.,239.,232.,228.5,225.,228.5,232.,235.,239.,242.,246.,253.],
        lower=[292.,285.,278.,275.,271.,275.,278.,281.5,285.,288.5,292.,299.]),
}
GROUPS = ("dN_dy_K0S_vs_mult", "proxy_dN_dy_pion_vs_mult",
          "proxy_dN_dy_kaon_vs_mult", "proxy_dN_dy_proton_vs_mult")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def interpolation_weights(x, targets):
    """Piecewise interpolation in log multiplicity, strictly without extrapolation."""
    x, targets = np.asarray(x, float), np.asarray(targets, float)
    if (x.ndim != 1 or len(x) < 2 or not np.isfinite(x).all() or np.any(x <= 0)
            or np.any(np.diff(x) <= 0) or not np.isfinite(targets).all()
            or np.any(targets < x[0]) or np.any(targets > x[-1])):
        raise ValueError("Need ordered positive source multiplicities and targets inside projection coverage")
    indices = np.clip(np.searchsorted(x, targets, side="right")-1, 0, len(x)-2)
    t = (np.log(targets)-np.log(x[indices]))/(np.log(x[indices+1])-np.log(x[indices]))
    weights = np.zeros((len(targets), len(x)))
    weights[np.arange(len(targets)), indices] = 1-t
    weights[np.arange(len(targets)), indices+1] = t
    return weights, indices


def digitization(root):
    for relative, expected in ((f"sources/{FIGURE}.pdf", PDF_SHA), (f"sources/{FIGURE}.png", PNG_SHA)):
        if digest(root / relative) != expected:
            raise ValueError(f"Projection source hash differs: {relative}")
    # Log axes: four x-decade ticks and five y-decade ticks. Native raster origin
    # is top left. Center-of-stroke readings, not glyph/text positions.
    xticks, xpix = [1.,10.,100.,1000.], [559.5,1071.5,1583.5,2095.5]
    yticks, ypix = [.1,.01,.001,.0001,.00001], [536.,1067.,1597.,2128.,2659.]
    bx, ax = np.polyfit(np.log10(xticks), xpix, 1)
    by, ay = np.polyfit(np.log10(yticks), ypix, 1)
    xs = 10**((np.asarray(XS)-ax)/bx)
    evidence = dict(schema=1, figure=FIGURE, status="projection_not_measurement",
        source_url=SOURCE_URL, source_pdf_sha256=PDF_SHA, source_image_sha256=PNG_SHA,
        source_image=f"sources/{FIGURE}.png", native_size=[2362,3012],
        projection_energy_TeV=6.37, target_energy_TeV=5.36, projection_luminosity_nb_inverse=1.,
        projection_method_reference=NOTE_URL,
        projection_method="ALICE-PUBLIC-2021-004 Fig.16, section 7.1: prior pp/p-Pb inputs and "
                          "multiplicity scaling; Run-2 systematic assumptions. No new O-O measurement.",
        convention_evidence=[
            dict(url="https://arxiv.org/pdf/1606.07424", locations="Fig.2 and Fig.3",
                 finding="Reference-series cross-check: Fig.2 explicitly uses 2*K0S/(pi+ + pi-); "
                         "its pp/p-Pb points agree with the projection's K0S/pi row. "
                         "Fig.3 explicitly defines p/pi=(p+pbar)/(pi+ + pi-)."),
            dict(url="https://arxiv.org/pdf/1307.6796", locations="Fig.4",
                 finding="The charged K/pi and p/pi reference ratios use sums of both charges.")],
        convention_caveat="The projection uses shorthand species labels, not explicit charge sums. "
                           "The K0S convention is resolved by the reference-series normalization "
                           "cross-check (ratio near 0.13-0.15 is 2*K0S/pi_sum, not K0S/pi_sum).",
        axis=dict(x_values=xticks,x_pixels=xpix,x_slope=float(bx),x_intercept=float(ax),
                  y_values=yticks,y_pixels=ypix,y_slope=float(by),y_intercept=float(ay)),
        reading_budget_native_pixels=3.,
        error_box_use="Digitized projected box displacements are retained for review only. They are "
                      "expected precision, not measured errors or a transfer uncertainty; NOT propagated.",
        rows={})
    for key, spec in SERIES.items():
        rows = []
        for x, xp, yp, upper, lower in zip(xs, XS, spec["ys"], spec["upper"], spec["lower"]):
            plotted = 10**((yp-ay)/by)
            rows.append(dict(multiplicity=float(x), ratio=float(plotted/spec["display_factor"]),
                plotted_value=float(plotted), display_factor=spec["display_factor"],
                definition=spec["definition"], marker_x_pixel=xp, marker_y_pixel=yp,
                upper_box_pixel=upper, lower_box_pixel=lower,
                projected_box_relative_plus=float(10**((upper-yp)/by)-1),
                projected_box_relative_minus=float(1-10**((lower-yp)/by))))
        evidence["rows"][key] = rows
    return evidence


def calculate(root=ROOT, transfer_kaon=.15, transfer_proton=.20, isospin=.10):
    root = Path(root)
    for value in (transfer_kaon, transfer_proton, isospin):
        if not np.isfinite(value) or not 0 <= value < 1:
            raise ValueError("Assumed fractional widths must be finite and between 0 and 1")
    evidence = digitization(root)
    anchor_path = root / "derived/dN_dy_K0S_vs_mult.dat"
    anchor = np.loadtxt(anchor_path, ndmin=2)
    if (anchor.shape != (8,3) or not np.isfinite(anchor).all() or np.any(anchor <= 0)
            or np.any(np.diff(anchor[:,0]) <= 0)):
        raise ValueError("Need the eight valid measured K0S anchors")
    anchor_evidence = json.loads((root / "uncertainties/dN_dy_K0S_vs_mult.json").read_text())
    for row, stored in zip(anchor, anchor_evidence["rows"]):
        if (not np.allclose(row, [stored['multiplicity'],stored['value'],stored['error']], rtol=1e-10)
                or stored['status'] != 'assumed'):
            raise ValueError("K0S anchor or its uncertainty status differs from retained evidence")
    x, k0s, anchor_error = anchor.T
    source_x = np.array([r["multiplicity"] for r in evidence["rows"]["kaon"]])
    weights, indices = interpolation_weights(source_x, x)
    source = {key: np.array([r['ratio'] for r in evidence['rows'][key]]) for key in SERIES}
    ratios = {key: np.exp(weights @ np.log(value)) for key,value in source.items()}
    # Explicit charge sums throughout. The neutral-kaon row already contains the
    # factor of TWO; the pion formula does not assume charged/neutral isospin.
    pion = 2*k0s/ratios['kaon']
    kaon = 2*k0s
    proton = ratios['proton']*pion
    values = np.concatenate([k0s, pion, kaon, proton])
    n, ns = len(x), len(source_x)
    size = len(values)
    components = {}

    def add(name, jacobian):
        components[name] = jacobian @ jacobian.T

    # Unknown anchor covariance: choose independent multiplicity bins explicitly,
    # while propagating each bin's normalization to all three derived species.
    ja = np.zeros((size,n))
    for group in range(4):
        ja[group*n+np.arange(n),np.arange(n)] = values[group*n:(group+1)*n]*anchor_error/k0s
    add('anchor_assumed',ja)
    jt = np.zeros((size,2))
    jt[n:2*n,0] = -transfer_kaon*pion
    jt[3*n:,0] = -transfer_kaon*proton
    jt[3*n:,1] = transfer_proton*proton
    add('transfer_assumed',jt)
    ji = np.zeros((size,1)); ji[2*n:3*n,0] = isospin*kaon
    add('isospin_assumed',ji)

    # Treat reading BOUNDS as adjustable Gaussian-equivalent nuisance widths for
    # this optional fit only; they are not experimental standard deviations.
    axis = evidence['axis']
    sigma_log_ratio = np.log(10)*3/abs(axis['y_slope'])
    jd = np.zeros((size,2*ns))
    jd[n:2*n,:ns] = -pion[:,None]*weights*sigma_log_ratio
    jd[3*n:,:ns] = -proton[:,None]*weights*sigma_log_ratio
    jd[3*n:,ns:] = proton[:,None]*weights*sigma_log_ratio
    add('digitization_y_assumed',jd)
    jx = np.zeros((size,ns))
    for key in SERIES:
        # d ln(r_interp)/d ln(x_source), inside each fixed interpolation segment.
        logs = np.log(source[key]); dx = np.diff(np.log(source_x))[indices]
        slopes = (logs[indices+1]-logs[indices])/dx
        derivative = -slopes[:,None]*weights*(np.log(10)*3/axis['x_slope'])
        if key == 'kaon':
            jx[n:2*n] -= pion[:,None]*derivative
            jx[3*n:] -= proton[:,None]*derivative
        else:
            jx[3*n:] += proton[:,None]*derivative
    add('digitization_x_assumed',jx)
    # Difference from interpolation linear in M and r is a numerical sensitivity
    # allowance. One nuisance per ratio/bin, shared by affected output species.
    jl = np.zeros((size,2*n))
    interpolation = {}
    for key in SERIES:
        alternative = np.interp(x,source_x,source[key])
        width = np.abs(np.log(ratios[key]/alternative))
        interpolation[key] = width.tolist()
        if key == 'kaon':
            jl[n+np.arange(n),np.arange(n)] = -pion*width
            jl[3*n+np.arange(n),np.arange(n)] = -proton*width
        else:
            jl[3*n+np.arange(n),n+np.arange(n)] = proton*width
    add('interpolation_assumed',jl)
    covariance = sum(components.values())
    labels = np.array([f'{name}__{m:.4g}_{m:.4g}' for name in GROUPS for m in x])
    metadata = dict(schema=1, status='ASSUMED_PROXY_NOT_OO_MEASUREMENT', default_enabled=False,
        anchor_file='derived/dN_dy_K0S_vs_mult.dat',anchor_sha256=digest(anchor_path),
        anchor_evidence_sha256=digest(root/'uncertainties/dN_dy_K0S_vs_mult.json'),
        source_pdf_sha256=PDF_SHA,source_image_sha256=PNG_SHA,
        definitions=dict(pion='pi+ + pi-',kaon='K+ + K-',proton='p + pbar',anchor='K0S'),
        formulas=dict(pion='2*K0S/R_S',kaon='2*K0S (approximate isospin symmetry)',proton='R_p*pion',
                      R_S='2*K0S/(pi+ + pi-)',R_p='(p+pbar)/(pi+ + pi-)'),
        interpolation='Piecewise linear in ln(ratio) versus ln(multiplicity); no extrapolation.',
        assumed_fractional_widths=dict(kaon_ratio_transfer=transfer_kaon,
            proton_ratio_transfer=transfer_proton,charged_neutral_kaon_isospin=isospin),
        uncertainty_policy='First-order Gaussian covariance around central values. All additional widths '
            'are analyst assumptions, not coverage-certified experimental errors. Projection error boxes '
            'are NOT propagated. Reading bounds of 3 native pixels are used as Gaussian-equivalent '
            'widths. The transfer widths include possible energy/system differences; no separate energy '
            'rescaling is applied. No additional 5% model discrepancy is included in these exports.',
        correlation_policy='Anchor errors assumed independent between multiplicity bins, shared by '
            'K0S/pi/K/p within each bin. Each transfer nuisance is fully correlated over multiplicity; '
            'R_S is shared by pion/proton. Isospin nuisance is fully correlated across kaon bins. '
            'Reading errors are independent between source points (x shared by both source ratios). '
            'Interpolation errors are independent between target bins, shared by affected species.',
        normalization_caveat=evidence['convention_caveat'],
        covariance_file='optional/yield_proxy_covariance.npz',
        covariance_components=list(components),
        warnings=['The K0S anchor central yields are measured, but all eight anchor errors remain assumed 10%.',
                  'No proxy yields or covariance are added to the default calibration.',
                  'Never use the three marginal-error DAT files as independent constraints.',
                  'No experimental covariance with other species or experiments is available.',
                  'Primary weak-decay subtraction must be matched, especially for proton feed-down.'],
        rows=[])
    for i,m in enumerate(x):
        row=dict(multiplicity=float(m),K0S=float(k0s[i]),R_S=float(ratios['kaon'][i]),
                 R_p=float(ratios['proton'][i]),interpolation_log_width={k:v[i] for k,v in interpolation.items()})
        for j,key in enumerate(('pion','kaon','proton'),1):
            pos=j*n+i
            row[key]=dict(value=float(values[pos]),error=float(np.sqrt(covariance[pos,pos])),
                status='assumed_proxy',components={k:float(np.sqrt(v[pos,pos])) for k,v in components.items()})
        metadata['rows'].append(row)
    archive=dict(labels=labels,values=values,covariance=covariance,**components)
    return evidence,metadata,archive


def build(root=ROOT, *, check=False, transfer_kaon=.15, transfer_proton=.20, isospin=.10):
    root=Path(root)
    evidence,metadata,archive=calculate(root,transfer_kaon,transfer_proton,isospin)
    optional=root/'optional'
    if not check:
        optional.mkdir(exist_ok=True)
    outputs={optional/'projection_ratios.json':json.dumps(evidence,indent=2)+'\n',
             optional/'yield_proxy_metadata.json':json.dumps(metadata,indent=2)+'\n'}
    for key,name in zip(('pion','kaon','proton'),GROUPS[1:]):
        lines=['# OPTIONAL ASSUMED PROXY; NOT a measured O-O identified yield.',
               '# Charge sums: '+metadata['definitions'][key]+'; |y|<0.5; full-pT yield per event.',
               '# Measured K0S central yields anchor the normalization; anchor errors remain ASSUMED 10%.',
               '# err is the marginal uncertainty from yield_proxy_covariance.npz; do NOT fit independently.',
               '# Transfer/isospin/digitization assumptions and provenance: yield_proxy_metadata.json',
               '# dNch_deta val err']
        lines += [f"{r['multiplicity']:.10g} {r[key]['value']:.10g} {r[key]['error']:.10g}" for r in metadata['rows']]
        outputs[optional/f'{name}.dat']='\n'.join(lines)+'\n'
    changed=[]
    for path,content in outputs.items():
        if not path.exists() or path.read_text()!=content:
            changed.append(str(path))
            if not check:
                path.write_text(content)
    covariance_path=optional/'yield_proxy_covariance.npz'
    same=False
    if covariance_path.exists():
        with np.load(covariance_path,allow_pickle=False) as old:
            same=set(old.files)==set(archive) and all(np.array_equal(old[k],v) for k,v in archive.items())
    if not same:
        changed.append(str(covariance_path))
        if not check:
            np.savez(covariance_path,**archive)
    if check and changed:
        raise ValueError('Proxy outputs differ: '+', '.join(changed))
    return evidence,metadata,archive


def make_review(root, evidence, metadata, archive):
    """Show the readings on source pixels and the resulting assumed yield errors."""
    from PIL import Image, ImageDraw, ImageFont
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    root=Path(root); review=root/'review'; review.mkdir(exist_ok=True)
    source=Image.open(root/evidence['source_image']).convert('RGB')
    header=190
    overlay=Image.new('RGB',(source.width,source.height+header+140),'white')
    overlay.paste(source,(0,header)); draw=ImageDraw.Draw(overlay)
    fonts='/usr/share/fonts/truetype/liberation2/'
    title=ImageFont.truetype(fonts+'LiberationSans-Bold.ttf',42)
    small=ImageFont.truetype(fonts+'LiberationSans-Regular.ttf',31)
    draw.text((25,20),'ALI-SIMUL-480342: digitized O-O projection (NOT data)',fill='#172b3a',font=title)
    draw.text((25,80),'Blue: R_S = 2 K0S / (pi+ + pi-)    Orange: 6 x R_p = 6 (p + pbar) / (pi+ + pi-)',
              fill='#172b3a',font=small)
    draw.text((25,128),'Crosses: marker centers. Side ticks: projected error-box edges (excluded from proxy errors).',
              fill='#53616b',font=small)
    for key,color in [('kaon','#0077bb'),('proton','#cc6500')]:
        for row in evidence['rows'][key]:
            x,y=row['marker_x_pixel'],header+row['marker_y_pixel']
            draw.line((x-5,y,x+5,y),fill=color,width=2)
            draw.line((x,y-5,x,y+5),fill=color,width=2)
            for edge in (row['upper_box_pixel'],row['lower_box_pixel']):
                draw.line((x-29,header+edge,x-20,header+edge),fill=color,width=2)
                draw.line((x+20,header+edge,x+29,header+edge),fill=color,width=2)
    y=source.height+header+20
    draw.text((25,y),'Native 2362 x 3012 source pixels retained. Reading allowance: +/-3 pixels in each axis.',
              fill='#53616b',font=small)
    draw.text((25,y+48),'The K0S shorthand uses the charge-average convention, checked against ALICE reference-series normalization.',
              fill='#53616b',font=small)
    overlay.save(review/'ALI-SIMUL-480342_ratios_overlay.png')

    fig,axes=plt.subplots(1,3,figsize=(14,4.7))
    for ax,key,label,color in zip(axes,('pion','kaon','proton'),
            (r'$\pi^++\pi^-$',r'$K^++K^-$',r'$p+\bar p$'),('#0077bb','#009988','#cc6500')):
        x=np.array([r['multiplicity'] for r in metadata['rows']])
        y=np.array([r[key]['value'] for r in metadata['rows']])
        error=np.array([r[key]['error'] for r in metadata['rows']])
        ax.errorbar(x,y,yerr=error,color=color,fmt='o',capsize=3,label='Optional proxy')
        ax.set(title=label,xlabel=r'$\langle dN_{\rm ch}/d\eta\rangle$',ylabel=r'$dN/dy$ at $|y|<0.5$')
        ax.grid(alpha=.2); ax.set_ylim(bottom=0)
        ax.text(.05,.95,f'Assumed marginal error: {100*np.min(error/y):.1f}%',
                va='top',transform=ax.transAxes,fontsize=10)
    fig.suptitle('O-O light-hadron yield proxies: measured K0S normalization + projected ratios',fontsize=14)
    fig.text(.5,.035,'All errors include analyst assumptions; species and multiplicity bins are correlated. '
             'Excluded from the default calibration.',ha='center',fontsize=10)
    fig.tight_layout(rect=(0,.08,1,.93))
    fig.savefig(review/'OO_light_hadron_yield_proxies.png',dpi=180); plt.close(fig)
    correlation=archive['covariance']/np.sqrt(np.outer(np.diag(archive['covariance']),np.diag(archive['covariance'])))
    fig,ax=plt.subplots(figsize=(7,6))
    image=ax.imshow(correlation,vmin=-1,vmax=1,cmap='RdBu_r')
    ax.set_xticks([3.5,11.5,19.5,27.5],['K0S anchor','Pions','Kaons','Protons'],rotation=25)
    ax.set_yticks([3.5,11.5,19.5,27.5],['K0S anchor','Pions','Kaons','Protons'])
    for edge in [7.5,15.5,23.5]:
        ax.axhline(edge,color='white',lw=.7); ax.axvline(edge,color='white',lw=.7)
    ax.set_title('Assumed joint proxy/anchor correlations\n8 multiplicities per species; no model-error floor')
    fig.colorbar(image,ax=ax,label='Correlation'); fig.tight_layout()
    fig.savefig(review/'OO_light_hadron_proxy_correlations.png',dpi=160); plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT)
    parser.add_argument('--transfer-kaon',type=float,default=.15,help='Assumed common fractional width of R_S transfer.')
    parser.add_argument('--transfer-proton',type=float,default=.20,help='Assumed common fractional width of R_p transfer.')
    parser.add_argument('--isospin',type=float,default=.10,help='Assumed common fractional width of K_sum/(2*K0S).')
    parser.add_argument('--check',action='store_true',help='Verify outputs without writing.')
    args=parser.parse_args()
    evidence,metadata,archive=build(args.data_dir,check=args.check,transfer_kaon=args.transfer_kaon,
                       transfer_proton=args.transfer_proton,isospin=args.isospin)
    if not args.check:
        make_review(args.data_dir,evidence,metadata,archive)
    for key in ('pion','kaon','proton'):
        fractions=[r[key]['error']/r[key]['value'] for r in metadata['rows']]
        print(f'{key}: {len(fractions)} OPTIONAL proxy points; ASSUMED marginal errors '
              f'{min(fractions):.2%}-{max(fractions):.2%}')


if __name__=='__main__':
    main()
