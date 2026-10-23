# Get your first result

[Handbook](README.md) · [Troubleshooting](troubleshooting.md)

Aim first for one initialized design and two terminal hydro events. After
that, test caching and analysis, then create a separate production campaign.
Two events test software interfaces; they cannot validate physics or useful
centrality-dependent uncertainties. Full model builds and event runtime depend
strongly on hardware and inputs; measure a small run before estimating a campaign.
“Small” refers to the event count: this example still uses the full 300×300
AuAu grid. One local run took about 27 minutes for the two-event simulation
with two OpenMP threads, in addition
to building the software. Other machines and random collisions will differ.

Use Bash and run each command block in order, stopping if a command fails.
Keep the same shell open so exported paths remain available. Do not copy paths
containing `/path/to/` literally: replace them with locations on your machine.
Everyone starts with section 1. NERSC users then follow the Perlmutter setup
guide, which ends with its own two-event smoke campaign; local users continue
with sections 2 and 3.

## 1. Obtain code and physics data

Clone the repository together with its SMASH submodule:

```bash
git clone --recurse-submodules https://github.com/Duke-QCD/pce-eventgen.git pce-eventgen
cd pce-eventgen
export REPO="$PWD"
```

In an existing clone without SMASH, run `git submodule update --init models/SMASH`.
TRENTo, MUSIC and iS3D are customized copies stored in this repository; SMASH is
unmodified upstream commit `0985d6b32fb801404c933d70c86c23759d60ff1f`
(`SMASH-3.0-1-g0985d6b3`), pinned by the submodule. Fresh upstream TRENTo, MUSIC
or iS3D releases do not reproduce this workflow.

Two large production datasets are not stored in Git. Obtain them from the
maintainer; checked-in manifests record their exact checksums:

| Dataset | Files | Where it goes |
|---|---|---|
| MUSIC EOS 18 | `strange_30_10000/0.dat` … `29.dat`, 360 MB | Any directory; select it with `HIC_EOS_PCE_DIR` |
| iS3D two-occupancy delta-f tables | `smash_pce_light_strange/` (`F G betaV betabulk betapi c0`–`c4`), 812 MB | `models/iS3D/deltaf_coefficients/vh/` before installing iS3D, or an external iS3D data root |

You also need Pythia **8.309** (section 2), and for OO5360 only the approved
`o16_nleft_o13.hdf5` nuclear table and provenance sidecar. Start with AuAu200
if you do not have the nuclear table.

Check both datasets:

```bash
# Select the numbered EOS directory itself, not its parent EOS/.
export HIC_EOS_PCE_DIR=/path/to/approved/EOS/PCE/strange_30_10000
python3 tools/check_physics_data.py eos "$HIC_EOS_PCE_DIR"
python3 tools/check_physics_data.py is3d models/iS3D/deltaf_coefficients/vh/smash_pce_light_strange
```

Expected: every file matches its manifest. Resolve missing or mismatching data
before going further; never edit a manifest to make the check pass. The
single-occupancy coefficient generator under `models/iS3D/generate_delta_f_coefficients/`
cannot replace the two-occupancy production tables.

## 2. Choose your installation path

**NERSC users:** follow [the Perlmutter setup guide](../nersc/SETUP.md) from
here. It covers storage choices, modules, Pythia, the build, your personal
profile, and a two-event smoke campaign through caching and analysis.

**Local Linux users:** follow this section and [local installation](../local/README.md).
The scripts assume Bash and Unix tools; Windows users need a Linux environment.
Other platforms may need build adaptation. Before building, arrange a consistent
C/C++/Fortran compiler stack, CMake >= 3.16, Boost (filesystem, program_options,
system), HDF5 with C++ support, GSL >= 2, Eigen3, OpenMP, and `zstd`.
SMASH needs a C++17 compiler and Pythia 8.309 already installed.

For Ubuntu/Debian systems, an administrator can provide the native dependencies
with the following package set. This is separate from installing Python packages:

```bash
sudo apt-get update
sudo apt-get install build-essential gfortran cmake git \
  libboost-filesystem-dev libboost-program-options-dev libboost-system-dev \
  libhdf5-dev libgsl-dev libeigen3-dev zstd
```

Use your institution's modules/package equivalents elsewhere. Check
`cmake --version`, `g++ --version`, and `h5c++ -show`; HDF5's C++ interface
must be present. You do not need `sudo` if these dependencies are already installed.

Use an isolated environment. For example, with an available conda installation:

```bash
# Make `conda activate` available in this Bash session if it is not initialized.
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create --name pce-eventgen -c conda-forge \
  python=3.11 'numpy<2' scipy cython h5py eigen zstd
conda activate pce-eventgen
export PYTHONNOUSERSITE=1
export HIC_BUILD_JOBS=2
python -c 'import numpy, scipy, h5py; print("Python dependencies ready")'
```

This is a starting environment, not a locked dependency specification. Compiler,
Boost, HDF5, and GSL discovery must also succeed; the environment command alone
does not install every system build dependency. If your system already supplies
compatible Python packages, the venv route in the local guide is an alternative.
The installer requires the environment to be active and does not download Pythia.

