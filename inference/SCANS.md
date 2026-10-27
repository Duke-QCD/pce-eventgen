# Small robustness and leave-one-out scans

This walkthrough checks the scan runners, saved results, plotting and resume behavior using 16 synthetic designs. It needs only the [inference environment](README.md#environment-and-paths). Allow a few minutes; no simulation binaries, cluster jobs or external matrix downloads are needed.

Aggregate plots use shared styling from `plot_style.py`. They read saved results and overwrite PDFs without training/sampling; rerun them to restyle old results. Main-fit corner options do not apply.

| Scan | Question | What changes between cases? |
|---|---|---|
| Robustness / observable jackknife | Which measurements influence the fitted parameters? | Remove one measurement group, retrain and fit the remaining real measurements |
| Leave-one-out (LOO) recovery | Can the inference procedure recover a known design point? | Hold out one model design, train on the others, generate noisy pseudo-data at the held-out truth and infer its parameters |
| LOO showcase | What do individual recovery posteriors look like? | Select three distinct injected truths and save their posterior samples for overlays |

The robustness runner supports AuAu200 and OO5360. It defaults to the system's primary exclusions and accepts `--system`, `--drop-specs` and `--oo-yield-proxies`; feed-down remains off. O-O proxy variants reassemble the selected joint covariance, including when the K0S anchor measurement is omitted. The primary O-O scan has nine group exclusions; enabling proxies and retaining K0S mean pT gives thirteen.

The aggregate and showcase LOO runners still use AuAu200 only: all 14 active parameters free, no feed-down, and primary exclusions `dN_dy_Lambda,dN_dy_proton,mean_pT_proton`. Do not pass an O-O matrix to these AuAu-only runners.

Inspect O-O variants without training or writes:

```bash
python robustness_scan.py --system OO5360 --dry-run
python robustness_scan.py --system OO5360 --oo-yield-proxies --drop-specs '' --dry-run
```

Run O-O primary or all-optional observable-group scans in separate destinations:

Supply your own matrix, design and [O-O measurements](data/OO5360.md); the study data are not bundled.

```bash
python robustness_scan.py --system OO5360 --matrix "$OO_MATRIX" --design "$OO_DESIGN" \
  --data-dir "$OO_DATA" --out-root scan_robust_OO5360_primary
python robustness_scan.py --system OO5360 --matrix "$OO_MATRIX" --design "$OO_DESIGN" \
  --data-dir "$OO_DATA" --oo-yield-proxies --drop-specs '' --out-root scan_robust_OO5360_optional
python plot_robustness_forest.py --summary scan_robust_OO5360_primary/summary.csv
```

Each retained group is removed once; excluding a measurement does not delete model columns needed for interpolation or mean-pT weighting. `--only-variants` selects named cases; concurrent invocations must use distinct output roots. Summary CSVs include convergence, maximum rank-Rhat and minimum bulk/tail ESS. Forest plots use open markers for fits failing convergence and refuse to silently omit unknown variant labels. Existing summary files without diagnostics remain readable.

The robustness CLI is sequential; it has no `--workers` option. Multiple external processes must use separate output roots because each invocation rewrites its own `summary.csv`. `--only-variants` also filters which cases enter that invocation's summary; it is not an append operation. A forest requires a successful baseline row, so a summary made from exclusion-only cases needs aggregation with its matching baseline before plotting.

These short toy runs test software behavior, not scientific robustness, convergence or calibration. The [single-fit tutorial](GETTING_STARTED.md) explains common files.

## 1. Prepare inputs in a fresh directory

Start in `inference/` after setting up its environment. Keep this Bash shell open throughout; later commands use the variables set here:

```bash
source .venv/bin/activate
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
SCAN_WALKTHROUGH=$(mktemp -d "$PWD/scan_walkthrough.XXXXXX")
printf 'Scan outputs: %s\n' "$SCAN_WALKTHROUGH"
python make_demo_inputs.py --out-dir "$SCAN_WALKTHROUGH/inputs"
SCAN_MATRIX="$SCAN_WALKTHROUGH/inputs/model_matrix_demo.npz"
SCAN_DESIGN="$SCAN_WALKTHROUGH/inputs/design.dat"
python check_inputs.py --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN"
```

Expect 16 usable designs, 14 emulator inputs and 70 matched observables, with the same exclusions and feed-down policy as the scans. The generated matrix and metadata are marked synthetic. Always pass the matching design file.

Each execution creates a new ignored directory. To resume, reactivate the environment, restore the printed path and input variables; do not regenerate into an existing destination.

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

The intentional failure must print `EXPECTED failure` without a posterior. If the actual fits succeed, the batch exits zero. Unexpected failures print tracebacks, appear as `FAIL: ...`, and return nonzero after remaining variants are attempted.

`--nsteps 40` means 40 post-burn steps per walker, after 20 burn-in steps. Each completed posterior has shape `(1280, 14)`. The runner suppresses fit figures; the separate forest command creates `robustness/robustness_forest.pdf`.

The top-level `summary.csv` holds per-variant 16th/50th/84th percentiles for `gamma_s_0`, `gamma_l_0`, `tau_l`, `tau_s`, `eta_min`, plus the ordering probabilities `P(gs<gl)` and `P(ts>tl)`. Each completed variant subdirectory has `auau_posterior.npz`, `auau_run.json` and `auau_diagnostics.json`. `ok` describes successful execution; inspect convergence separately:

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

Expect **NOT converged** for both short fits. The forest still plots their summaries, omitting failed rows and requiring a baseline. It shows full prior ranges, a blue baseline 68% envelope and hierarchy probabilities over 0–1; marker colours identify observable groups, not credible levels.

Here “failed rows” means execution failures. Completed fits that fail the scientific convergence screen remain in the figure with open markers. A finite interval or a filled marker does not by itself establish robustness. The summary's screen uses rank-Rhat and tail ESS; inspect the full per-parameter diagnostic files and fit logs before interpreting shifts.

The omitted measurement group leaves the likelihood, but its matrix columns may still be needed for weighting other observables. For example, a mean-pT bin uses the species yield for particle-count weights. Do not delete matrix columns to implement a jackknife.

## 3. Exercise every jackknife variant on the small grid

For a small test of the complete variant list, omit `--smoke` while keeping the explicit short-chain settings and 16-row inputs:

```bash
python robustness_scan.py \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-root "$SCAN_WALKTHROUGH/robustness_full" \
  --nsteps 40 --nburn 20 --nwalkers 32 --nsplits 2
python plot_robustness_forest.py --summary "$SCAN_WALKTHROUGH/robustness_full/summary.csv"
```

Expect 11 executions: baseline plus ten group exclusions, without the smoke test's intentional failure. The separate directory preserves the smoke results.

## 4. Run three leave-one-out cases

```bash
python loo_closure.py --smoke --K 3 \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/loo" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_loo_closure.py --out-dir "$SCAN_WALKTHROUGH/loo"
```

`--K` is the number of held-out **cases**, not the number of training designs. Each case here trains on the other 15 usable rows. The default selection seed 12345 chooses usable rows 10, 11 and 12 from this 16-row input; progress lines can arrive in a different order because two workers run concurrently.

Before the cases run, a two-fold calibration estimates per-observable emulator variance-inflation factors on the full valid grid. These factors are shared across held-out cases. This preserves the existing protocol; it is not a fully nested validation of the inflation calibration. Each case retrains its own emulator without the held-out row and draws pseudo-data around that row's model values using experimental plus emulator uncertainties. It then samples the 14-parameter posterior against those pseudo-data.

Expected files in `loo/`:

| File | Contents |
|---|---|
| `loo_0010.npz`, `loo_0011.npz`, `loo_0012.npz` | Per-case truth, ranks, interval-coverage flags, posterior quantiles, split-Rhat, original design ID, parameter names/ranges and configuration |
| `loo_rank_hist.pdf` | Four headline-parameter rank histograms; reference bands are pointwise 99% binomial intervals under uniform ranks |
| `loo_coverage.pdf` | Per-parameter 68% and 95% empirical coverage from ranks, with 95% Wilson intervals |

Aggregate files store **summaries**, not posterior draws. Stored `cov68`/`cov90` flags use 68%/90% intervals; plots compute 68%/95% coverage from ranks (fractions of posterior draws below truth). Uniform ranks can diagnose calibration with many suitable cases; three cases cannot. The plot displays 0–1 coverage and interval uncertainty.

The plot reports stored split-Rhat, distinct from the main fitter's rank-normalized Rhat/ESS. Expect large values here; worker `ok` and batch exit zero indicate execution only.

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

Expect robustness `resumed` and three LOO `cached` cases: saved results are reused, not chains extended. The intentional failure is checked again. LOO recomputes shared inflation calibration even when all cases are cached.

To add a fourth held-out case, keep the selection seed and other settings:

```bash
python loo_closure.py --smoke --K 4 \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/loo" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_loo_closure.py --out-dir "$SCAN_WALKTHROUGH/loo"
```

Expect three reused cases and one new case; explicit `--K 4` overrides smoke defaults. Plots read all matching files. Smaller K does not delete earlier cases, and a different seed can add others; use fresh directories for different experiments.

Changed inputs/code/settings refuse cache reuse (try `--nsteps 44`), preserving prior posterior/case files. Use a new directory or deliberate `--refit`, which overwrites requested cases rather than extending chains or cleaning mixed directories. Failed robustness runs still rewrite `summary.csv`; inspect the latest summary/log.

## 6. Save individual recovery posteriors

Use a separate directory for the showcase. It has no smoke preset, so provide all the reduced settings explicitly:

```bash
python loo_showcase.py \
  --matrix "$SCAN_MATRIX" --design "$SCAN_DESIGN" \
  --out-dir "$SCAN_WALKTHROUGH/showcase" \
  --nsteps 40 --nburn 20 --nwalkers 32 --lam-nsplits 2 --workers 2
python plot_showcase_overlay.py --out-dir "$SCAN_WALKTHROUGH/showcase"
```

Showcase selects distinct rows nearest `gamma_s_0` targets 0.15/0.50/0.75; toy truths are about 0.139/0.522/0.710. Plots use actual truths. Each `showcase_*.npz` stores `(1280, 14)` posterior samples, truth, split-Rhat, ranges, IDs and configuration. The overlay shows four fugacity/relaxation parameters and supports partially completed directories.

Showcase overwrites selected cases rather than implementing aggregate resume. Keep it separate from aggregate results and use a fresh directory when inputs change.

## Defaults and production use

Run `python SCRIPT.py --help` or search the [flag index](../docs/reference/cli-inference.md) for accepted options.

| Option / behavior | Robustness | Aggregate LOO | Showcase LOO |
|---|---|---|---|
| `--matrix`, `--design`, `--data-dir` | Explicit input paths | Explicit input paths | Explicit input paths |
| Output option | `--out-root` | `--out-dir` (alias `--outdir`) | `--out-dir` |
| Post-burn / burn-in defaults | 15000 / 5000; smoke 300 / 100 | 7000 / 3000; smoke 400 / 200 | 8000 / 4000 |
| `--nwalkers` | 128, including smoke | 128, including smoke | 128 |
| Calibration option | `--nsplits` (8; smoke 2) | `--lam-nsplits` (8; smoke 2) | `--lam-nsplits` (8) |
| Case count | Baseline + ten AuAu exclusions, nine primary O-O exclusions or thirteen all-optional O-O exclusions; smoke two fits + intentional failure | `--K` (100; smoke 3), capped at usable rows | Three distinct target-nearest truths |
| Processes | Sequential variants | `--workers` (8; smoke 3) | `--workers` (3) |
| `--seed` | MCMC seed; closure keeps its fixed seed | Held-out-row selection only | No selection-seed flag; targets are deterministic |
| Existing results | Checked posterior reuse; `--refit` replaces requested variants | Checked case reuse; `--refit` replaces requested cases | Selected showcase files overwritten |

`--smoke` does not subset the matrix or reduce walkers; these examples reduce both explicitly. Real-input GP training still uses every usable design. Match workers to local CPU/memory; no scheduler jobs are submitted.

LOO filenames use the stacked usable-row index `k`; each new file additionally stores `design_index`, the original zero-based full-design ID. With real gapped or filtered grids they differ. Keep the full design table that generated the events; a 16-row matrix with IDs up to 487 still needs those original rows in its design table. Per-case pseudo-data and MCMC seeds retain their fixed source bases plus `k`, so preserve matrix row order for reproducibility. Changing the LOO selection seed changes selected rows, not the noise drawn for the same row.

Scientific use needs real inputs, validated emulators, adequate chains/cases and convergence assessment. Shared training data correlate recovery cases, so binomial reference intervals are descriptive rather than an independence guarantee.
