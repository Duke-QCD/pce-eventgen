# NERSC campaign guide

For a first encounter with the code, start with the [handbook](README.md)
and [first-run tutorial](getting-started.md). During a campaign, use the
[command/flag reference](reference/commands.md),
[INI key reference](reference/configuration.md), and
[troubleshooting guide](troubleshooting.md) alongside this procedure.

The maintained sequence is initialization/generation → survey → cache/publish →
review/reap → observable extraction/aggregation. `models/campaign.py` dispatches
the existing tools using one campaign INI. Inference remains separate.

Start with [NERSC setup](../nersc/SETUP.md) for an installation and personal profile.
Use the [OSG runbook](osg-campaign.md) for AP staging, HTCondor
submission and draining. OSG output joins this workflow at the NERSC raw tree;
keep NERSC and OSG generation in separately owned designs.

## Select your profile

Run every command from the repository root with your profile active. Commands
that submit jobs, change event directories or delete data only preview unless
given `--submit` (or `--apply` for `repair` and `initialize`). For a generated
personal profile:

```bash
cd "$HOME/pce-eventgen"                     # adjust to your checkout
export PROFILE="$PWD/config/users/$USER-production"  # use $USER-smoke for setup's smoke campaign
source "$PROFILE/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
```

Each new shell and allocation needs the profile activated again. Cache commands
take their acceptance cuts from the INI's fingerprint, so no other environment
file is needed.

A generated profile starts at design row 0, initializes new designs, refuses to
touch existing design directories unless `--allow-existing` is given, and
applies repairs and archive finalization when a command acts. Inspect the INI
before using it. For small smoke campaigns, apply the resource and bin settings
in [setup section 6](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign)
first. Use the same `$CFG` throughout a campaign.

To look up backend options, append `--help` to the relevant command, for example
`python models/campaign.py --config "$CFG" cache --help` or
`python models/campaign.py --config "$CFG" analysis run --help`.

### 1. Generate or resume events

