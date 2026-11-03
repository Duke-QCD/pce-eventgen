# Troubleshooting and recovery

[Handbook](README.md) · [File/marker meanings](reference/files.md)

Preserve the failing command, first useful error, resolved configuration and input paths. NERSC needs both Slurm stdout/stderr and rank logs under `event_log_root`; cache/reap logs live in `record_dir`, OSG logs in each slot.

## Installation and startup

| Symptom | Check and next step |
|---|---|
| “must install in an active python virtualenv or conda env” | Activate the intended environment, check `command -v python`, then rerun the installer |
| `configure_user.py`: profile directory already exists | No files were changed. Check the existing profile's paths/configuration and source its `activate.sh`, or choose a new `--out-dir` for different settings; rebuilding software does not require a new profile |
| `numpy` has no attribute `typeDict`, h5py import failure, or binary compatibility error | User/site packages may be mixed. Set `PYTHONNOUSERSITE=1`, reactivate a consistent NumPy/h5py environment, and retry the import check below |
| CMake cannot find Boost/HDF5/GSL/Eigen | Check dependency headers and libraries, HDF5 C++ support, selected compiler, `CMAKE_PREFIX_PATH` and `HDF5_ROOT`; see local/NERSC build guide |
| CMake says compatibility with versions < 3.5 was removed | Select CMake >= 3.16 and < 4 (`conda install -c conda-forge 'cmake>=3.16,<4'` in the active environment), then retry; see [dependency versions](../local/README.md#dependency-versions) |
| TRENTo fails in Boost.Math or cannot find Boost's `system` component | Use one Boost 1.74–1.81 installation for headers and libraries; `conda install -c conda-forge 'boost-cpp=1.81.*'` and `export BOOST_ROOT="$CONDA_PREFIX"` select the documented Conda setup. Newer Boost requires build changes |
| SMASH reports `Eigen3 version ..` / missing `EIGEN3_VERSION_OK` | Its older dependency finder cannot read Eigen 5's version headers. In the active Conda environment, run `conda install -c conda-forge 'eigen=3.4.*'`, then rerun the installer; no manual cache deletion is needed. For other installations, select Eigen 3.4 |
| TRENTo link error mentioning `__cxa_call_terminate@CXXABI_1.3.15` | System compiler and conda HDF5 can require different C++ runtimes. Select one matching stack using the [local setup recipe](getting-started.md#2-choose-your-installation-path), then rebuild; the installer clears incompatible cached paths automatically |
| TRENTo builds but cannot write `initial.hdf` | Rebuild with HDF5 support; an HDF5-disabled executable is insufficient |
| TRENTo cannot load `libhdf5_cpp.so.*` / `libhdf5.so.*` after a successful build | Check the executable with `ldd` and confirm the selected HDF5 installation is accessible on compute nodes. Rebuild TRENTo in the intended environment/toolchain if needed (`./nersc/install trento` on NERSC); do not substitute a different library version with a symlink |
| Wrong SMASH revision, or `models/SMASH` empty | Run `git submodule update --init models/SMASH` (pinned commit `0985d6b32fb801404c933d70c86c23759d60ff1f`) and rebuild; do not copy an unrelated binary |
| Missing Pythia / wrong Pythia version | Build 8.309 with the same toolchain and pass its `pythia8-config` to the installer after `--` |
| Compilation ends with a `make` target failure | Find the first compiler `error:`/`fatal error:` above the target summary in `install.log`. If the compiler was `Killed`, retry with `HIC_BUILD_JOBS=1` and sufficient memory in a CPU allocation |
| Dynamic library cannot be loaded | Confirm installed `lib/`, dependency libraries (including Pythia), and matching runtime modules remain available; a copied executable alone is insufficient |
| Missing EOS / checksum failure | Select the numbered directory directly via `HIC_EOS_PCE_DIR`, check all 30 files, and obtain the approved set; do not regenerate or edit the manifest to bypass the error |
| Missing iS3D coefficient table | Check `HIC_IS3D_DELTAF_DIR` points to `vh/`, with the complete `smash_pce_light_strange/` below it; mode 6 requires two-occupancy production tables |
| `HIC_CHARGE_DICT is not set` from the converter | Source the installed iS3D paths helper or your profile; the converter reads the dictionary only from that variable |
| Unresolved `$VAR` in INI path | Source the personal profile in this shell/allocation; check the variable is exported and the path is real |

Useful checks, from the checkout with the environment active:

```bash
python -c 'import sys, numpy, scipy, h5py; print(sys.executable); print(numpy.__version__, h5py.__version__)'
command -v python trento MUSIChydro iS3D smash run-events-wrapper zstd
python tools/check_physics_data.py eos "$HIC_EOS_PCE_DIR"
python tools/check_workflow.py
python models/campaign.py --config "$CFG" events --print-config
```

Syntax/path checks do not prove model binaries work or data are correct. A fresh smoke root is the next check after installation, not a full campaign.

## Initialization and generation

**`initial.hdf` already exists:** initialization deliberately refuses to overwrite it. If this is an existing complete bundle, resume via the campaign or runner with its original event bound. Generated profiles set `refuse_existing = true`; pass `--allow-existing` deliberately for an inspected existing bundle (nothing is overwritten). If this is an abandoned partial test, choose a fresh test root. Do not delete initial conditions from a production tree to make initialization pass.

**Partial bundle refused:** confirm all of `parameters.dat`, `music_input`, and `initial.hdf` belong to the same initialization. A failed helper/model can leave a partial directory. The orchestrator refuses to submit it. Diagnose the original initialization log before deciding whether to recover or replace that test tree.

**Events skipped but not complete:** hydro skips existing event directories. A previous interruption can leave a directory without a usable surface. Stop all writers for the design, audit repair, and inspect its classification:

```bash
python models/campaign.py --config "$CFG" status --only review --details --max-rows 0
python models/campaign.py --config "$CFG" repair
```

That audit writes reports and routing outputs but does not apply repair. Read `campaign_state.json`, the bucket/reason, and logs. Apply the supported repair (`repair --apply`) only after confirming there are no queued/running workers or OSG transfers. Applied repair can remove locks regardless of age. The status tool does not ask Slurm whether a lock owner is alive.

For an initialized campaign, follow the [login-node resume procedure](nersc-campaign.md#1-generate-or-resume-events): use `--allow-existing --no-initialize` with the active profile. No allocation or `srun` is needed to submit that resume; the event workers run in the submitted batch job.

**Afterburner retries exhausted:** inspect `afterburner.failed`, persistent retry count, data selections, and logs. Fix the cause before planning another attempt. Raising the retry cap alone does not repair corrupt data.

**iS3D segfaults after sampling finishes:** a backtrace through `gsl_spline_free` and `Deltaf_Data::~Deltaf_Data()` can indicate an older build freeing uninitialized optional spline pointers during cleanup. The maintained source initializes those pointers and guards their frees. Check the executable selected by `HIC_IS3D_EXEC`, test the corrected build on an isolated event copy, and compare sampled output with the same fixed seed and physics inputs before resuming. Preserve retry counts and increase the retry ceiling only after fixing the crash.

The older `music-eventgen` runner could continue after a sampler crash if its sampled output file was nonempty. The current `run-events` requires a successful iS3D exit before conversion or SMASH, even if that file exists. Such crashes consume the persistent afterburner retry budget; once the ceiling is reached, later jobs skip the event.

**`archive.failed` after particle output:** check archive destination permissions, free space, `zstd`, and selected root. With valid completed particles and local surface, repair's finalization can finish archiving without rerunning transport. Use the documented applied/finalize/root controls and examine its refusal if particle structure or archive destinations disagree.

**`hydro.no_surface`:** this can be a valid zero-output event. It is not the same as MUSIC failing to create a surface file. Count clean no-surface events toward the target; resolve conflicting markers or particles before caching.

**The job exited successfully but event count is low:** runner success does not guarantee all expected slots completed. Inspect the survey's missing slots, hydro/afterburner shortfalls, retry exhaustion and lock counts. Keep `nevents` fixed across passes. A survey will not discover designs that have never been initialized and have no record; inspect the intended design-table range too.

## Cache, publication, review, and reaping

| Symptom | Meaning / response |
|---|---|
| `ready_to_cache` | Raw metadata is terminal; build/publish next. It is not deletion authorization |
| `verify_cache` | Existing cache evidence needs re-verification; rerun the configured cache workflow |
| Quarantined verdict / exit 2 | Preserve raw data; inspect verdict `quarantine_reason`, status reason, full dispatch/step logs, scope and count policy |
| Fingerprint mismatch | Verify system/profile/cuts/data provenance; separate scopes. Do not replace expected JSON just to match an unintended cache |
| Malformed row / unknown PDG / broken OSCAR block | Inspect the raw event and matching metadata dictionaries. Diagnostic acceptance flags do not establish deletion-safe data |
| Cache deficit | Distinguish recorded dropped bad events from genuine unfinished generation; only deliberate short designs use explicit shortfall acceptance |
| Published `cache.verified` but review blocked | Publisher verification is only one layer; wrapper verdict, reaper audit, history, and raw state must agree |
| `skipped_mtime_newer_than_cache_start` in reaper audit | Raw output is too recent relative to the cache's recorded start, or changed after caching. Stop writers and inspect timestamps. A cache built immediately after generation needs a deliberate fresh build after the 60-second quiet period; waiting later does not change its recorded start. `--force-rebuild` alone does not replace an already matching published cache; inspect the backend policy before recovery |
| Corrupt, duplicate, or anomalous records | Resolve history rather than bypassing review; nonzero review does not emit an approved replacement manifest |
| Cache/reap refuses local execution | Run in a CPU Slurm allocation, activate the same profile, and size the local pool for its concurrency |
| Reaper rejects manifest filename | Use review's reap-named manifest; do not substitute the general cache candidate manifest |
| Empty manifest | Can be a normal no-work result; inspect survey/review rather than forcing work |

Before optional [reaping](nersc-campaign.md#4-review-then-optionally-reap), stop all writers/transfers and preserve cache, records, inputs and fingerprint. A full reap removes event directories and writes `reaped.done`; absent particles alone do not establish reap history.

Cache staging and publication may both occupy space. Reaping raw frees neither cache staging nor surface archives. Check each storage budget separately.

## Analysis

Missing artifacts reported by `analysis audit` are not necessarily a nonzero exit; inspect counts. Extraction needs `analysis run --submit`. Aggregation writes immediately, can skip failed designs, and records failures in matrix metadata. Check `n_failed` and actual `design_index` before training anything.

A root-flow NaN with a nonpositive signed cumulant can be statistically valid; inspect the signed cumulant and uncertainty. Empty bins in tiny smoke samples are not useful physics. Run `validate`, then inspect labels, scope, finite values and uncertainties using [the analysis guide](analysis.md).

Mixed artifact fingerprints require separate output sets or consistent re-extraction, not concatenation. Changing bins requires new aggregation and validation; changing stored statistics/scope can require earlier stages.

## Inference

Use the separate [inference environment](../inference/README.md#environment-and-paths), then follow the [production workflow](../inference/PRODUCTION.md) for your chosen system and inputs. Preserve each fit's log, posterior and `*_run.json`; output directories can be overwritten by another invocation.

| Symptom | Check and next step |
|---|---|
| Missing metadata, wrong `system` or wrong `analysis_profile` | Stage the matrix and its matching same-stem `.meta.json`. O-O requires genuine `oo_cross_full_pt027_flat/v3` products; editing a profile string does not change the observables |
| Missing flow usage or nonzero same-sample fallback rejected | Use the matching aggregation metadata and inspect the actual fitted-bin pair-weight contributions. The default allowed share is zero; do not remove the guard to make preflight pass |
| Some multiplicity-space measurements are skipped | Read the common model multiplicity range and usable-design counts. Only points covered by every retained design enter; the number of table rows is not the fitted-vector length |
| Process exits zero but posterior says `NOT converged` | Inspect per-parameter Rhat/ESS, traces and prior boundaries. The standard 40,000 retained steps are a budget, not a guarantee; see [convergence review](../inference/PRODUCTION.md#check-results-before-interpreting-intervals) |
| Log is quiet during a full-grid fit | Training and sampling can take hours, and the stock sampler has no per-step progress output. Run with `python -u` and preserve the combined output/error log |
| Corner labels collide | Lower `--corner-font-scale` for a new fit or regenerate saved corners with `regen_corners.py --system OO5360 --run-dir PATH --font-scale 1.8`; this requires no MCMC or GP training |
| Saved predictive plotter reports changed inputs | Supply the recorded matrix/design/data or identical relocated copies. Optional selections are restored from the saved configuration; do not remove provenance checks to force different data into an old posterior |
| Cache refuses reuse after changing inputs/settings | Use a new output directory, or deliberate `--refit` on a tool that supports it. Reusing matching cached results does not extend a chain; the main fitter has no checkpoint/continuation CLI |

## OSG

For a check without copying inputs or running events, use [read-only setup validation](../osg/README.md#validation-without-copying-inputs-or-running-events). Reuse an initialized NERSC campaign's original event count; a staging dry-run needs it for gap calculation and directory padding, not to launch events. It does not check the source bundle. Check the raw root and bundle separately; the sweep dry-run only prints a plan.

If a helper fails at `/stage_osg_design.py`, `OSG_SCRIPTS` is unset. Source the complete `campaign_env.sh` from the intended AP work directory in that shell or tmux session. If `.venv/bin/activate` is missing, complete environment creation in [setup section 3](../osg/README.md#3-deploy-the-ap-helpers-and-python-environment) before running the checks. The documented venv should report Python under the selected work directory, not `/usr/bin/python`. Use a separate directory/environment/helper copy/log when testing alongside a running setup.

Existing submit files may use `container_image` and `+ProjectName` directly; a search for `IMAGE =` and `PROJECT =` alone will then return nothing. Read the existing settings and apply them to the new test copy. The container filename does not prove that its baked code/data match the checkout. Changing `OSG_CAMPAIGN` alone changes only AP slots; the drain destination comes from `NERSC_RAW_ROOT`.

Use the [OSG recovery procedure](osg-campaign.md#halts-restarts-and-drain-outages). Renew expired SSH certificates; reconcile Condor held/removed jobs, AP outboxes and NERSC terminal counts rather than relying on drain idle exit.

Resolve whole-design ownership conflicts without bypassing the guard. Drain failures retain AP output; verification checks terminal state/size rather than full checksums.

The sweep pads design numbers to NERSC's three digits (`design_027`) by default; manual stage/drain commands take the literal suffix `000`/`027`. Inspect printed names in a sweep preview before staging. The full event target also sets event-directory padding; use the splitter's `--n-events` cap to stage fewer events without changing that target. Stage/sweep have different dry-runs: staging queries remote ownership, while sweep dry-run prints a plan without contacting NERSC.

For a missing charge dictionary, check in-image `HIC_CHARGE_DICT` and canonical `/opt/data/iS3D/PDG/charge_dict.dat`. Host helper changes do not alter a SIF: rebuild/version the image for baked code/data changes.
