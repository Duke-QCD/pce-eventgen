# O-O 5.36 TeV measurement inputs

These are the O-O tables available to the standalone inference pipeline. The active `DATA_SPEC_OO5360` in [inference.py](../../inference.py) selects observables and centrality cuts; the full ranges below describe the files, not necessarily the fitted ranges.

This is a dataset-specific reference with preliminary inputs and optional assumed proxies. It is relevant only when deliberately selecting this O-O dataset; it is not required for the general installation walkthrough or analyses using other measurements. Campaign matrices, fitted results and personal analysis notes are separate from these input definitions.

## Centrality tables

Every file in `centrality/` has five columns:

```text
cent_low cent_high cent_mid val err
```

Centrality bounds and midpoints are percentages. Multiplicity and flow are dimensionless; mean pT and its error are in GeV.

| File | Measurement | Available bins | Error convention |
|---|---|---|---|
| [dN_deta_charged_CMS.dat](centrality/dN_deta_charged_CMS.dat) | Charged dN/deta, CMS Fig. 5a | 14 bins, 0–70% | Digitized systematic errors; statistical errors below 0.1% neglected |
| [v2_2_charged_ALICE.dat](centrality/v2_2_charged_ALICE.dat) | Charged v2{2} | 16 bins, 0–60% | HEPData statistical and systematic errors added in quadrature |
| [v3_2_charged_ALICE.dat](centrality/v3_2_charged_ALICE.dat) | Charged v3{2} | 16 bins, 0–60% | HEPData statistical and systematic errors added in quadrature |
| [mean_pT_charged_ATLAS.dat](centrality/mean_pT_charged_ATLAS.dat) | Fiducial charged mean pT | 20 bins, 0–80% | Larger of the upper and lower quadrature totals |

The ALICE/ATLAS JSON files in `uncertainties/` retain the source record/table, acceptance qualifiers and separate per-bin uncertainty components. In particular, [the ATLAS uncertainty file](uncertainties/mean_pT_charged_ATLAS.json) preserves both asymmetric systematic errors. Compressing those into one DAT error does not specify correlations; no full experimental covariance is included. [The CMS evidence file](uncertainties/dN_deta_charged_CMS.json) instead records an independent extraction from the published vector figure, including the source PDF hash; it is not a HEPData table or a covariance matrix.

## Identified-hadron tables and alternative mean pT

The six `derived/*_vs_mult.dat` files have three columns:

```text
dNch_deta val err
```

They contain digitized ALICE Preliminary SQM2026 values for K0S, Xi and Omega yields and mean pT versus mean charged multiplicity. **31 of the 46 identified-particle errors have now been extracted from the total-error boxes in the slides.** The remaining 15 errors are still assumptions: all eight K0S yields (10%) and seven Xi yields (12%). The experimental totals are approximate raster measurements, not original numerical experimental tables.

The historical central-value staging CSVs and conversion script are not retained. The new uncertainty extraction has a retained [reproduction script](../../extract_alice_uncertainties.py), native-resolution [source panel images](sources/), and six per-point JSON records under `uncertainties/*_vs_mult.json`. The extra decimal places in the original central-value tables do not establish experimental precision. The x coordinates were digitized independently: the most-central values range from 128.6643 to 130.0212 across the six files (about 1.05%). No x-axis uncertainties or correlations are encoded, and the fitter treats x as exact. Do not silently align these values or infer centrality boundaries from them.

Xi and Omega yields sum particles and antiparticles; K0S is a single species. Mean pT is the mean of the combined baryon sample, not a sum of means. Compare these tables at matched multiplicity (`kind="vs_mult"`), without converting CMS multiplicity into ALICE centrality percentiles. Original numerical experimental errors for the obscured points and a covariance treatment are still needed for a final calibration.

### Identified-particle error extraction on 2026-10-26

