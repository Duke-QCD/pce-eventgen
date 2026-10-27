# New-user setup on NERSC Perlmutter

Build a personal CPU installation, download physics data, then test generation, caching and analysis with two events. Inference runs [separately](../inference/README.md).

Use Bash on a Perlmutter login node for steps 1–5. You need a NERSC account, authorized CPU allocation and project storage access. Step 2 downloads the public physics-data bundle; step 6 tests the build, scheduler and storage end to end.

Keep the same shell open for these steps and stop on any failed command. After changing shells, restore the path variables in step 2 and activate the selected profile before continuing.

## 1. Agree on the storage locations

| Location                 | Contents                                                                     | Example                                             |
| ------------------------ | ---------------------------------------------------------------------------- | --------------------------------------------------- |
| Home                     | Complete source checkout, personal settings and small input templates        | `$HOME/pce-eventgen`                                |
| Global Common software   | Conda environment, model executables, libraries, Pythia                      | `/global/common/software/PROJECT/USER/pce-eventgen` |
| CFS static-data root     | Downloaded `iS3D/` and `EOS/` physics data; reusable read-only               | `/global/cfs/cdirs/PROJECT/USER/production_data`    |
| CFS writable-output root | Your surface archives, cache staging, published caches, records and analysis | `/global/cfs/cdirs/PROJECT/USER/pce-eventgen`       |
| Scratch                  | Initialized designs, working event directories and event/Slurm logs          | `$PSCRATCH/pce-eventgen`                            |

`PROJECT` is your authorized allocation and `USER` your NERSC username. Ask the project owner for writable personal directories and read/traverse access to shared data.

