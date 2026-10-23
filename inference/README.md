# Standalone inference pipeline

This standalone Bayesian-calibration pipeline consumes completed analysis
matrices, aligns them with the design table and measurements, trains a PCA/GP
emulator, and samples the posterior. It runs independently of event generation,
Slurm/HTCondor, raw caches, and `models/campaign.py`.

**First time here?** Set up the environment below, then follow the
[small end-to-end walkthrough](GETTING_STARTED.md). It supplies reproducible
tutorial inputs and exercises checking, closure, sampling and saved-result plots
without campaign data or simulation binaries.

## Environment and paths

Start in `inference/` and select a **Python 3.11 interpreter**. `python` on your
shell may be an older conda or system Python, and `python3.11` may not be on PATH
even when 3.11 is installed:

```bash
cd /path/to/pce-eventgen/inference  # replace with your checkout's path
PYTHON311=python3.11
"$PYTHON311" --version
```

If that command is missing, set `PYTHON311=/absolute/path/to/python3.11`, or
`PYTHON311=python` after activating an existing Python 3.11 environment. Check its
version before continuing. If you need to install Python, use your site's Python
module or a package/environment manager; for conda, create and activate a new
environment with `conda create --name pce-inference python=3.11 pip` and
`conda activate pce-inference`, then set `PYTHON311=python`.

Choose **one** dependency route. For a new isolated environment (downloads the
requirements, so this needs package-index access):

