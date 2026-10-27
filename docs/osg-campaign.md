# OSG campaign runbook

Complete [OSG setup](../osg/README.md) first. This runbook stages NERSC initial conditions on the AP, submits per-event HTCondor jobs and drains terminal output back to NERSC for [cache/analysis](nersc-campaign.md). The [flag index](reference/cli-osg.md) lists helper options.

Replace example account/storage paths. OSG helpers do not read the INI: match their NERSC root, system and target to `[run] directory`, `system` and `nevents`.

For a check without copying inputs or running events, complete the [read-only setup checks](../osg/README.md#validation-without-copying-inputs-or-running-events), configure the environment/access below, and stop after the [staging preview](#read-only-staging-preview). The smoke procedure and normal-operation sections contain real transfers and submissions.

Here, a **design point** is one row of the parameter table (`design_027`); the campaign spans many points. NERSC may progress upward while OSG progresses downward through the same campaign/raw root. Assign disjoint point ranges and maintain a stopping boundary. The marker guard does not reserve points or detect queued/running NERSC jobs. Current staging refuses to mix native NERSC and OSG terminal events within one point.

## Scripts and deployment

| File in the repository | Run location and purpose |
|---|---|
| `osg/stage_osg_design.py` | AP: query NERSC completion/ownership, pull the initial-condition bundle and stage its gap |
| `osg/split_design.py` | AP: split the monolithic HDF5 input in native dataset order, preserving event identities |
| `osg/sweep_osg_designs.py` | AP: stage, submit and tear down a descending sequence of designs |
| `osg/drain_osg_events_batched.py` | AP: push terminal output to NERSC and remove verified AP copies |
| `osg/events_fanout.submit`, `osg/job.sh` | HTCondor submission and the per-event worker |
| `osg/heavy-ion.def`, `osg/container_paths.sh` | Container build recipe and its runtime paths |

Sweep and drain run separately. These examples keep Python helpers in `$HOME/osgrun/helpers` and the submit/worker files in `$HOME/osgrun`, beside `stage/`. Deploy/check them using [setup section 3](../osg/README.md#3-deploy-the-ap-helpers-and-python-environment), which combines directory creation and file transfer into one authenticated SSH connection. For an independent test setup, replace `osgrun` with its separate directory name throughout, including the submit file's log path.

Before updates, stop sweep submissions, copy complete helpers and check them. Replace a checked worker atomically, outside active submission/startup, and keep AP copies matched to the reviewed source.

Keep the stage tree under the submit directory (`initialdir = stage/$(slot)`). Shared logs use `$ENV(HOME)/osgrun/fanout.log`; stdout/stderr are per-slot. Verify `IMAGE`, `PROJECT` and resources: defaults are one CPU, 2 GB memory and 4 GB disk, with worker threads clamped to one. Activate the AP Python environment in each session.

Changes to the baked runner, converter, models or data require [a validated, newly versioned image](../osg/README.md#5-build-a-new-image-when-the-approved-image-needs-updating). The image runs MUSIC → iS3D → SMASH; NERSC prepares initial conditions.

`job.sh` transfers separately from the image. Submit edits affect new submissions; worker edits may not reach existing jobs/retries because of spooling/transfer settings. Validate a fresh submission; see [HTCondor spooling](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html#copy_to_spool).

## AP environment and NERSC access

Save this entire block as `$HOME/osgrun/campaign_env.sh` on the **AP**, replacing the example selections with those of the intended campaign. For an isolated setup, use its directory (for example `osgrun-test`) both in `OSG_WORK` and in every command that sources this file. This is a shell convenience file, not a script-read config format. The Python environment must already have been created in [setup section 3](../osg/README.md#3-deploy-the-ap-helpers-and-python-environment).

```bash
export OSG_WORK="$HOME/osgrun"
export OSG_SCRIPTS="$OSG_WORK/helpers"
export OSG_STAGE="$OSG_WORK/stage"
export OSG_CAMPAIGN=my_campaign
export OSG_SYSTEM=AuAu200
export OSG_EVENT_TARGET=10000
export OSG_DESIGN=099      # literal suffix; use 000 for setup's first smoke design
export NERSC_USER=your_nersc_user
export NERSC_HOST=perlmutter
export NERSC_RAW_ROOT=/pscratch/sd/y/your_nersc_user/my_campaign  # the profile's [run] directory
export NERSC_IC_TEMPLATE="$NERSC_RAW_ROOT/design_{design}/$OSG_SYSTEM"
export SSH_PROXY=sshproxy   # command or absolute path to your installed client

export PYTHONNOUSERSITE=1
source "$OSG_WORK/.venv/bin/activate"
```

The AP slot path is `$OSG_STAGE/$OSG_CAMPAIGN/design_N/$OSG_SYSTEM/EVENT`. The drain lands output at `$NERSC_RAW_ROOT/design_N/$OSG_SYSTEM/EVENT`. `--ap-stage-root` takes `$OSG_STAGE`; the drain's `--ap-root` takes `$OSG_STAGE/$OSG_CAMPAIGN`. The two campaign directory names need not match. **Changing `OSG_CAMPAIGN` isolates AP slots only; it does not isolate NERSC output.** A live cross-check needs a separate `NERSC_RAW_ROOT` too.

Keep `OSG_EVENT_TARGET` equal to the original initialized `nevents`, including for a gap or dry-run: it sets the full position range and event-directory padding, not a submission limit. Use the actual system (`OO5360`, for example), not the `AuAu200` example by default. For setup's new isolated smoke campaign, save `OSG_CAMPAIGN=pce-osg-smoke`, `OSG_DESIGN=000`, `OSG_EVENT_TARGET=2` and that profile's literal NERSC raw root here before starting either tmux session. Obtain the raw root from `printf '%s\n' "$HIC_RAW_ROOT"` in the activated NERSC shell; do not copy an unexpanded `${HIC_RAW_ROOT}` from `--print-config`. AP and NERSC usernames can differ. If using a supported environment outside `$OSG_WORK/.venv`, replace the activation line accordingly.

Source the file in each new AP shell or tmux session. First test any existing AP→NERSC SSH configuration with one connection:

```bash
source "$HOME/osgrun/campaign_env.sh"
ssh -o BatchMode=yes "$NERSC_HOST" 'whoami'
```

Expect `NERSC_USER`. If this succeeds, reuse the existing credentials; do not reinstall or renew them merely because a new shell started. If access has not been configured, install the sshproxy client using the current [NERSC MFA instructions](https://docs.nersc.gov/connect/mfa/#sshproxy). Configure the SSH host used by both staging and draining, preserving any working entries:

```text
Host perlmutter
    HostName perlmutter.nersc.gov
    User your_nersc_user
    IdentityFile ~/.ssh/nersc
    IdentitiesOnly yes
```

Adjust `User` to match `NERSC_USER`. Obtain or renew the certificate when needed, then check unattended access:

```bash
source "$HOME/osgrun/campaign_env.sh"
"$SSH_PROXY" -u "$NERSC_USER"
ssh "$NERSC_HOST" 'echo ok'
ssh -o BatchMode=yes "$NERSC_HOST" 'echo ok'
ssh-keygen -L -f "$HOME/.ssh/nersc-cert.pub" | grep Valid
```

The interactive SSH check is needed to resolve a first host-key prompt; it is not required on every login. Require unattended AP→NERSC access to readable bundles and writable destinations before live work. Inspect actual certificate expiry (default 24 hours) and renew before it; subsequent drain connections use refreshed keys. Keep passwords/OTPs out of the environment file.

## Read-only staging preview

On the **AP**, source the complete environment file and preview the selected literal design suffix. This command does not copy inputs, create stage directories, write a manifest, delete files or submit jobs:

```bash
source "$HOME/osgrun/campaign_env.sh"
python "$OSG_SCRIPTS/stage_osg_design.py" \
  --design "$OSG_DESIGN" --nersc-host "$NERSC_HOST" \
  --nersc-ic-dir "$NERSC_IC_TEMPLATE" --pscratch-root "$NERSC_RAW_ROOT" \
  --ap-stage-root "$OSG_STAGE" --campaign "$OSG_CAMPAIGN" \
  --system "$OSG_SYSTEM" --total-nevents "$OSG_EVENT_TARGET" \
  --manifest "$OSG_WORK/manifest_${OSG_DESIGN}.txt" --dry-run
```

It checks SSH and queries NERSC completion/partition markers, then reports the gap. For a fresh two-event smoke bundle, expect a gap of two. An already drained or reaped point can report nothing to stage; native NERSC terminal events cause a guard refusal. The guard does not check for active writers.

The preview **does not check that the source bundle exists or read its contents**. A missing destination directory can still yield the full event gap, so success alone does not verify the campaign path. Check the literal raw root and the three-file bundle separately using [setup's read-only procedure](../osg/README.md#validation-without-copying-inputs-or-running-events). Stop here for validation without copying inputs or running events.

### Command effects

| Command | What it does |
|---|---|
| `stage_osg_design.py --dry-run` | Queries NERSC markers and reports a gap; no input copy, local writes or submissions |
| `stage_osg_design.py` without `--dry-run` | Copies the bundle to the AP, writes per-event inputs and a manifest; runs no models and submits no jobs |
| `sweep_osg_designs.py --dry-run` | Prints a range/plan only; does not contact NERSC or validate individual points |
| `sweep_osg_designs.py` without `--dry-run` | Stages inputs, submits HTCondor jobs and removes drained staging trees |
| `condor_submit -dry-run FILE ...` | Parses a submit file and writes job descriptions using an actual manifest/staged inputs; submits nothing |
| `drain_osg_events_batched.py --dry-run` | Scans AP output and queries NERSC markers when eligible output exists; transfers/deletes nothing |
| Drain with `--no-delete` | Transfers, verifies and writes NERSC markers, retaining AP copies; it is not a dry-run |
| Drain without either retention flag | Transfers/verifies output, commits NERSC markers and deletes verified AP event directories |

## First smoke campaign

This is a **live end-to-end test**: it copies inputs, submits two real event jobs and drains their results to NERSC. Skip it if your current scope is read-only validation of an existing campaign. Use the separate two-event NERSC bundle from [setup](../osg/README.md#2-prepare-a-two-event-input-bundle-on-nersc). Save its selections in `campaign_env.sh` first; sourcing that file in another terminal must keep the smoke root, target `2` and literal design suffix `000`. Keep the sweep stopped during this manual test.

Complete the [read-only staging preview](#read-only-staging-preview) first. Then, with those smoke selections still active, run staging **without** `--dry-run`. This copies the bundle and splits its two inputs on the AP, but does not yet submit jobs:

```bash
source "$HOME/osgrun/campaign_env.sh"
python3 "$OSG_SCRIPTS/stage_osg_design.py" \
  --design "$OSG_DESIGN" --nersc-host "$NERSC_HOST" \
  --nersc-ic-dir "$NERSC_IC_TEMPLATE" --pscratch-root "$NERSC_RAW_ROOT" \
  --ap-stage-root "$OSG_STAGE" --campaign "$OSG_CAMPAIGN" \
  --system "$OSG_SYSTEM" --total-nevents "$OSG_EVENT_TARGET" \
  --manifest "$OSG_WORK/manifest_${OSG_DESIGN}.txt"
```

Expected: two verified one-dataset HDF5 inputs and a two-row manifest, ending in `/0` and `/1`. A 10,000-event campaign instead uses four-digit event names; always keep the original target.

Inspect the staged inputs and parse the personal submit file before submitting:

```bash
cd "$OSG_WORK"
cat "manifest_${OSG_DESIGN}.txt"
wc -l "manifest_${OSG_DESIGN}.txt"
(
  set -e
  test -s "manifest_${OSG_DESIGN}.txt"
  cp "manifest_${OSG_DESIGN}.txt" manifest.txt
  condor_submit -maxjobs 2 -dry-run "$OSG_WORK/smoke-job.ads" events_fanout.submit
)
```

Require two intended slots and a successful parse. Inspect the ClassAds' image, project, directories, transfers and resources. This dry-run submits nothing and does not test image retrieval/model execution; see [submission options](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html#options).

With no existing jobs/transfers for this design, the following command **submits the two real smoke jobs**. Submit once:

```bash
condor_submit -maxjobs 2 events_fanout.submit
```

Record the reported cluster ID. Replace the example number below, then inspect queue, holds, history and the per-slot `job.<cluster>.<process>.out/.err` files:

```bash
export OSG_CLUSTER=123456  # replace with the newly reported cluster ID
condor_q "$OSG_CLUSTER" -af ClusterId ProcId JobStatus Iwd
condor_q -hold
condor_history "$OSG_CLUSTER" -af ClusterId ProcId JobStatus ExitCode
```

Wait for terminal outboxes: one nonempty particle list plus `afterburner.done`, or a clean `hydro.no_surface`. Exit zero or queue empty is insufficient. Preserve failed-slot logs and never restage over active jobs. Evicted/retried workers start fresh from their IC; there is no event checkpoint.

After the cluster has left the queue and transfers have stopped, allow the default 60-second output age. Preview the drain, then retain AP copies on the first real transfer:

```bash
python3 "$OSG_SCRIPTS/drain_osg_events_batched.py" \
  --nersc-host "$NERSC_HOST" --ap-root "$OSG_STAGE/$OSG_CAMPAIGN" \
  --pscratch-root "$NERSC_RAW_ROOT" --system "$OSG_SYSTEM" \
  --design "$OSG_DESIGN" --dry-run
python3 "$OSG_SCRIPTS/drain_osg_events_batched.py" \
  --nersc-host "$NERSC_HOST" --ap-root "$OSG_STAGE/$OSG_CAMPAIGN" \
  --pscratch-root "$NERSC_RAW_ROOT" --system "$OSG_SYSTEM" \
  --design "$OSG_DESIGN" --no-delete
```

Expect two eligible events and `drained-nodel=2`. If output is still too young, wait and repeat; a zero exit with no eligible work does not establish completion. On NERSC, activate the OSG smoke profile and run its `status --details`; expect two terminal slots with no blockers and a `drained.done` in each event directory. The three-file initialization bundle must remain intact.

Preserve needed slot logs before running drain without either retention flag. It removes committed AP copies, including ICs/logs; shared `fanout.log` remains. Restaging should then produce an empty gap manifest. Do not submit it.

For the analysis interface test, follow the NERSC guide with this profile and its small resource/bin settings. At least one smoke event must contain particles to exercise cache publication; an all-no-surface sample needs a fresh smoke campaign with a larger fixed bound. Never increase an already initialized bound.

## Normal operation: drain and sweep in separate tmux sessions

For production, use a campaign with its original full target, separate from the two-event smoke campaign. An existing initialized production campaign can keep its NERSC profile/raw root; configure the AP selections to match it. A new campaign needs its own profile/raw root and initialization. Initialize OSG-owned points without submitting NERSC event workers. Keep the AP environment and NERSC INI selections consistent. Replace the example 99–50 range with confirmed OSG-owned design points, preserving their bundles and event bound.

Check queued/running NERSC jobs yourself: the staging guard detects terminal NERSC output without `drained.done`, not every active writer. If the platforms traverse the table from opposite ends, bound their ranges so they cannot meet at an active point. Increasing/decreasing traversal order alone provides no coordination.

### Drain session

```bash
tmux new -s drain
source "$HOME/osgrun/campaign_env.sh"
python3 "$OSG_SCRIPTS/drain_osg_events_batched.py" \
  --nersc-host "$NERSC_HOST" --ap-root "$OSG_STAGE/$OSG_CAMPAIGN" \
  --pscratch-root "$NERSC_RAW_ROOT" --system "$OSG_SYSTEM" \
  --auto-discover --loop --interval 180 --stop-empty 300
```

Detach with Ctrl-b d. Drain rescans each cycle and exits after roughly 15 consecutive idle hours with these settings. Idle does not mean complete: queued work or conflicting outboxes can remain. Check/restart the process as needed.

Drain accepts the terminal states used in the smoke test, including completed `.unfinished` output and no-surface events. It retains empty, incomplete or conflicting output for inspection.

For eligible output older than `--min-age` (default 60 seconds), the drain:

1. Queries NERSC markers and refuses a destination with `reaped.done`.
2. Pushes only `outbox/`, leaving ICs and Condor logs on the AP.
3. Flattens and checks terminal artifacts and particle-file size on NERSC.
4. Commits `drained.done`, then removes the corresponding AP event directory.

Surfaces/worker archives do not transfer. Preserve initialized bundles for reruns and copy diagnostic slot logs before drain deletes their AP directories.

Verification checks terminal artifacts/size, not content checksums. `drained.done` permits cleanup of lingering AP copies. Query/transfer/verification failures preserve output; deletion failures are pending. One-pass mode returns nonzero, loop mode retries. `--dry-run` reads only; `--no-delete` retains verified copies and prevents sweep teardown.

### Sweep session

In a second terminal:

```bash
tmux new -s sweep
source "$HOME/osgrun/campaign_env.sh"
python3 "$OSG_SCRIPTS/sweep_osg_designs.py" \
  --start 99 --floor 50 --nersc-host "$NERSC_HOST" \
  --nersc-ic-dir "$NERSC_IC_TEMPLATE" --pscratch-root "$NERSC_RAW_ROOT" \
  --ap-stage-root "$OSG_STAGE" --campaign "$OSG_CAMPAIGN" \
  --system "$OSG_SYSTEM" --total-nevents "$OSG_EVENT_TARGET" \
  --submit-dir "$OSG_WORK" --max-running 2 --dry-run
```

The sweep dry-run prints the plan only; it does not query NERSC or check each design. After reviewing the range and paths, rerun without `--dry-run` and detach. The default helper paths find the staging/splitting scripts beside the sweep.

The range is descending/inclusive and defaults to three-digit design suffixes (`027`); manual staging uses the literal suffix supplied.

One sweep tracks at most `--max-running` designs; staging needs a free slot. It tears down a design after its queue is empty and AP terminal outboxes are drained. This does not verify the event target: failed/removed jobs can leave gaps.

Sweep copies each manifest to shared `manifest.txt`, submits from `$OSG_WORK` and skips empty gaps. Run only one sweep/manual submitter per submit directory.

## Manual staging and gap recovery

Set the intended literal `OSG_DESIGN` suffix in production `campaign_env.sh`, then use the [read-only staging preview](#read-only-staging-preview) with those production selections.

Staging dry-run queries SSH, ownership and NERSC markers without changing files; it does not check the source bundle. Drop `--dry-run` to copy the bundle, split missing positions in native HDF5 order, verify arrays and remove the pulled monolith; the NERSC bundle remains. Already drained/reaped designs receive empty manifests during real staging. A dry-run does not create or replace a manifest.

After staging, inspect the manifest. Only if it is nonempty and no jobs for this design are already queued, submit it from the submit directory:

```bash
cd "$OSG_WORK"
if [ -s "manifest_${OSG_DESIGN}.txt" ]; then
  cp "manifest_${OSG_DESIGN}.txt" manifest.txt
  condor_submit events_fanout.submit
fi
```

The `--manifest` filename is explicit here; stage defaults otherwise place it beside `stage/`. Manual submissions are not tracked by a running sweep. Keep the drain active and use the same no-overlap/restart rules below.

## Monitoring and queue policy

```bash
source "$HOME/osgrun/campaign_env.sh"
tmux ls
condor_q -totals
condor_q -factory
condor_q -af ClusterId JobStatus Iwd
condor_q -hold
du -sh "$HOME" "$OSG_WORK/stage"
ssh "$NERSC_HOST" 'showquota'
```

Reattach to inspect process progress/errors; `tmux ls` only proves the session exists. Compare `du` usage with your AP account quota; `df` shows shared capacity. Measure rather than assume drain throughput.

`events_fanout.submit` is authoritative for the retry expressions. It releases transfer holds (12/13) after 120 seconds while `NumJobStarts < 5`. Its removal rule applies only to held jobs and removes exhausted starts, codes 14/26 or jobs held over an hour. Running attempts are not removed merely for having five starts. `max_retries` governs another retry mechanism; it is not a promise that every missing event will eventually succeed. The supplied `max_idle = 400` selects a late-materialization factory and limits materialized non-running jobs, including held jobs, per cluster. It does not limit running jobs or represent campaign completion. Inspect `condor_q -factory` alongside per-job queue/history output, especially if a factory is paused or failing to materialize; the sweep's queue check queries process IDs and does not establish that the original event target was reached. These submit options are documented in the [HTCondor submission reference](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html).

| Hold code | Meaning and inspection |
|---|---|
| 12 | Output-transfer failure; inspect AP space and transfer errors. |
| 13 | Input-transfer failure; inspect image/input retrieval and the full reason. |
| 14 | Working directory is inaccessible; confirm path and permissions. It does not prove the event drained. |
| 26 | Site-policy hold; inspect the reason/subcode. CPU use is one possible cause. |

These meanings follow the [HTCondor hold-code reference](https://htcondor.readthedocs.io/en/latest/codes-other-values/hold-reason-codes.html). Repeated holds require inspection; an empty queue can also mean jobs were removed.

## HALTs, restarts and drain outages

### Partition guard or staging failure

A staging failure stops new sweep work while submitted clusters and drain continue. Inspect the actual cause: ownership conflict, SSH expiry, missing inputs or split failure.

For a partition conflict, decide whether the design remains NERSC-owned or is being deliberately reassigned. Do not mark NERSC output `drained.done` to bypass the guard. If reassigning, stop its NERSC writers and preserve their outputs separately while keeping `initial.hdf`, `parameters.dat` and `music_input` at `NERSC_IC_TEMPLATE`, or select another preserved bundle with `--nersc-ic-dir`. Moving the entire source design away without changing that path breaks staging. Resume only after ownership and the retained input bundle are clear.

### Restarting the sweep

Sweep state is in memory; restarting does not adopt surviving clusters. Staging can overwrite slot inputs, so inspect every surviving cluster's `Iwd` before restarting.

Prefer to let previous clusters finish/drain, survey NERSC totals, then restart at the highest incomplete design. Fully drained designs skip. To run alongside survivors, choose a range disjoint from **all** active designs and account for their concurrency; starting below only the highest active design can duplicate others.

### A drain outage or full AP home

Stop the sweep first to prevent new submissions. Preserve undrained outboxes. For each affected cluster you intend to pause, disable its automatic removal before holding it, using its actual cluster ID:

```bash
export OSG_CLUSTER=123456   # replace with the affected cluster ID
condor_qedit "$OSG_CLUSTER" PeriodicRemove False &&
  condor_hold "$OSG_CLUSTER"
```

This scopes the change to that cluster. See [condor_qedit](https://htcondor.readthedocs.io/en/latest/man-pages/condor_qedit.html). Manual holds otherwise remain exposed to the submit file's held-too-long removal rule. Inspect space usage and remove only known inactive logs/build copies or other disposable data; do not delete a slot containing unverified output. Free enough room to renew the SSH certificate, check NERSC destination writability and quota, then watch the drain complete successful passes.

Running drain retries transfers and uses renewed keys on later connections; restart it if exited. Once space/transfers recover, release the selected cluster:

```bash
condor_release "$OSG_CLUSTER"
```

The live `PeriodicRemove=False` edit persists for that cluster; supervise its remaining holds or deliberately restore the reviewed policy. New submissions use the submit file. Account for every design active during the outage on NERSC: removed jobs can leave gaps even after the sweep reports completion. Restage missing events only after the corresponding prior jobs/transfers have stopped. Keep the original fixed event target.

## Hand back to the NERSC workflow

Activate the NERSC profile and run `python models/campaign.py --config "$CFG" status --details`. Reconcile each processed design's terminal total and blockers; sweep/drain exit alone does not establish completion.

Once those designs have no remaining NERSC/OSG writers or transfers, follow [cache/publish and review/reap](nersc-campaign.md#3-cache-and-publish). Only reap designs you intend to stop generating. A later AP drain refuses `reaped.done`; it cannot append events to an already reaped design.
