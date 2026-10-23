# Local usage

For an end-to-end two-event example, start with the
[first-run tutorial](../docs/getting-started.md). The [handbook](../docs/README.md)
also explains the workflow, physics settings, all command flags, and recovery.

## Installation

Prerequisites:

- Python __3.8+__ with numpy, scipy, cython, and h5py (the guides use 3.11; the
  separate [inference environment](../inference/README.md) requires 3.11)
- C, C++, and Fortran compilers
- CMake 3.16+ and a C++17 compiler for SMASH
- Boost and HDF5 C++ libraries

The [first-run guide](../docs/getting-started.md#2-choose-your-installation-path)
provides a concrete Ubuntu/Debian dependency list, Python-environment choices,
Pythia build steps and compiler/HDF5 selection. Keep the native dependencies
consistent with the compiler: a conda HDF5 found on `PATH` can fail to link
with an older system compiler. Start a fresh generated build directory after
changing that selection so CMake does not retain the mismatched libraries.

Clone with `git clone --recurse-submodules` so that the pinned SMASH submodule
is present, and obtain the two external production datasets (MUSIC EOS and the
iS3D two-occupancy tables) as described in
[the first-run guide](../docs/getting-started.md#1-obtain-code-and-physics-data).

The models must be installed into an active Python [virtual environment](https://docs.python.org/3/library/venv.html) (venv) or a [conda environment](https://conda.io/docs/user-guide/tasks/manage-environments.html) (for example from [Miniconda](https://conda.io/miniconda.html)).

If Python and its packages are already installed on your system, the easiest choice is probably a venv:
```bash
python -m venv --system-site-packages --without-pip /path/to/venv
source /path/to/venv/bin/activate
```
Or if using conda:
```bash
conda create --name pce-eventgen numpy scipy cython h5py
conda activate pce-eventgen
```

After creating and activating the environment, run the [install](install) script.
With no model names it builds trento, music-hydro, iS3D and SMASH, and stops with
an error if any source tree is missing (for SMASH: run
`git submodule update --init models/SMASH`).
To pass a Pythia location while installing all models, use:

```bash
./local/install -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

Pythia 8.309 must already be built or installed; the installer does not download
or compile it automatically.

### iS3D

```bash
./local/install iS3D
source "$(python -c 'import sys; print(sys.prefix)')/share/iS3D/paths.sh"
```

Requires GSL and OpenMP. The installer builds `models/iS3D/src/cpp` in a fresh
`build-workflow/` directory, honoring the selected C++ compiler, then installs
`bin/iS3D`, `lib/libiS3D_lib.so`, and `share/iS3D/` data. Run from the event's
working directory containing `surface.dat`, with a `results/` directory.
The parameter file retains the workflow maximum of 10 samples and a target of
10,000 hadrons. `hrg_eos=6` also requires the production two-occupancy tables,
which are kept outside Git: copy them into
`models/iS3D/deltaf_coefficients/vh/smash_pce_light_strange/` and check them with
`python tools/check_physics_data.py is3d <that directory>` before installing.
See [iS3D's workflow notes](../models/iS3D/README.md) for the environment paths,
source layout, and documented PCE limitations.

### SMASH

To build only the SMASH checkout in `models/SMASH`, run from the repository root:

```bash
./local/install SMASH -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

`models/SMASH` is a Git submodule pinned to unmodified upstream SMASH commit
`0985d6b32fb801404c933d70c86c23759d60ff1f` (`SMASH-3.0-1-g0985d6b3`, one commit
after the 3.0 tag). It requires GSL >= 2.0, Eigen3 >= 3.0, and Pythia **8.309**.
The installer refuses any other revision before building.

SMASH uses its upstream particle and decay tables, which include sigma. Keep
those tables together: commenting out sigma in SMASH's particle table while
retaining its decay channels prevents startup, and the installer rejects that
mismatch. Sigma is excluded separately at particlization by iS3D's
`pdg_smash_pce.dat` and chosen particle list, so it is never sampled from the
surface but its transport and decay channels remain. Particle data are compiled
into SMASH, so editing a text table beside an existing binary changes nothing.

The installer uses `models/SMASH/build-workflow`. Set `HIC_SMASH_BUILD_DIR` for another build location and
`HIC_BUILD_JOBS` to limit parallel compilation. Extra arguments after `--` are
SMASH CMake options. Optional ROOT/HepMC/Rivet output and upstream unit tests
are disabled by default for the OSCAR workflow.

If Pythia 8.309 was built in `models/SMASH/build-deps/pythia8309`, it is found
automatically; an explicit CMake option after `--` overrides that location.
Release archives are available from the [official Pythia release repository](https://gitlab.com/Pythia8/releases/-/tags/pythia8309).

The executable installs to the active environment's `bin/smash`; SMASH's shared
library and input files install to `lib/` and `share/smash/`. Runtime library
paths retain linked dependencies, including the external Pythia installation.
Keep Pythia's library and XML data directory available after installation.

For the workflow, set `HIC_SMASH_EXEC` to that executable and
`HIC_SMASH_CONFIG` to the repository's `config/config.yaml`. The configuration
is separate from upstream example configurations and is not overwritten by the
installer. NERSC setup selects `$HIC_SOFTWARE_ROOT/bin/smash`.
An explicitly supplied `HIC_SMASH_EXEC` is preserved.

## Before running events

Activate the installed environment and use `run-events-wrapper`. It selects the
matching runner and iS3D data without changing the event working directory:

```bash
run-events-wrapper --mode both --nevents 10000 /absolute/path/to/design/system
```

The run directory must already contain `parameters.dat`, `music_input`, and
`initial.hdf` from initialization. Afterburner-only mode instead uses existing
per-event hydro markers and surfaces. TRENTO runs in initialization, not inside
`run-events`.

MUSIC now installs to `bin/MUSIChydro` and `lib/libmusic.so`, with a relative
runtime library path. Its numbered PCE dataset installs under
`share/music-hydro/EOS/PCE/strange_30_10000`. `HIC_EOS_PCE_DIR` selects the
directory containing `0.dat` through `29.dat` directly. Verify external
data with `python tools/check_physics_data.py eos /path/to/dataset`. New EOS binaries
are outside ordinary Git, with a small tracked checksum manifest. Set
`HIC_MUSIC_INSTALL_PCE_DATA=OFF` to reuse external data without an installed copy.
Like iS3D, it uses a separate `build-workflow/`; set
`HIC_MUSIC_BUILD_DIR` to choose another build directory. The installer also
packages the OSCAR converter and shared SMASH configuration under
`share/pce-eventgen/`, so an installed runner can use those without a checkout.
The converter's source is `models/iS3D/iS3D_OSCAR.py`; its installed copy is
`share/pce-eventgen/models/iS3D/iS3D_OSCAR.py`.
Set `HIC_MUSIC_EXEC`, `HIC_IS3D_EXEC`, `HIC_SMASH_EXEC`, `HIC_SMASH_CONFIG`, or
`HIC_IS3D_OSCAR_SCRIPT` when selecting another installation or configuration.

Use `HIC_SURFACE_ARCHIVE_ROOT` to select the surface archive destination;
otherwise `surface_archive/` is created under the launch directory. `zstd` is
required for archiving completed events. See the repository's event-runner
notes for modes and rerun behavior. The wrapper can also be invoked directly
as `./models/run-events-wrapper` while the environment is active.