```bash
"$PYTHON311" -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If your selected 3.11 interpreter already has the dependencies in
`requirements.txt`, you can reuse them without downloads. This inherits packages
from that interpreter's environment and remains dependent on it:

```bash
"$PYTHON311" -c 'import numpy, scipy, matplotlib, sklearn; print(numpy.__version__, scipy.__version__, matplotlib.__version__, sklearn.__version__)'
"$PYTHON311" -m venv --system-site-packages --without-pip .venv
source .venv/bin/activate
```

Compare the printed versions with `requirements.txt`; use the isolated route if
packages are missing or incompatible. Avoid installing inference dependencies
into an environment used for simulation work. Both routes finish with:

```bash
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
python -c 'import sys; assert sys.version_info[:2] == (3, 11); print(sys.executable)'
python -m pip check
python inference.py --help
python inference.py --selftest
```

`MPLCONFIGDIR` gives Matplotlib a writable font/cache directory; plotting uses
the noninteractive backend and needs no desktop display or TeX installation.
`pip check` should report no broken requirements. If a site environment omits
pip, check imports/versions above and use that site's dependency-check procedure.
For a later shell, return to `inference/`, activate `.venv`, and repeat the
exports; activation alone does not restore them. Use `deactivate` when finished.

Use Python 3.11; [requirements.txt](requirements.txt) records the direct package
versions used for validation. An existing environment with these packages also
works. The PCA/GP implementation and ensemble sampler are in `inference.py`;
STAT, Surmise and emcee are not required. Inference has its own environment and
does not need the native simulation binaries or their modules.

Default design, matrix and measurement paths are anchored to this folder, so
entry points also work when launched from the repository root or another directory:

```bash
# From the repository root, using an already activated inference environment:
python inference/check_inputs.py --help
```

Explicit relative paths and output directories are relative to your launch
directory. `~` should be expanded by your shell (for example `--matrix ~/data/matrix.npz`).
Default output directories are `plots_<system>`; prefer a descriptive `--out-dir`
for each fit.

The synthetic self-test does not require a model matrix or measurement files.
It checks emulator closure and exits nonzero if its criteria fail; it is not a
posterior-convergence test. Thread limits are especially useful for scan scripts
that start several worker processes.

## Stage the selected analysis inputs

This section is for **real campaign inputs**. For a first small run, use the
[tutorial](GETTING_STARTED.md) before staging production matrices here.

The campaign's [500-row design table](../config/initialization/design_params_combined500.dat)
(shared with event generation) and small [measurement tables](data/README.md)
are included. Generated matrices,
feed-down inventories, posterior samples and emulator caches must be supplied
or created for your run. Provide the exact matrix and same-stem metadata from your
chosen event campaign, for example:

```bash
# Replace both source paths with the matching AuAu200 aggregate products.
cp /path/to/analysis/model_matrix.npz model/model_matrix_AuAu200.npz
cp /path/to/analysis/model_matrix.meta.json model/model_matrix_AuAu200.meta.json
```

| System | Existing default matrix path | Required analysis profile | Default measurement directory |
|---|---|---|---|
| AuAu200 | `model/model_matrix_AuAu200.npz` | `baseline` (also used for missing profile field) | `data/AuAu200/STAR` |
| OO5360 | `model/model_matrix_OO5360.npz` | `oo_cross_full_pt027_flat/v3` | `data/OO5360/centrality` |

For another filename use `--matrix /absolute/path/matrix.npz` and keep its
`.meta.json` alongside it. Use `--design` only if your matrix came from a
different design table.

`design_index` is the zero-based join key into the original full design table;
matrix rows can have gaps. Preserve row order and the exact design table that
generated those events. The matrix's `Y_err` contains standard deviations. The
[analysis-input reference](../docs/reference/analysis-inputs.md) explains feature labels and
uncertainties. System/profile mismatches are rejected; O-O additionally checks
the per-design flow-fallback metadata. Matching filenames alone is insufficient.

Every entry point defaults to the system's **primary analysis**: all active
parameters sampled, the system's primary observable exclusions (AuAu200: Lambda
and proton yields and proton mean pT; O-O: K0S mean pT), and no feed-down
correction. `--drop-specs` replaces the exclusions (`--drop-specs ''` keeps every
observable). `--feeddown` applies the Sigma0 feed-down correction from the
system's default inventory, or `--feeddown PATH` from another; the inventory must
cover every selected design. Changing either choice requires reviewing its
physics and inputs.

## Check inputs without starting a fit

```bash
python check_inputs.py --system AuAu200
```

The checker and fitter share the same matrix loading, zero-based design
alignment, observable matching, metadata and O-O flow checks. It reports matched
observables, usable designs, the exclusions in force, and the system's fixed slice.
It does not train an emulator or start MCMC. Missing inputs or invalid contracts
give a nonzero exit. For O-O:

```bash
python check_inputs.py --system OO5360
```

O-O identified-hadron inputs retain their preliminary/assumed-error status;
see [data notes](data/README.md). Assembly can report skipped measurement bins
or excluded model designs. Inspect those counts; they determine what enters
the fit.

## Emulator validation, inference and outputs

First check closure on your chosen matrix/measurement set:

```bash
python inference.py --system AuAu200 --emulator-only --out-dir plots_auau_closure
```

This trains on the real design and writes closure/PCA figures, but performs no
sampling. It can take substantial time for a full matrix. `--nsplits` defaults
to 8; reducing it is a development shortcut, not equivalent validation.

The primary fits are:

```bash
python inference.py --system AuAu200 --out-dir plots_auau_primary
python inference.py --system OO5360 --out-dir plots_oo_primary
```

Two variants change the parameter treatment:

| Command | Parameter treatment |
|---|---|
| `inference.py --parameter-mode system` | The system's fixed slice: AuAu200 fixes six parameters at their reference values and samples eight; O-O fixes none |
| `run_flat_eta.py` | AuAu200 with both shear slopes fixed at zero (constant eta/s), 12 sampled; 80,000 post-burn steps |

These are actual fits. The production defaults are 128 walkers, 5,000 burn-in steps
and 40,000 post-burn steps. `--nsteps` changes post-burn steps only, so a small value does not
remove the 5,000-step burn-in. Use a new output directory for each configuration;
existing result filenames can be overwritten.

For a quick **execution check** on staged inputs, explicitly shorten both burn-in
and sampling. This does not establish posterior convergence:

```bash
python inference.py --matrix /path/to/matrix.npz --out-dir plots_execution_check \
  --nburn 20 --nsteps 40 --nwalkers 32 --nsplits 2 --no-plots
