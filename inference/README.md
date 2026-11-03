# Standalone inference pipeline

This pipeline aligns analysis matrices with the design table and measurements, trains a PCA/GP emulator and samples the posterior. It runs independently of event-generation binaries, schedulers and the campaign INI.

This folder contains the inference source, tests, dependency list, bundled design table, public AuAu200 measurements and all inference guides. It also works when copied out of the surrounding repository. Recreate the Python environment at the new location; generated model matrices and O-O study inputs are supplied separately. Commands in these guides run from this folder unless stated otherwise.

**New simulation campaign:** use the [design-generation guide](DESIGN.md) for `make_design.py`, space-filling subsets/remainders, and row inspection. Generation and inference share parameter bounds in `design_parameters.py`; retain the same final design table throughout simulation and inference.

**Joint calibration:** the [Au–Au + O–O guide](JOINT.md) describes `joint_inference.py`, which fits shared physics parameters and separate beam-energy normalizations with one emulator per system. Its example study defaults to flat shear viscosity (13 sampled joint parameters), with a toggle to temperature-dependent viscosity (15).

**First run:** set up the environment below, then follow the [small walkthrough](GETTING_STARTED.md) using synthetic inputs. Use this page as the reference for real inputs, fit options and saved results.

**Your own analysis:** follow the [production workflow](PRODUCTION.md) for supplied model/data paths, logged fits, convergence review and saved-result plotting. Personal campaign inputs and outputs are not required by the walkthrough.

The supplied AuAu200 measurement directories are reference datasets. O-O support is an input adapter; its study data and evidence are not distributed. The [O-O schema](data/OO5360.md) explains how to supply compatible measurements. Keep personal datasets and campaign notes in an external directory or ignored `studies/<study>/`.

## Environment and paths

Start in `inference/` with **Python 3.11**:

```bash
cd /absolute/path/to/inference  # the repository folder or a standalone copy
PYTHON311=python3.11
"$PYTHON311" --version
```

If unavailable, set `PYTHON311` to an absolute 3.11 executable or load/create a site environment first. For conda: `conda create --name pce-inference python=3.11 pip`, activate it, then set `PYTHON311=python`.

Choose **one** dependency route. For a new isolated environment (downloads the requirements, so this needs package-index access):

