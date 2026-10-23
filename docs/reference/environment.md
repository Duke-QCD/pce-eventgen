# Environment and path reference

[Handbook](../README.md) · [Configuration](configuration.md) · [Installation](../../local/README.md)

On NERSC, source your generated `activate.sh` in each shell and allocation. It
selects the conda environment, your paths file (`HIC_PATHS_FILE`) and your INI
(`HIC_CAMPAIGN_CONFIG`). Profiles are rendered from
[`config/templates/`](../../config/templates/); the paths file assigns every
variable below, so sourcing a different profile replaces all of them.
`osg/container_paths.sh` sets the same variables to baked `/opt` paths inside
the OSG image.

For local installed use, activate the Python environment and source
`<prefix>/share/iS3D/paths.sh`. That helper preserves explicit `HIC_*` values.
If you change a data root after sourcing it, update its already-exported
derived paths too (or start a clean shell). Shell exports are values, not
live formulas. The first-run guide sets each required path explicitly.

## Profile and storage

| Variable | Purpose |
|---|---|
| `HIC_PATHS_FILE` | The profile's paths file; the launcher and `fill` source it and refuse to run without it |
| `HIC_CAMPAIGN_CONFIG` | Personal activation's absolute INI path; pass as `--config "$HIC_CAMPAIGN_CONFIG"` |
| `HIC_CODE_HOME` | Checkout (or installed `share/pce-eventgen`) providing the converter and SMASH configuration |
| `HIC_SOFTWARE_ROOT` | Installed executable/library/data prefix, normally the active environment |
| `HIC_RUNTIME_GCC_MODULE` | NERSC wrapper runtime module; default `gcc-native/14` if unset; empty string disables the additional load |
| `HIC_CFS_ROOT` | Approved read-only static data root for the NERSC profile |
| `HIC_RAW_ROOT` | Raw campaign root in generated profiles and direct helper defaults; shared INI uses `[run] directory` |
| `HIC_CAMPAIGN_OUTPUT_ROOT` | Personal generated output root above archives, caches, analysis and records |
| `HIC_EVENT_CACHE_LOCAL_ROOT` | Environment default cache staging root; INI `cache_local_root` selects the exact campaign root |
| `HIC_EVENT_CACHE_CFS_ROOT` | Environment default publication root; INI `cache_cfs_root` selects the exact campaign root |
| `HIC_SURFACE_ARCHIVE_ROOT` | Root for verified `surface.dat.zst` files; runner fallback is `surface_archive/` below its launch directory |
| `HIC_SURFACE_ARCHIVE_BASE` | Absolute original source base stripped from event paths; overrides scratch-variable lookup. Generated profiles save it, and `[repair] archive_base` supplies it to campaign repair/event/fill jobs. Keep the INI and paths file consistent when updating a profile |
| `HIC_ZSTD_EXEC` | Surface archive compressor command/path; runner default `zstd` |
| `HIC_O16_NUCLEUS_TABLE` | Approved OO5360 HDF5 nucleus configurations; overrides `SYSTEM_CONFIG` table path |

## Executables and per-event support

| Variable | Purpose / normal installed selection |
|---|---|
| `HIC_MUSIC_EXEC` | `<prefix>/bin/MUSIChydro` |
| `HIC_IS3D_EXEC` | `<prefix>/bin/iS3D` |
| `HIC_SMASH_EXEC` | `<prefix>/bin/smash` |
| `HIC_SMASH_CONFIG` | Shared workflow YAML, normally `<checkout>/config/config.yaml` or packaged equivalent |
| `HIC_IS3D_OSCAR_SCRIPT` | `models/iS3D/iS3D_OSCAR.py` under checkout or installed `share/pce-eventgen` |
| `HIC_MUSIC_INPUT` | MUSIC input path relative to event directory; runner default `../music_input` |
| `HIC_SMASH_TMP_ROOT` | SMASH temporary output root beneath the system directory; default `smash_output`, with one subdirectory/event |
| `HIC_EOS_PCE_DIR` | Exact directory with numbered EOS files `0.dat`–`29.dat`; also selects the installer data source |

Outside a profile, the wrapper's installed/source discovery supplies these
defaults; an explicit export selects another installation.

## iS3D data and metadata

| Variable | Selection relative to an iS3D data root |
|---|---|
| `HIC_IS3D_DATA_ROOT` | Root, containing configuration, `PDG/`, `tables/`, and `deltaf_coefficients/` |
| `HIC_IS3D_CONFIG` | `iS3D_parameters.dat` |
| `HIC_IS3D_PDG_DIR` | `PDG/` for the sampler |
| `HIC_IS3D_TABLE_DIR` | `tables/` quadrature data |
| `HIC_IS3D_DELTAF_DIR` | `deltaf_coefficients/vh/`, **above** `smash_pce_light_strange/` |
| `HIC_IS3D_CHOSEN_PARTICLES` | `PDG/chosen_particles.dat` |
| `HIC_PDG_DIR` | Common metadata directory for analysis; set alongside sampler PDG selection when using direct tools |
| `HIC_CHARGE_DICT` | `charge_dict.dat`, used by OSCAR conversion |
| `HIC_BARYON_DICT` | `baryon_dict.dat`, used for signed-baryon reconstruction and provenance |
| `HIC_PDG_MASS` | Pole-mass table `PDG/pdg_smash_pce.dat` |