```

This still trains on every usable design in the supplied matrix. `--nsteps` and
`--nwalkers` reduce sampling work, not the size of the GP training grid. The
[tutorial](GETTING_STARTED.md) uses only 16 synthetic designs to keep the entire
exercise small. Always keep its matrix paired with its generated `--design`;
neither replaces your campaign's full design table.

Common fitting options (`run_flat_eta.py` accepts the same, without `--system`):

| Option | Meaning / default |
|---|---|
| `--system` | `AuAu200` (default) or `OO5360` |
| `--matrix`, `--design`, `--data-dir` | Override system matrix, shared full design, measurement directory |
| `--parameter-mode` | `all` (default): every active parameter sampled; `system`: the system's fixed slice; `flat-eta`: both shear slopes fixed at zero |
| `--drop-specs NAME,...` | Exclude named measurement groups (default: the system's primary exclusions; `''` keeps all); unknown names fail |
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

Results include `<prefix>_posterior.npz` (`auau`/`oo`) with samples, parameter
names, ranges, fixed values, and embedded input/settings provenance.
`<prefix>_run.json` records absolute input paths, content hashes (including matrix
metadata and measurement files), package versions, retained design IDs and sampler
settings. `<prefix>_diagnostics.json` records the printed convergence verdict and
per-parameter rank-Rhat/ESS values; non-finite diagnostics count as failed.
A successful process exit still does **not** imply convergence. Tiny execution
checks should fail the scientific convergence criteria. No automatic MCMC
checkpoint/resume is provided by the main fitter.

## Default figures and saved-result tools

Normal fits now automatically use the dissertation plotting scripts. The main
fitter, 14-D and flat-viscosity launchers, and O-O suite reuse the fit's trained
emulator, prepared inputs and samples: plotting does not train another emulator
or run MCMC again. No extra command is needed after a successful fit.

| Default figure | Contents |
|---|---|
| `<prefix>_{Posterior,Prior}Summary_yields.pdf` | Prior/posterior predictive yield grids with measurements |
| `<prefix>_{Posterior,Prior}Summary_ptflow.pdf` | Mean-pT and charged-flow grids with measurements |
| `<prefix>_posterior_full.pdf` and group corner PDFs | Paper parameter labels and larger fonts; groups appear when at least two parameters are sampled |
| Existing closure, PC-spectrum, `PredictedDesign` and `MCMCSamples` PDFs | Emulator checks, raw design predictions and sampler traces |

Here `<prefix>` is `auau` or `oo`; each brace pair expands to two files.
An observable group with no selected measurements is skipped. Charged-flow
summaries display v_n{2} by taking the square root of nonnegative predicted
cumulants; the fit still uses signed cumulants. These predictive bands show
the spread of emulator **means** over parameter draws, not draws of emulator
uncertainty. Plot settings and the separate plotting seed (zero) are recorded
under `plotting` in the run settings; they do not change the posterior samples.

Use `--plot-npost 128 --plot-nprior 128 --corner-font-scale 1.4` for small
execution checks. `--no-plots` suppresses every figure; `--emulator-only` produces
only closure/PCA figures. Robustness and recovery
scans keep their separate [aggregate plotting workflow](SCANS.md); they do not
produce these figures for every short sub-fit. Their forest, LOO rank/coverage
and showcase figures use the same theme and paper labels. Re-running an aggregate plot command restyles its existing results
without training or sampling. Shared colours, typography and paper-label
overrides live in `plot_style.py`.

The standalone scripts remain useful for regenerating figures after a fit,
including a fit made with `--no-plots`. Predictive regeneration reconstructs the
saved fixed slice and input selection, including flat-viscosity fits:

```bash
python make_flow_plots.py --run-dir plots_auau_primary
python make_flow_plots.py --run-dir plots_auau_flat_eta
python regen_corners.py --run-dir plots_auau_primary --font-scale 1.4
# For O-O also select its system/prefix:
python make_flow_plots.py --system OO5360 --run-dir plots_oo_primary
```

Standalone predictive plotting verifies the recorded input contents and retrains
one emulator, since fitted emulator objects are not saved in the posterior NPZ.
This extra training is only needed for standalone regeneration; the automatic
fit workflow shares its existing emulator.
If files have moved, pass `--matrix`, `--design` and/or `--data-dir` pointing to
identical copies. Posteriors saved before input provenance was recorded can
still restore their fixed parameters, but need matching input paths and
`--drop-specs`; a warning says so. Corner regeneration uses saved samples
without loading a matrix. The excluded-baryon diagnostic intentionally evaluates
the full observable set and additionally requires a complete Sigma0 inventory:

```bash
python plot_excluded_baryons.py --run-dir plots_auau_primary \
  --feeddown /path/to/complete/feeddown_yields.npz