If using a venv instead, create it with a Python that can already import NumPy,
SciPy and h5py, then activate it:

```bash
python3 -m venv --system-site-packages --without-pip /path/to/new/environment
source /path/to/new/environment/bin/activate
export PYTHONNOUSERSITE=1
export HIC_BUILD_JOBS=2
python -c 'import numpy, scipy, h5py; print("Python dependencies ready")'
```

Choose **one** environment route. When a venv sits on top of a conda Python,
the Python packages inherited by `--system-site-packages` come from that base
Python environment, not every environment on the machine.
The installer recognizes the active venv even if `CONDA_PREFIX` from its parent
conda environment is still exported; installed model files belong to the venv.

When using the Ubuntu/Debian **system** dependencies above, select their matching
compiler/HDF5 stack after activating either Python environment:

```bash
export CC=/usr/bin/gcc CXX=/usr/bin/g++ FC=/usr/bin/gfortran
export HDF5_ROOT="/usr/lib/$(/usr/bin/gcc -print-multiarch)/hdf5/serial"
unset CMAKE_PREFIX_PATH
ls "$HDF5_ROOT/include/H5Cpp.h" "$HDF5_ROOT/lib/libhdf5_cpp.so"
```

This HDF5 layout is specific to the Debian/Ubuntu package. On another system,
use the equivalent prefix with `include/H5Cpp.h` and `lib/libhdf5_cpp` and matching
compilers. Do not combine an old system compiler with libraries discovered from
an unrelated conda environment. Inspect TRENTo's `Found HDF5` line in the build
log; an error such as `__cxa_call_terminate@CXXABI_1.3.15` indicates a mismatched
C++ runtime. Correct the selections and move aside the generated TRENTo build
directory before retrying so CMake does not retain the old libraries.

Select your existing Pythia 8.309 installation:

```bash
export PYTHIA_CONFIG=/path/to/pythia8309/bin/pythia8-config
"$PYTHIA_CONFIG" --prefix
export PYTHIA_PREFIX="$("$PYTHIA_CONFIG" --prefix)"
grep '^#define PYTHIA_VERSION' "$PYTHIA_PREFIX/include/Pythia8/Pythia.h"
```