| Input | Replaced points | Digitized experimental total / value | Exported DAT error / value, including reading margin |
|---|---|---|---|
| K0S yield | 0/8 | Obscured by filled square markers | Assumed 10%, unchanged |
| Xi + anti-Xi yield | 1/8, lowest multiplicity | About 4.67% | About 6.37%; other seven remain assumed 12% |
| Omega + anti-Omega yield | 8/8 | About 9.70–13.80% | About 11.53–16.15% |
| K0S mean pT | 8/8 | About 0.95–1.53% | About 1.15–1.71% |
| Xi + anti-Xi mean pT | 7/7 | About 2.63–3.30% | About 2.97–3.67% |
| Omega + anti-Omega mean pT | 7/7 | About 3.67–5.20% | About 3.95–5.51% |

The error boxes were read from the original embedded 2048-pixel-wide panels, with their PDF soft masks composited over white, not from an enlarged page screenshot. The PDF page 10 labels its open boxes as total uncertainties; page 21 labels its errors as total uncertainties. No statistical/systematic decomposition has been inferred and the same error is not counted twice. Source figure IDs, image hashes, reviewed marker/box pixel coordinates, and fitted tick calibrations are recorded per table. Yield axes are logarithmic: each upper/lower pixel displacement is converted using the log calibration and its fractional error is applied to the retained central value. Mean-pT axes are linear.

For resolved points, the DAT error is the larger digitized upper/lower total after expanding the endpoint displacement by **three native pixels**. The JSON keeps `experimental_total_plus`, `experimental_total_minus`, `experimental_total_symmetric` and `digitization_margin` separately. That margin is a conservative allowance for finite stroke thickness, raster compression and reading the marker/box edges; it is not an experimental systematic or a Gaussian standard deviation. Its adequacy is a digitization judgment, not an experimentally established coverage statement. Original multiplicities and central values are unchanged; tick calibration is not a justification to replace their historical digitization. The model's separate 5% discrepancy term is still added by inference and is not included in these DAT errors.

For obscured points, no endpoint is inferred from marker size, an adjacent species, or another centrality. Their per-row status remains `assumed`; `assumed_error_before_extraction` also preserves the earlier errors for the replaced points. In particular, the Xi yield file mixes one extracted point with seven assumed ones. The default fitter warns for the K0S and Xi yield groups, rather than claiming all identified errors are experimental.

Check the reproduction or regenerate from the retained panels:

```bash
inference/.venv/bin/python inference/extract_alice_uncertainties.py --check
inference/.venv/bin/python inference/extract_alice_uncertainties.py
```

To reconstruct the panel PNGs as well, supply the linked source PDF with `--source-pdf /path/to/SQM26_StrangenessOO_Pucillo-1.pdf`. Its SHA-256 is checked before using the reviewed coordinates. These regenerated inputs change likelihood weights and require a fresh calibration; previous posteriors have not been rerun.

The [six-page visual review](review/ALICE_OO_uncertainty_digitization_review.pdf) overlays the recorded edges on each original panel and pairs untouched point crops with annotated enlargements. Blue ticks show extracted experimental endpoints; magenta ticks show the three-pixel reading margin; amber rings locate points whose errors remain assumed. Each crop annotates only its target point, leaving neighbouring markers as context. The captions give the experimental upper/lower errors and the symmetric DAT error separately. Marks use the recorded source centers, rather than moving them to the retained DAT central values. Native-resolution `*_overlay.png` and enlarged `*_review.png` files are also retained in `review/`.

Regenerate the review with `python inference/plot_alice_uncertainty_overlays.py` using a Python environment containing Pillow, numpy and reportlab. It checks source-image hashes and reads the saved uncertainty metadata without changing any data tables.

### Optional light-hadron yield proxies on 2026-10-27