```

This adds Sigma0 feed-down to Lambda only; the proton comparison stays primordial.
An old inventory covering fewer designs than the selected matrix is rejected.

## Script lookup

Every Python entry point and the shell suite now support `--help` without
starting a fit. Read a script's module docstring for its research context, and
use the [source argument index](../docs/reference/cli-inference.md) to search flags.
Shared input/fit options are declared in `inference.py` and reused by the other scripts.

| Scripts | Purpose / input |
|---|---|
| `make_demo_inputs.py` | Generate a small synthetic matrix/design pair for the student walkthrough; refuses existing destinations |
| `emulator_validation.py` | K-fold diagnostics/figures; cache checks input hashes and settings; use `--refit` to recompute |
| `make_flow_plots.py` | Flow/predictive figures; reconstruct saved inputs and parameter slice |
| `regen_corners.py` | Regenerate corner figures directly from saved posterior parameters/samples |
| `flat_eta_slice_compare.py` | Compare an existing 14-D posterior slice with the flat-shear fit |
| `compare_oo_matrix.py` | Compare new-profile and old-baseline O-O matrices; both are separate supplied inputs |
| `robustness_scan.py`, `plot_robustness_forest.py` | AuAu200 observable-group exclusion scans and figures |
| `loo_closure.py`, `plot_loo_closure.py` | AuAu200 leave-one-out parameter-recovery runs and figures |
| `loo_showcase.py`, `plot_showcase_overlay.py` | Selected injected-truth recovery cases and overlay figures |
| `plot_excluded_baryons.py` | AuAu200 posterior-predictive diagnostics for the excluded channels |
| `run_oo_suite.py` | Three sequential O-O fits varying which identified mean-pT channels are kept |
| `run_flat_eta.py` | AuAu200 fit with both shear slopes fixed at zero |

## Scans, caches and failure reporting

Follow the [small scan walkthrough](SCANS.md) for complete, runnable robustness,
aggregate LOO and showcase examples, including plots, expected statuses, checked
resume and extending a case set. It generates a 16-design toy matrix so training
is small as well as sampling.

The AuAu robustness and recovery tools accept `--matrix`, `--design`, `--data-dir`
and explicit sampling controls. They use the primary exclusions without
feed-down. Run `--help` for their individual sampling defaults, which are
shorter than the main fitter's. `--smoke` supplies short development defaults, and explicit sizes/output
paths take precedence.

```bash
python robustness_scan.py --help
python loo_closure.py --help
python loo_showcase.py --help
```

Smoke mode does not reduce the matrix or the 128-walker default. Showcase has no
smoke preset; its default 4,000 burn-in + 8,000 saved steps are substantial.
Use the explicit small settings in the walkthrough for a first run.

Robustness and aggregate LOO runs resume only when the input contents, implementation
and relevant settings match. A cache without provenance or with different settings
is refused; use a new output directory or `--refit` to explicitly overwrite it.
`emulator_validation.py` applies the same rule to its k-fold cache. Showcase runs
always refit/overwrite their selected results. Recovery plots reject mixed settings
or parameter sets; results saved before provenance was recorded remain readable.
LOO resume still recomputes its shared inflation calibration. Plots read all matching
files in the directory; reducing K or refitting only some cases does not delete others.
Use a fresh directory when changing the experiment or settings.

LOO filenames use the stacked **usable-row** index. The files also store the
original full-table `design_index`, so gaps and filtered rows cannot
be mistaken for original design IDs. `--seed` on aggregate LOO selects held-out rows;
pseudo-data and sampler seeds retain their per-row source definitions. Workers use
explicit initialization, including on platforms whose multiprocessing default is
`spawn`. Set `--workers` to match your allocation; these tools run locally.

Batches attempt all variants and return nonzero if any unexpected fit/worker fails.
The robustness smoke run deliberately drops all observables once: this must produce
`expected_failure` in its CSV and does not make an otherwise successful smoke run
fail. Its forest plot skips failed/expected-failure rows and requires a successful
baseline. Read convergence diagnostics separately from execution status.

The O-O suite uses the active Python environment and accepts the common input/sampling
options. Inspect commands without training or writes first:

```bash
python run_oo_suite.py --dry-run --out-root plots_oo_suite
```

Actual suite runs produce per-variant log files and append start/end/exit-status
records to `plots_OO5360_suite.status` under `--out-root`. Outputs are overwritten
on rerun; status history is appended. There is no suite-level posterior resume.
In Python integrations, call `I.use_system(...)` before overriding module globals.

## Development checks

From the repository root, in the inference environment:

```bash
python -m unittest discover -s inference/tests -v
python tools/check_workflow.py
python tools/build_docs.py --check
```

The inference tests create their own small, synthetic model grids, exercise real
GP training/sampling and plotting, and use no external campaign products. Small
fixtures test execution and contracts, not scientific accuracy or convergence.
