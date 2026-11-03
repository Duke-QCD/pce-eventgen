# Running inference with your own inputs

The [small walkthrough](GETTING_STARTED.md) tests installation with a generated model grid. This guide moves from that example to a supplied model matrix and measurement set. No completed campaign, private matrix or analyst's result directory is required.

Commands below run in `inference/`, with the [inference environment](README.md#environment-and-paths) active. They use the supported system adapters: `AuAu200` or `OO5360`. Selecting a system selects its observable definitions and primary exclusions; it does not establish that a measurement set is suitable for a scientific analysis.

## Select and check inputs

Set these paths to the inputs for your analysis:

```bash
FIT_SYSTEM=AuAu200
FIT_MATRIX=/absolute/path/to/model_matrix.npz
FIT_DESIGN=/absolute/path/to/original/design.dat
FIT_DATA=/absolute/path/to/measurements
FIT_SHEAR=temperature-dependent  # set to flat for eta_low = eta_high = 0

python check_inputs.py --system "$FIT_SYSTEM" \
  --shear-mode "$FIT_SHEAR" \
  --matrix "$FIT_MATRIX" --design "$FIT_DESIGN" --data-dir "$FIT_DATA"
```

The matrix needs its matching same-stem `.meta.json`. Pair it with the exact original design table; `design_index` selects zero-based design rows even when matrix rows are missing or reordered. Check system/profile compatibility, matched-observable counts, usable designs and skipped bins. The [matrix contract](INPUTS.md) defines labels, errors and metadata.

`--data-dir` changes the location of files used by the selected adapter. It does not infer a new observable schema from arbitrary tables. Preserve the expected filenames and any relative sibling-directory layout. The [measurement reference](data/README.md) describes the bundled AuAu200 tables and links the schema for user-supplied O-O data. A new collision system or observable definition needs a reviewed adapter in `inference.py`.

Matched counts depend on the supplied matrix and data; there is no required campaign row count. For multiplicity-space observables, only measurement points inside every retained design's covered range enter. Review those cuts rather than extrapolating or deleting matrix columns needed for weighting.

Keep your own data, configurations and results outside the tracked reference-data directories. An external work directory or ignored `studies/<study>/` is suitable. Supply its paths through the CLI. The default staged matrices, `plots_*` results and `scan_*` results are also ignored by Git.

## Run a fit with a preserved log

Create a fresh output root, retain its printed path, and keep the terminal/session alive for the run:

```bash
FIT_RUNS=$(mktemp -d "$PWD/plots_production.XXXXXX")
printf 'Inference outputs: %s\n' "$FIT_RUNS"
set -o pipefail

python -u inference.py --system "$FIT_SYSTEM" --parameter-mode all \
  --shear-mode "$FIT_SHEAR" \
  --matrix "$FIT_MATRIX" --design "$FIT_DESIGN" --data-dir "$FIT_DATA" \
  --out-dir "$FIT_RUNS/primary" \
  --nwalkers 128 --nburn 5000 --nsteps 40000 --nsplits 8 --seed 12345 \
  --corner-font-scale 1.8 2>&1 | tee "$FIT_RUNS/primary.log"
```

This applies the system's primary measurement exclusions and leaves feed-down off. Temperature-dependent shear samples all 14 active parameters; flat shear samples 12, conditioning both slopes at zero while `eta_min` supplies the constant eta/s. The toggle works for AuAu200 and OO5360, including `run_oo_suite.py`. Emulator training retains the full design in both modes. `--nsteps` counts retained steps per walker, after `--nburn`. A denser corner can use a smaller font scale; this is a rendering choice, not a change to the samples. `pipefail` retains a fitter failure as the pipeline's exit status.

Full-grid training and sampling may be substantial. The phases are eight-fold emulator closure, full-grid training, MCMC, then plotting. The stock sampler reports after sampling rather than printing per-step progress; `-u` promptly writes available phase messages to the log. Shorter chains or `--no-plots` do not reduce the training-design count.

Use a separate output directory for every changed selection or sampler configuration. `--drop-specs NAME,...` replaces the default exclusions; `--drop-specs ''` keeps all currently enabled groups. Dataset-specific optional groups need their own enable flag where applicable. Feed-down, parameter slices and optional measurement models must be selected deliberately and recorded with the run.

## Check results before interpreting intervals

The output prefix is `auau` or `oo`, according to the selected system:

| File | Contents |
|---|---|
| `<prefix>_posterior.npz` | Flattened retained samples, parameter names/ranges, fixed values and input/settings provenance |
| `<prefix>_run.json` | Absolute input paths, content hashes, library versions, retained design IDs and settings |
| `<prefix>_diagnostics.json` | Convergence verdict and per-parameter rank-normalized split-Rhat, bulk ESS and tail ESS |

Read the verdict and individual parameters, then inspect traces, corner structure and prior boundaries. The configured screen fails non-finite rank-Rhat/tail ESS, rank-Rhat above 1.05, or tail ESS below 400. Rhat above 1.01 is a softer warning. Acceptance is printed in the fit log. Walkers in the ensemble interact; raw draw count is not effective independent sample count.

A successful process exit or the default sampling budget does not guarantee convergence. Treat failed directions as unresolved when comparing medians and intervals. Compare runs with the same parameter definitions and record all changes to measurement selection, prior ranges and uncertainty models. A median shift divided by baseline posterior standard deviation describes sensitivity; it is not a significance test.

The main CLI does not checkpoint or continue interrupted MCMC. Rerunning with a larger `--nsteps` trains and samples again. Preserve complete inputs and outputs. The flattened posterior is sufficient for corner regeneration, but the CLI does not save the fitted emulator or original pre-burn chain/RNG state for continuation.

## MAP observable plots

`plot_map_observables.py` accepts saved single-system and joint calibrations. It infers the run type and system from the posterior filename; `--system AuAu200` or `--system OO5360` selects a single-system posterior when a directory contains several. It verifies saved inputs and parameter settings, retrains the emulator with its recorded uncertainty calibration, and estimates a MAP by screening posterior draws and refining the best candidates with bounded optimization. It runs no MCMC and generates no new collision simulations.

```bash
# Single-system fit (Au-Au or O-O is detected):
python plot_map_observables.py --run-dir plots_auau_primary

# Joint fit: one shared MAP vector, with a figure set for each system:
python plot_map_observables.py --run-dir plots_joint_auau_oo_flat
```

The objective is the posterior density in physical parameter coordinates, with the current uniform box prior, the complete Gaussian likelihood normalization and calibrated emulator predictive variance. It contains no logit-coordinate Jacobian. Fixed parameters, including zero shear slopes in flat mode, remain fixed. A vector of marginal posterior medians is not used as the MAP. The finite search produces a numerical estimate; it does not certify a global optimum.

The default search scores up to 1,024 saved posterior draws plus their componentwise median, then refines up to four distinct high-density starts. `--max-samples`, `--starts`, `--maxiter`, `--batch-size` and `--seed` control this work. `map_result.json` records all optimizer exits, the best candidate's convergence status and proximity to prior boundaries. Increase the search budget and compare solutions if convergence is poor or the posterior is multimodal. The source posterior's MCMC convergence still needs separate review.

Results go into a new `map/` directory under the saved run, or a new directory selected by `--out-dir`. Existing output directories are refused. `map_result.json` preserves the full MAP vector, fixed values, input/implementation provenance, emulator reconstruction and each system's likelihood contribution. Each system receives `<prefix>_MAP_observables.npz` containing signed model means, predictive standard deviations, measurements, labels and its projected parameter vector. Figures are `<prefix>_MAP_yields` and `<prefix>_MAP_ptflow`, omitting empty groups. PDF and PNG are written by default; use `--format pdf` or `--format png` for one format.

The curve is the emulator prediction at the MAP. The shaded band shows emulator uncertainty at that fixed point, not posterior parameter uncertainty. Data error bars retain the fit's configured floors and assumed uncertainties. Flow predictions and uncertainty bounds use the same clipped-root display convention as existing summary figures; saved arrays retain signed cumulants.

New fits save GP settings and variance inflation. Older single-system fits with input provenance and a recorded closure-fold count are supported by recomputing closure under current production emulator settings; the script explicitly warns and records that reconstruction. This is an estimate under the reconstructed emulator, not verification that the original numerical likelihood was reproduced. Fits lacking input provenance are rejected. Single-system `--matrix`, `--design`, and `--data-dir`, or joint `--config`, can point to relocated identical inputs; changed data or parameter policies are rejected.

## Figures from the main fitter

With plots enabled, the fitter produces these families where the selected groups are nonempty:

The prior/posterior observable grids and parameter corners below are the dissertation-style set, generated automatically for both single-system and joint calibrations. Joint fits write observable grids in each system's subdirectory and shared corners at the run root. `--no-plots` disables automatic rendering; it does not select a different plotting style.

| Files | Contents |
|---|---|
| `<prefix>_closure.pdf`, `<prefix>_PC_spectrum.pdf` | Raw emulator prediction closure and fitted PCA spectrum |
| `<prefix>_PredictedDesign.pdf` | Selected model designs versus measurements |
| `<prefix>_MCMCSamples.pdf` | Sampler traces, with burn-in marked |
| `<prefix>_{Posterior,Prior}Summary_{yields,ptflow}.pdf` | Four prior/posterior observable summaries |
| `<prefix>_posterior_full.pdf` and `_posterior_<group>.pdf` | Full and parameter-group corners |

Braces denote separate files. Predictive bands vary emulator means across parameter draws; they do not include emulator uncertainty draws. Flow is displayed as `sqrt(max(c_n, 0))` while inference uses signed cumulants. The normal rendering path reuses the trained emulator and samples.

For later rendering, predictive summaries verify the recorded inputs and retrain one mean-only emulator. Corners read saved samples without training or MCMC:

```bash
python make_flow_plots.py --system "$FIT_SYSTEM" --run-dir "$FIT_RUNS/primary"
python regen_corners.py --system "$FIT_SYSTEM" \
  --run-dir "$FIT_RUNS/primary" --font-scale 1.8
```

Recorded optional selections and fixed parameters are restored automatically. Replacement input paths must identify identical contents. These commands overwrite their corresponding figures, not the posterior. `make_flow_plots.py --talk` writes larger-label PNGs; its prior display uses one robust 99% band instead of the ordinary nested 68%/95% bands.

MAP and prior/posterior observable grids include every group selected by the saved fit. For O-O, a primary fit and an all-optional fit have different selections: K0S mean pT requires clearing its exclusion, and the correlated pi/K/p yield proxies additionally require `--oo-yield-proxies` when fitting. Point the plotting commands at the all-optional run to show those channels; plotting a primary posterior does not add them. Proxy panels retain their assumed-proxy labels.

Plot replay checks the measurement, matrix, design, evidence and covariance hashes. Updated implementation files do not by themselves invalidate a plot; proxy assembly also reproduces and checks the complete saved proxy outputs. Cache reuse retains the stricter check on implementation hashes as well as physical inputs.

## Additional emulator validation

Run validation against the same input and observable selection in a separate destination:

```bash
python -u emulator_validation.py --system "$FIT_SYSTEM" \
  --matrix "$FIT_MATRIX" --design "$FIT_DESIGN" --data-dir "$FIT_DATA" \
  --nsplits 8 --out-dir "$FIT_RUNS/validation"
```

Mirror the fit's `--drop-specs` and `--oo-yield-proxies` options when used. The latter is an O-O-only opt-in defined in the [input schema](data/OO5360.md#optional-correlated-proxy-extension). The standalone validator uses feed-down off; it does not replay a feed-down-corrected fit.

This writes `<prefix>_closure_parity_z.pdf`, `_closure_holdout.pdf`, `_closure_metrics.pdf` and `_pca_spectrum.pdf`. The lowercase PCA filename differs from the main fit's `_PC_spectrum.pdf`. Validation trains its own folds. Its `validation_kfold.npz` cache skips repeated fold training when provenance/settings match; rendering the PCA spectrum still trains one full-grid emulator. `--refit` explicitly replaces that cache. `--model-grid` validates raw model columns, a different observable selection from the likelihood-matched default.

Closure plots show raw predictive standard deviations. The fitter subsequently scales each observable's emulator standard deviation by `max(1, std(held-out standardized residuals))`. Measuring adjusted coverage on those same folds is descriptive, not independent validation of the inflation factors. Emulator prediction closure also does not establish parameter recovery or real-data MCMC convergence.

## Optional robustness and recovery studies

The [scan guide](SCANS.md) documents observable-group exclusions, aggregate leave-one-out recovery and selected injected-truth overlays. Robustness supports both system adapters; the recovery runners currently support AuAu200. These are optional analysis tools, not requirements for the installation walkthrough.

Each robustness exclusion reassembles the remaining data/covariance and repeats training and sampling. The standard runner is sequential, suppresses per-fit figures and defaults to 5,000 burn-in plus 15,000 retained steps. Its forest plot reads `summary.csv` without training. Match baseline and exclusion sampling settings, or record their difference explicitly, and assess convergence for every case. Reusing a matching cached result does not extend its chain.
