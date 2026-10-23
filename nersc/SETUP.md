# New-user setup on NERSC Perlmutter

This is the current CPU workflow: TRENTO → MUSIC → iS3D → SMASH, followed by
survey, cache/publish, reap, and analysis. Inference stays separate.

Each new user builds their own executables. Read-only physics data can be
shared within the same project or copied unchanged into another project.
The simulation prescriptions and acceptance settings are not changed by setup.

Use a Bash shell on a Perlmutter login node for steps 1–5. You need a NERSC
account, access to the project's storage, an authorized CPU allocation/account,
and the two external physics datasets from the maintainer. This guide does not
create those accounts or supply the data. Section 6's two-event smoke campaign
verifies your build, modules, Slurm submission and storage access end to end.

## 1. Agree on the storage locations

| Location | Contents | Example |
|---|---|---|
| Home | Complete source checkout, personal settings and small input templates | `$HOME/pce-eventgen` |
| Global Common software | Conda environment, model executables, libraries, Pythia | `/global/common/software/PROJECT/USER/pce-eventgen` |
| CFS static-data root | Approved `iS3D/` and `EOS/` physics data; reusable read-only | `/global/cfs/cdirs/PROJECT/production_data` |
| CFS writable-output root | Your surface archives, cache staging, published caches, records and analysis | `/global/cfs/cdirs/PROJECT/USER/pce-eventgen` |
| Scratch | Initialized designs, working event directories and event/Slurm logs | `$PSCRATCH/pce-eventgen` |

`PROJECT` is the account/allocation authorized for your jobs; `USER` is your
NERSC username. Ask the project owner to provide writable directories when
needed, and read/traverse access to approved shared software/data. Reading a
shared data tree does not require changing its permissions or ownership.