Install software from a login node: Global Common is read-only on compute nodes. Keep durable inputs/results on CFS; scratch can be purged. See [Global Common](https://docs.nersc.gov/filesystems/global-common/), [CFS](https://docs.nersc.gov/filesystems/community/) and [scratch](https://docs.nersc.gov/filesystems/perlmutter-scratch/) policies.

## 2. Obtain the checkout and production inputs

Clone the repository and its pinned SMASH submodule on the NERSC login node:

```bash
git clone --recurse-submodules https://github.com/Duke-QCD/pce-eventgen.git "$HOME/pce-eventgen"
```

Alternatively, upload an initialized local checkout from your own computer (Python 3.8+ and rsync 3+ on both ends):

```bash
cd /path/to/local/pce-eventgen
# Replace USER@HOST with your NERSC login or SSH alias.
python3 nersc/stage.py code --destination USER@HOST:pce-eventgen
# Review the preview, then transfer:
python3 nersc/stage.py code --destination USER@HOST:pce-eventgen --apply
```

`USER@HOST:pce-eventgen` selects a directory under your remote home; use `USER@HOST:/absolute/path/pce-eventgen` for another location. Choose a new destination whose parent already exists. The helper copies source, including initialized SMASH, while excluding Git history, environments, builds, personal profiles and physics data. It does not delete destination files. This produces a source copy; future updates come from repeating the upload from the original checkout.

Download physics data separately below. The archive is a GitHub release asset, not part of the Git checkout or source upload.

On the NERSC login node, select the source location and storage roots:

```bash
export REPO="$HOME/pce-eventgen"   # adjust to where you put this checkout
cd "$REPO"
export PROJECT=YOUR_ACCOUNT       # replace with your authorized allocation
export SOFTWARE="/global/common/software/$PROJECT/$USER/pce-eventgen"
export DATA="/global/cfs/cdirs/$PROJECT/$USER/production_data"
export OUTPUT="/global/cfs/cdirs/$PROJECT/$USER/pce-eventgen"
export WORK_SCRATCH="${PSCRATCH:-${SCRATCH:-}}"
: "${WORK_SCRATCH:?Set PSCRATCH or SCRATCH to your Perlmutter scratch directory}"
mkdir -p "$(dirname "$SOFTWARE")" "$OUTPUT"
```

Keep `SOFTWARE` in your own software area. Choose a writable personal `DATA` path, or select an existing copy of the same public bundle shared by your project.

The installer requires the pinned SMASH source, included by a recursive clone or the source-copy helper; fresh upstream copies of the customized models are not substitutes.

Download directly to the selected CFS location (Python 3.8+, no additional packages):

```bash
python3 tools/download_physics_data.py --directory "$DATA"
```

If you already uploaded the archive to NERSC, use `python3 tools/download_physics_data.py --archive /path/to/pce-eventgen-physics-data.tar.gz --directory "$DATA"` instead; it verifies and extracts the local file without downloading it.

The helper checks the extracted runtime inputs, retains the included attribution/license notices and creates the following runtime layout. Repeating the command verifies and reuses matching data, including a shared read-only copy. It refuses to change an existing mismatched data directory. Allow about 1.2 GB for unpacked data plus the compressed download during setup. You do not need access to the maintainer's NERSC directories.

```text
production_data/
├── iS3D/
│   ├── iS3D_parameters.dat
│   ├── PDG/
│   │   ├── pdg_smash_pce.dat
│   │   ├── chosen_particles.dat
│   │   ├── charge_dict.dat
│   │   └── baryon_dict.dat
│   ├── tables/
│   └── deltaf_coefficients/vh/
│       └── smash_pce_light_strange/  # ten production two-occupancy tables
└── EOS/PCE/strange_30_10000/
    └── 0.dat … 29.dat
```

Keep the complete iS3D tree, including its other coefficient tables. Do not substitute single-occupancy coefficients. See [particle/model conventions](../docs/reference/physics.md) for sigma handling. The optional `nersc/stage.py data --help` command describes copying an already verified tree to a new location; a public download needs no staging-copy step.

AuAu200 needs no external nuclear table. OO5360 requires approved `o16_nleft_o13.hdf5` plus its provenance sidecar under `$DATA/nuclear_tables/`, or an explicit `HIC_O16_NUCLEUS_TABLE` in the profile. Supply that pair separately; the data bundle contains only EOS/iS3D inputs. OO5360 also requires a matching fingerprint and analysis settings; see [system selection](../docs/nersc-campaign.md#configuration-and-selection).

## 3. Build your own installation

Use a consistent GNU compiler/dependency stack. Check available versions with `module avail`/`module spider`; project-provided modules may need an initial `module use` supplied by the maintainer. Use GSL 2.x and GNU HDF5 1.10/1.12/1.14 with C++ support. System GSL is sufficient when `gsl-config --version` reports 2.x; no module named `gsl` is assumed. If GSL is absent, install `gsl=2.*` in the selected Conda environment or load your site's actual GSL module. This recipe selects CMake, Boost and Eigen in Conda rather than relying on unversioned site-module defaults:

```bash
module load cpu PrgEnv-gnu cray-hdf5 conda
conda create --prefix "$SOFTWARE" --override-channels -c conda-forge \
  python=3.11 numpy=1.26.4 'scipy=1.14.*' 'h5py=3.15.*' 'hdf5=1.14.*' \
  'cmake>=3.16,<4' 'boost-cpp=1.81.*' 'eigen=3.4.*' zstd
conda activate "$SOFTWARE"
export PYTHONNOUSERSITE=1
export CC=cc CXX=CC FC=ftn
# Select the loaded GNU HDF5 before conda's transitive HDF5 dependency.
export HDF5_ROOT="${HDF5_DIR:?Inspect module show cray-hdf5 and set HDF5_DIR to its prefix}"
export CMAKE_PREFIX_PATH="$HDF5_ROOT:$CONDA_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export BOOST_ROOT="$CONDA_PREFIX"
test -r "$HDF5_ROOT/include/H5Cpp.h"  # must succeed before continuing
export HIC_BUILD_JOBS=2
cmake --version                     # must be >= 3.16 and < 4
"$CXX" --version                    # must identify the GNU backend
gsl-config --version                # must be 2.x
python -c 'import numpy, scipy, h5py; print("NumPy", numpy.__version__, "SciPy", scipy.__version__, "h5py", h5py.__version__)'
```

The [dependency reference](../local/README.md#dependency-versions) explains the version selections. Keep the GNU compiler and runtime consistent throughout the build and jobs. See NERSC guidance for [Conda environments](https://docs.nersc.gov/development/languages/python/nersc-python/) and [compiler wrappers](https://docs.nersc.gov/development/compilers/wrappers/).

Confirm the build reports HDF5 with C++ support from the selected GNU installation. Keep that installation available on compute nodes. See [NERSC HDF5](https://docs.nersc.gov/development/libraries/hdf5/).

Create a build/log directory:

```bash
export BUILD="$WORK_SCRATCH/pce-build"
mkdir -p "$BUILD"
```

Build Pythia **8.309** with the same GNU compiler stack:

```bash
export DEPENDENCIES="${SOFTWARE}-deps"
mkdir -p "$DEPENDENCIES"
cd "$BUILD"
curl --fail --location --output pythia8309.tgz \
  https://gitlab.com/Pythia8/releases/-/archive/pythia8309/releases-pythia8309.tar.gz
mkdir pythia8309                     # choose a fresh build directory
tar -xf pythia8309.tgz --strip-components=1 -C pythia8309
cd pythia8309
./configure --prefix="$DEPENDENCIES/pythia8309" --cxx="$CXX" \
  --cxx-common='-std=c++17 -O3 -fPIC -pthread'
make -j"$HIC_BUILD_JOBS"
make install
export PYTHIA_CONFIG="$DEPENDENCIES/pythia8309/bin/pythia8-config"
"$PYTHIA_CONFIG" --prefix
grep '^#define PYTHIA_VERSION' "$DEPENDENCIES/pythia8309/include/Pythia8/Pythia.h"
```

For offline builds, use the approved source archive. Keep the installed dependency prefix: SMASH links to its library. The model installer does not download Pythia or support substituting a newer release.

```bash
export HIC_EOS_PCE_DIR="$DATA/EOS/PCE/strange_30_10000"
export HIC_MUSIC_INSTALL_PCE_DATA=OFF  # reuse the separately managed CFS EOS

cd "$REPO"
set -o pipefail
./nersc/install -- -DPythia_CONFIG_EXECUTABLE="$PYTHIA_CONFIG" 2>&1 | tee "$BUILD/install.log"
```

Continue only if installation succeeds; retain `$BUILD/install.log`. Save `module -t list 2> "$OUTPUT/build-modules.txt"` and `conda list --explicit > "$OUTPUT/build-conda-explicit.txt"` for reproducibility.

The [installed layout](../local/README.md) includes executables, libraries and support data. This profile deliberately selects external CFS physics inputs; rebuilding executables does not replace them.

## 4. Generate your profile

`nersc/configure_user.py` renders four [profile templates](../config/templates/) into a new directory. It creates configuration only; storage preparation, installation and jobs are separate steps.

Choose a new profile directory; the helper refuses to overwrite one that already exists.

For the first test, use a separate two-event scratch campaign. `--nevents 2` selects `qos = debug` and `time = 00:30:00` for generation and cache jobs. Generation uses two tasks with 32 logical CPUs per task and defaults to 16 OpenMP threads per worker, with matching settings in the batch launcher. Select the GCC runtime module matching your build; `gcc-native/14` below is an example:

```bash
cd "$REPO"
python3 nersc/configure_user.py \
  --project "$PROJECT" --software-root "$SOFTWARE" --data-root "$DATA" \
  --runtime-gcc-module gcc-native/14 \
  --output-root "$OUTPUT" --raw-root "$WORK_SCRATCH/pce-setup-smoke" \
  --out-dir "$REPO/config/users/$USER-smoke" --nevents 2

source "$REPO/config/users/$USER-smoke/activate.sh"
```

The helper accepts `PSCRATCH` or `SCRATCH` and defaults software/output paths as in step 1. Generated `config/users/` profiles are ignored by Git. Sourcing `activate.sh` sets `REPO`, `CFG` and `PROJECT` as well as the environment and runtime paths; no separate exports are needed for later commands.

| Generated file    | Purpose                                                                                       |
| ----------------- | --------------------------------------------------------------------------------------------- |
| `nersc_paths.sh`  | Runtime paths and thread limits for Python control/cache/analysis processes                   |
| `pce-eventgen`    | Your Slurm generation launcher, with your account and log directory                           |
| `calibration.ini` | The campaign configuration every `campaign.py` command reads                                  |
| `activate.sh`     | Selects the profile: sources the paths file, activates the conda prefix, loads the CPU module |

The INI's `[files] launcher` names the launcher. The allocation is set in the launcher's `#SBATCH --account` and in `[cache] account`.

The INI selects **row 0 only** from the [500-row design](../config/initialization/design_params_combined500.dat), unless `--design-file` overrides it. It initializes new designs, protects existing ones unless `--allow-existing` is supplied, and enables repair/archive finalization for acting commands. Cache scope comes from its fingerprint; no separate cache environment file is needed.

`--runtime-gcc-module` must match the GNU runtime used for the build. Add any other required runtime modules to both `activate.sh` and `pce-eventgen`. System/Conda GSL needs no module.

## 5. Create writable directories and check the selections

```bash
# Generated profiles place records, surveys and manifests here:
export CAMPAIGN="$(basename "$HIC_RAW_ROOT")"
mkdir -p "$HIC_SURFACE_ARCHIVE_ROOT" \
  "$HIC_EVENT_CACHE_LOCAL_ROOT/$CAMPAIGN" "$HIC_EVENT_CACHE_CFS_ROOT/$CAMPAIGN" \
  "$HIC_CAMPAIGN_OUTPUT_ROOT/analysis/$CAMPAIGN" "$HIC_CAMPAIGN_OUTPUT_ROOT/records/$CAMPAIGN" \
  "$HIC_RAW_ROOT/_logs/slurm" "$HIC_RAW_ROOT/_logs/events"

# Stop this check block at the first failure; all checks must pass.
(
  set -e
  python -c 'import sys, numpy, scipy, h5py; print("Python:", sys.executable)'
  for program in trento run-events run-events-wrapper zstd; do
    command -v "$program"
  done
  for program in "$(command -v trento)" "$HIC_MUSIC_EXEC" "$HIC_IS3D_EXEC" "$HIC_SMASH_EXEC"; do
    echo "Executable: $program"
    test -x "$program"
    libraries="$(ldd "$program")"
    printf '%s\n' "$libraries"
    if [[ "$libraries" == *"not found"* ]]; then
      echo "Missing runtime libraries for $program; fix before initialization." >&2
      exit 1
    fi
  done
  for input in "$HIC_IS3D_CONFIG" "$HIC_IS3D_CHOSEN_PARTICLES" \
      "$HIC_CHARGE_DICT" "$HIC_BARYON_DICT" "$HIC_PDG_MASS" \
      "$HIC_SMASH_CONFIG" "$HIC_IS3D_OSCAR_SCRIPT"; do
    echo "Input: $input"
    test -s "$input"
  done
  python tools/check_physics_data.py is3d "$HIC_IS3D_DELTAF_DIR/smash_pce_light_strange"
  python tools/check_physics_data.py eos "$HIC_EOS_PCE_DIR"
  python models/campaign.py --config "$CFG" events --print-config
  python tools/check_workflow.py
  echo 'Setup checks passed.'
)
```

Confirm the selected launcher, raw/archive roots and design table. The workflow creates per-design/event directories later. All checks must pass, but only step 6 tests model execution. Resolve missing permissions with the project owner.

## 6. Run the isolated two-event smoke campaign

Edit `$CFG` with these smoke resources; keep its physics inputs and two-event bound:

| INI section           | Smoke settings                                                                                                                         |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `[submit]`            | `qos = debug`, `time = 00:30:00`, `nodes = 1`, `ntasks_per_node = 2`, `cpus_per_task = 32`                                             |
| `[cache]`             | `qos = debug`, `time = 00:30:00`, `nprocs = 2`, `designs_per_task = 1`, `throttle = 1`, `cores = 2`, `jobs = 1`, `validate_events = 2` |
| `[analysis_campaign]` | `jobs = 1`, `bin_edges = 0 100`, `n_boot = 0`                                                                                          |

The single `0 100` bin avoids mostly empty centrality bins. The generated profile already sets generation and cache QOS/walltime and generation resources; adjust the remaining cache and analysis settings above. The two event workers each receive 32 logical CPUs (16 physical cores). The launcher sets OpenMP and Python thread limits automatically. MUSIC uses OpenMP; SMASH runs serially within each event. The [debug QOS has a 30-minute limit](https://docs.nersc.gov/jobs/policy/#perlmutter-cpu); the full chain may need a longer walltime in another QOS.

### Generate

Initialization runs TRENTo, and NERSC compiler wrappers target compute nodes, so run the submitting command in a compute step. From the activated login shell:

```bash
source "$HOME/pce-eventgen/config/users/$USER-smoke/activate.sh"  # adjust the checkout path
salloc --nodes 1 --qos debug --time 00:30:00 --constraint cpu --account "$PROJECT"
cd "$REPO"
source "$REPO/config/users/$USER-smoke/activate.sh"
# These srun flags apply only to initialization/submission; generation uses [submit] resources in "$CFG".
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores \
  python models/campaign.py --config "$CFG" events --submit
exit  # release the interactive allocation; the submitted batch job remains queued/running
```

The `srun` step initializes the inputs and submits the generation job. That batch job takes its two-worker resources from `[submit]` in `$CFG`; the generated launcher handles thread counts and core binding.

Run `events` without `--submit` first to inspect the requested generation resources. Read the printed `sbatch` command and check `--ntasks-per-node` and `--cpus-per-task` before submitting.

Record the **generation** job ID, which differs from the interactive allocation's ID. Inspect `_logs/slurm/` and `_logs/events/` below `$HIC_RAW_ROOT`, then check terminal counts:

```bash
squeue -u "$USER"
sacct -j 12345678 --format=JobID,State,ExitCode,Elapsed  # replace with the generation job ID
python models/campaign.py --config "$CFG" status --details
```

Continue only with two terminal slots, no blockers and at least one particle event for cache validation. If both are no-surface, use a fresh smoke root with a larger bound.

If a job fails or times out, inspect its logs and follow [generation recovery](../docs/troubleshooting.md#initialization-and-generation) before resuming. Once all writers have stopped, an initialized campaign can be [resubmitted directly from the login node](../docs/nersc-campaign.md#1-generate-or-resume-events) using `events --submit --allow-existing --no-initialize`; no allocation or `srun` is needed for that submission. Keep the initialized inputs and event bound unchanged; select a longer `[submit]` walltime/QOS if needed.

### Cache and review

The reaper's audit requires raw particle files to predate cache construction by 60 seconds, so wait that long after the last event finished:

```bash
sleep 60  # quiet period; no writers or transfers may remain
python models/campaign.py --config "$CFG" prepare-cache
python models/campaign.py --config "$CFG" cache           # preview
python models/campaign.py --config "$CFG" cache --submit  # batch submission
# Wait for the cache job (squeue/sacct with its job ID), then:
python models/campaign.py --config "$CFG" review --details
```

Expect one design with a clean cache verdict, eligible for optional reaping. A clean cache can hold fewer particle payloads than terminal slots when there are valid no-surface events. Stop at a quarantine or review status and inspect its record.

### Analyze

Analysis runs inside a CPU allocation, with the profile active in the compute shell:

```bash
source "$HOME/pce-eventgen/config/users/$USER-smoke/activate.sh"  # adjust the checkout path
salloc --account="$PROJECT" --constraint=cpu --qos=debug \
  --nodes=1 --ntasks=1 --cpus-per-task=2 --time=00:30:00
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores --pty bash
cd "$REPO"
source "$REPO/config/users/$USER-smoke/activate.sh"
python models/campaign.py --config "$CFG" analysis run           # preview
python models/campaign.py --config "$CFG" analysis run --submit  # extract
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis aggregate
python models/campaign.py --config "$CFG" validate
exit  # leave the compute shell and return to the allocation shell
```

After returning to the allocation shell's prompt, run `exit` there to release the allocation.

Expected outputs are the per-design `_observables.npz`/`.meta.json` artifact and a `model_matrix.npz` with one design row in `[pipeline] analysis_out_dir`, and a passing cumulant check. Sparse or undefined observables are expected from two events: this run checks the software interfaces, not the physics.

Reaping is optional and unnecessary for setup. If you test it, use only this disposable campaign, with no remaining writers, following the [campaign guide](../docs/nersc-campaign.md#4-review-then-optionally-reap).

## 7. Start the real campaign separately

Generate a new profile with a fresh raw root and the normal event bound:

```bash
python nersc/configure_user.py \
  --project "$PROJECT" --software-root "$SOFTWARE" --data-root "$DATA" \
  --runtime-gcc-module "$HIC_RUNTIME_GCC_MODULE" \
  --output-root "$OUTPUT" --raw-root "$WORK_SCRATCH/my_campaign" \
  --out-dir "$REPO/config/users/$USER-production"
source "$REPO/config/users/$USER-production/activate.sh"
```

Repeat section 5 for this profile and set the design range, resources and walltime. Give separate campaigns unique raw-root basenames because records/cache paths use them. Profiles cannot be overwritten by the helper; edit deliberately or choose a new directory. Never increase the initialized smoke tree's event bound. Continue with [campaign operations](../docs/nersc-campaign.md).
