# Get your first result

[Handbook](README.md) · [Troubleshooting](troubleshooting.md)

Start with one design and two terminal events, then test caching/analysis before creating a production campaign. This checks software interfaces, not physics or centrality-dependent uncertainties. The example uses the full 300×300 AuAu grid; measure its runtime on your system before sizing a campaign.

Begin in a Bash shell without another campaign profile loaded. Replace example paths, keep the same shell open and stop on any failed command. NERSC users branch to the Perlmutter guide after cloning; local Linux users complete this page.

## 1. Obtain code and physics data

Clone the repository together with its SMASH submodule:

```bash
git clone --recurse-submodules https://github.com/Duke-QCD/pce-eventgen.git pce-eventgen
cd pce-eventgen
export REPO="$PWD"
```

For an existing clone, run `git submodule update --init models/SMASH`. TRENTo, MUSIC and iS3D are customized source trees; SMASH is pinned to upstream commit `0985d6b32fb801404c933d70c86c23759d60ff1f` (`SMASH-3.0-1-g0985d6b3`). Fresh upstream model releases do not reproduce this workflow.

**NERSC users:** continue with [Perlmutter setup](../nersc/SETUP.md) now. It downloads the same data directly into CFS and includes its own two-event test. Skip the local download/build instructions below.

Download the frozen EOS/iS3D runtime inputs with Python 3.8 or later; no additional Python packages are needed:

```bash
export PHYSICS_DATA="$HOME/pce-eventgen-data"
python3 tools/download_physics_data.py --directory "$PHYSICS_DATA"
export HIC_EOS_PCE_DIR="$PHYSICS_DATA/EOS/PCE/strange_30_10000"
```

The command downloads one archive, checks its extracted EOS/iS3D runtime inputs, and retains the included attribution/license notices automatically. Allow about 1.2 GB for unpacked inputs plus space for the compressed download. Repeating the command checks and reuses the same data. It refuses to replace mismatched runtime data; choose a new directory to preserve a different dataset. No private group storage or contact with the maintainer is needed. [Offline installation and release preparation](../data/README.md) are documented separately.

| Dataset | Files | Where it goes |
|---|---|---|
| MUSIC EOS 18 | `strange_30_10000/0.dat` … `29.dat`, 360 MB | `$PHYSICS_DATA/EOS/PCE/strange_30_10000/` |
| iS3D runtime data | Configuration, PDG/quadrature tables and coefficients, including the ten two-occupancy tables (812 MB) | `$PHYSICS_DATA/iS3D/` |

You also need Pythia **8.309** (section 2). Start with AuAu200: the download includes everything needed for its EOS/iS3D data. OO5360 additionally requires the approved `o16_nleft_o13.hdf5` nuclear table and provenance sidecar, which are not part of this release.

## 2. Choose your installation path

