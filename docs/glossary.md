# Glossary

[Handbook](README.md) · [Workflow](workflow.md)

Terms used throughout the code, logs and documentation, in workflow order.

## Physics and models

| Term | Meaning |
|---|---|
| PCE | Partial chemical equilibrium: light and strange quark occupancies start below 1 and relax toward equilibrium during the hydrodynamic evolution |
| Occupancy, fugacity, `gamma_l`, `gamma_s` | The light and strange chemical occupancies (the code also calls them fugacities). Their initial values and relaxation times are design parameters |
| EOS 18 | MUSIC's custom PCE equation of state, tabulated in energy density and both occupancies (the 30 tables `0.dat`–`29.dat`) |
| Switching temperature | The composition-dependent temperature at which MUSIC writes the particlization surface |
| Surface | `surface.dat`, MUSIC's particlization hypersurface; iS3D samples hadrons from it |
| Oversample (sample) | One independent iS3D draw from a surface, followed by SMASH. A hydro event has up to ten |
| σNN, cross section | The inelastic nucleon–nucleon cross section TRENTo uses, set per system in `models/run_designs.py` |

## Campaign structure

| Term | Meaning |
|---|---|
| Design, design point | One row of the design table: one choice of model parameters. Row *n* lives in `design_NNN/` (zero-based) |
| Design table | `config/initialization/design_params_combined500.dat`, the 500 parameter rows shared by generation and inference |
| System | Collision species and energy, e.g. `AuAu200`, `OO5360`; one subdirectory per design |
| Bundle | A design's `parameters.dat`, `music_input` and `initial.hdf`, written once at initialization |
| Hydro event, event | One initial condition and its MUSIC evolution, in a numbered event directory such as `0042/` |
| Event bound, `nevents` | The number of hydro events per design. Fixed for the life of a campaign: it sets event IDs and directory-name padding |
| Campaign | A set of designs for one system under one raw root, operated with one INI |
| Raw root | The scratch directory holding a campaign's design directories (`[run] directory`) |
| Profile | A user's four generated files (paths, launcher, INI, `activate.sh`) from `nersc/configure_user.py` |
| Launcher | The Slurm batch script (`pce-eventgen`) that runs the event runner for one design |

## Event states

| Term | Meaning |
|---|---|
| Marker | A small file recording an event's state: `hydro.done`, `hydro.no_surface`, `afterburner.done`, `afterburner.failed`, `archive.failed` |
| No-surface event | A hydro event for which MUSIC wrote an empty surface, typically a very peripheral collision that never exceeds the switching temperature (`hydro.no_surface`); it is complete and has no particles |
| Terminal event | A finished event: `afterburner.done` with one nonempty particle list, or a clean `hydro.no_surface` |
| Lock | `afterburner.lock/`, created while a worker runs iS3D/SMASH for an event |
| Surface archive | The compressed copy `surface.dat.zst` kept after an event finishes; it mirrors the event path relative to the saved `archive_base`, including nested campaign parents (see [layout](reference/files.md#surface-archive)) |
| Repair | The between-pass check of every event directory (`campaign.py repair`); it audits by default and applies fixes with `--apply` |
| Route | Repair's verdict for a design in `auto` mode: run `both`, run `afterburner`, `ready` to cache, or `blocked` |
| Fill | Finishing nearly complete designs inside one allocation (`campaign.py fill-plan` / `fill`) |

## Caching and analysis

| Term | Meaning |
|---|---|
| Survey, status | A scan of raw event markers joined with cache/reap history (`campaign.py status`) |
| Manifest | A text list of `design system` rows selecting work for a cache or reap run |
| Cache (v3 cache) | A compact, verified summary of a design's particle output with enough information to compute the supported observables |
| Staging, published cache | Where a cache is built (`cache_local_root`) and its verified, compressed copy (`cache_cfs_root`) |
| Fingerprint | The JSON fixing a cache's scope: acceptance cuts, species, histogram edges and conventions. `config/cache_fingerprint_*.json` |
| Record, verdict | The JSON result of caching (`*_stage1.json`) or reaping (`*_stage2.json`) a design, in the record directory |
| Review | Classification of all records into a clean-only reap manifest (`campaign.py review`) |
| Reap | Deleting raw particle lists whose data a verified published cache holds (`campaign.py reap --submit`) |
| Extraction, artifact | Per-design sufficient statistics read from a published cache (`<design>_<system>_observables.npz`) |
| Aggregation, model matrix | Reducing all artifacts to centrality-binned observables, one row per design (`model_matrix.npz`) |
| Feed-down inventory | Separate per-design hadron yields used to correct Lambda for Sigma0 decays (`feeddown_yields.npz`) |

## Inference

| Term | Meaning |
|---|---|
| Inference | The standalone emulator and Bayesian fit in `inference/`, run after a model matrix exists |
| Emulator | The PCA plus Gaussian-process model that predicts observables between design points |
| Closure | Training on some designs and predicting held-out ones, to check the emulator |
| Primary analysis | The default fit: all parameters sampled, each system's standard observable exclusions, no feed-down correction |
| Posterior | The sampled parameter distribution (`<prefix>_posterior.npz`) |