```bash
"$PYTHON311" -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your selected 3.11 interpreter already has the dependencies in `requirements.txt`, you can reuse them without downloads. This inherits packages from that interpreter's environment and remains dependent on it:

```bash
"$PYTHON311" -c 'import numpy, scipy, matplotlib, sklearn, PIL; print(numpy.__version__, scipy.__version__, matplotlib.__version__, sklearn.__version__, PIL.__version__)'
"$PYTHON311" -m venv --system-site-packages --without-pip .venv
source .venv/bin/activate
```

Compare versions with [requirements.txt](requirements.txt); use the isolated route for missing/incompatible packages. Keep inference dependencies separate from simulation environments. Both routes finish with:

```bash
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
python -c 'import sys; assert sys.version_info[:2] == (3, 11); print(sys.executable)'
python -m pip check
python inference.py --help
python inference.py --selftest
```

`MPLCONFIGDIR` supplies a writable plotting cache; no display or TeX is needed. Require a clean `pip check` (or the site's equivalent if pip is absent). In later shells, reactivate `.venv` and repeat the exports; finish with `deactivate`.

The PCA/GP and sampler are implemented in `inference.py`; STAT, Surmise and emcee are not required. The synthetic self-test checks closure without campaign inputs, not posterior convergence.

Default inputs are anchored to the folder containing the scripts, so commands also work from another directory:

```bash
python /absolute/path/to/inference/check_inputs.py --help
```

Explicit relative paths and output directories are relative to your launch directory. `~` should be expanded by your shell (for example `--matrix ~/data/matrix.npz`). Default output directories are `plots_<system>`; prefer a descriptive `--out-dir` for each fit.

## Stage the selected analysis inputs

The [500-row design table](model/design_params_combined500.dat) and [AuAu200 reference measurements](data/README.md) are included; generated analysis products and O-O study measurements are not. Copy a matching matrix/metadata pair from the selected campaign:

```bash
# Replace both source paths with the matching AuAu200 aggregate products.
cp /path/to/analysis/model_matrix.npz model/model_matrix_AuAu200.npz
cp /path/to/analysis/model_matrix.meta.json model/model_matrix_AuAu200.meta.json
```

| System | Existing default matrix path | Required analysis profile | Default measurement directory |
|---|---|---|---|
| AuAu200 | `model/model_matrix_AuAu200.npz` | `baseline` (also used for missing profile field) | `data/AuAu200/STAR` |
| OO5360 | `model/model_matrix_OO5360.npz` | `oo_cross_full_pt027_flat/v3` | Supplied with `--data-dir`; `data/OO5360/centrality` is an ignored local staging default |

For another filename use `--matrix /absolute/path/matrix.npz` and keep its `.meta.json` alongside it. Use `--design` only if your matrix came from a different design table.

Align gapped matrix rows through `design_index` into the exact original design table. `Y_err` contains standard errors. The [input contract](INPUTS.md) defines metadata, labels and uncertainties; O-O additionally requires its flow-fallback metadata. Filenames alone do not establish compatibility.

The main fitter defaults to the system's **primary analysis**: all active parameters sampled, the system's primary observable exclusions (AuAu200: Lambda and proton yields and proton mean pT; O-O: K0S mean pT), and no feed-down correction. Launchers and scans have their own defaults, described below. `--drop-specs` replaces the exclusions (`--drop-specs ''` keeps every currently enabled observable). Optional O-O yield proxies additionally need `--oo-yield-proxies`; clearing the exclusions alone does not enable them. `--feeddown` applies the Sigma0 feed-down correction from the system's default inventory, or `--feeddown PATH` from another; the inventory must cover every selected design. Changing either choice requires reviewing its physics and inputs.

## Check inputs without starting a fit

The [measurement notes](data/README.md) describe the bundled reference inputs and any dataset-specific audit tools. The common checker below validates the selected matrix/design/data combination without training or sampling:

```bash
python check_inputs.py --system AuAu200
```

The checker uses the fitter's alignment, observable and metadata checks without training/sampling. It reports selected observables, usable designs and parameter policy, returning nonzero for invalid/missing inputs. For O-O:

```bash
python check_inputs.py --system OO5360 --data-dir /absolute/path/to/oo-data/centrality
```

Assembly can report skipped measurement bins, excluded model designs and dataset-specific uncertainty assumptions. Inspect those counts and the applicable data notes; they determine what enters the fit. The preflight is a compatibility check, not a validation of experimental uncertainties or physics assumptions.

O-O has an explicitly enabled assumed-proxy extension, `--oo-yield-proxies`, described in the [input schema](data/OO5360.md#optional-correlated-proxy-extension). It requires a supplied input/evidence bundle and is disabled by default.

## Emulator validation, inference and outputs

First check closure on your chosen matrix/measurement set:

```bash
python inference.py --system AuAu200 --emulator-only --out-dir plots_auau_closure
```

This writes closure/PCA figures without sampling. Full-grid training can be substantial. Production closure uses eight folds; reducing `--nsplits` is an execution shortcut, not equivalent validation.

The primary fits are:

```bash
python inference.py --system AuAu200 --out-dir plots_auau_primary
python inference.py --system OO5360 --data-dir /absolute/path/to/oo-data/centrality --out-dir plots_oo_primary
```

Choose the shear-viscosity treatment with the same toggle for either system:

```bash
python inference.py --system AuAu200 --shear-mode flat --out-dir plots_auau_flat
python inference.py --system OO5360 --shear-mode flat --out-dir plots_oo_flat
# Use --shear-mode temperature-dependent to sample both slopes instead.
```

With the default `--parameter-mode all`, flat viscosity samples 12 parameters per system: `eta_min` is the constant eta/s, and `eta_low = eta_high = 0`. Temperature-dependent viscosity samples all 14. Both modes train on the full 14-dimensional design; flat inference conditions the emulator on zero slopes. The existing simulations and prior on `eta_min` are retained. Use the same `--shear-mode` in `check_inputs.py` to inspect the selected sampled count before fitting.

Additional parameter treatments:

| Command | Parameter treatment |
|---|---|
| `inference.py --parameter-mode system` | The system's fixed slice: AuAu200 fixes six parameters at their reference values and samples eight; O-O fixes none |
| `inference.py --parameter-mode flat-eta` | Legacy equivalent of `--parameter-mode all --shear-mode flat`, for either system |
| `run_flat_eta.py` | AuAu200 with both shear slopes fixed at zero (constant eta/s), 12 sampled; 80,000 post-burn steps; rejects parameter selections that are not flat |

`--parameter-mode system --shear-mode flat` retains the system's other fixed values and overrides both slopes to zero. `--parameter-mode flat-eta --shear-mode temperature-dependent` is rejected as contradictory; select `--parameter-mode all` for the temperature-dependent fit.

Production defaults are 128 walkers, 5,000 burn-in and 40,000 post-burn steps. `--nsteps` changes only post-burn work. Use a distinct output directory per configuration; existing results can be overwritten.

For a quick **execution check** on staged inputs, explicitly shorten both burn-in and sampling. This does not establish posterior convergence:

```bash
python inference.py --matrix /path/to/matrix.npz --out-dir plots_execution_check \
  --nburn 20 --nsteps 40 --nwalkers 32 --nsplits 2 --no-plots
