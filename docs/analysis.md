# From particle output to a calibration matrix

[Handbook](README.md) · [Campaign commands](nersc-campaign.md#5-extract-aggregate-and-validate-observables) · [Analysis-input reference](reference/analysis-inputs.md)

This guide explains cache reduction and result interpretation. Follow the [campaign commands](nersc-campaign.md#5-extract-aggregate-and-validate-observables) to produce results, then use the separate [inference pipeline](../inference/README.md).

The [small inference walkthrough](../inference/GETTING_STARTED.md) exercises the downstream interface with synthetic inputs.

## Why cache before analyzing

The v3 cache reduces raw particle lists to counts, histograms, flow Q-vectors and inventories. Publication verifies payloads/provenance on persistent storage. Quantities outside this retained scope may require new simulation after raw reaping.

The **fingerprint** fixes schema, species, acceptance cuts, bins and conventions. Mismatches need investigation; keep distinct scopes in separate cache/artifact roots.

The bundled profiles differ, for example:

| Scope | AuAu200 gap015 | OO5360 ALICE subevent gap14 |
|---|---|---|
| Charged flow \|η\| limit | 1.0 | 0.8 |
| Flow pT range (GeV) | 0.2–5.0 | 0.2–3.0 |
| Full subevent η gap | 0.15 | 1.4 |
| Identified-yield \|y\| limit | 0.5 | 0.5 |
| Identified-flow \|y\| limit | 1.0 | 1.0 |
| Inventory \|η\| limit | 2.0 | 2.0 |

Read the complete JSON in `config/cache_fingerprint_*.json` for all conventions. Centrality estimator windows and charged scalar histogram windows are distinct from the flow cuts; a field called `eta05` is not a global cut on every observable.

## Extraction, reduction, aggregation

**Extraction** reads a published cache and writes per-design sufficient statistics (`_observables.npz` plus metadata). It preserves the event structure needed for reduction. `analysis run` previews; add `--submit` to execute. Scope- and analysis-profile-matching completed artifacts are skipped unless forced.

**Reduction** assigns events to centrality classes using a cached multiplicity estimator, then computes observables and uncertainties. The generated campaign INI uses `cent_ch_eta10` and percentile edges `0 5 10 20 30 40 50 60 70 80`. Lower centrality percentiles correspond to the high-multiplicity end. The definition is within each design's simulated population; it is not an automatic experimental detector/trigger model.

**Aggregation** reduces selected artifacts and stacks them into a matrix with stable feature labels. It checks fingerprint and analysis-profile consistency before combining designs. A design that fails reduction is recorded in metadata and skipped; the command returns nonzero if any design fails. A matrix file existing is therefore not proof that every requested design succeeded.

Analysis reads published caches before or after optional raw reaping. Keep raw data until the retained scope meets your scientific plan.

## OO5360 observable profile

For `oo_cross_full_pt027_flat/v3`, use the OO5360 cache fingerprint and set these values in the campaign INI (update the existing sections):

```ini
[pipeline]
expected_fingerprint = /absolute/path/to/pce-eventgen/config/cache_fingerprint_OO5360_ALICE_subevent_gap14.json
analysis_out_dir = /absolute/path/to/analysis/OO5360_pt027_v3

[analysis_campaign]
analysis_profile = oo_cross_full_pt027_flat/v3
estimator = cent_ch_eta10
bin_edges = 0 1 2 3 4 5 10 15 20 25 30 35 40 45 50 55 60 65 70 75 80
y_window = 0.5
charged_window = eta05
n_boot = 0
```

The campaign must already select `run.system = OO5360` and the matching published-cache root. Use a new analysis output directory when switching profiles, then run the usual `analysis run --submit`, `analysis audit`, `analysis aggregate`, and `validate` commands in the [campaign guide](nersc-campaign.md#5-extract-aggregate-and-validate-observables). Existing caches suffice: no raw particle output or cache rebuild is needed. Subsequent runs resume normally. Old baseline/v1/v2 artifacts cannot be relabeled or mixed with v3.

This profile retains the original matrix order: 110 features per bin, 2200 for these 20 bins. It changes only charged mean pT and charged n=2,3 flow:

- Charged mean pT targets 0.27–5 GeV. Per hydro event, it subtracts `0.4 * N_boundary` from cached 0.25–5 GeV counts and `0.104 GeV * N_boundary` from their pT sum, assuming flat dN/dpT in the cached [0.25, 0.30) bin. Interpolation uncertainty is not included in `Y_err`.
- Charged n=2,3 use distinct oversamples over the full cached flow acceptance (|η| < 0.8, 0.2 < pT < 3 GeV), with pair contributions normalized by `k(k−1)` per hydro event. One-sample events use same-sample pairs with self-pairs removed; fewer than two particles contribute zero pairs. Metadata records fallback counts and pair-weight fractions. This is a provisional proxy for experimental gap flow; the fallback can retain same-sample nonflow.
- `c2_gap` and `c3_gap` remain compatibility column labels for signed cross-full cumulants. Identified flow keeps its original gap reference. Charged multiplicity, charged n=4, identified observables and ET retain their calculations.

Matrix metadata records the analysis profile separately from the cache fingerprint. Equal-event weighting and hydro-event jackknife errors match the original v3 implementation. Exact reproduction also requires the same designs, cached events, settings and numerical environment; compare both matrix arrays and scientific metadata. For a reference with nonconsecutive designs, `analysis aggregate --designs design_000 design_007 ...` selects those rows and errors if any artifact is missing. The default `baseline` profile preserves the existing calculations for other analyses.

## What the statistical unit is

Hydro events are independent initial geometries. iS3D/SMASH oversamples from one surface share that geometry and cannot be counted as independent collisions for error estimation. The v3 implementation retains per-oversample flow Q-vectors and ET histograms, while identified-yield/scalar histograms are aggregated at the hydro-event level. Reduction handles these storage levels and uses `equal_event` weighting in the matrix pipeline.

`n_boot = 0` computes analytic jackknife/SEM uncertainties. It does **not** turn errors off. Positive values request a slower hydro-event bootstrap. Matrix `Y_err` is one standard error per cell; it is not cross-observable covariance. A covariance estimate needs a shared-replicate design-level procedure using the retained event statistics. Do not build it by treating all oversamples as independent.

## Read a result safely

Load the matrix and its same-stem metadata using the [array contract and Python example](reference/analysis-inputs.md#matrix-files-and-design-alignment). `Y` holds observables, `Y_err` their diagonal standard errors, and `feature_labels` identify columns. Join rows to the original design table using `design_index`, which can have gaps.

Inspect shape, labels, centrality settings and `n_failed` in the actual product. The standard AuAu selection has 110 features per bin, but the unchanged `model_matrix/v1` schema does not identify the selection by itself.

## Undefined flow and useful validation

Baseline charged two-subevent flow uses `v_n{2} = sqrt(c_n{2})` for positive cumulants. Finite samples can give zero or negative cumulants, where the stored root is NaN. The matrix also includes signed charged `c2_gap`, `c3_gap`, and `c4_gap`. Retaining the signed quantity avoids losing those measurements at the square root step, but does not make a low-significance bin informative.

`campaign.py validate` checks charged cumulant/root consistency, not detector matching or all observables. Also inspect failures, finite values, scope and labels. The [input reference](reference/analysis-inputs.md#campaign-commands) explains validator diagnostics.

## ET, kaons, and feeddown conventions

The cache uses PDG pole masses for ET, not the mass column from an OSCAR row. It records total-visible and hadronic-component ET masks with explicit EM assignments; “hadronic component” is experimental ET bookkeeping, not a quark-content classification. Unknown visible species trigger audits by default. The fingerprint documents baryon-sign corrections and the `exp_mN` and `exp_pdg_mass` conventions.

The retained neutral-kaon convention uses `abs(pdg)==311`, with half weight for K0S yield/ET species quantities, unweighted pooled K0S flow, and **full** neutral kaon weight in global ET. Do not silently apply the yield convention to all other quantities.

`analysis feeddown` exports inventory PID yields into a separate file. Its inventory has a wide pseudorapidity acceptance, unlike identified rapidity yields; simple window rescaling does not supply a validated midrapidity correction. Use the [inventory acceptance and correlated-ratio guidance](reference/analysis-inputs.md#optional-feed-down-inventory). `analysis/feeddown_census.py` is another diagnostic with a different report.

## What to rerun after a change

| Change | Work to revisit |
|---|---|
| Centrality estimator/bins, supported reduction window, bootstrap count | Reaggregate and validate using matching retained statistics |
| Analysis profile, extraction ET window or extracted statistic content | Re-extract into a clearly identified output set, then aggregate/validate |
| Cache cuts, fingerprint, species/conventions, or quantities outside retained statistics | Rebuild/re-publish from appropriate raw data; if raw were deleted, simulation may be required |
| Design physics, initial conditions, EOS/coefficient/particle inputs | A separate, consistently initialized simulation/provenance set |

Diagnostics outside this chain are listed in the [analysis directory guide](../models/analysis/README.md).
