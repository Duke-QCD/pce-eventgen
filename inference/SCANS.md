# Small robustness and leave-one-out scans

This walkthrough checks the scan runners, saved results, plotting and resume
behavior using 16 synthetic designs. It needs only the
[inference environment](README.md#environment-and-paths). Allow a few minutes;
no simulation binaries, cluster jobs or external matrix downloads are needed.

All three plotting commands below use the dissertation theme by default:
serif text, paper parameter labels, amber/blue intervals and consistent marker
colours. Coverage labels include all sampled parameters, and relaxation times
use the same equilibrium-time notation as the main fit figures. The shared
settings are in `plot_style.py`; no styling flag is needed.

The plotting commands read existing scan results and overwrite only their PDFs;
they do not retrain, resample or change saved scan results. You can rerun just
these commands to restyle an older scan. Fit options such as
`--corner-font-scale` apply to the main fitter, not these aggregate figures.

| Scan | Question | What changes between cases? |
|---|---|---|
| Robustness / observable jackknife | Which measurements influence the fitted parameters? | Remove one measurement group, retrain and fit the remaining real measurements |
| Leave-one-out (LOO) recovery | Can the inference procedure recover a known design point? | Hold out one model design, train on the others, generate noisy pseudo-data at the held-out truth and infer its parameters |
| LOO showcase | What do individual recovery posteriors look like? | Select three distinct injected truths and save their posterior samples for overlays |

These runners use the AuAu200 primary analysis: all 14 active parameters free,
no feed-down, and the primary exclusions `dN_dy_Lambda,dN_dy_proton,mean_pT_proton`.
They do not accept `--system`, `--drop-specs` or `--feeddown`: those policies are
built into these scans.

The toy model is deliberately unrelated to the experimental data. These short
chains and three/four recovery cases check execution, not robustness of a
physics conclusion, posterior convergence or calibration. The first
[single-fit tutorial](GETTING_STARTED.md) explains the common input/output files.

## 1. Prepare inputs in a fresh directory

Start in `inference/` after setting up its environment. Keep this Bash shell
open throughout; later commands use the variables set here:

```bash
source .venv/bin/activate
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
SCAN_WALKTHROUGH=$(mktemp -d "$PWD/scan_student_walkthrough.XXXXXX")
printf 'Scan outputs: %s\n' "$SCAN_WALKTHROUGH"
python make_demo_inputs.py --out-dir "$SCAN_WALKTHROUGH/inputs"
SCAN_MATRIX="$SCAN_WALKTHROUGH/inputs/model_matrix_demo.npz"
SCAN_DESIGN="$SCAN_WALKTHROUGH/inputs/design.dat"
python check_inputs.py --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN"
```

Expect 16 usable designs, 14 emulator inputs and 70 matched observables, with
the same exclusions and feed-down policy as the scans. The generated matrix and
metadata are marked synthetic. Always pass the matching design file.

Each execution of this block creates a new directory, preserving earlier
results. Its `scan_student_walkthrough.*` name is ignored by Git. To resume in a
later shell, reactivate the environment, repeat the exports, set
`SCAN_WALKTHROUGH` to the printed directory and reset `SCAN_MATRIX`/`SCAN_DESIGN`.
Do not run the input generator again on an existing destination.

## 2. Run the robustness smoke scan

```bash
python robustness_scan.py --smoke \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-root "$SCAN_WALKTHROUGH/robustness" \
  --nsteps 40 --nburn 20 --nwalkers 32 --nsplits 2
python plot_robustness_forest.py --summary "$SCAN_WALKTHROUGH/robustness/summary.csv"
```

This attempts three variants:

| Variant | Expected execution status | Meaning |
|---|---|---|
| `baseline` | `ok` | Primary observable selection |
| `drop_dN_dy_Xi` | `ok` | Primary selection with Xi yields additionally excluded |
| `BROKEN_drop_everything` | `expected_failure` | Intentionally empty likelihood; tests failure handling |

The last case must print `EXPECTED failure` and must **not** produce a posterior.
If both actual fits finish, the whole smoke command exits zero. Unexpected
failures are recorded as `FAIL: ...`, print a traceback and make the command
exit nonzero after the remaining variants have been attempted.

`--nsteps 40` means 40 post-burn steps per walker, after 20 burn-in steps. Each
completed posterior has shape `(1280, 14)`. The runner suppresses fit figures;
the separate forest command creates `robustness/robustness_forest.pdf`.

The top-level `summary.csv` holds per-variant 16th/50th/84th percentiles for
`gamma_s_0`, `gamma_l_0`, `tau_l`, `tau_s`, `eta_min`, plus the ordering
probabilities `P(gs<gl)` and `P(ts>tl)`. Each completed variant subdirectory has
`auau_posterior.npz`, `auau_run.json` and `auau_diagnostics.json`.
`ok` describes successful execution; inspect convergence separately:

```bash
python - "$SCAN_WALKTHROUGH/robustness" <<'PY'
import csv
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
with (root / "summary.csv").open() as stream:
    for row in csv.DictReader(stream):
        print(row["variant"], "execution:", row["status"])
        if row["status"] in ("ok", "resumed"):
            diagnostics = json.loads((root / row["variant"] / "auau_diagnostics.json").read_text())
            print("  convergence:", diagnostics["verdict"])
PY
```

Both short fits should report **NOT converged**. The forest still draws their
summary values; its bars are not an endorsement of convergence. It omits failed
and expected-failure rows and requires a successful baseline.
The forest displays the full prior range for each parameter, a blue baseline
68% reference envelope, and hierarchy probabilities over the full zero-to-one
range. Its height and group legend adapt to the successful variants, so a smoke
scan does not show empty groups. Marker colours distinguish yields, mean-pT
and flow exclusions; they are not credible-level colours in this figure.

The omitted measurement group leaves the likelihood, but its matrix columns
may still be needed for weighting other observables. For example, a mean-pT
bin uses the species yield for particle-count weights. Do not delete matrix
columns to implement a jackknife.

## 3. Exercise every jackknife variant on the small grid

For a small test of the complete variant list, omit `--smoke` while keeping the
explicit short-chain settings and 16-row inputs:

```bash
python robustness_scan.py \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-root "$SCAN_WALKTHROUGH/robustness_full" \
  --nsteps 40 --nburn 20 --nwalkers 32 --nsplits 2
python plot_robustness_forest.py --summary "$SCAN_WALKTHROUGH/robustness_full/summary.csv"
```

Expect 11 successful **executions**: baseline plus one exclusion for each of
the ten included observable groups. There is no intentional failure in the full
list. The separately named directory keeps it distinct from the smoke scan.
This is still a small software exercise, not a scientific robustness study.

## 4. Run three leave-one-out cases

```bash
python loo_closure.py --smoke --K 3 \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/loo" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_loo_closure.py --out-dir "$SCAN_WALKTHROUGH/loo"
```

`--K` is the number of held-out **cases**, not the number of training designs.
Each case here trains on the other 15 usable rows. The default selection seed
12345 chooses usable rows 10, 11 and 12 from this 16-row input; progress lines
can arrive in a different order because two workers run concurrently.

Before the cases run, a two-fold calibration estimates per-observable emulator
variance-inflation factors on the full valid grid. These factors are shared
across held-out cases. This preserves the existing protocol; it is not a fully
nested validation of the inflation calibration. Each case retrains its own
emulator without the held-out row and draws pseudo-data around that row's model
values using experimental plus emulator uncertainties. It then samples the
14-parameter posterior against those pseudo-data.

Expected files in `loo/`:

| File | Contents |
|---|---|
| `loo_0010.npz`, `loo_0011.npz`, `loo_0012.npz` | Per-case truth, ranks, interval-coverage flags, posterior quantiles, split-Rhat, original design ID, parameter names/ranges and configuration |
| `loo_rank_hist.pdf` | Four headline-parameter rank histograms; reference bands are pointwise 99% binomial intervals under uniform ranks |
| `loo_coverage.pdf` | Per-parameter 68% and 95% empirical coverage from ranks, with 95% Wilson intervals |

The aggregate LOO files save **summaries**, not posterior draws. The saved
`cov68` and `cov90` flags refer to 68%/90% intervals; the plot instead computes
68%/95% coverage from the stored ranks. A rank is the fraction of posterior
draws below the injected true parameter. With many suitable, well-mixed cases,
approximately uniform ranks are a useful calibration check. Three cases cannot
establish that. A result of “0 of 3” or “3 of 3” has substantial uncertainty;
the plot retains that uncertainty and displays the full zero-to-one range.

`plot_loo_closure.py` also prints split-Rhat values. These are the recovery
runner's stored split-Rhat, not the main fitter's rank-normalized Rhat/ESS report.
Expect large Rhat for these short chains. `ok` in worker output and a zero
batch exit code mean execution succeeded, not that a recovery fit converged.

## 5. Check resume, then extend the case set

Repeat the two runners with the same inputs and settings:

```bash
python robustness_scan.py --smoke \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-root "$SCAN_WALKTHROUGH/robustness" \
  --nsteps 40 --nburn 20 --nwalkers 32 --nsplits 2
python loo_closure.py --smoke --K 3 \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/loo" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
```

Expect `resumed` for the two robustness posteriors and `cached` for all three
LOO cases. Their saved results are reused rather than extended or resampled.
The robustness harness still checks its intentional empty-observable case.
LOO still loads inputs and recomputes its shared inflation calibration before
checking case caches, so a fully cached run is not free on a large grid.

To add a fourth held-out case, keep the selection seed and other settings:

```bash
python loo_closure.py --smoke --K 4 \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/loo" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_loo_closure.py --out-dir "$SCAN_WALKTHROUGH/loo"
```

This should reuse three cases and create one more. Explicit `--K 4` overrides
the smoke default of three. The plot now reads all four files. Reusing a
directory with a **smaller** K does not remove earlier cases; changing the
selection seed can add a different set. Plots read every matching result file
in the directory. Use a fresh directory for a different experiment.

Changed inputs, implementation or fit settings cannot silently reuse a cache.
For example, changing `--nsteps` to 44 in these directories will return nonzero
with a provenance mismatch; existing posterior/case files are preserved.
Use a new output directory for the new settings. `--refit` explicitly replaces
the currently requested variants/cases with fresh runs: it does not resume a
chain, remove unselected case files, or repair a mixed directory automatically.
A failed robustness invocation rewrites `summary.csv` with its failure statuses
even when earlier posterior files remain, so inspect the latest summary/log.

## 6. Save individual recovery posteriors

Use a separate directory for the showcase. It has no smoke preset, so provide
all the reduced settings explicitly:

```bash
python loo_showcase.py \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/showcase" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_showcase_overlay.py --out-dir "$SCAN_WALKTHROUGH/showcase"
```

The runner chooses three distinct rows nearest the `gamma_s_0`
targets 0.15, 0.50 and 0.75. For this toy input their actual truths are about
0.139, 0.522 and 0.710. The overlay uses the **actual** truths; a small or narrow
input grid need not reach every target. Each `showcase_*.npz` stores `samp`
with shape `(1280, 14)`, the injected parameters, split-Rhat, ranges, IDs and
configuration. `loo_showcase_overlay.pdf` plots four fugacity/relaxation-time
parameters across these three cases.
The injected-truth legend sits above the panels, and column headings give the
actual injected `gamma_s_0` values. Partial directories containing one or two
showcase cases can also be plotted.

Showcase fits always rerun their selected cases; they do not implement the
aggregate runner's checked resume. Changing inputs can change which rows are
selected, so use a new directory rather than leaving earlier showcase files
alongside a different experiment. Keep aggregate and showcase results separate.

## Defaults and production use

Run `python SCRIPT.py --help` or search the
[flag index](../docs/reference/cli-inference.md) for accepted options.

| Option / behavior | Robustness | Aggregate LOO | Showcase LOO |
|---|---|---|---|
| `--matrix`, `--design`, `--data-dir` | Explicit input paths | Explicit input paths | Explicit input paths |
| Output option | `--out-root` | `--out-dir` (alias `--outdir`) | `--out-dir` |
| Post-burn / burn-in defaults | 15000 / 5000; smoke 300 / 100 | 7000 / 3000; smoke 400 / 200 | 8000 / 4000 |
| `--nwalkers` | 128, including smoke | 128, including smoke | 128 |
| Calibration option | `--nsplits` (8; smoke 2) | `--lam-nsplits` (8; smoke 2) | `--lam-nsplits` (8) |
| Case count | Baseline + ten exclusions; smoke two fits + intentional failure | `--K` (100; smoke 3), capped at usable rows | Three distinct target-nearest truths |
| Processes | Sequential variants | `--workers` (8; smoke 3) | `--workers` (3) |
| `--seed` | MCMC seed; closure keeps its fixed seed | Held-out-row selection only | No selection-seed flag; targets are deterministic |
| Existing results | Checked posterior reuse; `--refit` replaces requested variants | Checked case reuse; `--refit` replaces requested cases | Selected showcase files overwritten |

`--smoke` changes some defaults; it does **not** subset a matrix or reduce the
walker count. These examples explicitly reduce the training grid, chain lengths
and walkers. On real inputs, training still uses every usable design, so even
K=1 or a short chain can require substantial GP work. Choose workers to fit
your CPU and memory allocation; each worker trains an emulator. These commands
run locally and do not submit cluster jobs.

LOO filenames use the stacked usable-row index `k`; each new file additionally
stores `design_index`, the original zero-based full-design ID. With real gapped
or filtered grids they differ. Keep the full design table that generated the
events; a 16-row matrix with IDs up to 487 still needs those original rows in
its design table. Per-case pseudo-data and MCMC seeds retain their fixed source
bases plus `k`, so preserve matrix row order for reproducibility. Changing the
LOO selection seed changes selected rows, not the noise drawn for the same row.

For a scientific study, select real campaign inputs, validate their emulator,
use adequate chains and enough held-out cases, and assess convergence before
interpreting shifts, ranks or coverage. Recovery cases share training data, so
simple binomial reference intervals are descriptive rather than a guarantee of
independent-case calibration. These tiny runs demonstrate that the software
works. They do not establish a physics result.