Keep the exact approved set. `tools/check_physics_data.py is3d` verifies the
two-occupancy coefficient tables against their checked-in checksums.

## Build controls

| Variable | Meaning |
|---|---|
| `HIC_BUILD_JOBS` | Parallel compilation cap; use a modest value such as 2 for a first build |
| `HIC_MUSIC_BUILD_DIR` | Alternate MUSIC build directory; normal installer uses `build-workflow/` |
| `HIC_IS3D_BUILD_DIR` | Alternate CPU iS3D build directory |
| `HIC_SMASH_BUILD_DIR` | Alternate SMASH build directory |
| `HIC_MUSIC_INSTALL_PCE_DATA` | `OFF` to reuse external EOS data without installing a second copy |
| `CC`, `CXX`, `FC` | Selected C/C++/Fortran compilers; keep dependencies and runtime consistent |
| `CMAKE_PREFIX_PATH`, `HDF5_ROOT` | Help CMake find environment/dependency and HDF5 installations |
| `CONDA_PREFIX`, `VIRTUAL_ENV` | Active environment; installer validates that Python belongs to it |
| `PYTHONNOUSERSITE` | Set `1` to avoid unrelated user-installed Python packages shadowing the environment |

SMASH-specific CMake arguments follow `--` in the installer. Standalone MUSIC
CMake exposes `MUSIC_PCE_TABLE_DIR` and `MUSIC_INSTALL_PCE_DATA`; standalone
iS3D CMake exposes `IS3D_INSTALL_DATA`. These are CMake settings, not INI keys.

## Cache scope and direct-script controls

Config-based cache commands read the expected fingerprint and export these
cuts before invoking backends. Change the reviewed fingerprint/profile as a
unit; setting a cut in an unrelated shell does not change an existing cache.

| Variable | Fingerprint field / purpose |
|---|---|
| `HIC_CACHE_SYSTEM` | System of the cache run; `campaign.py` sets it from `[run] system` |
| `HIC_EXPECTED_FINGERPRINT_V3` | Frozen expected JSON, default for direct analysis/cache use |
| `HIC_ANALYSIS_CHARGED_ETA_MAX` | `charged_eta_max` |
| `HIC_ANALYSIS_IDENTIFIED_Y_MAX` | `identified_y_max` |
| `HIC_ANALYSIS_IDENTIFIED_FLOW_Y_MAX` | `identified_flow_y_max` |
| `HIC_ANALYSIS_FLOW_PT_MIN`, `HIC_ANALYSIS_FLOW_PT_MAX` | Flow transverse momentum range, GeV |
| `HIC_ANALYSIS_SUBEVENT_ETA_GAP` | Full pseudorapidity gap between flow subevents |
| `HIC_ANALYSIS_INVENTORY_ETA_MAX` | Wide inventory acceptance; cannot reconstruct narrower rapidity yields merely by rescaling |
| `HIC_ALLOW_MALFORMED_ROWS` | Direct builder malformed-row allowance; CLI/config can override |
| `HIC_MAX_DROPPED_EVENTS` | Direct builder dropped-event allowance; CLI/config can override |

The cache backends default to this checkout's `models/cache/` scripts;
`HIC_CACHE_SCRIPT`, `HIC_PUBLISH_SCRIPT` and `HIC_REAPER_SCRIPT` override them
for testing an alternative build of one backend.

## Threads and fill

`OMP_NUM_THREADS` controls threaded numerical work; keep it within assigned
CPUs. `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, and `NUMEXPR_NUM_THREADS`
can be set to 1 to avoid extra thread pools in each Python worker. The OSG
worker clamps library/OpenMP threads to one. SMASH in this chain is serial.

The fill shell helper consumes `design ntasks mode restore short system`
rows, requires `SLURM_JOB_ID`, and accepts:

| Variable | Default / meaning |
|---|---|
| `CPT` | 16 logical CPUs/task |
| `CPT_AFTERBURNER` | `CPT`; can reduce for afterburner-only plan rows |
| `OMP_THREADS` | Per-line CPUs/task; explicit fill thread override |
| `RAW_ROOT` | `HIC_RAW_ROOT`; required |
| `NEVENTS` | Required; must match the initialized campaign |
| `MAXRETRY` | 3; persistent afterburner attempt cap |
| `LOG_ROOT` | `$SCRATCH/logs/fill_<timestamp>` |
| `SYSTEM` | Plan system; an explicit conflicting value is refused |

`campaign.py fill` supplies these from the INI where mapped. Each step stays
on one node and queues within the allocation when needed. More allocated nodes
do not make an individual Python cache/analysis pool span nodes.
