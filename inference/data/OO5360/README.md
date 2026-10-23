# O-O 5.36 TeV measurement inputs

These are the O-O tables available to the standalone inference pipeline. The
active `DATA_SPEC_OO5360` in [inference.py](../../inference.py) selects observables
and centrality cuts; the full ranges below describe the files, not necessarily
the fitted ranges.

## Centrality tables

Every file in `centrality/` has five columns:

```text
cent_low cent_high cent_mid val err
```

Centrality bounds and midpoints are percentages. Multiplicity and flow are
dimensionless; mean pT and its error are in GeV.

| File | Measurement | Available bins | Error convention |
|---|---|---|---|
| [dN_deta_charged_CMS.dat](centrality/dN_deta_charged_CMS.dat) | Charged dN/deta, CMS Fig. 5a | 14 bins, 0–70% | Digitized systematic errors; statistical errors below 0.1% neglected |
| [v2_2_charged_ALICE.dat](centrality/v2_2_charged_ALICE.dat) | Charged v2{2} | 16 bins, 0–60% | HEPData statistical and systematic errors added in quadrature |
| [v3_2_charged_ALICE.dat](centrality/v3_2_charged_ALICE.dat) | Charged v3{2} | 16 bins, 0–60% | HEPData statistical and systematic errors added in quadrature |
| [mean_pT_charged_ATLAS.dat](centrality/mean_pT_charged_ATLAS.dat) | Fiducial charged mean pT | 20 bins, 0–80% | Larger of the upper and lower quadrature totals |

The three JSON files in `uncertainties/` retain the source record/table,
acceptance qualifiers and separate per-bin uncertainty components. In particular,
[the ATLAS uncertainty file](uncertainties/mean_pT_charged_ATLAS.json) preserves
both asymmetric systematic errors. Compressing those into one DAT error does
not specify correlations; no full experimental covariance is included.

## Identified-hadron tables and alternative mean pT

The six `derived/*_vs_mult.dat` files have three columns:

```text
dNch_deta val err
```

They contain digitized ALICE Preliminary SQM2026 values for K0S, Xi and Omega
yields and mean pT versus mean charged multiplicity. They are **preliminary test
inputs**: the yield errors are assumed to be 10% for K0S, 12% for Xi and 15% for
Omega; all three mean-pT errors are assumed to be 3%. These are not published
experimental uncertainties or complete digitization-error estimates.

Xi and Omega yields sum particles and antiparticles; K0S is a single species.
Mean pT is the mean of the combined baryon sample, not a sum of means. Compare
these tables at matched multiplicity (`kind="vs_mult"`), without converting
CMS multiplicity into ALICE centrality percentiles. Numerical experimental
uncertainties and a covariance treatment are needed for a final calibration.

[mean_pT_charged_ATLAS_pT0.dat](derived/mean_pT_charged_ATLAS_pT0.dat) uses the
five-column centrality format and is extrapolated to pT=0. It is retained for
comparison tools, is incompatible with the current fiducial O-O profile, and
must not be fitted alongside the fiducial table: both use the same spectra.

## Acceptance and model matching

| Measurement | Acceptance / definition | Event classifier |
|---|---|---|
| CMS multiplicity | Primary charged hadrons, \|eta\|<0.5, corrected to pT=0 | HF transverse energy |
| ALICE charged flow | \|eta\|<0.8, 0.2<pT<3 GeV/c, pair \|Delta eta\|>1.4 | FT0C |
| ATLAS charged mean pT | \|eta\|<0.5, 0.27<pT<5 GeV; fiducial mean | FCal transverse energy |
| ALICE strange hadrons | \|y\|<0.5; yields and mean pT extrapolated to full pT | FT0C classes, displayed versus <dNch/deta> at \|eta\|<0.5 |

ATLAS uses a 30 ps mean-lifetime threshold for primary charged particles,
including products of shorter-lived particles. Match weak-decay feed-down
conventions and particle selections to each measurement.

O-O inference requires `analysis_profile="oo_cross_full_pt027_flat/v3"` and its
flow-sample-usage metadata. Its charged `mean_pT` corrects cached 0.25–5 GeV
moments to target 0.27–5 GeV, assuming flat dN/dpT in the boundary bin
[0.25, 0.30) GeV. Per hydro event, the count loses `0.4*N_boundary` and the
momentum sum loses `0.104 GeV*N_boundary` before reduction and jackknife.
The resulting statistical error excludes uncertainty in that spectral-shape
assumption.

Under this profile, charged `c2_gap` and `c3_gap` are compatibility labels for
signed `cross_full` cumulants: pairs come from distinct oversamples of the same
hydro event over the full flow acceptance. This is a provisional proxy for the
experimental pair-gap observable. For k>=2 oversamples, sample-pair numerator
and denominator sums are normalized by k(k-1) before the ratio of hydro-event
sums. The k=1 same-sample fallback can retain nonflow; the fitter's default
`FLOW_FALLBACK_MAX=0.0` rejects any fallback pair weight in fitted flow bins.
Flow-definition mismatch is not included in the statistical errors.

Use the [matrix reference](../../../docs/reference/analysis-inputs.md) for
array layout, design alignment and uncertainty conventions. Preserve signed
model cumulants; the fitter maps measured flow to c=v² once, rather than fitting
both forms as independent constraints. Current O-O specifications cut c3 at
45% centrality. Read labels and metadata rather than assuming fixed bin positions.

## Sources

- CMS multiplicity: [paper](https://arxiv.org/abs/2606.02285),
  [result page](https://cms-results.web.cern.ch/cms-results/public-results/publications/HIN-25-010/),
  [HEPData record](https://www.hepdata.net/record/ins3166912).
- ALICE charged flow: [paper](https://arxiv.org/abs/2509.06428),
  [HEPData record](https://www.hepdata.net/record/ins2967353).
- ATLAS charged mean pT: [paper](https://arxiv.org/abs/2606.20257),
  [HEPData record](https://www.hepdata.net/record/ins3170344), table
  `MeanpT_vs_eta_OO_centdep_midrapidity_pT270MeV.yaml`.
- ALICE strange hadrons: [Pucillo SQM2026 contribution](https://indico.global/event/13943/contributions/143551/),
  [presentation](https://indico.global/event/13943/contributions/143551/attachments/68171/132172/SQM26_StrangenessOO_Pucillo-1.pdf)
  (PDF pages 21 for yields and 10 for mean pT),
  [proceedings](https://arxiv.org/abs/2608.02874).
