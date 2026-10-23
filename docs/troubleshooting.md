# Troubleshooting and recovery

[Handbook](README.md) · [File/marker meanings](reference/files.md)

Start with the failing stage's full log, the resolved configuration, and exact
input paths. Preserve the first useful error message and command, rather than
only the last line. For NERSC events, inspect Slurm stdout/stderr **and** the
rank-specific runner logs under `[submit] event_log_root`. Cache/reap logs live
in `[pipeline] record_dir`. OSG jobs also have per-slot Condor output/error.

## Installation and startup

| Symptom | Check and next step |
|---|---|
| “must install in an active python virtualenv or conda env” | Activate the intended environment, check `command -v python`, then rerun the installer |
| `numpy` has no attribute `typeDict`, h5py import failure, or binary compatibility error | User/site packages may be mixed. Set `PYTHONNOUSERSITE=1`, reactivate a consistent NumPy/h5py environment, and retry the import check below |
| CMake cannot find Boost/HDF5/GSL/Eigen | Check dependency headers and libraries, HDF5 C++ support, selected compiler, `CMAKE_PREFIX_PATH` and `HDF5_ROOT`; see local/NERSC build guide |
| TRENTo link error mentioning `__cxa_call_terminate@CXXABI_1.3.15` | System compiler and conda HDF5 can require different C++ runtimes. Select one matching stack using the [local setup recipe](getting-started.md#2-choose-your-installation-path), then move aside the generated TRENTo build directory to clear cached library paths |
| TRENTo builds but cannot write `initial.hdf` | Rebuild with HDF5 support; an HDF5-disabled executable is insufficient |
| Wrong SMASH revision, or `models/SMASH` empty | Run `git submodule update --init models/SMASH` (pinned commit `0985d6b32fb801404c933d70c86c23759d60ff1f`) and rebuild; do not copy an unrelated binary |
| Missing Pythia / wrong Pythia version | Build 8.309 with the same toolchain and pass its `pythia8-config` to the installer after `--` |
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

Syntax/path checks do not prove model binaries work or data are correct. A
fresh smoke root is the next check after installation, not a full campaign.

## Initialization and generation

**`initial.hdf` already exists:** initialization deliberately refuses to
overwrite it. If this is an existing complete bundle, resume via the campaign
or runner with its original event bound. Generated profiles set
`refuse_existing = true`; pass `--allow-existing` deliberately for an inspected
existing bundle (nothing is overwritten). If this is an abandoned partial test, choose a fresh test root. Do not
delete initial conditions from a production tree to make initialization pass.

**Partial bundle refused:** confirm all of `parameters.dat`, `music_input`,
and `initial.hdf` belong to the same initialization. A failed helper/model can
leave a partial directory. The orchestrator refuses to submit it. Diagnose the
original initialization log before deciding whether to recover or replace that
test tree.

**Events skipped but not complete:** hydro skips existing event directories.
A previous interruption can leave a directory without a usable surface. Stop
all writers for the design, audit repair, and inspect its classification:

```bash
python models/campaign.py --config "$CFG" status --only review --details --max-rows 0
python models/campaign.py --config "$CFG" repair
```

That audit writes reports and routing outputs but does not apply repair.
Read `campaign_state.json`, the bucket/reason, and logs. Apply the supported
repair (`repair --apply`) only after confirming there are no queued/running
workers or OSG transfers. Applied repair can remove locks regardless of age. The status tool
does not ask Slurm whether a lock owner is alive.

**Afterburner retries exhausted:** inspect `afterburner.failed`, persistent
retry count, data selections, and logs. Fix the cause before planning another
attempt. Raising the retry cap alone does not repair corrupt data.

**`archive.failed` after particle output:** check archive destination permissions,
free space, `zstd`, and selected root. With valid completed particles and local
surface, repair's finalization can finish archiving without rerunning transport.
Use the documented applied/finalize/root controls and examine its refusal if
particle structure or archive destinations disagree.

**`hydro.no_surface`:** this can be a valid zero-output event. It is not the
same as MUSIC failing to create a surface file. Count clean no-surface events
toward the target; resolve conflicting markers or particles before caching.

**The job exited successfully but event count is low:** runner success does
not guarantee all expected slots completed. Inspect the survey's missing slots,
hydro/afterburner shortfalls, retry exhaustion and lock counts. Keep `nevents`
fixed across passes. A survey will not discover designs that have never been
initialized and have no record; inspect the intended design-table range too.

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

Raw reaping is optional. It independently verifies cache and raw state before
deleting eligible particle lists; a fully reaped design also gets `reaped.done`
and loses its event directories. Do not run
it while any writer or OSG transfer can touch those designs. Missing raw
particles by themselves do not prove a previous reap. Preserve the published
cache, records, inputs and fingerprint when raw output is removed.

Cache staging and publication may both occupy space. Reaping raw frees neither
cache staging nor surface archives. Check each storage budget separately.

## Analysis

Missing artifacts reported by `analysis audit` are not necessarily a nonzero
exit; inspect counts. Extraction needs `analysis run --submit`. Aggregation writes
immediately, can skip failed designs, and records failures in matrix metadata.
Check `n_failed` and actual `design_index` before training anything.

A root-flow NaN with a nonpositive signed cumulant can be statistically valid;
inspect the signed cumulant and uncertainty. Empty bins in tiny smoke samples
are not useful physics. Run `validate`, then inspect labels, scope, finite
values and uncertainties using [the analysis guide](analysis.md).

Mixed artifact fingerprints require separate output sets or consistent
re-extraction, not concatenation. Changing bins requires new aggregation and
validation; changing stored statistics/scope can require earlier stages.

## OSG

Start with [OSG setup](../osg/README.md) for the first deployment and smoke campaign.
Read [the OSG runbook](osg-campaign.md) before recovery.
An expired SSH proxy certificate blocks staging/drain; inspect actual expiry
and renew it. A drain idle timeout does not prove all jobs finished. Check
Condor held/removed jobs, AP outboxes, and NERSC terminal counts separately.

Staging refuses apparent mixed NERSC/OSG ownership. Preserve design identities
and resolve allocation of whole designs; do not disable the guard to fill
arbitrary gaps. Drain verification checks terminal state/size, not full content
checksums. Transfer/verification failures retain AP output. Updating Python
helpers does not update code or data baked into the image; rebuild and use a
new image version for those changes.

The sweep pads design numbers to NERSC's three digits (`design_027`) by default;
manual stage/drain commands take the literal suffix `000`/`027`. Inspect printed
names in a sweep preview before staging. The full event target also sets event-directory padding;
use the splitter's `--n-events` cap to stage fewer events without changing that
target. Stage/sweep have different dry-runs: staging queries remote ownership,
while sweep dry-run prints a plan without contacting NERSC.

If an image cannot find the charge dictionary, inspect its `HIC_CHARGE_DICT`.
Images built from `osg/container_paths.sh` expect the canonical data layout
(`/opt/data/iS3D/PDG/charge_dict.dat`); stage the data that way before building.
Updating the host copy of that helper does not update an existing SIF; rebuild
and version the image.