`optional/` contains **assumed proxies, not measured O-O pion/charged-kaon/proton yields**. They are disabled by default. Twelve points each of the projected K0S/pion and proton/pion ratios were digitized from the original [ALI-SIMUL-480342 figure](https://alice-figure.web.cern.ch/system/files/figures/bianchil/img_ToPionRatiosOOfin_1.pdf), whose PDF and native 2362x3012 raster are retained in `sources/`. The projection targets 6.37 TeV and 1 nb^-1, rather than the actual 5.36 TeV run. [ALICE-PUBLIC-2021-004](https://cds.cern.ch/record/2765973/files/ALICE_PN_ProjectionShortOxygenRun.pdf), Fig.16 and section 7.1, describes prior pp/p-Pb inputs, a multiplicity-scaling assumption and Run-2 systematic assumptions. The projection error boxes describe expected precision; they are digitized for review but **not propagated as measured uncertainties**.

The projection's shorthand K0S/pi row agrees with the reference series explicitly labelled **2 K0S / (pi+ + pi-)** in [ALICE arXiv:1606.07424, Fig.2](https://arxiv.org/pdf/1606.07424#page=4). Thus its denominator is the charge-averaged pion yield when written as K0S/pi. The proton ratio is **(p+pbar)/(pi+ + pi-)**, explicitly defined in Fig.3 of that paper. Its displayed factor **x6 is removed once**. The shorthand projection does not itself spell out these sums; the neutral-kaon convention is resolved by the reference-series normalization cross-check, rather than by its axis title alone. These definitions, coordinates, calibrated log axes, projected box displacements and source hashes are in [projection_ratios.json](optional/projection_ratios.json).

At each of the eight measured K0S multiplicities M, interpolate ln(ratio) linearly in ln(M), without extrapolation. With R_S=2 K0S/(pi+ + pi-) and R_p=(p+pbar)/(pi+ + pi-), construct:

- **Pions:** Y_pi = 2 Y_K0S / R_S.
- **Charged kaons:** Y_K = 2 Y_K0S, using approximate isospin symmetry. This assumption is separate from the charge convention of R_S and does not enter the pion formula.
- **Protons:** Y_p = R_p Y_pi.

All Y are charge-summed dN/dy at |y|<0.5, extrapolated over pT. Multiplicity is a matching coordinate; no dNch/deta to dN/dy conversion or independent charged-multiplicity normalization is used. Match the primary weak-decay subtraction to the model, especially for protons. No posterior has been rerun.

| Assumed uncertainty component | Default width and correlation |
|---|---|
| K0S anchor | Existing **assumed 10%**, independent between multiplicity bins; shared by the anchor and all three proxies in each bin |
| R_S transfer between systems/energies | **15%**, common across multiplicity and shared by pion/proton proxies |
| R_p transfer between systems/energies | **20%**, common across multiplicity; independent of R_S transfer |
| Charged/neutral-kaon relation | **10%**, common across charged-kaon bins |
| Reading coordinates | **3 native pixels** in x and y, used as assumed Gaussian-equivalent widths; x shared between the two ratio series |
| Interpolation choice | Difference from interpolation linear in M and ratio, treated as an additional per-bin width |

The transfer and isospin widths are analyst choices, **not experimentally established uncertainties or guaranteed conservative coverage**. They include possible energy/system changes without applying a separate central-value energy rescaling. Source reading bounds are also not experimental standard deviations. The resulting marginal errors are about **18.1% for pions, 14.1% for charged kaons and 27.0% for protons**, before the fitter's separate 5% diagonal model-discrepancy term. See [per-point components and assumptions](optional/yield_proxy_metadata.json), the [yield plot](review/OO_light_hadron_yield_proxies.png), the [source overlay](review/ALI-SIMUL-480342_ratios_overlay.png) and the [assumed correlation matrix](review/OO_light_hadron_proxy_correlations.png).

The three `optional/proxy_dN_dy_*_vs_mult.dat` files contain M, yield and marginal error. **Do not fit them as independent measurements, or independently alongside their K0S anchor.** The joint covariance in `optional/yield_proxy_covariance.npz` covers K0S, pions, charged kaons and protons (eight bins each). It retains all shared anchor, ratio, isospin, reading and interpolation components under the stated correlation assumptions. It is an assumed covariance, not an experimental covariance matrix.

Regenerate or check from the repository root:

```bash
inference/.venv/bin/python inference/build_oo_yield_proxies.py
inference/.venv/bin/python inference/build_oo_yield_proxies.py --check
# Example sensitivity variation; these choices replace the optional proxy outputs:
inference/.venv/bin/python inference/build_oo_yield_proxies.py --transfer-kaon 0.20 --transfer-proton 0.30 --isospin 0.15
```

The default calibration remains unchanged. Explicitly enable all three proxies with:

```bash
inference/.venv/bin/python inference/check_inputs.py --system OO5360 --oo-yield-proxies
inference/.venv/bin/python inference/inference.py --system OO5360 --oo-yield-proxies --out-dir plots_OO5360_yield_proxies
```

This validates the retained evidence, selects the matched covariance submatrix after coverage cuts/exclusions, and uses the correlated Gaussian likelihood with the separate 5% model term. Proxy provenance and uncertainty choices enter the run/cache identity. Individual proxies can be excluded with their full `proxy_dN_dy_*_vs_mult` spec names via `--drop-specs`; that option replaces the usual exclusions, so include `mean_pT_K0S_vs_mult` when preserving the primary exclusion. The matrix must contain the pi_tot, K_tot and p_tot full-pT yield columns and cover the requested multiplicities. Compare against a fit without proxies, and vary the assumed transfer widths before interpreting added constraints.

### Charged-multiplicity closure check on 2026-10-27

**The proxy central values increasingly undershoot charged multiplicity, but remain compatible with the broad assumed proxy errors.** This is a conditional consistency check, not an additional measurement or a closure-normalized replacement for the proxies. The comparison uses the measured multiplicity coordinates attached to the **same ALICE FT0C yield classes**. It does not identify ALICE classes with CMS HF or ATLAS FCal centrality percentiles. Multiplicity-axis errors/covariance were not extracted.

For a locally flat true-rapidity distribution, convert each species using its normalized full-pT spectrum, with the finite-window Jacobian

`J_s = <asinh[(pT/mT) sinh(0.5)]/0.5>_{dN/(dpT dy)}`.

Then `dNch/deta = sum_s J_s * dN_s/dy`. The brackets require a spectral integral; evaluating the Jacobian at the mean pT overestimates it. No measured O-O pion/charged-kaon/proton spectra are available in these inputs. The diagnostic therefore transfers the [ALICE p-Pb reference blast-wave fits](https://arxiv.org/pdf/1307.6796), Eq.(1)-(2), Tables 1 and 5, and rescales all momenta to the measured O-O K0S mean pT in each class. Reference parameters interpolate below M=45 and are held fixed above that reference coverage before rescaling. This is an explicit shape assumption, not an O-O PID fit. A scan over all seven reference shapes plus a deliberately wide 0–60% soft-pion admixture at T=100 MeV tests sensitivity. The simultaneous reference pion fit excludes pT<0.5 GeV: its naive full-pT continuation can be too hard. The scan allows softer spectra but is **not a confidence interval or guaranteed uncertainty coverage**. Flat rapidity near midrapidity is another unquantified approximation.

The charged sum includes measured Xi-/anti-Xi+ and Omega-/anti-Omega+ yields. Unmeasured Sigma+/Sigma- and antiparticles use a direct Boltzmann estimate at T=155 MeV, approximately `0.5225*(p+pbar)`, with an assumed 100% common normalization width; resonance feed-down is not explicitly calculated. A tiny nuclei allowance of `0.005*(p+pbar)` uses J=1 and an assumed 100% common width. These are estimates, not measured O-O Sigma/nuclei yields or strict bounds. Neutral K0S/K0L/Lambda and their weak-decay daughters are excluded; strong-resonance daughters are already in the inclusive primary yields. Count charged particles once, including multiply charged nuclei, rather than summing units of charge. Prompt leptons are excluded in the CMS definition; possible ALICE prompt-lepton contributions are only a separate illustrative +1% stress test. Material conversions and long-lived weak-decay daughters are excluded. See the [ALICE primary-particle definition](https://cds.cern.ch/record/2270008) and [CMS multiplicity paper](https://arxiv.org/pdf/2606.02285).

| Measured ALICE M | Nominal predicted dNch/deta | Assumed propagated error | Prediction / M | Illustrative shape range |
|---:|---:|---:|---:|---:|
| 11.71 | 12.44 | 2.12 | 1.062 | 11.88–12.52 |
| 26.90 | 26.36 | 4.48 | 0.980 | 25.24–26.64 |
| 37.84 | 35.85 | 6.08 | 0.948 | 34.37–36.30 |
| 51.06 | 47.17 | 7.99 | 0.924 | 45.22–47.78 |
| 67.02 | 61.07 | 10.32 | 0.911 | 58.52–61.86 |
| 87.28 | 79.01 | 13.35 | 0.905 | 75.66–80.02 |
| 106.72 | 95.20 | 16.05 | 0.892 | 91.10–96.40 |
| 129.47 | 113.06 | 19.01 | 0.873 | 108.08–114.45 |

At the highest multiplicity, nominal J_pi/J_K/J_p are 0.914/0.779/0.715. The pi/K/p contributions to dNch/deta are 94.51/12.08/3.91; other charged species add only **2.56**, predominantly the assumed Sigma component (1.99). The deficit is **16.41 (12.7%)**, or about **0.86 of the assumed marginal diagnostic error**. The illustrative shape scan gives a **11.6–16.5% deficit**. Even doubling the Sigma/nuclei estimates and adding the illustrative +1% prompt-lepton allowance leaves the highest point about 9% low for the hardest scanned shape. These choices do not establish a rigorous bound on missing species or spectra.

The error propagates the **full joint proxy covariance**, preserving the shared K0S anchor and common ratio/isospin assumptions; it also includes Xi/Omega marginal errors, the Sigma/nuclei assumptions and the K0S mean-pT response. The shape scan is separate. The conditional quadratic residual is **3.23 for eight points**, using this assumed covariance; it is not assigned an experimental p-value because the multiplicity errors, true O-O PID spectral uncertainties and several experimental covariances are missing. In particular, the 10% anchor errors and their independence across bins are assumptions. At fixed K0S and proton/pion ratio, the highest point would require raising pi and p by about **16.3%**, corresponding to lowering R_S from 0.150 to about **0.129**. This lies within the scale of the assumed 15% ratio-transfer uncertainty, but the required adjustment varies across multiplicity. No renormalization has been applied, and proxies remain disabled by default.

The [comparison plot](review/OO_proxy_multiplicity_closure.png), [per-class results and assumptions](optional/proxy_multiplicity_closure.json), [numeric diagnostic](optional/proxy_multiplicity_closure.dat) and [propagated covariance](optional/proxy_multiplicity_closure_covariance.npz) are retained separately from the calibration inputs. Reproduce or check without modifying yields:

```bash
inference/.venv/bin/python inference/check_oo_proxy_closure.py
inference/.venv/bin/python inference/check_oo_proxy_closure.py --check
```

[mean_pT_charged_ATLAS_pT0.dat](derived/mean_pT_charged_ATLAS_pT0.dat) uses the five-column centrality format and is extrapolated to pT=0. It is retained for comparison tools, is incompatible with the current fiducial O-O profile, and must not be fitted alongside the fiducial table: both use the same spectra.

## Acceptance and model matching

| Measurement | Acceptance / definition | Event classifier |
|---|---|---|
| CMS multiplicity | Primary charged hadrons, \|eta\|<0.5, corrected to pT=0 | HF transverse energy |
| ALICE charged flow | \|eta\|<0.8, 0.2<pT<3 GeV/c, pair \|Delta eta\|>1.4 | FT0C |
| ATLAS charged mean pT | \|eta\|<0.5, 0.27<pT<5 GeV; fiducial mean | FCal transverse energy |
| ALICE strange hadrons | \|y\|<0.5; yields and mean pT extrapolated to full pT | FT0C classes, displayed versus <dNch/deta> at \|eta\|<0.5 |

ATLAS uses a 30 ps mean-lifetime threshold for primary charged particles, including products of shorter-lived particles. Match weak-decay feed-down conventions and particle selections to each measurement.

O-O inference requires `analysis_profile="oo_cross_full_pt027_flat/v3"` and its flow-sample-usage metadata. Its charged `mean_pT` corrects cached 0.25–5 GeV moments to target 0.27–5 GeV, assuming flat dN/dpT in the boundary bin [0.25, 0.30) GeV. Per hydro event, the count loses `0.4*N_boundary` and the momentum sum loses `0.104 GeV*N_boundary` before reduction and jackknife. The resulting statistical error excludes uncertainty in that spectral-shape assumption.

Under this profile, charged `c2_gap` and `c3_gap` are compatibility labels for signed `cross_full` cumulants: pairs come from distinct oversamples of the same hydro event over the full flow acceptance. This is a provisional proxy for the experimental pair-gap observable. For k>=2 oversamples, sample-pair numerator and denominator sums are normalized by k(k-1) before the ratio of hydro-event sums. The k=1 same-sample fallback can retain nonflow; the fitter's default `FLOW_FALLBACK_MAX=0.0` rejects any fallback pair weight in fitted flow bins. Flow-definition mismatch is not included in the statistical errors.

Use the [matrix reference](../../../docs/reference/analysis-inputs.md) for array layout, design alignment and uncertainty conventions. Preserve signed model cumulants; the fitter maps measured flow to c=v² once, rather than fitting both forms as independent constraints. Current O-O specifications cut c3 at 45% centrality. Read labels and metadata rather than assuming fixed bin positions.

## Audit on 2026-10-26

Run the reproducible offline check from the repository root:

```bash
inference/.venv/bin/python inference/audit_oo_data.py
```

This checks all 11 DAT files (132 rows, including the unused pT=0 alternative), centrality/multiplicity ordering, positive errors, the remaining assumed-error formulas, the new digitized-error calculations and panel-image hashes, and agreement with the retained source evidence. It does not certify that an external source snapshot is complete or current.

| Input | Audit finding | Remaining limitation |
|---|---|---|
| CMS multiplicity | All 14 central values agree with a fresh independent extraction from the published Fig. 5a to within 0.0012, and all systematic errors within 0.0009 in dN/deta. The central result is consistent with the paper's 135.0 ± 4.0. | Digitized, not original numerical tables. Keep the existing values; their many decimals overstate the source precision. Statistical errors below 0.1% are omitted. |
| ALICE v2 and v3 | All 32 rows, bins and quadrature errors exactly match the retained JSON transcriptions to rounding precision. The source identifies OO, not Ne–Ne; the cuts agree with the paper. | Fresh HEPData requests returned HTTP 403. This verifies internal transcription consistency, not an independent row-by-row comparison against a newly downloaded original. |
| ATLAS fiducial charged mean pT | All 20 rows match the retained JSON; the larger asymmetric total is used correctly. The 0.27 GeV cut is the intended fiducial definition. | Fresh HEPData access was denied. Conservative symmetrization discards the strong asymmetry: the 0–1% total is +0.001630 / −0.000561 GeV. |
| Identified yields | Eight rows per species visually agree with the OO series in Pucillo PDF p. 21; K0S is single-species, Xi/Omega are particle plus antiparticle. No factor-of-two error found. Nine errors were subsequently extracted as detailed above. | Fifteen errors are still assumed; no original numerical tables or x-errors. Central values were not re-extracted. |
| Identified mean pT | K0S has eight points; Xi/Omega each have seven. Values visually agree with the species panels on PDF p. 10; no display-scale factor needs undoing. All 22 errors were subsequently extracted as detailed above. | Approximate raster extraction with a reading margin; missing lowest-multiplicity cascade points have not been filled or extrapolated. |
| ATLAS pT=0 alternative | Its centrality grid and numerical format pass checks; it is not selected by the current fit. | No separate uncertainty-component snapshot is retained, so its quoted errors have not been independently reconstructed in this audit. |

The source PDF hashes recorded below identify the versions visually inspected:

- Pucillo SQM presentation: SHA-256 `b3146a9e118398a653f08c4ec08f4c7ca1a01a8cb6d96ca4b4aebb6e5f365340`.
- CMS Fig. 5a: see `source_sha256` in the CMS evidence JSON.

### Calibration issues that remain open

- **Default selected inputs still include some assumed uncertainties.** The primary O-O selection uses the three identified yields and Xi/Omega mean pT; only K0S mean pT is excluded. Assembly warns about the K0S/Xi yield groups, where 15 points retain assumptions. Observable selection was preserved; 31 identified-particle error values were subsequently replaced as detailed above.
- **The old K0S exclusion argument is stale.** The comment quoting a data K0S/charged mean-pT ratio near 1.40 used the extrapolated charged mean pT. The most-central stored numbers give 0.885648 / 0.7976 = 1.110 for the current fiducial definition, versus 0.885648 / 0.64356 = 1.376 for the unused pT=0 alternative. These are illustrative nearby central samples, not an exact matched-multiplicity comparison. The claimed model reachability bound cannot be carried to the current profile without checking the matrix. The exclusion remains in place pending that check.
- **The 5% `sys_floor` is not experimental uncertainty.** It is added in quadrature as a diagonal discrepancy term, not applied as a maximum. The remaining assumed 10/12% yield errors become 11.18/13.00%; the digitized points use their individual exported errors plus the same 5% term. In flow space, it is 5% on v, becoming approximately 10% on c=v² before experimental error is included. This does not replace a correlated systematic-error model.
- **Centrality selections differ.** CMS HF, ALICE FT0C and ATLAS FCal percentiles are compared to a model multiplicity estimator. Matching bin edges alone does not match the selected populations. The CMS 0–5% point aggregates five model bins; the other CMS bins and the ALICE/ATLAS bins match the intended fine model grid individually.
- **Multiplicity interpolation is an approximation.** Identified inputs are interpolated separately for each design at fixed measured mean multiplicity; x-errors are ignored, and class averages of nonlinear observables need not equal an interpolated curve. Only x points inside every design's covered range survive; adding a design can therefore change the fitted data selection. Interpolated model errors do not supply cross-observable covariance.
- **Flow is a proxy comparison.** Cross-oversample full-acceptance model cumulants are compared with experimental same-event gapped cumulants; nonflow and longitudinal decorrelation differences are not quantified. The 45% v3 cutoff is a model-resolution choice, not an experimental-data defect. The v→c error conversion uses a first-order approximation; it is least accurate for the peripheral v3 points, which are excluded from the primary fit.
- **Primary-particle conventions need matching.** ATLAS includes charged leptons in its lifetime-based charged-particle definition, whereas CMS multiplicity is primary charged hadrons. The model's hadronic final-particle sample may not implement every experiment's weak-decay and lifetime convention. Lambda inputs would additionally require Sigma0 feed-down matching.
- **This audit is data-only.** It does not establish retained multiplicity-space points, per-bin model statistics, fallback pair weights, model reachability or posterior consequences for a supplied campaign matrix. Run `check_inputs.py --system OO5360` with the actual matrix, then follow the [production workflow](../../PRODUCTION.md). A data-only pass is not a calibration validation.

The CMS result page currently links the unrelated arXiv paper 2606.09567. The correct multiplicity paper is [2606.02285](https://arxiv.org/abs/2606.02285), confirmed by its title, report number and abstract. The existing local citation was correct and was retained.

## Sources

- CMS multiplicity: [paper](https://arxiv.org/abs/2606.02285), [result page](https://cms-results.web.cern.ch/cms-results/public-results/publications/HIN-25-010/), [HEPData record](https://www.hepdata.net/record/ins3166912).
- ALICE charged flow: [paper](https://arxiv.org/abs/2509.06428), [HEPData record](https://www.hepdata.net/record/ins2967353).
- ATLAS charged mean pT: [paper](https://arxiv.org/abs/2606.20257), [HEPData record](https://www.hepdata.net/record/ins3170344), table `MeanpT_vs_eta_OO_centdep_midrapidity_pT270MeV.yaml`.
- ALICE strange hadrons: [Pucillo SQM2026 contribution](https://indico.global/event/13943/contributions/143551/), [presentation](https://indico.global/event/13943/contributions/143551/attachments/68171/132172/SQM26_StrangenessOO_Pucillo-1.pdf) (PDF pages 21 for yields and 10 for mean pT), [proceedings](https://arxiv.org/abs/2608.02874).
