# Analysis inputs for inference

[Analysis overview](../analysis.md) · [NERSC campaign guide](../nersc-campaign.md)
· [Standalone inference](../../inference/README.md)

This reference describes the files passed from cache analysis to inference:
the observable matrix, its per-event source artifacts, and the optional
inventory-derived feed-down table. It explains their labels, uncertainties,
alignment and acceptance conventions. For a first fit, follow the
[student inference walkthrough](../../inference/GETTING_STARTED.md).

Use each product's metadata and the current campaign audit for actual design
counts, species, bins and analysis profile.

## Matrix files and design alignment

`analysis aggregate` writes `model_matrix.npz` and a same-stem
`model_matrix.meta.json` in the configured analysis output directory. A renamed
matrix needs its matching same-stem metadata, for example `campaign.npz` and
`campaign.meta.json`. The optional long CSV contains the same values in tidy
form for inspection.

| NPZ key | Shape | Meaning |
|---|---|---|
| `Y` | `(n_designs, n_features)` | Observable values; rows are designs and columns are labelled features |
| `Y_err` | Same as `Y` | Diagonal statistical standard errors, in the same units as `Y` |
| `feature_labels` | `(n_features,)` | Species, bin and observable for each column |
| `design_index` | `(n_designs,)` | Original zero-based design-table row IDs |
| `design_name` | `(n_designs,)` | Corresponding directory names, such as `design_027` |

Aggregation orders successful rows by design number. Failed or unselected
designs leave gaps. **Join the original full design table using `design_index`**;
matrix row 27 need not be design 27. Preserve the exact design table that
generated the events, and preserve matrix row order when aligning other arrays.
The matrix does not contain the simulator's input parameter table.

Read metadata fields including `schema`, `system`, `analysis_profile` when
present, `n_designs`, `n_features`, `row_order`, `estimator`,
`centrality_edges_pct`, `centrality_bins`, `y_window`, `charged_window`,
`weighting`, `n_boot`, `sigma_method`, `Y_err_is`, `covariance_note`,
`excluded_from_likelihood`, `vn_note`, `flow_calibration_note`, `nan_policy`,
and `n_failed` / `failed`. Profile-specific fields can impose additional
requirements: O-O inference checks its analysis profile and flow-fallback
metadata. Matching filenames alone do not establish compatibility.

Load arrays with `allow_pickle=False` and close the archive when done:

```python
from pathlib import Path
import json
import numpy as np

matrix = Path("/absolute/path/to/model_matrix.npz")
with np.load(matrix, allow_pickle=False) as saved:
    values = saved["Y"].copy()
    errors = saved["Y_err"].copy()
    labels = saved["feature_labels"].astype(str)
    design_ids = saved["design_index"].copy()
metadata = json.loads(matrix.with_suffix(".meta.json").read_text())
print(values.shape, errors.shape, design_ids)
print(metadata["system"], metadata["n_failed"])
```

