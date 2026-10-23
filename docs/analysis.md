# From particle output to a calibration matrix

[Handbook](README.md) · [Campaign commands](nersc-campaign.md#5-extract-aggregate-and-validate-observables)
· [Analysis-input reference](reference/analysis-inputs.md)

The matrix-consuming Stage B code now lives in [inference/](../inference/README.md),
with its own environment and entry points. Run it separately after selecting the
matrix, metadata, exact design table and measurement inputs. Campaign commands
do not automatically launch inference.

New students can start with the [small inference walkthrough](../inference/GETTING_STARTED.md),
which generates a synthetic input set and follows it through checks, sampling and plots.

## Why cache before analyzing

Raw OSCAR particle lists are large. The v3 cache stores selected counts,
histograms, flow Q-vectors and inventories, with enough structure to reconstruct
the supported observables. Publication verifies compressed payloads and
provenance on persistent storage. A cache is a data reduction with a defined
scope, not a copy of every particle coordinate. Once raw data are reaped,
observables outside the retained information may require new simulation.

The **fingerprint** records the schema, species, acceptance cuts, histogram
edges, flow conventions, ET masks, and storage levels. Code compares it to the
expected JSON. A mismatch is a scope/provenance issue to investigate, not a
message to suppress. Keep different scopes in different cache/artifact roots.

The bundled profiles differ, for example:

| Scope | AuAu200 gap015 | OO5360 ALICE subevent gap14 |
|---|---|---|
| Charged flow \|η\| limit | 1.0 | 0.8 |
| Flow pT range (GeV) | 0.2–5.0 | 0.2–3.0 |
| Full subevent η gap | 0.15 | 1.4 |
| Identified-yield \|y\| limit | 0.5 | 0.5 |
| Identified-flow \|y\| limit | 1.0 | 1.0 |
| Inventory \|η\| limit | 2.0 | 2.0 |

Read the complete JSON in `config/cache_fingerprint_*.json` for all conventions.
Centrality estimator windows and charged scalar histogram windows are distinct
from the flow cuts; a field called `eta05` is not a global cut on every observable.

## Extraction, reduction, aggregation

**Extraction** reads a published cache and writes per-design sufficient
statistics (`_stageA.npz` plus metadata). It preserves the event structure
needed for reduction. `analysis run` previews; add `--submit` to execute.
Scope-matching completed artifacts are skipped unless forced.

**Reduction** assigns events to centrality classes using a cached multiplicity
estimator, then computes observables and uncertainties. The generated campaign INI
uses `cent_ch_eta10` and percentile edges `0 5 10 20 30 40 50 60 70 80`.
Lower centrality percentiles correspond to the high-multiplicity end. The
definition is within each design's simulated population; it is not an automatic
experimental detector/trigger model.

**Aggregation** reduces selected artifacts and stacks them into a matrix with
stable feature labels. It checks fingerprint consistency before combining
designs. A design that fails reduction is recorded in metadata and skipped;
the command returns nonzero if any design fails. A matrix file existing is
therefore not proof that every requested design succeeded.

Use the [campaign sequence](nersc-campaign.md) rather than invoking cache
builder/publisher/reaper manually for normal operations. Analysis can run
before or after optional raw reaping. Keep raw data until you are confident
the retained scope meets the scientific plan.

## What the statistical unit is

Hydro events are independent initial geometries. iS3D/SMASH oversamples from
one surface share that geometry and cannot be counted as independent collisions
for error estimation. The v3 implementation retains per-oversample flow Q-vectors
and ET histograms, while identified-yield/scalar histograms are aggregated at
the hydro-event level. Reduction handles these storage levels and uses
`equal_event` weighting in the matrix pipeline.

`n_boot = 0` computes analytic jackknife/SEM uncertainties. It does **not** turn
errors off. Positive values request a slower hydro-event bootstrap. Matrix
`Y_err` is one standard error per cell; it is not cross-observable covariance.
A covariance estimate needs a shared-replicate design-level procedure using
the retained event statistics. Do not build it by treating all oversamples as
independent.

## Read a result safely

```python
from pathlib import Path
import json
import numpy as np

matrix = Path("/absolute/path/to/analysis/model_matrix.npz")
with np.load(matrix, allow_pickle=False) as data:
    print(data["Y"].shape)
    print(data["design_index"])
    print(data["feature_labels"][:5])
    # Copy arrays needed after the file is closed.
    values = data["Y"].copy()
    errors = data["Y_err"].copy()
metadata = json.loads(matrix.with_suffix(".meta.json").read_text())
print(metadata["centrality_edges_pct"], metadata["n_failed"])
```

| Array | Meaning |
|---|---|
| `Y` | Rows = designs, columns = centrality/species/observable features |
| `Y_err` | Same shape; diagonal statistical standard errors |
| `design_index` | Actual integer IDs in row order; may have gaps |
| `design_name` | Corresponding directory names |
| `feature_labels` | Labels such as `<species>__<cent_low>_<cent_high>__<observable>` |

Join your input design table using `design_index`; never assume matrix row 27
means design 27. Read the actual matrix shape, labels and metadata for your campaign.
The current feature selection has 110 features per centrality bin; read actual
labels and metadata even though the schema remains `model_matrix/v1`.

## Undefined flow and useful validation

Charged two-subevent flow uses `v_n{2} = sqrt(c_n{2})` for positive cumulants.
Finite samples can give zero or negative cumulants, where the stored root is
NaN. The matrix also includes signed charged `c2_gap`, `c3_gap`, and `c4_gap`.
Retaining the signed quantity avoids losing those measurements at the square
root step, but does not make a low-significance bin informative.

`campaign.py validate` checks requested harmonics' charged cumulant/root
consistency. It does not validate every physical observable, detector match,
uncertainty model, or input design. Check the analysis audit, failure counts,
finite-value patterns, acceptance conventions, and labels as well. For a
code/data-interface regression, run
`python models/analysis/check_cache_profiles.py` in a compatible NumPy environment;
it constructs synthetic AuAu and OO fixtures rather than running the models.

## ET, kaons, and feeddown conventions

The cache uses PDG pole masses for ET, not the mass column from an OSCAR row.
It records total-visible and hadronic-component ET masks with explicit EM
assignments; “hadronic component” is experimental ET bookkeeping, not a
quark-content classification. Unknown visible species trigger audits by default.
The fingerprint documents baryon-sign corrections and the `exp_mN` and
`exp_pdg_mass` conventions.

The retained neutral-kaon convention uses `abs(pdg)==311`, with half weight for
K0S yield/ET species quantities, unweighted pooled K0S flow, and **full** neutral
kaon weight in global ET. Do not silently apply the yield convention to all
other quantities.

`analysis feeddown` exports inventory PID yields into a separate file. Its
inventory has a wide pseudorapidity acceptance, unlike identified rapidity
yields; simple window rescaling does not supply a validated midrapidity
correction. Use the [inventory acceptance and correlated-ratio guidance](reference/analysis-inputs.md#optional-feed-down-inventory).
`analysis/feeddown_census.py` is another diagnostic with a different report.

## What to rerun after a change

| Change | Work to revisit |
|---|---|
| Centrality estimator/bins, supported reduction window, bootstrap count | Reaggregate and validate using matching retained statistics |
| Extraction ET window or extracted statistic content | Re-extract into a clearly identified output set, then aggregate/validate |
| Cache cuts, fingerprint, species/conventions, or quantities outside retained statistics | Rebuild/re-publish from appropriate raw data; if raw were deleted, simulation may be required |
| Design physics, initial conditions, EOS/coefficient/particle inputs | A separate, consistently initialized simulation/provenance set |

Diagnostics outside this chain are listed in the
[analysis directory guide](../models/analysis/README.md).
