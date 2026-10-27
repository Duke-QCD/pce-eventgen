# Local installation reference

For dependencies, environment creation, Pythia 8.309 and a complete two-event run, follow the [first-run tutorial](../docs/getting-started.md). This page covers installer options and installed files.

## Dependency versions

Use this baseline for the model chain, staging, caching and analysis. These are setup recommendations for this checkout, not a claim that every other version fails. Inference has its own [requirements](../inference/requirements.txt).

| Dependency | Selection and reason |
|---|---|
| Python | 3.11 for a new environment; the standalone data downloader and OSG helpers also support 3.8+ |
| NumPy / SciPy / h5py | New local/NERSC environments use NumPy 1.26.4, SciPy 1.14.x and h5py 3.15.x, with HDF5 1.14.x supplied by Conda. Install them together from one channel; do not mix old binary packages or user-site packages. Cython is not needed to run or build this workflow |
| CMake | 3.16 or later, **below 4**. [CMake 4 removed support for policy versions below 3.5](https://cmake.org/cmake/help/v4.0/release/4.0.html#deprecated-and-removed-features), used by these older model build files |
| Boost | Prefer 1.74–1.81, including filesystem, program_options and system from one installation. TRENTo builds as C++11; [Boost.Math requires C++14 from 1.82](https://www.boost.org/releases/1.82.0/). Boost 1.89 also [removed the compiled System library](https://www.boost.org/releases/1.89.0/), which TRENTo's finder requests. The Conda recipes select `boost-cpp=1.81.*` |
| Eigen | 3.4.x. Pinned SMASH reads version macros from `Macros.h`; Eigen 5 [moved them to `Eigen/Version`](https://gitlab.com/libeigen/eigen/-/raw/5.0.0/Eigen/Version) |
| GSL | 2.x, at least 2.0, including its CBLAS library and `gsl-config`. Select headers and libraries from the same installation |
| Native HDF5 | Use the C++ interface from a matching GNU stack; 1.10, 1.12 or 1.14 are the setup baseline. `H5Cpp.h`, `libhdf5_cpp` and `libhdf5` must belong to that installation. HDF5 2.x is outside this baseline. h5py may use its own HDF5 installation: it runs in a separate process from TRENTo |
| Compiler / OpenMP | GNU C/C++ with C++17 support (GCC >= 8; GCC 11–14 is the recommended starting range), with the matching OpenMP and C++ runtimes. On NERSC use `PrgEnv-gnu` and its `cc`/`CC` wrappers; use the same runtime stack for Pythia and jobs |
| Pythia | **8.309 exactly**, required by pinned SMASH. Retain its installed library and XML data |
| Other libraries | Use system `zstd` 1.x. SMASH already bundles YAML-CPP 0.7.0, Cuba 4.2.1 and Einhard; no separate installation is needed. ROOT, HepMC and Rivet are disabled in the workflow builds |

The OSG image uses Rocky Linux 9's compiler/CMake/native-library stack and Python packages selected for its Python version. Its AP environment only needs NumPy/h5py; it does not build models. Keep the image and AP recipes in [OSG setup](../osg/README.md).

## Installation

From the repository root with the intended venv or conda environment active:

```bash
./local/install -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

With no model names, the installer builds TRENTo, MUSIC, iS3D and SMASH. It requires their source trees, native dependencies and approved physics data described in [setup](../docs/getting-started.md#1-obtain-code-and-physics-data). It does not download Pythia. On NERSC, `nersc/install` uses the same builder with the loaded toolchain.

| Control | Purpose |
|---|---|
| Model names, e.g. `./local/install iS3D` | Build selected components only |
| Arguments after `--` | Additional SMASH CMake options |
| `HIC_BUILD_JOBS` | Limit parallel compilation |
| `HIC_TRENTO_BUILD_DIR`, `HIC_MUSIC_BUILD_DIR`, `HIC_IS3D_BUILD_DIR`, `HIC_SMASH_BUILD_DIR` | Override the corresponding `build-workflow/` directory |
| `HIC_EOS_PCE_DIR` | Select numbered EOS tables for MUSIC installation and runtime |
| `HIC_MUSIC_INSTALL_PCE_DATA=OFF` | Reuse external EOS data without installing another copy |

All four models build in `build-workflow/`. Dependency paths are rediscovered on each run; changed source locations, environments or toolchains trigger a CMake reset automatically.

TRENTo records its HDF5 runtime library paths and checks the installed executable before installation completes. Keep the selected HDF5 installation available at runtime.

### iS3D

```bash
./local/install iS3D
source "$(python -c 'import sys; print(sys.prefix)')/share/iS3D/paths.sh"
```

The CPU build installs `bin/iS3D`, `lib/libiS3D_lib.so` and `share/iS3D/`. The first-run tutorial selects the complete downloaded data tree with `HIC_IS3D_DATA_ROOT`; source its `paths.sh` when using that external selection. To install a second copy of the production coefficients in the environment instead, place them in `models/iS3D/deltaf_coefficients/vh/smash_pce_light_strange/` before building. The [iS3D guide](../models/iS3D/README.md) covers standalone sampling, data paths and supported PCE modes.

### SMASH

```bash
./local/install SMASH -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

The installer requires the pinned SMASH submodule and Pythia 8.309. Selection precedence is an explicit `-DPythia_CONFIG_EXECUTABLE` or `-DPYTHIA_ROOT_DIR` after `--`, then `PYTHIA_CONFIG`, then `PYTHIA_ROOT_DIR`/`PYTHIA8`, then `models/SMASH/build-deps/pythia8309` if present. A selected installation is reused; the installer does not copy or rebuild Pythia. ROOT/HepMC/Rivet output and upstream tests are disabled for this build.

SMASH installs to `bin/smash`, `lib/` and `share/smash/`, retaining runtime links to Pythia. Keep Pythia's library and XML data available. Its particle/decay tables are compiled in and must agree; [the physics reference](../docs/reference/physics.md#smash-configuration-and-particles) explains sigma handling. The workflow uses the separate `config/config.yaml`, which installation does not overwrite.

## Before running events

Activate the environment, select approved data and archives, then pass the initialized run directory to the wrapper:

```bash
run-events-wrapper --mode both --nevents 10000 /absolute/path/to/design/system
```

The target must already contain its matching `parameters.dat`, `music_input` and `initial.hdf`. Afterburner-only work uses existing hydro markers and surfaces. See [modes and resuming](../docs/workflow.md#modes-and-resuming) before retrying interrupted events.

MUSIC installs `bin/MUSIChydro`, `lib/libmusic.so` and, unless disabled, `share/music-hydro/EOS/PCE/strange_30_10000`. Shared conversion/configuration files install under `share/pce-eventgen/`, allowing the installed runner to work without a checkout. The [environment reference](../docs/reference/environment.md) lists executable/data overrides; the [archive reference](../docs/reference/files.md#surface-archive) explains the destination and path layout. The wrapper can also run directly as `./models/run-events-wrapper` with the environment active.
