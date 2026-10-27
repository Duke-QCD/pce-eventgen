
MUSIC
--------------------------------------

Directory scope
--------------------------------------

This directory contains MUSIC's source, EOS data, examples, tests, and utilities. `create_music_input.py` renders the workflow's MUSIC input; `create_1cellsurface.py` generates a single-cell test surface. The standalone EOS generator is described in [EOS/PCE/README.md](EOS/PCE/README.md).

Campaign caching lives in [../cache/](../cache/), cross-model analysis in [../analysis/](../analysis/README.md), and standalone event/archive maintenance in [../maintenance/](../maintenance/). Use [../campaign.py](../campaign.py) for the main workflow.

Workflow installation
--------------------------------------

The shared local/NERSC installer builds MUSIC in `build-workflow/` and installs `bin/MUSIChydro` plus `lib/libmusic.so` into the active environment. The binary uses a relative runtime library path. `HIC_MUSIC_BUILD_DIR` selects another build directory. The event runner honors `HIC_MUSIC_EXEC`; NERSC setup selects `$HIC_SOFTWARE_ROOT/bin/MUSIChydro`. The EOS 18 reader consumes `HIC_EOS_PCE_DIR` directly. That directory must contain `0.dat` through `29.dat`. The event runner preserves an explicit selection and otherwise finds the installed production set or the source-tree copy under `EOS/PCE/strange_30_10000`.

Large EOS data
--------------------------------------

The production set is **360 MB**: 30 binary files of 12,000,000 bytes each. Each strange-fugacity slice contains 10,000 energy-grid points, 30 light-fugacity points, and five little-endian doubles per record: `epsilon^(1/4), P, s, T, cs2`. `gamma_l` and `gamma_s` both run from 0 to 1 in 30 equally spaced values.

EOS binaries are supplied by the [public physics-data bundle](../../data/README.md). The one-command download verifies them automatically. The tracked `data/pce-strange-30-10000.json` also supports checking an independent copy:

```bash
python tools/check_physics_data.py eos /path/to/strange_30_10000
export HIC_EOS_PCE_DIR=/path/to/strange_30_10000
```

The shared installer includes only this production set when available, under `share/music-hydro/EOS/PCE/strange_30_10000`. `HIC_EOS_PCE_DIR` selects an external source for installation as well as runtime. Set `HIC_MUSIC_INSTALL_PCE_DATA=OFF` to install code while using an existing external dataset. Standalone CMake exposes the equivalent `MUSIC_PCE_TABLE_DIR` and `MUSIC_INSTALL_PCE_DATA` options. If data are absent, code still builds; runtime requires an explicit complete dataset. Container builds keep their staged `/opt/data` dataset and disable duplicate installation. The dataset manifest installs under `share/music-hydro/data`.

The [EOS generator](EOS/PCE/README.md) requires the matching custom `frzout` HRG. Installation does not invoke it; generated tables do not become approved production data automatically.

Custom PCE modifications
--------------------------------------

`EOS_to_use 18` selects the custom EOS with independent light and strange quark fugacities. Its input parameters are `Initial_light_quark_fugacity`, `Initial_strange_quark_fugacity`, `Light_quark_equilibration_time`, and `Strange_quark_equilibration_time`; the relaxation times are in fm.

The main implementation points are:

- `src/eos_PCE.cpp` loads the thermodynamic tables, interpolates in `(epsilon^(1/4), gamma_l, gamma_s)`, and evaluates fugacity relaxation.
- `src/cell.h`, `src/init.cpp`, `src/reconst.cpp`, and `src/advance.cpp` carry a fluid-cell clock, initialized at `tau0`, advanced by `d tau / u^tau`, and advected transversely. It is distinct from the global evolution time.
- `src/evolve.cpp` locates `T/T_switch(gamma_l,gamma_s) = 1`, interpolates the local clock and energy at the surface, and writes the fugacities for iS3D. `T_switch = 0.260 - 0.090 sqrt(gamma_l) - 0.012 sqrt(gamma_s)` in GeV.
- Surface columns 29 and 30 contain `gamma_l` and `gamma_s`, after the shear tensor and before optional bulk/charge fields. Binary surfaces hold 36 floats per element (`kBinarySurfaceRecordFloats` in `src/freeze.h`); the workflow writes text (`freeze_surface_in_binary 0`) because iS3D reads text.
- `src/transport_coeffs.cpp` places the SIMS shear-viscosity kink at the local switching temperature, and `src/eos_PCE.cpp` smoothly sends the temperature of extremely dilute cells (epsilon < 1e-6 fm^-4) to zero.
- `src/freeze_pseudo.cpp` retains a separate light-fugacity spectrum ansatz for MUSIC's internal spectra calculation. The production iS3D handoff uses the surface fields instead.

