iS3D (c) Mike McNelis, Derek Everett, Sameed Pervaiz and Lipei Du.

The [license notice](LICENSE) was restored verbatim from [upstream](https://github.com/derekeverett/iS3D/blob/829c7f837405b3491f3c25620fb974c07f5249fd/LICENSE).

## Directory scope

This directory contains iS3D source, physics tables, configuration, and model-specific build/run and diagnostic tools. `iS3D_OSCAR.py` converts iS3D's sample output into the OSCAR input used by the event runner; the shared installer packages it with that runner.

The scripts in `scripts/` cover iS3D oversampling, native particle/spectrum plots, sampling-versus-smooth comparisons, and continuous-spectrum observables. They retain their individual input formats and execution assumptions; they are not part of `campaign.py` analysis, which lives in [../analysis/](../analysis/README.md).

## Workflow build and installation

The maintained CPU sources are in `src/cpp`. Edit that directory for active changes; the shared installation builds it with CMake in `build-workflow/`.

From an active Python/conda environment at the repository root:

```bash
./local/install iS3D
# At NERSC, with the compiler, CMake and GSL modules already loaded:
./nersc/install iS3D
```

Requires CMake >= 3.16, C++11, GSL and OpenMP. The build honors `CXX`, `HIC_IS3D_BUILD_DIR` and `HIC_BUILD_JOBS`; installs go to `bin/iS3D`, `lib/` and `share/iS3D/`. `IS3D_INSTALL_DATA=OFF` disables data installation for containers. The [first-run guide](../../docs/getting-started.md#1-obtain-code-and-physics-data) downloads and selects the complete runtime data; sampling settings and limitations are below.

For a standalone event, invoke from the directory containing `surface.dat`:

```bash
source /path/to/environment/share/iS3D/paths.sh
mkdir -p results
/path/to/environment/bin/iS3D
```

The helper resolves data relative to itself and preserves explicit `HIC_*` overrides. For a source-tree build, `bash /path/to/models/iS3D/cleanMakeCPU.sh` builds without deleting event output; from an event directory, `bash /path/to/models/iS3D/runCPU.sh 2` runs it with two threads. Set `HIC_IS3D_EXEC` to use an installed executable with that helper.

`surface.dat`, `average_thermodynamic_quantities.dat`, and `results/` belong to the event directory. Do not change into the model source directory to run a production event. Static paths are controlled by `HIC_IS3D_CONFIG`, `HIC_IS3D_PDG_DIR`, `HIC_IS3D_TABLE_DIR`, `HIC_IS3D_DELTAF_DIR` (the `vh/` directory), and `HIC_IS3D_CHOSEN_PARTICLES`. OSCAR conversion uses `HIC_CHARGE_DICT` (`PDG/charge_dict.dat`); the helper also exposes `HIC_BARYON_DICT` and `HIC_PDG_MASS` for analysis.

NERSC profiles select CFS data; OSG uses staged `/opt/data/iS3D`. Rebuilding a binary does not replace those datasets.

## Physics data names

The data names identify the coefficient setup:

| Name | Meaning |
| --- | --- |
| `smash_pce_light` | `hrg_eos=5`: temperature and light occupancy |
| `smash_pce_light_strange` | `hrg_eos=6`: temperature and both occupancies |
| `smash_equilibrium` | `hrg_eos=4`: equilibrium coefficient tables |
| `pdg_smash_pce.dat` | PCE particle list, with sigma excluded |

These names apply to `deltaf_coefficients/vh/`, the active C++ members, and the corresponding generator recipes. `hrg_eos=5/6` share the same particle list but use different coefficient tables. The reader also accepts the former names `smash_new`, `smash_pce`, `smash_eq` and `pdg_new2.dat` (see `src/cpp/data_paths.h`); new data trees use the names above. The conventional SMASH and box-format modes use `smash` and `smash_box`.

## Custom PCE workflow and limitations

The production combination is `operation=2`, `mode=6`, `dimension=2`, `hrg_eos=6`, `df_mode=1`, without baryon density/diffusion. Mode 6 reads the custom MUSIC text record: 28 standard columns, light and strange occupancies (columns 29/30), then bulk pressure (column 31). It converts MUSIC units and writes surface-weighted averages including both occupancies.

`src/cpp/gaussThermal.cpp::hadron_fugacity` supplies the valence-dependent composition used by the main sampler and thermal integrals. It retains the `0.15 + 0.85*gamma` mapping and the existing eta/eta-prime assignments. `deltafReader.cpp` uses `(T,gamma_l,gamma_s)` interpolation for `hrg_eos=6`; the interpolation arguments now use `gamma_l` and `gamma_s`. Shared grid storage uses `composition_*`, since the same storage represents baryon chemical potential in equilibrium modes and occupancy in PCE modes.

The following need adjustment or validation before reuse. Search for `WARNING(PCE)` at the corresponding implementation sites:

- **Required production tables:** `hrg_eos=6` requires ten four-column tables in `deltaf_coefficients/vh/smash_pce_light_strange/`. Keep the production directory with the installed data. The single-occupancy `smash_pce_light` tables are not a substitute. The generator under `generate_delta_f_coefficients/legacy_pce_generator` still uses a single occupancy and meson/baryon powers, so its recipe is not a validated replacement.
- **Other surface readers and memory input:** they do not populate both PCE occupancies and their averages. Mode 6 also needs adjustment for upstream text without occupancy columns, binary input, extra baryon/diffusion fields, or a fully three-dimensional surface.
- **Alternate spectra:** the main smooth momentum spectrum with `df_mode=1/2` uses the species fugacity, but the legacy spacetime/modified smooth kernels retain light-only powers. The `df_mode=4` preparation is disabled and the PCE interpolators reject it. These paths are not equivalent to the main PCE sampler.
- **Event-count handoff:** oversampling computes `min(ceil(10000 / estimated_yield), 10)`. Ten is a cap, not a fixed count or a guarantee of 10,000 particles. The shared SMASH configuration requests ten events when used directly. `models/run-events` counts the converted samples and overrides SMASH's event count for each invocation, so it consumes exactly the samples produced without changing the iS3D sampling policy.
- **Archived workflows:** CUDA/make helpers have not been adapted to this installation and PCE interface; use `models/run-events` and `models/run-events-wrapper`.
