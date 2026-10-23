# A first small inference run

This walkthrough follows the complete file flow: inputs → input checks →
emulator closure → a short posterior run with automatic figures. It needs only
the inference Python environment and this checkout. Allow a few minutes; plot
generation can take longer than the tiny fit itself.

The model values generated here are **synthetic toy functions**, not TRENTo,
MUSIC, iS3D or SMASH predictions. They are compared with the bundled real
AuAu200 measurements only to exercise the interfaces. The resulting posterior,
bands and closure scores have no physics interpretation.

## 1. Activate the environment

Complete the [environment setup](README.md#environment-and-paths), and keep
that Bash shell open in `inference/`. If you just opened a new shell, return
to this folder, activate the environment and restore its settings:

```bash
source .venv/bin/activate
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
python --version
```

Expect Python 3.11. This environment runs independently of native simulator
builds, campaign INI files and cluster login modules. Keep the same shell for
the commands below: later steps use variables set in the next step.

## 2. Create a small input set

Create a fresh output directory inside `inference/` and generate the 16-design
example:

```bash
WALKTHROUGH=$(mktemp -d "$PWD/plots_student_walkthrough.XXXXXX")
printf 'Walkthrough outputs: %s\n' "$WALKTHROUGH"
python make_demo_inputs.py --out-dir "$WALKTHROUGH/inputs"
TUTORIAL_MATRIX="$WALKTHROUGH/inputs/model_matrix_demo.npz"
TUTORIAL_DESIGN="$WALKTHROUGH/inputs/design.dat"
```

The generator writes a matrix NPZ, its same-stem `.meta.json`, and a design
table with the standard 17 columns. Both the metadata and command output mark
the matrix as synthetic. It uses the first 16 rows of the bundled design and
stores zero-based row IDs; always pass the generated design alongside it.
The existing measurement files are read from `inference/data/`.

The generator refuses to overwrite an existing directory. Repeating this block
creates a fresh walkthrough, so earlier products are preserved. These
`plots_student_walkthrough.*` directories are ignored by Git. Record the printed
path if you close your shell; to resume, set `WALKTHROUGH` to that path and reset
the two input-path variables above.

## 3. Check inputs before training

```bash
python check_inputs.py --system AuAu200 \
  --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN"
```

Expect `PASS`, a matrix shape of `(16, 1040)`, aligned inputs `(16, 14)`,
70 matched observables, 16 usable designs and zero designs excluded by
assembly. The 14 columns are the system's active emulator inputs, all sampled by
default; the other three design columns are normalizations for other collision
energies. The checker also lists the default AuAu200 exclusions (Lambda and
proton yields, proton mean pT) and reports feed-down off, which is the primary
analysis; the tutorial has no feed-down inventory.

## 4. Check emulator closure

```bash
python inference.py --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN" \
  --emulator-only --nsplits 2 --out-dir "$WALKTHROUGH/closure"
```

Closure holds out part of the design, trains on the rest, and compares emulator
predictions with the held-out values. Here two folds keep the example small;
production validation defaults to eight. The command then trains the full-grid
emulator. It writes `auau_closure.pdf`, `auau_PC_spectrum.pdf` and
`auau_run.json` in `closure/`, and does **not** sample a posterior. The toy grid
has three retained principal components.

The following fit repeats closure/training; it does not load the emulator from
this step. The stand-alone emulator-only command is useful for inspecting
closure before committing to a long scientific run.

## 5. Run a short fit

```bash
python inference.py --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN" \
  --out-dir "$WALKTHROUGH/fit" \
  --nburn 20 --nsteps 40 --nwalkers 32 --nsplits 2 \
  --plot-npost 128 --plot-nprior 128 --corner-font-scale 1.4
```

This trains the emulator and runs 32 walkers for 20 burn-in steps plus 40 saved
steps. The saved sample table has **1,280 rows and 14 columns**. `--nsteps`
counts steps per walker after burn-in; it is not the total number of samples.
The fit automatically writes prior/posterior summaries and corner figures using the same emulator and samples. The last three options
reduce the plotting ensembles and corner font size for this tutorial; normal
defaults are 3,000 posterior draws (capped at the sample count), 5,000 prior
draws and a 2.6 font multiplier. Plotting does not alter the fit. To skip all
figures while keeping calibration, saved samples and diagnostics, use `--no-plots`.

Expect a **NOT converged** verdict. These settings test execution and are far
too short for scientific inference; a zero command exit code means execution
succeeded, not that the posterior converged. The toy model also cannot explain
all the real data. Do not interpret edge-hugging samples as physics constraints.

Inspect the saved arrays and verdict without knowing the internal code:

```bash
python - "$WALKTHROUGH/fit" <<'PY'
import json
from pathlib import Path
import sys
import numpy as np

run = Path(sys.argv[1])
with np.load(run / "auau_posterior.npz", allow_pickle=False) as posterior:
    print("Saved sample shape:", posterior["samples"].shape)
    print("Sampled parameters:", ", ".join(posterior["param_names"]))
print(json.loads((run / "auau_diagnostics.json").read_text())["verdict"])
PY
```

The fit writes:

| File in `fit/` | Purpose |
|---|---|
| `auau_posterior.npz` | Samples, parameter names/ranges, fixed values and embedded input/settings provenance |
| `auau_run.json` | Human-readable input paths/hashes, versions, retained design IDs and run settings |
| `auau_diagnostics.json` | Convergence verdict and per-parameter rank-Rhat/ESS diagnostics |
| `*.pdf` | Automatic closure/PCA checks, design/trace diagnostics, predictive summaries and corner figures |

## 6. Inspect the automatic figures

List the products from the fit:

```bash
ls "$WALKTHROUGH/fit"
```

The predictive renderer writes four PDFs: `auau_PosteriorSummary_yields.pdf`,
`auau_PosteriorSummary_ptflow.pdf`, `auau_PriorSummary_yields.pdf` and
`auau_PriorSummary_ptflow.pdf`. The point labels refer to the real measurements;
the bands come from the tutorial's synthetic model. Charged flow is displayed
as v_n{2}, while the fit itself uses signed cumulants. The bands show the spread
of emulator means across parameter draws, without sampling emulator uncertainty.

The corner renderer uses the saved-run samples without training or sampling. It writes
`auau_posterior_full.pdf` and group figures for geometry, equilibration, shear
and bulk. Corner plots can take longer and use more memory than the fit. The
individual group figures are often easier to read than the full 14-parameter
triangle. Open the PDFs in a viewer; no plot window is expected in your terminal.
Font-fallback or font-metadata timestamp warnings can be harmless if the figures
are written successfully.

No second training or sampling step is needed for these automatic figures.
To change figures later, use the [saved-result tools](README.md#default-figures-and-saved-result-tools):
`make_flow_plots.py` then reconstructs inputs and retrains one emulator;
`regen_corners.py` only reads the posterior NPZ.

## 7. Move to real campaign inputs

Return to [staging real inputs](README.md#stage-the-selected-analysis-inputs).
Replace **both** tutorial input paths with your campaign matrix and its original
full design table, and keep the matching matrix metadata alongside the NPZ.
Repeat input checks and real-design closure before sampling. Select your
collision system, parameter treatment, feed-down policy and measurements
deliberately; O-O has different profile/data requirements.

A short chain on a 500-row matrix still trains on all 500 designs. Smaller
`--nsteps` or `--nwalkers` does not reduce that cost. Use production sampling and
validation settings from the [inference reference](README.md), and read the
diagnostics before interpreting results. The full workflow does not automatically
reuse the tutorial emulator or resume a partially completed MCMC chain.

Use `python SCRIPT.py --help` or the [flag index](../docs/reference/cli-inference.md)
for specific options. To repeat this exercise, create a fresh walkthrough
directory. To keep working with an existing saved posterior, preserve its input
files; predictive plotting needs them, while corner regeneration only needs the
posterior NPZ.

Next, follow the [small robustness and leave-one-out scan walkthrough](SCANS.md)
to test observable exclusions, injected-truth recovery and checked resume.