```

Sampling controls do not reduce GP training: every usable matrix row is trained. The [tutorial](GETTING_STARTED.md) instead supplies 16 synthetic designs with a matching design file.

Common fitting options (`run_flat_eta.py` accepts the same, without `--system`, and requires a flat selection):

| Option | Meaning / default |
|---|---|
| `--system` | `AuAu200` (default) or `OO5360` |
| `--matrix`, `--design`, `--data-dir` | Override system matrix, shared full design, measurement directory |
| `--parameter-mode` | `all` (default): every active parameter sampled before the shear choice; `system`: the system's fixed slice; `flat-eta`: legacy shorthand for `all` + flat shear |
| `--shear-mode` | `temperature-dependent` (default): use the parameter mode's slope treatment; `flat`: fix both slopes to zero and sample `eta_min` as constant eta/s. The legacy `flat-eta` parameter mode defaults to flat |
| `--drop-specs NAME,...` | Exclude named measurement groups (default: the system's primary exclusions; `''` keeps all); unknown names fail |
| `--oo-yield-proxies` | O-O only: enable assumed correlated pi/K/p yield proxies; pair with `--drop-specs ''` for the all-optional selection; not accepted by the flat-viscosity launcher |
| `--feeddown [PATH]` | Apply the Sigma0 feed-down correction from the system's inventory (or PATH); off by default |
| `--out-dir` | Destination for settings, posterior and figures; existing files can be overwritten |
| `--nsteps` | Post-burn steps per walker: 40,000; flat launcher: 80,000 |
| `--nburn`, `--nwalkers` | Burn-in 5,000; walkers 128 (even, at least four); small development ensembles do not validate a fit |
| `--seed` | MCMC seed 12345; closure retains its production seed |
| `--nsplits` | Real-design closure folds: eight; must be between two and the usable-design count |
| `--emulator-only` | Closure and emulator training without sampling |
| `--no-plots` | Suppress **all** figures, including closure/PCA; still train/calibrate the emulator and save settings/posterior |
| `--plot-npost`, `--plot-nprior` | Prediction draws: 3,000 posterior (capped at available samples) and 5,000 prior; reduce for quick execution checks |
| `--corner-font-scale` | Corner font multiplier: 2.6; use e.g. 1.4 for smaller labels |
| `--selftest` | Synthetic closure without campaign inputs |

Results include `<prefix>_posterior.npz` (`auau`/`oo`) with samples, parameter names, ranges, fixed values, and embedded input/settings provenance. `<prefix>_run.json` records absolute input paths, content hashes (including matrix metadata and measurement files), package versions, retained design IDs, the resolved `shear_mode` and sampler settings. Saved predictive plots restore the fixed slopes from the posterior. `<prefix>_diagnostics.json` records the printed convergence verdict and per-parameter rank-Rhat/ESS values; non-finite diagnostics count as failed. A successful process exit still does **not** imply convergence. Tiny execution checks should fail the scientific convergence criteria. No automatic MCMC checkpoint/resume is provided by the main fitter.

The configured failure thresholds are rank-normalized split-Rhat above 1.05 or tail ESS below 400; Rhat above 1.01 is a softer warning. Inspect individual parameters and the trace/corner plots, including prior boundaries. The 40,000-step default is a sampling budget, not a convergence guarantee. The diagnostic JSON records rank-Rhat/ESS; acceptance is printed in the log. See the [production workflow](PRODUCTION.md#check-results-before-interpreting-intervals) for result review and preservation.

## Default figures and saved-result tools

Fits automatically generate summaries/corners with the trained emulator and saved samples; no second training or sampling is needed. The O-O suite and flat-viscosity launcher use the same plotting path.

These are the dissertation-style figures: serif observable grids with nested 68%/95% bands and paper-label parameter corners. Single-system and [joint fits](JOINT.md) produce them by default; there is no separate dissertation-mode flag. `--no-plots` disables them. Robustness scan summaries are separate, and the standard scan runner suppresses individual-fit figures.

The [figure checklist](PRODUCTION.md#figures-from-the-main-fitter) lists the main fitter's output families and the four additional validation figures produced by `emulator_validation.py`.

| Default figure | Contents |
|---|---|
| `<prefix>_{Posterior,Prior}Summary_yields.pdf` | Prior/posterior predictive yield grids with measurements |
| `<prefix>_{Posterior,Prior}Summary_ptflow.pdf` | Mean-pT and charged-flow grids with measurements |
| `<prefix>_posterior_full.pdf` and group corner PDFs | Paper parameter labels and larger fonts; groups appear when at least two parameters are sampled |
| Existing closure, PC-spectrum, `PredictedDesign` and `MCMCSamples` PDFs | Emulator checks, raw design predictions and sampler traces |

`<prefix>` is `auau` or `oo`; braces denote two files. Plots omit groups without selected measurements. Flow summaries display roots of nonnegative cumulants, while fitting uses signed cumulants. Bands show variation of emulator means over parameter draws, without emulator uncertainty draws. Plot settings/seed are recorded separately and do not change samples.

For small execution checks use `--plot-npost 128 --plot-nprior 128 --corner-font-scale 1.4`. `--no-plots` suppresses all figures; `--emulator-only` writes closure/PCA only. Scans use their separate [aggregate plotting workflow](SCANS.md); shared styling lives in `plot_style.py`.

The standalone scripts remain useful for regenerating figures after a fit, including a fit made with `--no-plots`. Predictive regeneration reconstructs the saved fixed slice and input selection, including flat-viscosity fits:

```bash
python make_flow_plots.py --run-dir plots_auau_primary
python make_flow_plots.py --run-dir plots_auau_flat
python regen_corners.py --run-dir plots_auau_primary --font-scale 1.4
# For O-O also select its system/prefix:
python make_flow_plots.py --system OO5360 --run-dir plots_oo_primary
```

Standalone prediction plots verify recorded inputs and retrain one emulator; fitted emulator objects are not saved. Moved inputs need overrides pointing to identical copies. Older posteriors without provenance need matching paths/`--drop-specs` and emit a warning. Corner regeneration reads only saved samples. The excluded-baryon diagnostic additionally needs a complete Sigma0 inventory:

```bash
python plot_excluded_baryons.py --run-dir plots_auau_primary \
  --feeddown /path/to/complete/feeddown_yields.npz
