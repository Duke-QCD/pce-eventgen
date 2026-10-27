# Physics parameters and model input reference

[Handbook](../README.md) · [All template keys/defaults](model-input-defaults.md)

This page explains the custom production path and where its parameters enter. It is an implementation guide, not a derivation or scientific validation of the model. Preserve reviewed physics choices within a campaign. Broader model options appear in the source/reference links below, but alternate modes may not implement this repository's two-occupancy interface.

## Design table → `parameters.dat`

The design file is whitespace-delimited. Its header starts with `Parameter` (optionally `# Parameter`); numerical rows have exactly the remaining column count. `Parameter` is a header marker, not an extra numerical ID column. Directory IDs are zero-based row positions. `norm5360`, `norm5020`, `norm2760`, and `norm200` are system-specific normalization columns; the selected system uses one. Extra header columns do not automatically become model parameters: the rendering function explicitly maps supported fields.

| Design column | Rendered key | Meaning / units |
|---|---|---|
| Selected `norm*` | `normalization` | TRENTo profile normalization in this workflow's convention |
| `w` | `nucleon_width` | Gaussian nucleon width, fm |
| `k` | `gamma_fluctuation` | TRENTo gamma-fluctuation shape parameter; distinct from chemical occupancy |
| `gamma_l_0`, `gamma_s_0` | `Initial_light_quark_fugacity`, `Initial_strange_quark_fugacity` | Initial dimensionless light/strange chemical occupancies |
| `tau_l`, `tau_s` | `Light_quark_equilibration_time`, `Strange_quark_equilibration_time` | Chemical equilibration time scales, fm/c |
| `eta_min` | `eta_over_s_at_kink` | Shear viscosity/entropy density at the kink, dimensionless |
| `eta_low`, `eta_high` | `eta_over_s_low_T_slope_in_GeV`, `eta_over_s_high_T_slope_in_GeV` | Low/high-temperature slopes; multiply temperature differences in GeV, so slopes have units GeV⁻¹ |
| `zeta_max` | `zeta_over_s_max` | Peak bulk viscosity/entropy density, dimensionless |
| `zeta_width` | `zeta_over_s_width_in_GeV` | Bulk peak width, GeV |
| `zeta_T` | `zeta_over_s_T_peak_in_GeV` | Bulk peak temperature, GeV |
| `zeta_skew` | `zeta_over_s_lambda_asymm` | Dimensionless bulk-width asymmetry |

The base template ([base_parameters.dat](../../config/initialization/base_parameters.dat)) supplies the fixed values, such as `eta_over_s_T_kink_in_GeV`; the design row supplies the sampled ones. `SYSTEM_CONFIG` in [run_designs.py](../../models/run_designs.py) owns species, normalization-column selection, collision cross section, and transverse grid:

| System | Species / input | Normalization | σNN (fm²) | Grid step (fm) | Requested half-extent (fm) |
|---|---|---|---|---|---|
| `AuAu200` | `Au Au` | `norm200` | 4.23 | 0.1 | 15 |
| `PbPb2760` | `Pb Pb` | `norm2760` | 6.4 | 0.1 | 15 |
| `PbPb5020` | `Pb Pb` | `norm5020` | 7.0 | 0.1 | 15 |
| `OO5360` | Approved O16 HDF5 table twice | `norm5360` | 7.0 | 0.1 | 10 |

These are code-defined campaign conventions, not a claim of preferred current experimental cross sections. One fm² is ten mb. Each design keeps the `parameters.dat` it was initialized with, so a later change to these values does not reach existing designs; `run_designs.py` reports such differences as a NOTE. Check `cross_section` there before treating designs as one sample.

`initialize.py` passes the grid, width, normalization, cross section, and fluctuation to TRENTo, with `--nucleon-min-dist 1.0`. That minimum distance applies to Woods–Saxon nuclei and is inert for table-based ManualNucleus input. Other TRENTo options use that executable's defaults. See [TRENTo usage](../../models/trento/doc/usage.rst) and the [custom source](../../models/trento/src/trento.cxx).

## MUSIC lattice and templates

`create_music_input.py` fills named `KEY {}` placeholders, then replaces the four transverse lattice settings from the TRENTo grid:

```text
n = ceil(2 * grid_max / grid_step)
Grid_size_in_x = Grid_size_in_y = n
X_grid_size_in_fm = Y_grid_size_in_fm = n * grid_step
```

MUSIC's sizes are full extents, not half-extents. Nonintegral requested grid sizes round up to the actual TRENTo extent. Conflicting explicit derived values are ignored with a warning. For `grid_max=15`, `grid_step=0.1`, this gives 300 cells and a 30 fm full extent. Keep the initial grid and hydro lattice matched.

The supplied template selects evolution-only `mode=2`, initial profile 92, boost-invariant evolution, start time 0.6 fm/c, time step 0.005 fm/c, maximum evolution time 25 fm/c, viscous shear/bulk evolution, EOS 18, and text surfaces. All active template entries are in the [generated defaults](model-input-defaults.md). The full parameter reader is [read_in_parameters.cpp](../../models/music-hydro/src/read_in_parameters.cpp); its comments, validation, and defaults cover keys outside this template.

## Chemical occupancy and EOS 18