**Local Linux users:** continue below. You need Bash/Unix tools (a Linux environment on Windows), GNU compilers with C++17 support, CMake >= 3.16 and < 4, Boost 1.74–1.81 filesystem/program_options/system, HDF5 C++, GSL 2.x, Eigen 3.4, OpenMP and `zstd`. See the [dependency version reference](../local/README.md#dependency-versions) for compatibility limits. Other platforms may need build adaptation.

Use the downloaded data directly for the local example:

```bash
export HIC_IS3D_DATA_ROOT="$PHYSICS_DATA/iS3D"
export HIC_MUSIC_INSTALL_PCE_DATA=OFF
```

Keep the download in place after building. The runtime uses it without copying the large datasets into the checkout or software environment. An iS3D build warning about absent source-tree coefficients is expected with this external data selection.

For Ubuntu/Debian systems, an administrator can provide the native dependencies with the following package set. This is separate from installing Python packages:

```bash
sudo apt-get update
sudo apt-get install build-essential gfortran cmake git \
  libboost-filesystem-dev libboost-program-options-dev libboost-system-dev \
  libhdf5-dev libgsl-dev libeigen3-dev zstd
```

Use your institution's modules/package equivalents elsewhere. Distribution packages vary: Ubuntu 22.04's native versions fit this baseline; newer distributions may supply CMake 4, Boost >= 1.82 or Eigen 5. The Conda recipe below supplies compatible CMake/Boost/Eigen even on those systems. If using only system dependencies with a venv, select versions within the reference ranges first. HDF5's C++ interface must be present. You do not need `sudo` if these dependencies are already installed.

Use an isolated environment. For example, with an available conda installation:

```bash
# Make `conda activate` available in this Bash session if it is not initialized.
source "$(conda info --base)/etc/profile.d/conda.sh"
conda create --name pce-eventgen --override-channels -c conda-forge \
  python=3.11 numpy=1.26.4 'scipy=1.14.*' 'h5py=3.15.*' 'hdf5=1.14.*' \
  'cmake>=3.16,<4' 'boost-cpp=1.81.*' 'eigen=3.4.*' zstd
conda activate pce-eventgen
export PYTHONNOUSERSITE=1
export HIC_BUILD_JOBS=2
python -c 'import numpy, scipy, h5py; print("NumPy", numpy.__version__, "SciPy", scipy.__version__, "h5py", h5py.__version__)'
cmake --version
```

This environment does not supply the native compiler or GSL. The recipe below uses system HDF5 for TRENTo and builds Pythia 8.309 separately.

If using a venv instead, first provide the native versions from the reference, then create it with a Python that can already import compatible NumPy, SciPy and h5py packages:

```bash
python3 -m venv --system-site-packages --without-pip /path/to/new/environment
source /path/to/new/environment/bin/activate
export PYTHONNOUSERSITE=1
export HIC_BUILD_JOBS=2
python -c 'import numpy, scipy, h5py; print("Python dependencies ready")'
```

Choose one route. A venv inherits packages from its base Python, not other environments. The installer selects the active venv even when its parent conda environment still exports `CONDA_PREFIX`.

Select the native compilers before building Pythia or the models. Preserve any site-provided selections; otherwise use the standard GNU commands:

```bash
export CC="${CC:-gcc}" CXX="${CXX:-g++}" FC="${FC:-gfortran}"
```

When using the Ubuntu/Debian **system** HDF5 above, select its matching compiler after activating either Python environment:

```bash
export CC=/usr/bin/gcc CXX=/usr/bin/g++ FC=/usr/bin/gfortran
export HDF5_ROOT="/usr/lib/$(/usr/bin/gcc -print-multiarch)/hdf5/serial"
unset CMAKE_PREFIX_PATH
ls "$HDF5_ROOT/include/H5Cpp.h" "$HDF5_ROOT/lib/libhdf5_cpp.so"
# For the Conda recipe, keep Boost headers and libraries in that environment.
if test -z "${VIRTUAL_ENV:-}" && test -f "$CONDA_PREFIX/include/boost/version.hpp"; then
  export BOOST_ROOT="$CONDA_PREFIX"
fi
"$CXX" --version
gsl-config --version
```

Other systems need their equivalent HDF5 C++ prefix and matching compiler. Inspect TRENTo's `Found HDF5` build-log entry. If discovery mixes system and conda libraries, correct the stack and retry; the installer refreshes cached paths automatically. See [link-error recovery](troubleshooting.md#installation-and-startup).

Build Pythia **8.309** with the same compiler stack, using the [official source archive](https://gitlab.com/Pythia8/releases/-/tags/pythia8309):

```bash
export PYTHIA_BUILD="$(mktemp -d /tmp/pythia8309-build.XXXXXX)"
export PYTHIA_PREFIX="$HOME/pce-dependencies/pythia8309"
curl --fail --location --output "$PYTHIA_BUILD/pythia8309.tgz" \
  https://gitlab.com/Pythia8/releases/-/archive/pythia8309/releases-pythia8309.tar.gz
tar -xf "$PYTHIA_BUILD/pythia8309.tgz" --strip-components=1 -C "$PYTHIA_BUILD"
cd "$PYTHIA_BUILD"
./configure --prefix="$PYTHIA_PREFIX" --cxx="$CXX" \
  --cxx-common='-std=c++17 -O3 -fPIC -pthread'
make -j"$HIC_BUILD_JOBS"
make install
cd "$REPO"
export PYTHIA_CONFIG="$PYTHIA_PREFIX/bin/pythia8-config"
grep '^#define PYTHIA_VERSION' "$PYTHIA_PREFIX/include/Pythia8/Pythia.h"
```

Use a new installation prefix if that one already holds another dependency version. Keep the installed Pythia library and XML data after the build; SMASH links to that installation.

Now build/install the models, retaining a log:

```bash
cd "$REPO"
set -o pipefail
./local/install -- -DPythia_CONFIG_EXECUTABLE="$PYTHIA_CONFIG" 2>&1 | tee install.log
```

`pipefail` preserves installer failures while `tee` saves the log. Continue only on success; diagnose the first error in `install.log`.

Expect `trento`, `MUSIChydro`, `iS3D`, `smash`, `run-events` and `run-events-wrapper` in the environment's `bin/`. See [installed layout and component rebuilds](../local/README.md).

## 3. Run a local two-event example

At the checkout root with the environment active, select the downloaded iS3D data and a fresh output directory. The example [parameters.dat](../config/initialization/parameters.dat) is AuAu200 design row 0. Use a new directory for each fresh test; interrupted runs need recovery rather than reinitialization.

```bash
export HIC_SOFTWARE_ROOT="$(python -c 'import sys; print(sys.prefix)')"
export HIC_IS3D_DATA_ROOT="$PHYSICS_DATA/iS3D"
source "$HIC_IS3D_DATA_ROOT/paths.sh"
export HIC_SMASH_CONFIG="$REPO/config/config.yaml"
export EXAMPLE_ROOT="$REPO/example-output/smoke-local-01"
export HIC_SURFACE_ARCHIVE_ROOT="$EXAMPLE_ROOT/surface_archive"
export HIC_SURFACE_ARCHIVE_BASE="$EXAMPLE_ROOT/raw"
export RUN="$EXAMPLE_ROOT/raw/smoke-local-01/design_000/AuAu200"
export OMP_NUM_THREADS=2
export OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1

(
  set -e
  for executable in python trento MUSIChydro iS3D smash run-events-wrapper zstd; do
    command -v "$executable"
  done
  for input in "$HIC_IS3D_CONFIG" "$HIC_IS3D_CHOSEN_PARTICLES" \
    "$HIC_CHARGE_DICT" "$HIC_BARYON_DICT" "$HIC_PDG_MASS" "$HIC_SMASH_CONFIG"; do
    echo "Input: $input"
    test -r "$input"
  done
  python tools/check_physics_data.py is3d "$HIC_IS3D_DELTAF_DIR/smash_pce_light_strange"
  python tools/check_physics_data.py eos "$HIC_EOS_PCE_DIR"
)
```

Every check must succeed, with executables in the selected installation (except system `zstd`). The block stops at its first failure. The paths helper preserves prior exports; if it selects old data, correct those exports before continuing.

Initialize and run, stopping if initialization fails:

```bash
python models/initialize.py "$RUN" "$REPO/config/initialization/parameters.dat" 2 \
  "$REPO/config/initialization/music_input_template"
printf 'Event log: %s\n' "$RUN/events.log"
run-events-wrapper --mode both --nevents 2 --logfile "$RUN/events.log" "$RUN" \
  > "$RUN/console.log" 2>&1
```

Initialization creates the input bundle and refuses to replace `initial.hdf`. The wrapper runs MUSIC, iS3D and SMASH using it; surface archives use the [configured mirrored layout](reference/files.md#surface-archive).

`events.log` records stages/failures; `console.log` captures SMASH/converter output. MUSIC/iS3D output is buffered until each command returns, so quiet periods are normal. `--loglevel debug` also records their successful stdout. In another terminal, use `tail -f /actual/absolute/log/path`; it does not inherit `RUN`. Inspect event directories `0/` and `1/`:

- A particle event has `hydro.done`, `afterburner.done`, and exactly one nonempty `particle_lists.oscar` or `particle_lists.oscar.unfinished`. Its surface is archived and the local surface may have been removed.
- A valid zero-output event has a clean `hydro.no_surface` marker. It counts toward the hydro event target and has no particle list.
- A failure or lock is not completion. Follow [troubleshooting](troubleshooting.md) before resuming. Repeating `both` can skip existing hydro directories; repair/audit is required for failed partial states.

Keep the original logs before retrying. Reusing `--logfile "$RUN/events.log"` overwrites that runner log, and `> "$RUN/console.log"` overwrites the console capture. Choose new filenames for a recovery pass.

Check the terminal counts with the same survey used by campaigns:

```bash
python models/cache/survey_raw_designs.py \
  --raw-root "$EXAMPLE_ROOT/raw/smoke-local-01" --system AuAu200 \
  --target-events 2 --nprocs 1 --details --json "$EXAMPLE_ROOT/survey.json"
```

Expect two terminal events and no blockers (`ready_to_cache`). This completes the local generation test; keep its raw tree separate from NERSC profiles and production.

For a hydro-only study, select `--mode hydro` at the initial run, then use `--mode afterburner` later with the **same** event bound. Restoring a missing archived surface requires `--mode afterburner --restore-archives` and the original archive root.

## 4. Next steps

Caching and analysis use NERSC's scheduler and storage; the [Perlmutter setup guide](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign) runs them on its own two-event campaign. Do not point a NERSC profile at this local example's raw tree.

## 5. Move to production deliberately

Create a separate profile and raw root with the full event bound. Select rows, account/resources, fingerprint and physics together. Follow [campaign usage](nersc-campaign.md) for generation, repair/fill, cache/review, optional raw deletion, and analysis. Preserve the exact initialized bundles, software/data provenance, INI, fingerprint, manifests, and records.