Global Common is for software and is read-only on compute nodes: install there
from a login node. CFS holds persistent project data; scratch is working space
subject to purging. Keep authoritative inputs and valuable results elsewhere.
See the official [Global Common](https://docs.nersc.gov/filesystems/global-common/),
[CFS](https://docs.nersc.gov/filesystems/community/), and
[Perlmutter scratch](https://docs.nersc.gov/filesystems/perlmutter-scratch/) guidance.

## 2. Obtain the checkout and production inputs

Put the current project checkout in your home directory, then define:

```bash
export REPO="$HOME/pce-eventgen"   # adjust to where you put this checkout
cd "$REPO"
export PROJECT=m2730              # replace with YOUR authorized allocation
export SOFTWARE="/global/common/software/$PROJECT/$USER/pce-eventgen"
export DATA="/global/cfs/cdirs/$PROJECT/production_data"
export OUTPUT="/global/cfs/cdirs/$PROJECT/$USER/pce-eventgen"
export WORK_SCRATCH="${PSCRATCH:-${SCRATCH:-}}"
: "${WORK_SCRATCH:?Set PSCRATCH or SCRATCH to your Perlmutter scratch directory}"
mkdir -p "$(dirname "$SOFTWARE")" "$OUTPUT"
```

Keep `SOFTWARE` in your own software area. To reuse a shared copy of the
production data instead of copying it, set `DATA` to the location the maintainer
gives you.

Clone with `git clone --recurse-submodules` (or run
`git submodule update --init models/SMASH` in an existing clone). TRENTo, MUSIC
and iS3D are customized copies in this repository; SMASH is the unmodified
upstream commit `0985d6b32fb801404c933d70c86c23759d60ff1f`
(SMASH-3.0-1-g0985d6b3) pinned by the submodule, and the installer refuses any
other revision.

The static-data root must have this layout:

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

Copy the complete approved `iS3D/` tree, including the other coefficient tables
it contains. Preserve the production two-occupancy tables; do not substitute
single-occupancy tables or regenerate coefficients using the archived generator.
SMASH includes sigma; its exclusion in iS3D is separate.

The MUSIC PCE data are **30 × 12,000,000 bytes = 360 MB**, externally managed.
The small checksum manifest is in Git, but these production binaries are not.
A source checkout alone is insufficient. Reuse readable production data, or have
the maintainer supply a copy into the selected data root. For an approved local source:

```bash
# Skip copying if DATA already points to the approved shared tree.
mkdir -p "$DATA/iS3D" "$DATA/EOS/PCE/strange_30_10000"
rsync -a /approved/source/iS3D/ "$DATA/iS3D/"
rsync -a /approved/source/EOS/PCE/strange_30_10000/ \
  "$DATA/EOS/PCE/strange_30_10000/"

python3 "$REPO/tools/check_physics_data.py" eos "$DATA/EOS/PCE/strange_30_10000"
python3 "$REPO/tools/check_physics_data.py" is3d "$DATA/iS3D/deltaf_coefficients/vh/smash_pce_light_strange"
```

The copy commands intentionally contain `/approved/source`: replace it with
the maintainer-provided source. They do not delete destination files. Both
checks must report verified files; they cover the EOS and the coefficient tables,
not the smaller iS3D parameter and particle files.

For AuAu200, standard nuclei need no external nucleus table. OO5360 additionally
needs the approved `o16_nleft_o13.hdf5` and provenance sidecar. Put them under
`$DATA/nuclear_tables/`, or set `HIC_O16_NUCLEUS_TABLE` in your personal
`nersc_paths.sh` to the approved table's location; both nuclei use that table.
Generated profiles start with AuAu200; changing to OO also requires its
matching fingerprint and analysis settings, as described in the
[campaign guide](../docs/nersc-campaign.md#configuration-and-selection).

## 3. Build your own installation

Use a consistent GNU compiler/dependency stack. Check available versions with
`module avail`/`module spider`; project-provided modules may need an initial
`module use` supplied by the maintainer. A starting recipe is:

```bash
module load cpu PrgEnv-gnu cmake gsl boost cray-hdf5 conda
conda create --prefix "$SOFTWARE" -c conda-forge \
  python=3.11 'numpy<2' scipy cython h5py eigen zstd
conda activate "$SOFTWARE"
export PYTHONNOUSERSITE=1
export CC=cc CXX=CC FC=ftn
# Select the loaded GNU HDF5 before conda's transitive HDF5 dependency.
export HDF5_ROOT="${HDF5_DIR:?Inspect module show cray-hdf5 and set HDF5_DIR to its prefix}"
export CMAKE_PREFIX_PATH="$HDF5_ROOT:$CONDA_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
test -r "$HDF5_ROOT/include/H5Cpp.h"  # must succeed before continuing
export HIC_BUILD_JOBS=2
```

The Python/NumPy versions above are a conservative starting environment, not a
replacement for a maintainer-tested package lock. Keep build/runtime module
versions consistent. Save `module -t list` and `conda list --explicit` with your
installation. NERSC documents [custom conda environments](https://docs.nersc.gov/development/languages/python/nersc-python/)
in Global Common and [compiler wrappers](https://docs.nersc.gov/development/compilers/wrappers/)
`cc`, `CC`, `ftn`. The workflow is CPU-only; GPU modules are not required.

Prerequisites include Boost filesystem/program_options/system, GSL >= 2,
Eigen3, and HDF5 **C++** headers/libraries. TRENTO must build with HDF5 support;
an executable built without it is not sufficient for initialization. If HDF5
is not found, inspect `module show cray-hdf5`, select that installation with
`HDF5_ROOT`, and confirm CMake reports `Found HDF5:` with its C++ and C libraries.
Use a GNU-compatible installation that includes `H5Cpp.h` and `libhdf5_cpp`.
The compiler and HDF5 must agree: activating conda installs another HDF5 through
`h5py`, so a successful package installation alone does not settle the native
library choice. After configuration, inspect `HDF5_INCLUDE_DIRS`, `HDF5_CXX_LIB`
and `HDF5_C_LIB` in `models/trento/build/CMakeCache.txt` if discovery is unclear.
The [NERSC HDF5 guide](https://docs.nersc.gov/development/libraries/hdf5/)
describes the serial `cray-hdf5` module used here.

SMASH additionally requires **Pythia 8.309**, built with the same GNU compiler
stack. Obtain that exact release's source from the maintainer or the
[official release archive](https://pythia.org/releases/). The checked-in container
recipe uses the corresponding pinned GitLab release archive; an equivalent
standalone build is:

```bash
export DEPENDENCIES="${SOFTWARE}-deps"
export BUILD="$WORK_SCRATCH/pce-build"
mkdir -p "$BUILD" "$DEPENDENCIES"
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

Build from the approved source archive instead if outbound downloads are
unavailable. The build directory is disposable; the installed dependency
prefix is not: installed SMASH links to its library. The shared model installer
does not download Pythia. Do not substitute a newer Pythia version.

```bash
export HIC_EOS_PCE_DIR="$DATA/EOS/PCE/strange_30_10000"
export HIC_MUSIC_INSTALL_PCE_DATA=OFF  # reuse the separately managed CFS EOS

cd "$REPO"
set -o pipefail
./nersc/install -- -DPythia_CONFIG_EXECUTABLE="$PYTHIA_CONFIG" 2>&1 | tee "$BUILD/install.log"
```

Run installation from a login node because Global Common is read-only on
compute nodes. Keep build parallelism modest and follow the site's compilation
policy. The installer uses separate MUSIC/iS3D/SMASH workflow build directories
and reuses them on rebuild;
if a handed-over TRENTO `build/` cache refers to somebody else's checkout,
rename that generated directory before rebuilding TRENTO. Keep source/physics
inputs intact. For a different compiler stack, use clean build directories.
Stop if installation returns nonzero; keep `$BUILD/install.log` for diagnosis.
Save the selected module versions and package list somewhere durable, for example
`module -t list 2> "$OUTPUT/build-modules.txt"` and
`conda list --explicit > "$OUTPUT/build-conda-explicit.txt"`.

After installation, `bin/` contains the runner pair and four executables;
`lib/` contains their installed shared libraries; `share/` contains co-located
iS3D data and conversion/configuration files. This guide deliberately selects
the approved external CFS iS3D/EOS data for runtime. Installing a fresh binary
does not replace those physics inputs. See [local installation details](../local/README.md).

## 4. Generate your profile

`nersc/configure_user.py` renders the four files in
[`config/templates/`](../config/templates/) with your paths and account into a
**new** profile directory. It does not create CFS/scratch directories, install
software, copy data, initialize events, or submit anything.

For the first test, use a separate two-event scratch campaign. Select the GCC
runtime module matching your build; `gcc-native/14` below is an example:

```bash
cd "$REPO"
python3 nersc/configure_user.py \
  --project "$PROJECT" --software-root "$SOFTWARE" --data-root "$DATA" \
  --runtime-gcc-module gcc-native/14 \
  --output-root "$OUTPUT" --raw-root "$WORK_SCRATCH/pce-setup-smoke" \
  --out-dir "$REPO/config/users/$USER-smoke" --nevents 2

source "$REPO/config/users/$USER-smoke/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
```

If your shell exposes only `SCRATCH`, use that value for `--raw-root`. When
`--raw-root` is omitted, the helper accepts either variable. `--software-root`
and `--output-root` also have the per-project/per-user defaults from step 1.
`config/users/` is ignored by Git, so profiles stay private to your checkout.

The helper also saves an explicit surface-archive source base in `[repair]`
`archive_base` and `HIC_SURFACE_ARCHIVE_BASE`. By default it selects the
`PSCRATCH`/`SCRATCH` root containing the raw campaign, or the raw root's parent
outside scratch. This selection happens once when the profile is generated.
For an existing campaign, pass `--archive-base /original/scratch/root` using the
base that produced its current archive layout. The archive destination remains
`HIC_SURFACE_ARCHIVE_ROOT`; the base determines the relative path beneath it.

| Generated file | Purpose |
|---|---|
| `nersc_paths.sh` | Your runtime paths: software prefix, physics data, archives, caches, GCC runtime module |
| `pce-eventgen` | Your Slurm generation launcher, with your account and log directory |
| `calibration.ini` | The campaign configuration every `campaign.py` command reads |
| `activate.sh` | Selects the profile: sources the paths file, activates the conda prefix, loads CPU/GSL modules |

The INI's `[files] launcher` names the launcher. The allocation is set in the
launcher's `#SBATCH --account` and in `[cache] account`.

The generated INI starts at **row 0 only**, initializes new designs, refuses to
touch existing design directories unless you pass `--allow-existing`, and
applies repairs and archive finalization when a command acts. Its sampling,
cache limits and analysis settings are the standard ones. It uses the
500-row design table,
[`config/initialization/design_params_combined500.dat`](../config/initialization/design_params_combined500.dat),
unless you pass `--design-file`. Cache cuts and provenance settings come from
the INI's fingerprint; no other environment file is needed.

`--runtime-gcc-module` saves `HIC_RUNTIME_GCC_MODULE` in the generated
paths file, so activation, batch generation and fill use the same selection.
When omitted, it captures the current `HIC_RUNTIME_GCC_MODULE`, or defaults to
`gcc-native/14` if unset. It does not detect your compiler version. Pass
`--runtime-gcc-module ''` if you deliberately load the required runtime yourself;
this disables the event wrapper's additional module load.
The generated files load only conda/CPU/GSL, so add any other modules required
by your particular build to both `activate.sh` and `pce-eventgen`.

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
  for program in "$HIC_MUSIC_EXEC" "$HIC_IS3D_EXEC" "$HIC_SMASH_EXEC"; do
    echo "Executable: $program"
    test -x "$program"
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

The generated `HIC_RAW_ROOT` is the exact `[run] directory`. The `mkdir` commands
are directory preparation only; the workflow creates per-design and per-event
subdirectories. Confirm the resolved settings show your launcher, scratch root,
archive root, and selected design table. If a project path is not writable, ask
the owner to provide your directory; do not alter another user's tree.
These are import and path checks, not a model execution test.

## 6. Run the isolated two-event smoke campaign

This section takes the smoke profile from section 4 through generation,
caching and analysis. Edit the generated `$CFG` first. These settings keep the
physics inputs and the fixed event bound while reducing resource requests:

| INI section | Smoke settings |
|---|---|
| `[submit]` | `nodes = 1`, `ntasks_per_node = 1`, `cpus_per_task = 2`, `time = 01:00:00` |
| `[cache]` | `nprocs = 2`, `designs_per_task = 1`, `throttle = 1`, `cores = 2`, `jobs = 1`, `validate_events = 2` |
| `[analysis_campaign]` | `jobs = 1`, `bin_edges = 0 100`, `n_boot = 0` |

A single `0 100` bin keeps two events from being split among nine mostly empty
centrality bins. The template otherwise keeps production requests, including a
ten-minute generation walltime. Two real local events took about 27 minutes
with two OpenMP threads; Perlmutter timing is not measured here. Choose
adequate walltime and follow your project's queue policy.

### Generate

Initialization runs TRENTo, and NERSC compiler wrappers target compute nodes,
so run the submitting command in a compute step. From the activated login shell:

```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint cpu --account "$PROJECT"
cd "$REPO"
source "$REPO/config/users/$USER-smoke/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores \
  python models/campaign.py --config "$CFG" events --submit
exit  # release the interactive allocation; the submitted batch job remains queued/running
```

Run `events` without `--submit` first if you want to see the preview. The
submitted job inherits the thread settings; match `OMP_NUM_THREADS` to
`[submit] cpus_per_task` when you change the generation layout.

Record the **generation** job ID printed as `Submitted batch job ...`; it differs
from the interactive allocation's ID. Back on the login shell, use it in the
scheduler checks below (replace `12345678`). Inspect stdout and stderr under
`$HIC_RAW_ROOT/_logs/slurm/` and the event logs under `_logs/events/`. A job
leaving the queue or exiting 0 does not prove its events finished; the status
command does.

```bash
squeue -u "$USER"
sacct -j 12345678 --format=JobID,State,ExitCode,Elapsed
python models/campaign.py --config "$CFG" status --details
```

Continue when both event slots are terminal, no locks or blockers remain, and
at least one event has particles (the cache's raw-reference validation needs
one; if both are no-surface events, start a fresh smoke root with a larger event
bound rather than changing this one). Do not rerun `events --submit` just
because submission returned. To resume an interrupted design after checking
status, use `events --submit --allow-existing`; it keeps the existing initial
conditions.

### Cache and review

The reaper's audit requires raw particle files to predate cache construction by
60 seconds, so wait that long after the last event finished:

```bash
sleep 60  # quiet period; no writers or transfers may remain
python models/campaign.py --config "$CFG" prepare-cache
python models/campaign.py --config "$CFG" cache           # preview
python models/campaign.py --config "$CFG" cache --submit  # batch submission
# Wait for the cache job (squeue/sacct with its job ID), then:
python models/campaign.py --config "$CFG" review --details
```

Expect one design with a clean cache verdict, eligible for optional reaping. A
clean cache can hold fewer particle payloads than terminal slots when there
are valid no-surface events. Stop at a quarantine or review status and inspect
its record.

### Analyze

Analysis runs inside a CPU allocation, with the profile active in the compute shell:

```bash
salloc --account="$PROJECT" --constraint=cpu --qos=interactive \
  --nodes=1 --ntasks=1 --cpus-per-task=2 --time=00:30:00
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores --pty bash
cd "$REPO"
source "$REPO/config/users/$USER-smoke/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
python models/campaign.py --config "$CFG" analysis run           # preview
python models/campaign.py --config "$CFG" analysis run --submit  # extract
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis aggregate
python models/campaign.py --config "$CFG" validate
exit; exit  # leave the compute shell, then release the allocation
```

Expected outputs are the per-design `_stageA.npz`/`.meta.json` artifact and a
`model_matrix.npz` with one design row in `[pipeline] analysis_out_dir`, and a
passing cumulant check. Sparse or undefined observables are expected from two
events: this run checks the software interfaces, not the physics.

Reaping is optional and unnecessary for setup. If you test it, use only this
disposable campaign, with no remaining writers, following the
[campaign guide](../docs/nersc-campaign.md#4-review-then-optionally-reap).

## 7. Start the real campaign separately

Generate a new profile with a fresh raw root and the normal event bound:

```bash
python nersc/configure_user.py \
  --project "$PROJECT" --software-root "$SOFTWARE" --data-root "$DATA" \
  --runtime-gcc-module "$HIC_RUNTIME_GCC_MODULE" \
  --output-root "$OUTPUT" --raw-root "$WORK_SCRATCH/my_campaign" \
  --out-dir "$REPO/config/users/$USER-production"
source "$REPO/config/users/$USER-production/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
```

Create its writable directories as in section 5. Edit `[run] row_start`,
`row_end`, resources and walltime before submitting. The helper refuses to
overwrite an existing profile: edit it in place for an intentional change, or
choose a new `--out-dir`. An initialized design keeps its original `nevents`;
never turn the two-event smoke tree into a 10,000-event campaign by editing its
INI. The helper names cache and record directories after the raw root's
basename, so give separate campaigns distinct basenames. Continue with the
[campaign guide](../docs/nersc-campaign.md), including `fill-plan` and `fill`
for nearly complete designs.

Before resuming someone else's campaign, obtain its exact design table,
initialized bundles, cache fingerprint, archive root, original archive base and
cache/reap history. Keep the base unchanged when handing a campaign to another
operator; their scratch variables must not change existing archive locations.
Follow the [archive migration instructions](../docs/reference/files.md#surface-archive)
for older profiles. A fresh profile does not migrate or reconstruct that history.

## Updating an installation

After pulling a new checkout, rerun `./nersc/install` (it reinstalls the event
runner and converter with the models). Existing profiles are not rewritten; if
`config/templates/` changed, compare your profile's files with it, or generate
a new profile.