For positive equilibration time, each sector evolves in proper time as

```text
gamma(tau) = 1 - (1 - gamma_initial) * exp((tau0 - tau) / tau_equilibration)
```

The EOS implementation returns equilibrium for nonpositive equilibration time (or negative `tau0`); negative proper time is an error path. See [EOS_PCE](../../models/music-hydro/src/eos_PCE.cpp). Light and strange sectors have separate initial values and times. The tabulated occupancy domain is 0–1 for both sectors; arbitrary new parameter ranges require scientific and numerical review.

The custom switching temperature is `T_switch = 0.260 - 0.090*sqrt(gamma_l) - 0.012*sqrt(gamma_s)` GeV. It approaches 0.158 GeV in chemical equilibrium. The EOS-18 surface finder uses this composition-dependent condition, and the SIMS shear-viscosity kink follows the same local temperature. Thus the template's `T_freeze=0.158` and `eta_over_s_T_kink_in_GeV=0.18` do not define independent constant thresholds for this custom production path.

MUSIC EOS 18 uses 30 strange slices × 30 light occupancies × 10,000 energy points. Each record is five little-endian float64 values. The first is `q=(epsilon/HBARC)^(1/4)` in fm⁻¹, followed by pressure (GeV/fm³), entropy density (fm⁻³), temperature (GeV), and dimensionless sound speed squared. See [EOS data/generator notes](../../models/music-hydro/EOS/PCE/README.md) and the [checksum manifest](../../models/music-hydro/data/pce-strange-30-10000.json). Stock `frzout` cannot regenerate these data: the generator requires the custom HRG with `fug_l`/`fug_s`. Generating files does not make them approved inputs.

## Transport coefficients

With temperature-dependent shear flag 3, the implemented shear function is piecewise linear about the kink: `eta_at_kink + slope*(T - T_kink)`, with separate low/high slopes and a floor of `1e-6`. In EOS 18, the local switching temperature supplies the kink as described above.

With temperature-dependent bulk flag 3, the skewed Cauchy function is `zeta_max / (1 + r*r)`, where `r = (T - T_peak) / (width*(1 + skew*sign(T - T_peak)))`. Check widths/asymmetries for a valid parameter domain before generating new designs. The authoritative implementation is [transport_coeffs.cpp](../../models/music-hydro/src/transport_coeffs.cpp).

## iS3D production configuration

| Key | Production selection | Role |
|---|---|---|
| `operation` | 2 | Sample particle lists |
| `mode` | 6 | Custom MUSIC PCE text surface reader |
| `dimension` | 2 | Boost-invariant surface |
| `hrg_eos` | 6 | Light **and** strange occupancy coefficient interpolation |
| `df_mode` | 1 | 14-moment viscous correction |
| `include_baryon`, `include_baryondiff_deltaf` | 0 | No baryon chemical potential/diffusion in this production path |
| `include_bulk_deltaf`, `include_shear_deltaf`, `regulate_deltaf` | 1 | Include and regulate viscous corrections |
| `oversample`, `min_num_hadrons`, `max_num_samples` | 1, 10000, 10 | Adaptive sample count, capped at 10 |
| `sampler_seed` | -1 | Clock-based random seed; changing to a fixed seed supports controlled standalone comparisons |
| `do_resonance_decays` | 0 | Sampling path hands hadrons to SMASH |

The custom text surface contains 28 standard columns, `gamma_l`/`gamma_s` in columns 29/30, and bulk pressure in column 31. Mode 6 performs unit conversion and computes surface-weighted occupancy averages. Binary surfaces, upstream text without occupancies, baryon/diffusion extensions, other readers, and full 3D inputs are not drop-in substitutes.

`hrg_eos=5` uses light-only coefficient tables; it cannot replace mode 6's two-occupancy data. The valence-dependent hadron fugacity uses the retained `0.15 + 0.85*gamma` prescription and special eta/eta-prime assignments in [gaussThermal.cpp](../../models/iS3D/src/cpp/gaussThermal.cpp). Read the [iS3D limitations](../../models/iS3D/README.md#custom-pce-workflow-and-limitations) before selecting alternate smooth distributions, delta-f modes, CUDA helpers, or regenerated coefficients. `df_mode=4` preparation is disabled in this path.

## SMASH configuration and particles

The [workflow YAML](../../config/config.yaml) selects List mode, end time 2000 fm/c, clock/random seed `-1`, OSCAR2013 particle output, and `Force_Decays_At_End: false`. The commented `No_Collisions` line does not disable collisions. The runner overrides input directory and actual sample count for each invocation without rewriting this shared YAML. Its literal `Nevents: 10` is only the direct-invocation default.

SMASH must include the revision's matching sigma particle and decay entries. Sigma exclusion at particlization is implemented separately by iS3D's PCE particle/chosen list. Deleting sigma only from SMASH's particle table leaves inconsistent decay channels. Source tables are embedded at compilation, so editing a text file beside an old binary does not establish a new model.

Broader configuration: [SMASH 3.0 source and user guide](https://github.com/smash-transport/smash/tree/SMASH-3.0), [installation](../../models/SMASH/INSTALL.md), and `smash --help`. Preserve the exact source revision and Pythia dependency documented in [local installation](../../local/README.md).