```

This adds Sigma0 feed-down to Lambda only; the proton comparison stays primordial. An old inventory covering fewer designs than the selected matrix is rejected.

Saved predictive replay restores optional measurement selections as well as fixed parameters. The standalone validator accepts `--oo-yield-proxies` for an explicitly selected O-O validation; mirror the fitter's observable options when running it.

For MAP observables, use `python plot_map_observables.py --run-dir PATH`. The script detects a saved single-system or joint posterior, verifies its inputs, reconstructs calibrated emulator uncertainty, and optimizes the posterior density in physical parameter coordinates. A joint fit uses one MAP vector for every system. New single-system fits save GP settings and variance inflation for this reconstruction. See the [MAP plotting guide](PRODUCTION.md#map-observable-plots) for outputs, search controls and older-fit reconstruction.

## Script lookup

Entry points support `--help` without fitting. The [source argument index](CLI.md) lists flags; shared fit options originate in `inference.py`.

| Scripts | Purpose / input |
|---|---|
| `joint_inference.py` | Joint study preflight, per-system emulator validation, shared posterior and saved predictive replay; see [joint workflow](JOINT.md) |
| `make_design.py`, `design_subset.py`, `inspect_design.py` | Generate a full design, prioritize a subset/remainder and inspect rows; see [design generation](DESIGN.md) |
| `design_parameters.py`, `design_tools.py` | Shared parameter schema and design library; importing them writes nothing |
| `build_docs.py` | Regenerate/check the local [CLI reference](CLI.md) without importing fitters; Python standard library only |
| `make_demo_inputs.py` | Generate a small synthetic matrix/design pair for the inference walkthrough; refuses existing destinations |
| `emulator_validation.py` | K-fold diagnostics/figures; cache checks input hashes and settings; use `--refit` to recompute |
| `make_flow_plots.py` | Flow/predictive figures; reconstruct saved inputs and parameter slice |
| `plot_map_observables.py` | Optimize a saved single-system or joint MAP and plot each system's fitted observables; save parameters, predictions and optimizer diagnostics |
| `regen_corners.py` | Regenerate corner figures directly from saved posterior parameters/samples |
| `flat_eta_slice_compare.py` | Compare an existing 14-D posterior slice with the flat-shear fit |
| `robustness_scan.py`, `plot_robustness_forest.py` | AuAu200 and OO5360 observable-group exclusion scans and figures, including optional O-O proxies |
| `loo_closure.py`, `plot_loo_closure.py` | AuAu200 leave-one-out parameter-recovery runs and figures |
| `loo_showcase.py`, `plot_showcase_overlay.py` | Selected injected-truth recovery cases and overlay figures |
| `plot_excluded_baryons.py` | AuAu200 posterior-predictive diagnostics for the excluded channels |
| `run_oo_suite.py` | Three sequential O-O fits varying identified mean-pT channels; none enables the optional yield proxies |
| `run_flat_eta.py` | AuAu200 fit with both shear slopes fixed at zero |

## Scans, caches and failure reporting

The [scan walkthrough](SCANS.md) covers robustness, aggregate LOO and showcase inputs, outputs, plotting, seeds and resume. Robustness supports AuAu200 and OO5360, including `--drop-specs` and optional O-O yield proxies; aggregate LOO and showcase remain AuAu200-only. All use feed-down off. `--smoke` shortens some defaults but does not subset the matrix or reduce walkers; use explicit sizes for a small run and match `--workers` to your local allocation where the tool supports it. The robustness CLI runs sequentially and has no `--workers` option.

Robustness/aggregate LOO reuse results only when provenance/settings match; use a new directory or deliberate `--refit` for changes. Showcase overwrites selected cases. Plots read all matching files, so do not mix experiments. `emulator_validation.py` also checks provenance for its k-fold cache. Execution status and convergence diagnostics remain separate.

The O-O suite uses the active Python environment and accepts the common input/sampling options. Inspect commands without training or writes first:

```bash
python run_oo_suite.py --dry-run --out-root plots_oo_suite
```

Actual suite runs produce per-variant log files and append start/end/exit-status records to `plots_OO5360_suite.status` under `--out-root`. Outputs are overwritten on rerun; status history is appended. There is no suite-level posterior resume. In Python integrations, call `I.use_system(...)` before overriding module globals.

The suite's `idpt_all` adds K0S mean pT to the primary selection; it does not enable optional yield proxies. Supply an O-O dataset as described in the [input schema](data/OO5360.md).

## Development checks

From this folder, in the inference environment:

```bash
python -m unittest discover -s tests -v
python build_docs.py --check
```

The inference tests create their own small, synthetic model grids and O-O input fixtures, exercise real GP training/sampling and plotting, and use no private study inputs or external campaign products. Small fixtures test execution and contracts, not scientific accuracy or convergence.

The portability regression also copies this folder's maintained files into a temporary standalone tree and runs design generation, the tutorial input generator, preflight and a short fit there. Campaign integration tests live in the surrounding repository's test suite and are not needed for these checks. After changing CLI declarations, run `python build_docs.py`, then review `CLI.md`.
