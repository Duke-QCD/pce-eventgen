# NERSC campaign guide

Complete [NERSC setup](../nersc/SETUP.md) first. This guide operates an initialized installation through generation → survey → cache/publish → review/optional reap → analysis. Use the [command reference](reference/commands.md), [INI reference](reference/configuration.md) and [troubleshooting](troubleshooting.md) as needed.

For OSG generation, follow its [runbook](osg-campaign.md), then join here at the raw survey. Assign whole designs to one generation platform. Inference runs separately.

## Select your profile

Run every command from the repository root with your profile active. `events`, `cache`, `reap` and `analysis run` preview until `--submit`; `repair` and `initialize` act with `--apply`. Other commands, including `fill`, can execute or write immediately; see [command effects](reference/commands.md#campaign-commands-and-their-effects). For a generated personal profile:

```bash
cd "$HOME/pce-eventgen"                     # adjust to your checkout
export PROFILE="$PWD/config/users/$USER-production"  # use $USER-smoke for setup's smoke campaign
source "$PROFILE/activate.sh"
```

Activate the same profile in every shell/allocation. Activation sets `REPO`, `CFG`, `PROJECT`, runtime paths and Python thread limits. Cache cuts come from its INI-selected fingerprint, so no separate cache environment file is needed.

Inspect the INI: generated profiles select row 0, enable initialization and applied repairs/archive finalization, and protect existing designs. Use [setup's resource/bin overrides](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign) for smoke runs.

Append `--help` to a command for its backend options, for example `python models/campaign.py cache --help`.

### 1. Generate or resume events

Set generation resources in `[submit]`; the launcher derives its OpenMP count from the actual Slurm CPUs per task and sets core binding automatically. With `cpus_per_task = 32` and `ntasks_per_node = 2`, each worker uses 16 OpenMP threads. Python library thread limits come from the profile; no manual thread exports are required. These batch resources are separate from the small initializer/submission step. Initialization runs TRENTo, so use [setup's compute step](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign) for compute-targeted builds or substantial initialization.

```bash
python models/campaign.py --config "$CFG" events --print-config   # resolved settings only
python models/campaign.py --config "$CFG" events                  # preview
python models/campaign.py --config "$CFG" events --submit         # act
```

`--print-config` prints settings and exits. The preview lists initialization, audits repair and prints routes/Slurm commands. `--submit` initializes new designs when enabled, applies the configured repair policy and submits selected modes. Wait for jobs before modifying or caching their designs.

Keep each reported `Submitted batch job ...` ID. On the login node:

```bash
squeue -u "$USER"
sacct -j 12345678 --format=JobID,State,ExitCode,Elapsed  # replace with the reported job ID
```

Submission success, queue disappearance and exit zero do not prove completion. Inspect logs and the terminal counts in step 2; walltime expiry can leave partial progress.

`row_start`/`row_end` select zero-based design rows inclusively (`design_000`, etc.). Keep the initialized `nevents` and matching `parameters.dat`, `music_input`, `initial.hdf` bundle unchanged. To resume initialized designs, activate the profile and submit **directly from the login node, without an allocation or `srun`**. First confirm no queued/running workers or OSG transfers can write the selected designs, and audit their state:

```bash
python models/campaign.py --config "$CFG" repair
python models/campaign.py --config "$CFG" events --submit \
  --allow-existing --no-initialize
```

`--allow-existing` permits the selected resume without replacing initialized inputs. `--no-initialize` prevents TRENTo from running for new designs; the command uses the existing bundles, applies the configured repair policy and submits batch jobs with `[submit]` resources. Creating new initial conditions still uses the compute-allocation procedure above.

Repair and initialization can also run on their own, between passes:

```bash
python models/campaign.py --config "$CFG" repair             # audit only
python models/campaign.py --config "$CFG" repair --apply     # apply repairs, never submit
python models/campaign.py --config "$CFG" initialize --apply # initialize new designs only
```

The audit writes per-design audit logs and `campaign_state.json` but changes no event. Applied repair clears dead locks and removes safely regenerable event directories; it does not ask Slurm whether a lock's owner is alive, so run it only with no queued or running workers for the selected designs.

### 2. Survey and select cache candidates

```bash
python models/campaign.py --config "$CFG" status
python models/campaign.py --config "$CFG" status --only needs_run --details --max-rows 0
python models/campaign.py --config "$CFG" status --only review --details
python models/campaign.py --config "$CFG" prepare-cache
```

`status` refreshes the raw/history survey JSON. `prepare-cache` also writes `[pipeline] cache_manifest`, without caching/deleting. Inspect that file: rows are `design_name system`, e.g. `design_000 AuAu200`. Generated profiles place it under `$HIC_CAMPAIGN_OUTPUT_ROOT/records/$(basename "$HIC_RAW_ROOT")/`. An empty manifest means no selected work; investigate survey reasons rather than forcing incomplete designs through.

| Status | Meaning and next action |
|---|---|
| `needs_run` | Events remain; details distinguish hydro/both and afterburner work. |
| `waiting` | Locks remain; check active jobs or stale locks. The survey does not query Slurm. |
| `ready_to_cache` | Raw inputs are terminal with no detected blockers; cache/publish next. |
| `verify_cache` | Published evidence needs verification; run cache/publish again. |
| `ready_to_reap` | A clean cache record found raw files to delete; review before explicit reap. |
| `cached` | Clean cache record with nothing to reap; continue to analysis. |
| `reaped` | Successful reap evidence exists; continue to analysis. |
| `review` | Conflicting metadata, scope mismatch, quarantine or ambiguous history needs inspection. |

A terminal event has one nonempty particle-list variant and `afterburner.done`, or a clean `hydro.no_surface` marker. `.unfinished` is an accepted particle filename when its completion marker is present. No-surface events count toward the target. Missing particles alone do not prove that an event was reaped.

`raw_ready_for_cache` measures input readiness, not deletion permission. With an event target, `scanned_expected` counts all expected slots, including missing ones; `terminal_frac` is the fraction that are terminal. Status labels reflect historical markers/records rather than fresh cache-payload verification.

The survey includes existing and record-only designs, but not never-created designs without records. Generation row selection controls initialization; `reaped.done` skips unnecessary raw scans.

`--details` shows reasons with a default limit of 20 rows; `--max-rows 0` shows all matching rows. `--only` and review's `--show` filter display only. They do not restrict the survey JSON, written manifest, or later execution.

### 3. Cache and publish

Stop writers/transfers and wait at least 60 seconds before a new cache build: the reaper audit requires particle mtimes to predate the recorded cache start by that margin.

```bash
python models/campaign.py --config "$CFG" cache
python models/campaign.py --config "$CFG" cache --submit
```

Preview checks inputs, creates record/log directories and writes `_submit_cache.sbatch`; `--submit` launches its array. Record the job ID and wait before review, using the scheduler checks above. Cache logs are `cachereap_cache_<jobid>_<arrayindex>.out` under `[pipeline] slurm_log_root`. Neither command deletes raw events.

Each task builds/reuses a cache, publishes/verifies on CFS and records a reaper dry-run verdict. A publication marker alone is insufficient; inspect quarantined records/logs before retrying.

Each one-node array task runs up to `min(designs_per_task, cores // nprocs)` designs. `cores` counts logical CPUs, `nprocs <= cores`, and `throttle` limits concurrent tasks. The job freezes manifest rows, paths, scope and corruption limits; later INI/manifest edits do not affect it, and changed fingerprints fail preflight. It also pins the submitting Python, so submit from the intended environment.

For interactive caching, use one existing CPU allocation:

```bash
python models/campaign.py --config "$CFG" cache --execution local
python models/campaign.py --config "$CFG" cache --execution local --submit
```

Local concurrency is `[cache] jobs`; reserve at least `jobs × nprocs` CPUs on one node. Enter the [compute shell below](#allocations-and-optional-event-filling) before executing: `SLURM_JOB_ID` alone does not place Python on a compute node. Do not overlap manifests on the same cache/design paths.

### 4. Review, then optionally reap

```bash
python models/campaign.py --config "$CFG" review --details
python models/campaign.py --config "$CFG" review --json "$PROFILE/cache-review.json"
```

Review writes `[pipeline] reap_manifest` from clean ready-to-reap records. Corrupt, duplicate or anomalous history blocks emission and returns nonzero. Inspect counts, reasons and the manifest; filters affect display only. Review does not deeply reverify caches. `--json` writes a report, not the reap manifest.

Inside the allocation's compute shell, with the same environment/profile loaded:

```bash
python models/campaign.py --config "$CFG" reap
python models/campaign.py --config "$CFG" reap --submit
```

The first command previews; `--submit` executes. Applied reap independently verifies current cache/raw state, deletes eligible particle lists and, once the design is fully reaped, writes `reaped.done` and removes event directories. Use the reviewed reap-named manifest, such as `reap_ready.manifest`; never substitute the general cache manifest.

Reaping requires an allocation and no NERSC/OSG writers or drain transfers for those designs. `[cache] jobs` controls one-node concurrency. Preserve records/published caches; raw deletion does not free cache staging or archives.

Reaping is optional. Analysis reads published caches and can run before or after raw reaping. Never reap a short design that you intend to fill later.

### 5. Extract, aggregate and validate observables

Run cache-reading analysis inside an allocation with access to the configured published caches, fingerprint and output directory. Enter the compute shell shown below first, then run:

```bash
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis run          # preview extraction
python models/campaign.py --config "$CFG" analysis run --submit
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis aggregate
python models/campaign.py --config "$CFG" validate
```

Audit reports complete/missing/suspect artifacts against eligible published caches. Inspect its counts: missing artifacts alone do not make audit exit nonzero. Extraction skips complete, scope-matching artifacts unless `--force` is requested; failures are recorded in `analysis_failures.txt`.

`aggregate` writes immediately and `validate` checks immediately. For changed bins/statistics, follow [what to rerun](analysis.md#what-to-rerun-after-a-change).

The configured analysis directory receives per-design artifacts and `model_matrix.npz` plus sidecars. Join downstream inputs by `design_index`, which may have gaps; see [the input contract](reference/analysis-inputs.md).

Additional analysis tools use the same INI:

```bash
python models/campaign.py --config "$CFG" inventory --design design_000 --list
python models/campaign.py --config "$CFG" analysis-design validate --design design_000
python models/campaign.py --config "$CFG" analysis-design reduce --design design_000 --json "$PROFILE/design_000.json"
python models/campaign.py --config "$CFG" analysis feeddown
```

Select an existing cached design; per-design validation/reduction also requires its extracted artifact. `feeddown` immediately writes a separate PID-inventory product using `[feeddown]` plus the shared estimator/bins/jobs. It does not change the matrix. Follow [inventory acceptance and correlated-ratio guidance](reference/analysis-inputs.md#optional-feed-down-inventory).

## Configuration and selection

The [INI reference](reference/configuration.md) defines every section/key and CLI precedence. Relative paths resolve from the launch directory, not the INI's directory; use a consistent launch location.

Generation's row range does not restrict the survey, manifests or analysis. Those tools use their own root/prefix, manifests or selection flags. To select one extraction, use `analysis run --designs design_027 --submit`. To restrict cache or reap execution, supply a separately reviewed `--manifest /absolute/path/to/file`. For selection overrides, run `status` and `manifest` individually; `prepare-cache` takes its selection settings from the INI.

Generated INIs allow 50 dropped bad events and 1000 malformed rows; these are separate, frozen cache policies, not acceptance of unfinished generation. For a genuinely short design, create and review a separate selection:

```bash
python models/campaign.py --config "$CFG" status
python models/campaign.py --config "$CFG" manifest \
  --min-terminal-frac 0.99 --out "$HOME/short-cache.manifest"
# Review that file and keep only designs deliberately accepted as short.
python models/campaign.py --config "$CFG" cache \
  --manifest "$HOME/short-cache.manifest" \
  --accept-shortfall --shortfall-reason 'Reviewed generation shortfall'
```

The last command is still a preview. Add `--submit` only for the reviewed selection; do not put shortfall acceptance on a general campaign manifest. Changing a corruption limit or supplying a reason does not fix an invalid cache.

When changing systems, coordinate the system key, fingerprint, nuclear inputs, analysis settings and storage roots using [the configuration reference](reference/configuration.md#changing-a-campaign). `--system` alone does not select a complete campaign.

## Allocations and optional event filling

| Work | Where to run |
|---|---|
| Submission, small repair audits, manifest preparation and record review | Login node; repair only between worker passes |
| Initialization using compute-targeted TRENTo, large repairs/surveys | Compute step/allocation; repair only between worker passes |
| Full-campaign survey | Allocation recommended; it performs many metadata operations |
| Cache with default batch execution | Submit from login; workers run in their requested allocation |
| Cache with `--execution local`, reap, extraction and cache inventory/feeddown | CPU allocation, sized for configured concurrency |
| Aggregate and matrix validation | Allocation with enough memory for the artifacts/matrix |

Choose account, walltime and CPUs using NERSC's [interactive-job guidance](https://docs.nersc.gov/jobs/interactive/). For setup's small smoke configuration (`cache.jobs = 1`, `cache.nprocs = 2`, analysis jobs 1), start on the login node with your personal profile selected:

```bash
export PROJECT=YOUR_ACCOUNT  # replace; same account as your personal launcher/INI
salloc --nodes 1 --qos debug --time 00:30:00 --constraint cpu --account "$PROJECT"
# After the allocation is granted, launch ONE shell on its compute node:
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores --pty bash
cd "$HOME/pce-eventgen"      # adjust to your checkout
source "$PROFILE/activate.sh"
# Run local cache/reap or the analysis commands from the sections above here.
exit  # leave the compute shell after the commands finish
```

At the allocation shell's prompt, run `exit` again to release the allocation.

`salloc` reserves resources; `srun --ntasks=1` enters one compute process for a local pool. Production cache work needs at least `cache.jobs × cache.nprocs` CPUs; reap/analysis need their configured concurrency. Pools stay on one node. Load build-specific runtime modules and preserve logs if the allocation ends early.

For near-complete event designs, `fill-plan` writes plan rows to stdout and diagnostics to stderr:

```bash
python models/campaign.py --config "$CFG" fill-plan > "$PROFILE/fill.plan"
cat "$PROFILE/fill.plan"
```

Inspect the six-field plan (`design ntasks mode restore short system`): it scans the whole raw root/prefix, independently of generation rows. Defaults select 1–499 missing events (`--max-short 500` is exclusive), skip complete/reaped designs and report stuck work to stderr. Do not execute an empty plan. Repair stuck events and regenerate plans after each wave; archived afterburner tails may require a second pass.

`fill` launches its own `srun` steps. Run it from the allocation shell, before entering a CPU-consuming `srun --pty bash` step, so those steps can obtain CPUs. With no active writers for the selected designs, request an allocation and use the same profile/config:

```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint cpu --account "$PROJECT"
cd "$HOME/pce-eventgen"  # adjust
source "$PROFILE/activate.sh"
test -s "$PROFILE/fill.plan" && python models/campaign.py --config "$CFG" fill "$PROFILE/fill.plan"
exit  # release this allocation after all fill steps finish
```

Fill requires `HIC_PATHS_FILE`, activates its software prefix and takes resource/path settings from the INI. It rejects plans whose system differs from the configuration.

Steps remain on one node and queue for CPUs. Failures return nonzero with per-design logs. `[submit] cpus_per_task` supplies `CPT`; `CPT_AFTERBURNER=2` can reduce CPUs for serial tails. Use a fresh `event_log_root` to preserve prior `dispatch_<design>.out` files, which are overwritten. Survey again before caching.

## Logs, retries and direct tools

Inspect verdicts and dispatch logs under `[pipeline] record_dir`; [file names and layouts](reference/files.md#cache-products-and-records) are documented separately. Records appear at terminal verdicts, so a missing record does not imply a missing payload.

Retries can reuse published or staged caches. Budget scratch, CFS staging/publication and archives separately; reaping frees raw scratch only.

On quarantine, retain raw data and inspect `status_reason`, `quarantine_reason` and step logs. Resolve nonzero review exits before reap. For timestamp quarantine, waiting after publication does not change the cache start, and `--force-rebuild` does not replace a matching published cache; follow [timestamp recovery](troubleshooting.md#cache-publication-review-and-reaping).

To inspect dispatch without invoking any backend, use:

```bash
python models/campaign.py --config "$CFG" --print-commands cache
```

Native tools also accept the INI, for example `python models/submit_cache_reap_campaign.py --config "$CFG" --stage cache`. Calling `cache_reap_one_design.py cache` directly performs cache/publish work immediately; use the campaign preview for normal operation.