The maintained fitter and `check_inputs.py` perform design alignment
and input checks. Available parameter modes are
documented in the [inference guide](../../inference/README.md#emulator-validation-inference-and-outputs).

## Feature labels and observable selection

Labels have the form `{species}__{cent_lo}_{cent_hi}__{observable}`, for example
`pi+__20.0_30.0__dN_dy`, `charged__0.0_5.0__c3_gap`, or
`global_ET__0.0_5.0__dET_total_visible_deta`. Parse the bin bounds numerically;
`40_50` and `40.0_50.0` describe the same bin.

```python
def split_feature(label):
    species, interval, observable = str(label).split("__")
    low, high = map(float, interval.split("_"))
    return species, low, high, observable

charged_c3 = [j for j, label in enumerate(labels)
              if split_feature(label)[0] == "charged"
              and split_feature(label)[3] == "c3_gap"]
print("Charged c3 columns:", [labels[j] for j in charged_c3])
```

The standard AuAu product contains 110 features per centrality bin:

| Group | Features per bin | Observables |
|---|---:|---|
| Charged | 8 | `dNch_deta`, `mean_pT`, `v2_2`, `c2_gap`, `v3_2`, `c3_gap`, `v4_2`, `c4_gap` |
| Each identified species/channel | 5 | `dN_dy`, `mean_pT`, `v2_2`, `v3_2`, `v4_2` |
| Global ET | 2 | `dET_total_visible_deta`, `dET_hadronic_deta` |

Its identified channels are `pi+`, `pi-`, `K+`, `K-`, `p`, `pbar`, `K0S`,
`Lambda`, `antiLambda`, `Xi-`, `antiXi+`, `Omega-`, `antiOmega+`, `phi`, plus
`pi_tot`, `K_tot`, `p_tot`, `Lambda_tot`, `Xi_tot`, `Omega_tot`. Charge-combined
channels sum sufficient statistics before reduction; they do not average
already reduced ratios. Read actual labels and metadata for another profile
or species set. Neither a fixed width nor the unchanged `model_matrix/v1`
schema identifies the observable selection by itself.

Charged `c{n}_gap` is the signed two-subevent cumulant. The stored `v{n}_2`
is its square root for positive cumulants and is undefined otherwise.
Identified flow uses the differential particle-of-interest versus charged
reference estimator; it is not interchangeable with charged self flow. There
is no v1 column in this layout.

The matrix retains columns that may be excluded from a likelihood. Metadata
flags `p`, `pbar`, `p_tot` and `phi`; the fitter additionally selects observable
groups and bins through its system-specific data specifications and
`--drop-specs`. Do not treat metadata flags as automatic removal by every
consumer. Review acceptance, event weighting, centrality definitions, harmonic
resolution and experimental measurements when choosing a fit.

Charged-flow calibration uses signed cumulants to retain finite negative
estimates near zero. Measured flow values map as `c_data = v_data**2`; the
approximate uncertainty map is `sigma_c = 2*v_data*sigma_v`. The central-value
map is exact, while the uncertainty map is a delta-method approximation that
needs resolved, sufficiently precise measurements. Squaring cannot correct an
acceptance or estimator mismatch. Choose resolution cuts and experimental
comparisons for the selected system's data specifications and matching profile.

Rare-species mean-pT bins can remain undefined even when charged cumulants are
finite. Check selected features and usable designs before PCA/GP training:
the [input checker](../../inference/README.md#check-inputs-without-starting-a-fit)
reports the assembled selection. Changing presentation to
`v_n = sqrt(max(c_n, 0))` does not change the signed-cumulant calibration.

## Statistical uncertainties and covariance

`Y_err` stores standard deviations of the estimated observables, not variances
or a full covariance matrix. With `--n-boot 0`, reduction uses analytic
delete-one jackknife errors for ratios/flow and SEM for linear yields. Positive
`--n-boot` requests a hydro-event bootstrap. Metadata records the method.

The statistical unit is the hydro event. Oversamples from one surface share
the same initial geometry and must not be counted as independent collisions.
Reduction uses equal-hydro-event weighting and retains zero-multiplicity
events. The signed-cumulant error comes directly from the cumulant jackknife;
the root-observable jackknife omits nonpositive root replicates and behaves
differently near zero. Use the error for the quantity actually being fitted.

Cross-observable and cross-centrality covariance requires shared design-level
event replicates or another justified covariance model. It cannot be recovered
by treating the diagonal errors as off-diagonal information. The presence of
`Y_err` also does not specify how a downstream likelihood uses statistical,
experimental and emulator uncertainties; consult the maintained
[inference implementation](../../inference/inference.py) for that model.

## Per-event source artifacts

`design_*_stageA.npz` and same-stem metadata store per-event sufficient
statistics extracted from a scope-matching published cache. They are the source
for reaggregation, not the emulator input. Keep them when further rebinning or
event-level uncertainty work is needed; the matrix alone cannot reproduce
those operations. File sizes depend on event count and retained scope.

All arrays share an event-row axis. Useful fields include:

| Fields | Meaning / remaining axes |
|---|---|
| `event_id`, `nsub_used`, `zero_event` | Hydro-event identity, retained oversample count and zero-event flag |
| `cent_mult` | Multiplicity estimators used to assign centrality |
| `yh_N`, `yh_sum_pT`, `yh_sum_pT2` | Identified counts and momentum sums by species and rapidity histogram bin |
| `charged_N`, `charged_sum_pT`, `charged_sum_pT2` | Charged scalar statistics by retained window |
| `flow_pooled_num`, `flow_pooled_den` | Single-subevent self-flow statistics by species and harmonic |
| `flow_gap_num`, `flow_gap_den` | Two-subevent self-flow statistics, including the charged reference |
| `flow_diff_num`, `flow_diff_den` | Identified particle-of-interest versus charged-reference statistics |
| `et_central` | Central-window ET sums by mask and field |
| `scalar_sums` | Oversample-summed scalar cross-checks |

Read species, harmonic, estimator and window axis orders from each artifact's
metadata. Use the reducer's oversample weighting and zero-event handling rather
than dividing blindly by `nsub_used`. For charged flow, reduction forms the
weighted numerator sum divided by the weighted denominator sum, then takes a
root only for the root observable. Averaging per-event roots or ratios gives
a different estimator.

## Campaign commands

Commands run from the **repository root**, with the selected campaign environment
active and `CFG` set to its INI. The [campaign guide](../nersc-campaign.md) covers
allocation setup, paths, resource settings and previews.

```bash
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis run --submit
python models/campaign.py --config "$CFG" analysis aggregate
python models/campaign.py --config "$CFG" validate
```

`[analysis_campaign]` supplies estimator, percentile edges, windows and
statistical settings. Aggregation reduces the existing per-event artifacts;
it does not rescan cache shards. Rebin supported retained statistics with an
explicit override, then validate:

```bash
python models/campaign.py --config "$CFG" analysis aggregate --n-boot 0 \
  --bin-edges 0 5 10 20 30 40 50 60 70 80
python models/campaign.py --config "$CFG" validate --harmonics 2 3
```

Estimator/window changes must remain within the information retained by the
cache and artifact scope. Extraction changes require re-extraction; cache-cut
or missing-information changes can require rebuilding caches or simulations.
See [what to rerun](../analysis.md#what-to-rerun-after-a-change).

Audit reports artifact completeness; missing artifacts alone do not necessarily
give a nonzero exit. Aggregation skips failed designs, records `n_failed` /
`failed`, and exits nonzero if reductions failed. Inspect both the process status
and output metadata before using a partially populated matrix.

The cumulant validator checks populated charged cumulant columns, equality of
positive-cumulant roots, and provenance of root NaNs; these failures affect
its exit status. Negative cumulants normalized by their statistical errors are
also reported, with significance flags advisory. A negative-fraction map is a
diagnostic, not a proof that each negative estimate is statistically consistent
with zero. Request harmonic 4 explicitly when inspecting its columns.
This validator does not check all detector definitions, scientific adequacy,
design alignment or every observable.

Single-design and inventory tools use the same campaign configuration:

```bash
python models/campaign.py --config "$CFG" analysis-design validate --design design_027
python models/campaign.py --config "$CFG" analysis-design reduce --design design_027 --json design_027.json
python models/campaign.py --config "$CFG" inventory --design design_027 --list
python models/campaign.py --config "$CFG" analysis feeddown
```

`design_027` is an example: select a design present in your campaign. The
implementation modules live in [models/analysis](../../models/analysis/README.md):
`extract_observables.py` extracts artifacts, `reduce_observables.py` computes
observables/errors, `analyze_design.py` handles one design, and `run_analysis.py`
dispatches multi-design analysis and feed-down export. Use the
[flag index](cli-analysis.md) for exact options.

## Optional feed-down inventory

`analysis feeddown` writes a separate `feeddown_yields.npz` and metadata with
schema `feeddown_yields/v1`. These per-PID multiplicities are not extra measured
calibration targets. They can supply correction factors to the model predictions
when an inference fit passes `--feeddown`; the primary analysis does not use
them. See [the inference input policy](../../inference/README.md#stage-the-selected-analysis-inputs)
for matching inventory requirements.

Export reads `inv_sub`, `inv_pdg` and `inv_vals` directly from verified v3
caches. Per-event analysis artifacts do not contain this inventory. Published
cache inventories remain available after optional raw-particle reaping.
`[feeddown]` selects PIDs, antiparticle combination and output; the default
destination is `<pipeline.analysis_out_dir>/feeddown_yields.npz`.

The NPZ uses the same five array names as the model matrix. Labels have the
form `{pidTOKEN}__{lo}_{hi}__N_eta{eta_max}`: `pid3212` selects a single PID and
`pidpm3212` combines particle and antiparticle. `Y` is the equal-event mean
multiplicity per hydro event; `Y_err` is its SEM. Metadata records
`inventory_eta_max`, PIDs, combination policy, binning, source, failures and
acceptance/ratio notes.

The following conventions matter when consuming it:

- The inventory is integrated over pseudorapidity, typically `|eta| < 2` for
  these profiles, with no rapidity resolution. It is not the identified
  `dN/dy` observable at `|y| < 0.5`. Read the actual acceptance from metadata;
  a wider inventory cannot be sliced into a midrapidity yield.
- Values are raw multiplicities. Dividing by `2*eta_max` gives a window-averaged
  pseudorapidity density, not a rapidity density. Form parent/product ratios
  using the same inventory window; such a ratio does not itself validate a
  midrapidity correction.
- Parent and product share events. Propagating separately reduced columns as
  independent misses their covariance. If correction uncertainty matters,
  reduce the ratio itself with shared event replicates, for example a
  delete-one jackknife.
- Align inventory and observable rows by `design_index`, even if both happen
  to be sorted. Failed designs can differ between products. The fitter requires
  coverage of all selected designs; a partial historical inventory is insufficient.
- Inspect export `n_failed` / `failed`, not just a zero exit status. Malformed
  or unverified designs are skipped and logged.
- Check PID presence before relying on a new correction. SMASH decay settings
  determine which species survive into the output. An absent PID can export
  zeros; re-caching cannot recover particles already decayed during simulation.
  Historical confirmation of Sigma0 in one campaign does not establish its
  presence in another.

Export decompresses complete cache shards and writes its combined output at
the end. Use a compute allocation for a full pass, allow enough walltime, and
keep the selected environment active in that allocation. It has no checked
per-design resume. If splitting by `--start-index` / `--end-index`, use distinct
output files and verify their metadata, uniqueness and design alignment before
combining. Keep estimator and bin edges matched to the observable matrix.

For a matching particle-plus-antiparticle inventory, labels such as
`pidpm3212__0.0_5.0__N_eta2` and `pidpm3122__0.0_5.0__N_eta2` select Sigma0 and
Lambda in the same window. Check actual labels and metadata instead of assuming
their presence or formatting. The downstream correction, experimental feed-down
convention and uncertainty treatment remain explicit physics choices.