Comments marked `PCE` describe the custom integration. The notes below mark retained upstream code paths that the PCE changes do not support.

PCE compatibility warnings
--------------------------------------

Source comments use `WARNING(PCE)` for a confirmed incompatibility or a path that needs adjustment for the stated use. `REVIEW(PCE)` marks a retained prescription or incomplete interface that needs validation, rather than declaring it broken. These annotations do not disable or repair any code. The production reference is hydro-only MUSIC with the custom text surface, followed by the separate iS3D and SMASH stages.

| Area | Required adjustment before the affected use |
| --- | --- |
| Internal MUSIC spectra (`main.cpp`, `music.cpp`, `freeze*.cpp`) | The internal spectrum ansatz uses light fugacity without the separate strange/species treatment of the custom iS3D sampler. It needs adjustment/validation before replacing that sampler. |
| Surface imports (`freeze.cpp`) | The internal text reader expects the added fugacity columns unconditionally; upstream surfaces without them need explicit format adaptation. |
| Legacy freeze-out controls (`evolve.cpp`, `data.h`) | Cornelius uses `T/T_switch = 1`, not the requested energy thresholds or `TFO`. Multiple threshold loops all write `surface.dat`. Restore the criterion and naming before using legacy scans or multiple surfaces. |
| Non-PCE EOS freeze-out (`eos_base.h`, `evolve.cpp`) | Other EOS models inherit a constant `get_T_ratio()`. They need an EOS-aware freeze-out criterion before their original freeze-out behavior can be used. |
| Initial-state alternatives (`init.cpp`) | Only profiles 8, 9/91/92, 11 and 666 initialize the chemical clock at `tau0`. Other initializers, including JETSCAPE/source-driven paths, need a clock prescription. The cylindrical test also assigns temperature directly to the energy variable and needs a consistent EOS inversion for physical use. |
| Full 3+1D PCE transport (`advance.cpp`) | Clock advection is transverse only. Add/validate longitudinal clock transport before interpreting rapidity-dependent chemical evolution. |
| PCE entropy (`eos_base.cpp/.h`) | The nonvirtual base API uses the thermodynamic identity with the caller's local clock; it remains distinct from the tabulated `EOS_PCE` entropy helper. Account for that distinction when changing entropy interfaces. |
| Finite-charge PCE thermodynamics (`eos_PCE.h`) | EOS 18 has zero conserved-charge chemical potentials. Enabling baryon-current evolution does not add finite-density thermodynamics; a consistent EOS extension is required for that use. |
| Diagnostic modes 71/73 (`eos_base.cpp`, `dissipative.cpp`) | Scans use a fixed zero clock. Specify a consistent composition/clock prescription before interpreting them as PCE diagnostic tables. |
| Evolution/probe outputs (`grid_info.cpp`, `HydroinfoMUSIC.h`) | Text and binary evolution outputs contain different fields. The photon output no longer matches its old schema comments; in-memory/photon interfaces omit the chemical clock and fugacities. Adapt readers and extend composition-dependent interfaces before reuse. |
| Legacy surface/job tools (`utilities/`) | `check_surface.py` filters for 28 fields; sweepers/jobs expect old `surface_eps_*` names and internal spectra stages. Adjust schemas, naming and configuration before using them with current PCE output. The equilibrium energy-list generator does not define the PCE switching surface. |
| Archived make recipes (`src/old_makefiles/`) | Old source lists/wrappers do not build the current split PCE EOS target. Update them before reuse; the current build uses CMake. |

Building and running MUSIC on its own
--------------------------------------

For a standalone CMake build:

    cmake -S models/music-hydro -B build-music -DMUSIC_INSTALL_PCE_DATA=OFF
    cmake --build build-music --parallel 4

MUSIC reads a text input file of `name value` lines ending in `EndOfData`; omitted parameters take the defaults in `src/read_in_parameters.cpp`. The workflow renders each design's input with `create_music_input.py` from `config/initialization/music_input_template`. To run one event by hand from a directory containing the initial-condition file named by the input:

    export OMP_NUM_THREADS=2
    export HIC_EOS_PCE_DIR=/path/to/strange_30_10000
    build-music/src/MUSIChydro music_input

The scripts in `utilities/` come from upstream MUSIC and predate the PCE surface format; see the compatibility table above before using them.