If Pythia 8.309 is not installed yet, build the exact source release first,
using the local recipe below. (The installer also finds a Pythia built under
`$REPO/models/SMASH/build-deps/pythia8309` automatically.) Obtain the Pythia **8.309** source tarball from the maintainer
or the [official release repository](https://gitlab.com/Pythia8/releases/-/tags/pythia8309),
and replace its filename in the extraction command. Skip this block when an
existing matching installation is available:

```bash
export PYTHIA_BUILD="$(mktemp -d /tmp/pythia8309-build.XXXXXX)"
export PYTHIA_PREFIX="$HOME/pce-dependencies/pythia8309"
tar -xf /path/to/pythia8309-source.tar.gz --strip-components=1 -C "$PYTHIA_BUILD"
cd "$PYTHIA_BUILD"
./configure --prefix="$PYTHIA_PREFIX" --cxx="$CXX" \
  --cxx-common='-std=c++17 -O3 -fPIC -pthread'
make -j"$HIC_BUILD_JOBS"
make install
cd "$REPO"
export PYTHIA_CONFIG="$PYTHIA_PREFIX/bin/pythia8-config"
grep '^#define PYTHIA_VERSION' "$PYTHIA_PREFIX/include/Pythia8/Pythia.h"
```

Use a new installation prefix if that one already holds another dependency
version. Keep the installed Pythia library and XML data after the build;
SMASH links to that installation.

Now build/install the models, retaining a log:

```bash
cd "$REPO"
set -o pipefail
./local/install -- -DPythia_CONFIG_EXECUTABLE="$PYTHIA_CONFIG" 2>&1 | tee install.log
```

`tee` shows build progress and saves it; `pipefail` preserves an installer
failure as the command's exit status. The install command must return
successfully before continuing. On failure,
read `install.log` starting at the first error. If a handed-over TRENTo `build/`
directory contains another machine's CMake cache, move that generated directory
aside before rebuilding; do not remove model source or physics inputs.

Expected installed commands are `trento`, `MUSIChydro`, `iS3D`, `smash`,
`run-events`, and `run-events-wrapper` in the environment's `bin/`. Libraries
live in `lib/`; tables and workflow support files live in `share/`. Rebuild a
single component with, for example, `./local/install iS3D`.

## 3. Run a local two-event example

Stay at the checkout root with the environment active. Select approved iS3D
data and a **new** writable run location. The example uses
[parameters.dat](../config/initialization/parameters.dat): design row 0 rendered
for AuAu200 exactly as a campaign renders it. Choose a new output directory for
every fresh test; do not reuse a failed partial run as a fresh initialization.

```bash
export HIC_SOFTWARE_ROOT="$(python -c 'import sys; print(sys.prefix)')"
# Start in a fresh shell/profile: stale HIC_* exports select old installations.
# Default: use the data installed with iS3D.
export HIC_IS3D_DATA_ROOT="$HIC_SOFTWARE_ROOT/share/iS3D"
# If using external data instead, change ONLY the preceding assignment to its
# approved root BEFORE sourcing the helper.
source "$HIC_SOFTWARE_ROOT/share/iS3D/paths.sh"
export HIC_SMASH_CONFIG="$REPO/config/config.yaml"
export EXAMPLE_ROOT="$REPO/example-output/smoke-local-01"
export HIC_SURFACE_ARCHIVE_ROOT="$EXAMPLE_ROOT/surface_archive"
export RUN="$EXAMPLE_ROOT/raw/smoke-local-01/design_000/AuAu200"
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=1

for executable in python trento MUSIChydro iS3D smash run-events-wrapper zstd; do
  command -v "$executable" || echo "MISSING executable: $executable"
done
for input in "$HIC_IS3D_CONFIG" "$HIC_IS3D_CHOSEN_PARTICLES" \
  "$HIC_CHARGE_DICT" "$HIC_BARYON_DICT" "$HIC_PDG_MASS" "$HIC_SMASH_CONFIG"; do
  test -r "$input" || echo "MISSING input: $input"
done
python tools/check_physics_data.py is3d "$HIC_IS3D_DELTAF_DIR/smash_pce_light_strange"
python tools/check_physics_data.py eos "$HIC_EOS_PCE_DIR"
```

Every executable should resolve inside the selected installation (except a
system-provided `zstd`), there should be no `MISSING` lines, and both data
checks should report verified files. The paths helper preserves explicit
exports: if it still shows old data paths, start with a clean profile instead
of changing only the root after sourcing it.

Initialize and run, stopping if initialization fails:

```bash
python models/initialize.py "$RUN" "$REPO/config/initialization/parameters.dat" 2 \
  "$REPO/config/initialization/music_input_template"
printf 'Event log: %s\n' "$RUN/events.log"
run-events-wrapper --mode both --nevents 2 --logfile "$RUN/events.log" "$RUN" \
  > "$RUN/console.log" 2>&1
```

Initialization creates `parameters.dat`, `music_input`, `initial.hdf`, and a
copy of the template. It refuses to overwrite an existing `initial.hdf`.
The wrapper selects the matching runner and static data without changing the
event working directory. Archives use campaign/design/system/event nesting
under the selected archive root.

The runner records stage commands and failures in `events.log`; `console.log`
captures SMASH/converter output that is not included in that log. A quiet terminal
does not mean the run is stuck. MUSIC and iS3D stdout are buffered while those
commands run, so there may be no new `events.log` lines until a stage finishes.
`--loglevel debug` also records their successful captured stdout after each
command returns; SMASH's output remains in `console.log`.
In a second terminal, use `tail -f` with the **actual
absolute filename** printed/set above; that terminal does not inherit `RUN`.
After running, inspect the log and event directories `0/`, `1/`:

- A particle event has `hydro.done`, `afterburner.done`, and exactly one
  nonempty `particle_lists.oscar` or `particle_lists.oscar.unfinished`.
  Its surface is archived and the local surface may have been removed.
- A valid zero-output event has a clean `hydro.no_surface` marker. It counts
  toward the hydro event target and has no particle list.
- A failure or lock is not completion. Follow [troubleshooting](troubleshooting.md)
  before resuming. Repeating `both` can skip existing hydro directories;
  repair/audit is required for failed partial states.

Keep the original logs before retrying. Reusing `--logfile "$RUN/events.log"`
overwrites that runner log, and `> "$RUN/console.log"` overwrites the console
capture. Choose new filenames for a recovery pass.

Check the terminal counts with the same survey used by campaigns:

```bash
python models/cache/survey_raw_designs.py \
  --raw-root "$EXAMPLE_ROOT/raw/smoke-local-01" --system AuAu200 \
  --target-events 2 --nprocs 1 --details --json "$EXAMPLE_ROOT/survey.json"
```

Expected: two terminal events and no blockers (`ready_to_cache` for this fresh
uncached sample). Stop here for a local event-generation smoke test. The next
section uses NERSC's scheduler and a different, campaign-rendered run; do not
point a new production profile at this standalone test's raw tree.

For a hydro-only study, select `--mode hydro` at the initial run, then use
`--mode afterburner` later with the **same** event bound. Restoring a missing
archived surface requires `--mode afterburner --restore-archives` and the
original archive root.

## 4. Next steps

Caching and analysis use NERSC's scheduler and storage; the
[Perlmutter setup guide](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign)
runs them on its own two-event campaign. Do not point a NERSC profile at this
local example's raw tree.

## 5. Move to production deliberately

Create a separate profile and raw root with the full event bound. Select rows,
account/resources, fingerprint and physics together.
Follow [campaign usage](nersc-campaign.md) for generation, repair/fill,
cache/review, optional raw deletion, and analysis. Preserve the exact initialized
bundles, software/data provenance, INI, fingerprint, manifests, and records.
