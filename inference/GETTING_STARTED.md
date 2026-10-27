# A first small inference run

Follow synthetic inputs → checks → emulator closure → a short posterior fit and figures. You need only the inference environment and checkout; allow a few minutes, including plotting.

The model values are **synthetic toy functions**, compared with real AuAu200 measurements only to test interfaces. Posteriors and closure scores here have no physics interpretation.

## 1. Activate the environment

Complete the [environment setup](README.md#environment-and-paths), and keep that Bash shell open in `inference/`. If you just opened a new shell, return to this folder, activate the environment and restore its settings:

```bash
source .venv/bin/activate
export PYTHONNOUSERSITE=1
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export MPLCONFIGDIR="$PWD/.matplotlib"
python --version
```

Expect Python 3.11. Keep this shell open: later steps use its variables. No simulation binaries or cluster modules are needed.

## 2. Create a small input set

Create a fresh output directory inside `inference/` and generate the 16-design example:

```bash
WALKTHROUGH=$(mktemp -d "$PWD/plots_walkthrough.XXXXXX")
printf 'Walkthrough outputs: %s\n' "$WALKTHROUGH"
python make_demo_inputs.py --out-dir "$WALKTHROUGH/inputs"
TUTORIAL_MATRIX="$WALKTHROUGH/inputs/model_matrix_demo.npz"
TUTORIAL_DESIGN="$WALKTHROUGH/inputs/design.dat"
```

The generator writes a matrix NPZ, same-stem metadata and 17-column design from the first 16 bundled rows. Outputs are marked synthetic. Keep the generated design paired with its matrix; measurements come from `inference/data/`.

Existing destinations are refused. Each block execution creates a new ignored `plots_walkthrough.*` directory. To resume later, restore the printed `WALKTHROUGH` path and both input variables instead of regenerating inputs.

## 3. Check inputs before training

```bash
python check_inputs.py --system AuAu200 \
  --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN"
```

Expect `PASS`: matrix `(16, 1040)`, emulator inputs `(16, 14)`, 70 matched observables, 16 usable designs and none excluded by assembly. The other three design columns normalize other collision energies. The checker reports primary AuAu exclusions (Lambda/proton yields and proton mean pT), with feed-down off.

## 4. Check emulator closure

```bash
python inference.py --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN" \
  --emulator-only --nsplits 2 --out-dir "$WALKTHROUGH/closure"
```

Closure trains on part of the design and compares predictions with held-out rows. Two folds keep this example small; production uses eight. It then trains the full-grid emulator and writes `auau_closure.pdf`, `auau_PC_spectrum.pdf` and `auau_run.json`, without sampling. This toy grid retains three PCs.

The next command repeats closure/training; it does not load this emulator.

## 5. Run a short fit

```bash
python inference.py --matrix "$TUTORIAL_MATRIX" --design "$TUTORIAL_DESIGN" \
  --out-dir "$WALKTHROUGH/fit" \
  --nburn 20 --nsteps 40 --nwalkers 32 --nsplits 2 \
  --plot-npost 128 --plot-nprior 128 --corner-font-scale 1.4
```

This runs 32 walkers for 20 burn-in plus 40 saved steps, producing **1,280 × 14** samples. `--nsteps` counts post-burn steps per walker. The last options reduce plotting work/font size; normal plot defaults are in the [reference](README.md#emulator-validation-inference-and-outputs). `--no-plots` skips figures while preserving the fit and diagnostics.

Expect **NOT converged**: these chains test execution only. A zero exit does not establish convergence, and boundary-hugging toy samples are not physics constraints.

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

The predictive renderer writes four PDFs: `auau_PosteriorSummary_yields.pdf`, `auau_PosteriorSummary_ptflow.pdf`, `auau_PriorSummary_yields.pdf` and `auau_PriorSummary_ptflow.pdf`. The point labels refer to the real measurements; the bands come from the tutorial's synthetic model. Charged flow is displayed as v_n{2}, while the fit itself uses signed cumulants. The bands show the spread of emulator means across parameter draws, without sampling emulator uncertainty.

Corners write `auau_posterior_full.pdf` plus geometry/equilibration/shear/bulk groups from the saved samples. Smaller groups are often clearer than the full triangle. Open the PDFs in a viewer; no plot window appears. Corners may take more time/memory than the fit; font warnings can be harmless when files are written.

For later changes, use [saved-result plotting](README.md#default-figures-and-saved-result-tools): predictive regeneration retrains an emulator; corners read only the posterior.

## 7. Move to real campaign inputs

Follow [real-input staging](README.md#stage-the-selected-analysis-inputs), replacing both toy paths with the campaign matrix/original full design table and retaining matching metadata. Repeat checks/closure and select the system, parameter and measurement/feed-down policy.

Short sampling still trains on every usable real design. Use adequate production validation/sampling and inspect convergence. The fitter neither reuses this tutorial emulator nor resumes interrupted MCMC.

For your own inputs, continue with the [production workflow](PRODUCTION.md). It covers supplied matrix/design/measurement paths, preserved logs, convergence review and the standard plotting commands. Dataset-specific selections are described separately with the relevant measurements.

Use `--help` or the [flag index](../docs/reference/cli-inference.md) for options. Preserve saved-run inputs for predictive regeneration; use fresh directories for repeat exercises.

Next, follow the [small robustness and leave-one-out scan walkthrough](SCANS.md) to test observable exclusions, injected-truth recovery and checked resume.