Before submitting, set `OMP_NUM_THREADS` to the selected `[submit] cpus_per_task`
and set `OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`; the launcher inherits those
values. For setup's two-CPU smoke layout, use `export OMP_NUM_THREADS=2
OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1`. Production layouts may need a different
OpenMP count. Initialization runs TRENTo, so use the
[compute-step example in setup](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign)
for compute-targeted builds or substantial initialization work.

```bash
python models/campaign.py --config "$CFG" events --print-config   # resolved settings only
python models/campaign.py --config "$CFG" events                  # preview
python models/campaign.py --config "$CFG" events --submit         # act
```

`--print-config` prints the resolved options and exits. The preview lists the
designs that would be initialized, runs repair as a dry-run audit and prints
each design's route and the `sbatch` commands it would issue, without changing
event directories or submitting anything. `--submit` initializes new designs
(when `[run] initialize = true`), runs the repair pass (applied when `[repair]
apply_repairs = true`), chooses each design's mode and submits the jobs. Wait
for those jobs before modifying or caching the selected designs.

Keep each reported `Submitted batch job ...` ID. On the login node:

```bash
squeue -u "$USER"
sacct -j 12345678 --format=JobID,State,ExitCode,Elapsed  # replace with the reported job ID
```

Do not treat an absent queue entry, an exit code of zero, or the submitter
returning as proof of complete generation. Inspect the job logs and the terminal
event counts in step 2. A short walltime can leave valid partial progress.

`[run] row_start` and `row_end` are zero-based design-table rows; the end is
inclusive. Rows map to `design_000`, `design_001`, etc. Keep `nevents` fixed for
an initialized campaign: it determines the event bound and directory padding.
A design bundle contains `parameters.dat`, `music_input`, and `initial.hdf`.
The initializer refuses to overwrite the initial-condition file; inspect a
partial bundle before retrying it.
Generated profiles set `refuse_existing = true`: `events --submit` stops before
doing anything if a selected design directory already exists. After checking
for active writers and auditing an interrupted design, resume it with:

```bash
python models/campaign.py --config "$CFG" events --submit --allow-existing
```

This keeps the existing bundle, its initial conditions and its event bound;
nothing is overwritten. Use it only for the selected designs you intend to resume.

Repair and initialization can also run on their own, between passes:

```bash
python models/campaign.py --config "$CFG" repair             # audit only
python models/campaign.py --config "$CFG" repair --apply     # apply repairs, never submit
python models/campaign.py --config "$CFG" initialize --apply # initialize new designs only
```

The audit writes per-design audit logs and `campaign_state.json` but changes no
event. Applied repair clears dead locks and removes safely regenerable event
directories; it does not ask Slurm whether a lock's owner is alive, so run it
only with no queued or running workers for the selected designs.

### 2. Survey and select cache candidates

```bash
python models/campaign.py --config "$CFG" status
python models/campaign.py --config "$CFG" status --only needs_run --details --max-rows 0
python models/campaign.py --config "$CFG" status --only review --details
python models/campaign.py --config "$CFG" prepare-cache
```

`status` reads raw metadata and cache/reap history and refreshes the configured
survey JSON. `prepare-cache` runs `status` and writes `[pipeline] cache_manifest`;
it performs no caching or deletion. Review that selection before executing it.
Open `[pipeline] cache_manifest`; each non-comment row is `design_name system`,
such as `design_000 AuAu200`. Generated personal profiles place it at
`$HIC_CAMPAIGN_OUTPUT_ROOT/records/$(basename "$HIC_RAW_ROOT")/cache.manifest`.
An empty manifest means no selected work; resolve the survey's reasons instead
of forcing an incomplete design into the cache.

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

A terminal event has one nonempty particle-list variant and `afterburner.done`,
or a clean `hydro.no_surface` marker. `.unfinished` is an accepted particle
filename when its completion marker is present. No-surface events count toward
the target. Missing particles alone do not prove that an event was reaped.

`raw_ready_for_cache` describes raw-input readiness, not permission to reap.
`terminal_frac` is the terminal fraction of expected event slots;
`scanned_expected` includes missing slots when a target is supplied.
Status labels use historical records/markers, not fresh cache-payload verification.

The survey finds existing design directories and record-only designs. A design
that has never been created and has no record is outside its scope; use the
generation row selection to initialize it. `reaped.done` avoids an unnecessary
per-event scan of an already reaped design.

`--details` shows reasons with a default limit of 20 rows; `--max-rows 0` shows
all matching rows. `--only` and review's `--show` filter display only. They do
not restrict the survey JSON, written manifest, or later execution.

### 3. Cache and publish

Leave at least 60 seconds with no output writers/transfers before starting a
new cache build. The reaper audit's default timestamp margin requires raw
particle files to predate the recorded cache start by 60 seconds. Queuing often
provides that delay, but immediate interactive or tiny smoke runs may not.

```bash
python models/campaign.py --config "$CFG" cache
python models/campaign.py --config "$CFG" cache --submit
```

The first command checks resolved inputs and writes `_submit_cache.sbatch` in
the record directory. It creates the needed record/log directories but submits
no job. `--submit` submits the cache array. Wait for completion before review.
Neither command deletes raw events.
Record the cache array job ID and use the same `squeue`/`sacct` checks as for
generation. Its Slurm output is `cachereap_cache_<jobid>_<arrayindex>.out` under
`[pipeline] slurm_log_root`. A successful submission is not a completed cache.

For each design the wrapper builds or reuses a suitable cache, publishes and
verifies it on CFS, then runs a reaper dry-run and writes a verdict. A published
marker alone is not a clean wrapper verdict. A quarantined cache can remain
available for diagnosis; inspect its record and logs before retrying.

Batch jobs pack up to `[cache] designs_per_task` designs on one node.
`nprocs` is the process count per design, `cores` is the requested CPU count,
and `throttle` limits concurrent array tasks. The rendered job freezes selected
manifest rows, resolved paths, scope cuts and corruption limits. Later INI or
manifest edits do not change that job; a changed fingerprint file is refused
by its on-node preflight.
`cores` is passed to Slurm's `--cpus-per-task`, so it counts logical CPUs.
The number of simultaneous designs in one array task is
`min(designs_per_task, cores // nprocs)`. Require `nprocs <= cores` and use one
node per task. The job pins the submitting Python interpreter and explicit
cache scope settings; submit from the intended activated environment.

For interactive caching, use one existing CPU allocation:

```bash
python models/campaign.py --config "$CFG" cache --execution local
python models/campaign.py --config "$CFG" cache --execution local --submit
```

`[cache] jobs` or `--jobs` controls local design concurrency. Size the allocation
for `jobs × nprocs`; this local pool stays on one node. Do not independently
launch overlapping manifests against the same design/cache paths.
Enter the compute shell in [the allocation example below](#allocations-and-optional-event-filling)
before executing the local variant. Merely having `SLURM_JOB_ID` in a shell is
not evidence that its Python process is running on a compute node.

### 4. Review, then optionally reap

```bash
python models/campaign.py --config "$CFG" review --details
python models/campaign.py --config "$CFG" review --json "$PROFILE/cache-review.json"
```

Review reads records for the configured system and writes `[pipeline] reap_manifest`
from clean, ready-to-reap records. Corrupt, duplicate or novel/anomalous records
block manifest emission and produce a nonzero exit. Display filters do not
narrow the written manifest. Review is not a new deep cache verification.
Inspect the printed counts, reasons and reap-manifest path. A smoke run with
one clean design and both particle events present should select that one design.
The JSON filename is a review report, not the manifest consumed by `reap`.

Inside the allocation's compute shell, with the same environment/profile loaded:

```bash
python models/campaign.py --config "$CFG" reap
python models/campaign.py --config "$CFG" reap --submit
```

The first command previews execution. The second runs local reapers, independently
verifies the current cache and raw state, and deletes the particle lists of
eligible events. When every cached event of a design is reaped cleanly, it also
writes `reaped.done` and removes that design's event directories. Keep the reviewed
manifest filename descriptive, such as `reap_ready.manifest`; the submitter
requires a reap-named manifest by default. Do not bypass a refusal to substitute
the general cache manifest.

Reaping requires a Slurm allocation and no queued/running event writers, OSG
jobs or drain transfers for those designs. `[cache] jobs` controls local reap
concurrency; it does not become a multi-node pool merely because more nodes
are allocated. Preserve records and published caches. Reaping frees raw scratch;
it does not clear CFS cache staging or surface archives.

Reaping is optional. Analysis reads published caches and can run before or
after raw reaping. Never reap a short design that you intend to fill later.

### 5. Extract, aggregate and validate observables

Run cache-reading analysis inside an allocation with access to the configured
published caches, fingerprint and output directory. Enter the compute shell
shown below first, then run:

```bash
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis run          # preview extraction
python models/campaign.py --config "$CFG" analysis run --submit
python models/campaign.py --config "$CFG" analysis audit
python models/campaign.py --config "$CFG" analysis aggregate
python models/campaign.py --config "$CFG" validate
```

Audit reports complete/missing/suspect artifacts against eligible published
caches. Inspect its counts: missing artifacts alone do not make audit exit
nonzero. Extraction skips complete, scope-matching artifacts unless `--force`
is requested; failures are recorded in `analysis_failures.txt`.

Only `analysis run` is a preview until `--submit`. `aggregate` writes the model
matrix immediately; `validate` immediately checks charged-cumulant consistency.
Changing centrality bins or reduction settings requires a new aggregate and
validation. Re-extract when the stored sufficient statistics or scope must change.

Outputs under `[pipeline] analysis_out_dir` include per-design
`<design>_<system>_stageA.npz` / `.meta.json` and `model_matrix.npz` with its
sidecars. Join downstream inputs by the stored `design_index`, which need not
be contiguous. The [analysis-input reference](reference/analysis-inputs.md) documents the
matrix, uncertainties and scientific choices for the separate inference workflow.

Additional analysis tools use the same INI:

```bash
python models/campaign.py --config "$CFG" inventory --design design_000 --list
python models/campaign.py --config "$CFG" analysis-design validate --design design_000
python models/campaign.py --config "$CFG" analysis-design reduce --design design_000 --json "$PROFILE/design_000.json"
python models/campaign.py --config "$CFG" analysis feeddown
```

`feeddown` immediately reads the published caches and writes the separate
`feeddown_yields.npz`; it does not modify the model matrix. It shares estimator,
bins and job count with analysis and takes PID/particle-combination settings
from `[feeddown]`. Join by `design_index` and use the reference's inventory-acceptance
and correlated-ratio uncertainty guidance.
Here `design_000` is the first row used by setup; substitute an existing cached
design in your own campaign. Per-design validation/reduction also needs its
extracted stage-A artifact, so run extraction first.

## Configuration and selection

| Section | Purpose |
|---|---|
| `[files]` | Base parameter template, design table, batch launcher and MUSIC input template |
| `[run]` | Raw root, system, design prefix/rows, fixed event count and initialization |
| `[submit]`, `[repair]` | Generation resources, retry/routing and repair/archive policy |
| `[pipeline]` | Records, survey, manifests, staging/published caches, fingerprint, provenance tables and analysis output |
| `[cache]` | Cache corruption policy, concurrency and array resources |
| `[analysis_campaign]` | Cache-based observable extraction/reduction settings |
| `[feeddown]` | Inventory-derived PID yields and their separate output |

CLI values override INI values, then each tool's defaults apply. Paths expand
`~` and environment variables and resolve relative to the launch directory.
Keep the launch directory consistent when the INI contains relative values;
an absolute INI filename does not change their base directory. Unresolved path
variables are errors.

Generation's row range does not restrict the survey, manifests or analysis.
Those tools use their own root/prefix, manifests or selection flags. To select
one extraction, use `analysis run --designs design_027 --submit`. To restrict cache
or reap execution, supply a separately reviewed `--manifest /absolute/path/to/file`.
For selection overrides, run `status` and `manifest` individually;
`prepare-cache` takes its selection settings from the INI.

Generated INIs permit up to 50 dropped bad events and 1000 malformed OSCAR rows
during cache building. These are separate
limits: dropped events are unsalvageable inputs excluded by the builder;
malformed rows are skipped within its configured row allowance. They are
frozen into each cache job. They do not authorize incomplete generation.
A deficit fully explained by recorded dropped events follows that cache policy;
a genuine short generation run requires explicit shortfall acceptance.

For an intentionally short design, first survey, then make a separate candidate
selection and inspect it:

```bash
python models/campaign.py --config "$CFG" status
python models/campaign.py --config "$CFG" manifest \
  --min-terminal-frac 0.99 --out "$HOME/short-cache.manifest"
# Review that file and keep only designs deliberately accepted as short.
python models/campaign.py --config "$CFG" cache \
  --manifest "$HOME/short-cache.manifest" \
  --accept-shortfall --shortfall-reason 'Reviewed generation shortfall'
```

The last command is still a preview. Add `--submit` only for the reviewed
selection; do not put shortfall acceptance on a general campaign manifest.
Changing a corruption limit or supplying a reason does not fix an invalid cache.

The expected fingerprint determines the cache physics cuts; configured cache
commands load those cuts and verify the generated fingerprint before work.
When changing systems, update `[run] system`, the fingerprint, any nucleus-table
paths, analysis bins/acceptances and every campaign root together. Changing
`--system` alone does not select a complete new campaign. The personal setup
helper starts with AuAu200; OO5360 also needs the nucleus table and matching
profile described in [setup](../nersc/SETUP.md).

## Allocations and optional event filling

| Work | Where to run |
|---|---|
| Submission, small repair audits, manifest preparation and record review | Login node; repair only between worker passes |
| Initialization using compute-targeted TRENTo, large repairs/surveys | Compute step/allocation; repair only between worker passes |
| Full-campaign survey | Allocation recommended; it performs many metadata operations |
| Cache with default batch execution | Submit from login; workers run in their requested allocation |
| Cache with `--execution local`, reap, extraction and cache inventory/feeddown | CPU allocation, sized for configured concurrency |
| Aggregate and matrix validation | Allocation with enough memory for the artifacts/matrix |

Choose account, walltime and CPUs using NERSC's
[interactive-job guidance](https://docs.nersc.gov/jobs/interactive/). For setup's
small smoke configuration (`cache.jobs = 1`, `cache.nprocs = 2`, analysis jobs 1),
start on the login node with your personal profile selected:

```bash
export PROJECT=YOUR_ACCOUNT  # replace; same account as your personal launcher/INI
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint cpu --account "$PROJECT"
# After the allocation is granted, launch ONE shell on its compute node:
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores --pty bash
cd "$HOME/pce-eventgen"      # adjust to your checkout
source "$PROFILE/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
# Run local cache/reap or the analysis commands from the sections above here.
exit  # leave the compute shell after the commands finish
exit  # release the interactive allocation when finished
```

`salloc` reserves resources; `srun` places the Python process on a compute node.
Use `--ntasks=1` for a local Python pool to avoid launching duplicate campaigns.
Increase the compute shell's `--cpus-per-task` for production: local cache
concurrency needs at least `cache.jobs × cache.nprocs` logical CPUs; reap and
analysis need capacity for their configured job count. The pools stay on one
node. The shared cache submitter refuses local execution outside an allocation.
Load any extra runtime modules your installation needs in this shell too.
An allocation ending can interrupt unfinished work; preserve logs and inspect
state before resuming.

For near-complete event designs, `fill-plan` writes plan rows to stdout and
diagnostics to stderr:

```bash
python models/campaign.py --config "$CFG" fill-plan > "$PROFILE/fill.plan"
cat "$PROFILE/fill.plan"
```

The plan is selected from the whole configured raw root/prefix, independently
of generation's row range. Inspect its `design ntasks mode restore short system`
rows. By default it includes designs missing **1–499** events (`--max-short 500`
is an exclusive bound), skips complete/reaped designs, and reports stuck events
to stderr. A successful empty plan means no fill work; do not run `fill` on it.
It is a current disk snapshot; regenerate after each wave and audit/repair stuck
directories before expecting them to advance. Some archived tails need a later
afterburner pass following the hydro/both pass.

`fill` launches its own `srun` steps. Run it from the allocation shell, before
entering a CPU-consuming `srun --pty bash` step, so those steps can obtain CPUs.
With no active writers for the selected designs, request an allocation and use
the same profile/config:

```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint cpu --account "$PROJECT"
cd "$HOME/pce-eventgen"  # adjust
source "$PROFILE/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
test -s "$PROFILE/fill.plan" && python models/campaign.py --config "$CFG" fill "$PROFILE/fill.plan"
exit  # release this allocation after all fill steps finish
```

Fill loads the profile's `HIC_PATHS_FILE` (set by `activate.sh`; fill refuses to
start without it) and activates its `HIC_SOFTWARE_ROOT` Conda environment. The
campaign config supplies the raw paths, event bound,
CPUs/task, retry limit, logs and archive root.

The plan includes the collision system and is rejected if it disagrees with
the config. Each `srun` step stays on one node; steps queue within the allocation
as CPUs become available. Failed steps give a nonzero exit and per-design logs.
`[submit] cpus_per_task` supplies fill's default `CPT`; optionally export
`CPT_AFTERBURNER=2` for serial afterburner tails, and size the allocation for the
chosen packing. Set a fresh `[submit] event_log_root` for a fill wave if you need
to preserve previous `dispatch_<design>.out` files; those dispatch logs are
overwritten. Re-survey after the wave before caching.

## Logs, retries and direct tools

Cache/reap records live in `[pipeline] record_dir`; per-design dispatch logs are
`<design>_<system>_cache_dispatch.log` / `_reap_dispatch.log`, with additional
step logs and `*_stage1.json` / `*_stage2.json` verdict records. Inspect the
reported paths after a failure. A record is written at a terminal verdict;
its absence does not prove the cache payload is absent.

A rerun can verify/reuse a published cache or reusable staging rather than
rebuild from raw. CFS publication and staging can hold two copies; inspect
space use before large runs. Scratch, CFS and the surface archive are separate
storage budgets. Reaping raw does not free the latter two.

Preserve raw inputs when a wrapper quarantines. Check `status_reason`, the
record's `quarantine_reason`, scope/provenance paths and backend logs. Review
refuses incomplete/ambiguous history; do not ignore a nonzero exit to proceed
to reap. An empty manifest is a successful no-work result.
For timestamp quarantine, the quiet period must precede a **new** cache build.
Waiting after an early cache was published does not change its recorded start
time. A matching verified published cache is reused even if the wrapper receives
`--force-rebuild`; do not retry that loop or change raw timestamps. Preserve the
diagnostic products and follow the
[timestamp troubleshooting procedure](troubleshooting.md).

To inspect dispatch without invoking any backend, use:

```bash
python models/campaign.py --config "$CFG" --print-commands cache
```

Native tools also accept the INI, for example
`python models/submit_cache_reap_campaign.py --config "$CFG" --stage cache`.
Calling `cache_reap_one_design.py cache` directly performs cache/publish work
immediately; use the campaign preview for normal operation.
