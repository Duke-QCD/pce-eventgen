# OSG campaign runbook

For a new account, begin with [OSG setup](../osg/README.md): NERSC initialization
without NERSC generation, AP Python/tools, personal submit settings and the
approved image. The [flag index](reference/cli-osg.md) provides option lookup.

OSG generates events from initial conditions prepared on NERSC. On the OSPool
access point (AP), stage a design, submit one HTCondor job per missing event,
and drain terminal output back to its NERSC raw tree. Then use the
[NERSC campaign guide](nersc-campaign.md) for survey, cache/publish, review/reap
and analysis. Inference remains separate.

Replace account, allocation and storage paths in the examples with your own.
The OSG scripts do not read the campaign INI. Their NERSC root, system and
event target must match `[run] directory`, `system` and `nevents` in the NERSC
campaign config.

## Scripts and deployment

| File in the repository | Run location and purpose |
|---|---|
| `osg/stage_osg_design.py` | AP: query NERSC completion/ownership, pull the initial-condition bundle and stage its gap |
| `osg/split_design.py` | AP: split the monolithic HDF5 input in native dataset order, preserving event identities |
| `osg/sweep_osg_designs.py` | AP: stage, submit and tear down a descending sequence of designs |
| `osg/drain_osg_events_batched.py` | AP: push terminal output to NERSC and remove verified AP copies |
| `osg/events_fanout.submit`, `osg/job.sh` | HTCondor submission and the per-event worker |
| `osg/heavy-ion.def`, `osg/container_paths.sh` | Container build recipe and its runtime paths |

The sweep and drain are separate processes. The sweep does not start the drain.
The deployed Python helpers in these examples live in the AP home directory;
the submit file and worker live in `$HOME/osgrun`, beside its `stage/` tree.

Deploy the helpers and check them on the AP as described in
[OSG setup, section 3](../osg/README.md#3-deploy-the-ap-helpers-and-python-environment).

Those checks catch syntax/import failures and unavailable dependencies; they do
not test event execution, SSH access, the submit policy or the container. For
updates, stop the sweep before replacing helpers, copy files completely and
check them before restarting. Stage an updated worker as a separate file and
rename it into place after checking it; avoid changing `job.sh` while jobs are
being submitted or starting. Keep AP copies matched to the reviewed checkout.

The submit file's `initialdir = stage/$(slot)` requires the stage tree under
the submit directory. Its shared Condor log is `$ENV(HOME)/osgrun/fanout.log`;
`output` and `error` are per-slot files. Set `IMAGE` and `PROJECT` at the top
of the file and check the resources before submission. Current requests are one CPU, 2 GB memory
and 4 GB disk; `job.sh` clamps numerical-library/OpenMP threads to one CPU.
Use the AP-supported Python environment in every session; the setup guide
shows a venv recipe. `--help` on each helper gives its actual parser options,
for example `python3 "$HOME/stage_osg_design.py" --help`. These scripts do not
inherit the NERSC campaign dispatcher's INI settings.

The runner, converter, model binaries and physics data are baked into the image;
copying new AP helpers does not update them. Rebuild using the context listed in
`osg/heavy-ion.def` when those inputs change, validate the image, publish under
a new versioned filename and update the submit URI. Initial conditions are
prepared by TRENTO on NERSC; the image runs MUSIC → iS3D → SMASH.

`job.sh` is transferred separately from the image, so it can change without an
image rebuild. Submit-file edits affect new submissions. Do not assume editing
`job.sh` changes running jobs or every queued retry: executable spooling and
transfer settings matter. Validate a new submission when changing worker code.
See HTCondor's [submission reference](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html#copy_to_spool).

## AP environment and NERSC access

Save these exports as `$HOME/osgrun/campaign_env.sh` on the AP and adjust them
for the intended campaign. This is a shell convenience file, not a script-read
config format:

```bash
export OSG_WORK="$HOME/osgrun"
export OSG_SCRIPTS="$HOME"
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
```

The AP slot path is
`$OSG_STAGE/$OSG_CAMPAIGN/design_N/$OSG_SYSTEM/EVENT`.
The drain lands output at `$NERSC_RAW_ROOT/design_N/$OSG_SYSTEM/EVENT`.
`--ap-stage-root` takes `$OSG_STAGE`; the drain's `--ap-root` takes
`$OSG_STAGE/$OSG_CAMPAIGN`. The two campaign directory names need not match.
Keep the initialized event target fixed, including when staging only a gap.
Add activation of your AP Python environment and `export PYTHONNOUSERSITE=1`
to this file. For setup's isolated smoke campaign, save `OSG_CAMPAIGN=pce-osg-smoke`,
`OSG_DESIGN=000`, `OSG_EVENT_TARGET=2` and that profile's literal NERSC raw root
here before starting either tmux session. AP and NERSC usernames can differ.

Install the sshproxy client using the current
[NERSC MFA instructions](https://docs.nersc.gov/connect/mfa/#sshproxy).
On the AP, configure the SSH host used by both staging and draining:

```text
Host perlmutter
    HostName perlmutter.nersc.gov
    User your_nersc_user
    IdentityFile ~/.ssh/nersc
    IdentitiesOnly yes
```

Adjust `User` to match `NERSC_USER`, then in each session:

```bash
source "$HOME/osgrun/campaign_env.sh"
"$SSH_PROXY" -u "$NERSC_USER"
ssh "$NERSC_HOST" 'echo ok'
ssh -o BatchMode=yes "$NERSC_HOST" 'echo ok'
ssh-keygen -L -f "$HOME/.ssh/nersc-cert.pub" | grep Valid
```

Renew before expiry. The default certificate lifetime is 24 hours; inspect the
actual expiry printed by the client. New SSH connections use the refreshed key,
so a running drain can recover without restarting. See the linked NERSC
instructions for client installation and certificate options.
Resolve first-connection host-key prompts interactively before the batch-mode
check. Staging/drain need unattended AP→NERSC access, including a readable
source bundle and writable destination. Do not put passwords or OTPs in the
environment file.

## First smoke campaign

Use the separate two-event NERSC bundle from [setup](../osg/README.md#2-prepare-a-two-event-input-bundle-on-nersc).
Save its selections in `campaign_env.sh` first; sourcing that file in another
terminal must keep the smoke root, target `2` and literal design suffix `000`.
Keep the sweep stopped during this manual test.

On the AP, preview staging:

```bash
source "$HOME/osgrun/campaign_env.sh"
python3 "$OSG_SCRIPTS/stage_osg_design.py" \
  --design "$OSG_DESIGN" --nersc-host "$NERSC_HOST" \
  --nersc-ic-dir "$NERSC_IC_TEMPLATE" --pscratch-root "$NERSC_RAW_ROOT" \
  --ap-stage-root "$OSG_STAGE" --campaign "$OSG_CAMPAIGN" \
  --system "$OSG_SYSTEM" --total-nevents "$OSG_EVENT_TARGET" \
  --manifest "$OSG_WORK/manifest_${OSG_DESIGN}.txt" --dry-run
```

Expected: SSH succeeds, no conflicting NERSC terminal events, and a gap of two.
This preview does not pull/read the bundle or write a manifest. Run the same
command **without** `--dry-run` to stage it. Expected: two verified one-dataset
HDF5 inputs and a two-row manifest, ending in `/0` and `/1`. A 10,000-event
campaign instead uses four-digit event names; always keep the original target.

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

Continue only when the manifest contains the intended two slots and the parser
succeeds. Inspect the resulting ClassAds for the selected image, project,
working directories, input/output files and resource requests. `condor_submit
-dry-run FILE` writes job descriptions and submits nothing; it does not prove
that workers can fetch the image or execute the models. See the
[submission reference](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html#options).

With no existing jobs/transfers for this design, submit once:

```bash
condor_submit -maxjobs 2 events_fanout.submit
```

Record the reported cluster ID. Replace the example number below, then inspect
queue, holds, history and the per-slot `job.<cluster>.<process>.out/.err` files:

```bash
export OSG_CLUSTER=123456  # replace with the newly reported cluster ID
condor_q "$OSG_CLUSTER" -af ClusterId ProcId JobStatus Iwd
condor_q -hold
condor_history "$OSG_CLUSTER" -af ClusterId ProcId JobStatus ExitCode
```

Wait for returned `outbox/` directories with unambiguous terminal output.
Job exit zero and an empty queue are insufficient by themselves. Each slot
needs one nonempty particle list and `afterburner.done`, or a clean
`hydro.no_surface` marker. If a slot fails, preserve its logs and follow the
recovery sections; do not restage over queued/running jobs. Workers start fresh
from their single IC after eviction/retry; this workflow has no event checkpoint.

After the cluster has left the queue and transfers have stopped, allow the
default 60-second output age. Preview the drain, then retain AP copies on the
first real transfer:

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

Expect two eligible events and `drained-nodel=2`. If output is still too young,
wait and repeat; a zero exit with no eligible work does not establish completion.
On NERSC, activate the OSG smoke profile and run its `status --details`; expect
two terminal slots with no blockers and a `drained.done` in each event directory.
The three-file initialization bundle must remain intact.

Save any per-slot stdout/stderr you need before cleanup. Run the drain again
without `--dry-run` or `--no-delete` only after inspecting those results. It
checks the committed NERSC markers and removes the verified AP slot copies,
including their ICs and per-slot logs. The shared `fanout.log` remains.
Repeat actual staging afterward to check gap recovery: it should replace the
manifest with an empty file and stage no events. Do not submit an empty manifest.

For the analysis interface test, follow the NERSC guide with this profile and
its small resource/bin settings. At least one smoke event must contain particles
to exercise cache publication; an all-no-surface sample needs a fresh smoke
campaign with a larger fixed bound. Never increase an already initialized bound.

## Normal operation: drain and sweep in separate tmux sessions

After smoke validation, prepare a separate production NERSC profile/raw root
and AP campaign directory with the approved full event target and design range.
Initialize those designs without NERSC job submission. Update `campaign_env.sh`
and the matching NERSC profile together; do not turn the initialized two-event
smoke tree into production by editing its target. Replace the example range
99–50 below with the designs assigned to OSG.

First confirm ownership: no NERSC jobs should be generating the OSG-owned
designs. The staging guard detects NERSC terminal markers without `drained.done`;
it does not discover queued jobs or every in-progress event. Preserve
`initial.hdf`, `parameters.dat` and `music_input` for every selected design.

### Drain session

```bash
tmux new -s drain
source "$HOME/osgrun/campaign_env.sh"
python3 "$OSG_SCRIPTS/drain_osg_events_batched.py" \
  --nersc-host "$NERSC_HOST" --ap-root "$OSG_STAGE/$OSG_CAMPAIGN" \
  --pscratch-root "$NERSC_RAW_ROOT" --system "$OSG_SYSTEM" \
  --auto-discover --loop --interval 180 --stop-empty 300
```

Detach with Ctrl-b d. The drain rescans staged designs each cycle. The long
idle limit allows gaps between designs; it is not a completion test. With these
settings it stops after roughly 15 hours of consecutive cycles with no eligible
output. Jobs may still be queued, or incomplete/conflicting outboxes may remain.
Check the session and restart it if it has exited before the campaign is finished.

A drain pass accepts either one nonempty particle-list variant plus
`afterburner.done`, or a clean `hydro.no_surface` with no contradictory particle,
hydro-done or afterburner-done artifacts. `.unfinished` is accepted when its
done marker is present. No-surface events are valid zero-output events and
must drain too. Empty/conflicting/incomplete output is retained for inspection.

For eligible output older than `--min-age` (default 60 seconds), the drain:

1. Queries NERSC markers and refuses a destination with `reaped.done`.
2. Pushes only `outbox/`, leaving ICs and Condor logs on the AP.
3. Flattens and checks terminal artifacts and particle-file size on NERSC.
4. Commits `drained.done`, then removes the corresponding AP event directory.

Only the terminal outbox is returned. The worker's hydro surface and local
surface archive are not transferred to NERSC; keep the initialized bundle for
any later rerun. Draining also deletes per-slot Condor logs along with each AP
slot, so preserve logs needed for diagnostics before enabling deletion.

Verification is not a content-checksum comparison. An existing `drained.done`
permits cleanup of a lingering AP copy on retry. Transfer/query/verification
failures preserve AP output; failed local deletion is reported as pending.
One-pass mode exits nonzero for these failures; loop mode retries. Use
`--dry-run` for a read-only pass or `--no-delete` to retain verified AP copies.
Retaining copies also prevents the sweep's normal drained-output teardown.

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

The sweep dry-run prints the plan only; it does not query NERSC or check each
design. After reviewing the range and paths, rerun without `--dry-run` and detach.
The default helper paths find the staging/splitting scripts beside the sweep.

The range is descending and inclusive. Design numbers are zero-padded to NERSC's
three digits (`--design-width 3` by default) through staging, submission,
draining and teardown, so `--start 27 --floor 20` selects `design_027` through
`design_020`. Manual staging uses the suffix exactly as supplied, such as
`--design 027`.

At most `--max-running` designs belong to one sweep process. Staging uses a
free running slot; there is no third staged design while two slots are full.
For each submitted cluster, the sweep waits for an empty queue and no AP
particle/no-surface outbox artifacts before removing its remaining staging tree.
It then advances. This checks queue/drain state, not the requested event total.
Failed or removed jobs can leave a short design that the sweep reports processed.

The sweep copies each design manifest to `manifest.txt` and submits from
`$OSG_WORK`; the supplied submit file queues slots from that file. No manual
submit-file edit is needed per design. An empty gap manifest is skipped.
Run only one sweep or manual submitter against that shared submit directory.

## Manual staging and gap recovery

Use an AP shell with the environment above loaded:

```bash
python3 "$OSG_SCRIPTS/stage_osg_design.py" \
  --design "$OSG_DESIGN" --nersc-host "$NERSC_HOST" \
  --nersc-ic-dir "$NERSC_IC_TEMPLATE" --pscratch-root "$NERSC_RAW_ROOT" \
  --ap-stage-root "$OSG_STAGE" --campaign "$OSG_CAMPAIGN" \
  --system "$OSG_SYSTEM" --total-nevents "$OSG_EVENT_TARGET" \
  --manifest "$OSG_WORK/manifest_${OSG_DESIGN}.txt" --dry-run
```

This dry-run checks SSH, NERSC markers and the partition guard without pulling,
splitting, writing manifests or deleting files. For actual staging, drop
`--dry-run`. The stage helper skips `drained.done` event positions, splits the
remaining IC datasets in their native order, verifies their arrays, and removes
the pulled monolith after a successful split. Its source NERSC bundle is preserved.
A fully drained or reaped design gets an empty manifest on a real staging pass.

After staging, inspect the manifest. Only if it is nonempty and no jobs for this
design are already queued, submit it from the submit directory:

```bash
cd "$OSG_WORK"
if [ -s "manifest_${OSG_DESIGN}.txt" ]; then
  cp "manifest_${OSG_DESIGN}.txt" manifest.txt
  condor_submit events_fanout.submit
fi
```

The `--manifest` filename is explicit here; stage defaults otherwise place it
beside `stage/`. Manual submissions are not tracked by a running sweep. Keep
the drain active and use the same no-overlap/restart rules below.

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

Reattach to each session to inspect actual progress and failures, then detach;
`tmux ls` alone does not prove the process is healthy. Compare AP usage with the
quota reported for your account by the AP service. `du` measures disk use; `df`
reports shared filesystem capacity, not your personal limit. Do not assume a
fixed home quota or a fixed drain throughput.

`events_fanout.submit` is authoritative for the retry expressions. It releases
transfer holds (12/13) after 120 seconds while `NumJobStarts < 5`. Its removal
rule applies only to held jobs and removes exhausted starts, codes 14/26 or jobs
held over an hour. Running attempts are not removed merely for having five starts.
`max_retries` governs another retry mechanism; it is not a promise that every
missing event will eventually succeed.
The supplied `max_idle = 400` selects a late-materialization factory and limits
materialized non-running jobs, including held jobs, per cluster. It does not
limit running jobs or represent campaign completion. Inspect `condor_q -factory`
alongside per-job queue/history output, especially if a factory is paused or
failing to materialize; the sweep's queue check queries process IDs and does
not establish that the original event target was reached. These submit options
are documented in the [HTCondor submission reference](https://htcondor.readthedocs.io/en/25.0/man-pages/condor_submit.html).

| Hold code | Meaning and inspection |
|---|---|
| 12 | Output-transfer failure; inspect AP space and transfer errors. |
| 13 | Input-transfer failure; inspect image/input retrieval and the full reason. |
| 14 | Working directory is inaccessible; confirm path and permissions. It does not prove the event drained. |
| 26 | Site-policy hold; inspect the reason/subcode. CPU use is one possible cause. |

These meanings follow the [HTCondor hold-code reference](https://htcondor.readthedocs.io/en/latest/codes-other-values/hold-reason-codes.html).
Repeated holds require inspection; an empty queue can also mean jobs were removed.

## HALTs, restarts and drain outages

### Partition guard or staging failure

A staging failure stops the sweep; already submitted clusters keep running and
the separate drain can continue. Read the actual error: SSH/certificate expiry,
missing inputs and split failures also halt staging, not only ownership conflicts.

For a partition conflict, decide whether the design remains NERSC-owned or is
being deliberately reassigned. Do not mark NERSC output `drained.done` to bypass
the guard. If reassigning, stop its NERSC writers and preserve their outputs
separately while keeping `initial.hdf`, `parameters.dat` and `music_input` at
`NERSC_IC_TEMPLATE`, or select another preserved bundle with `--nersc-ic-dir`.
Moving the entire source design away without changing that path breaks staging.
Resume only after ownership and the retained input bundle are clear.

### Restarting the sweep

The sweep stores active cluster/design state only in memory. Restarting does not
adopt surviving clusters, and rerunning staging can overwrite slot inputs for
missing event positions. Before restarting, inspect every surviving cluster's
`Iwd`, not just the highest design number.

The straightforward restart is to keep draining, let all clusters from the
previous sweep finish, confirm their eligible AP output has drained, then inspect
NERSC event totals and restart at the highest design still needing work.
Fully drained designs in that range will be skipped. If restarting alongside
surviving clusters, choose a range disjoint from **all** active designs and
budget their concurrency separately. Starting one below the highest running
design can duplicate another still-running design.

### A drain outage or full AP home

Stop the sweep first to prevent new submissions. Preserve undrained outboxes.
For each affected cluster you intend to pause, disable its automatic removal
before holding it, using its actual cluster ID:

```bash
export OSG_CLUSTER=123456   # replace with the affected cluster ID
condor_qedit "$OSG_CLUSTER" PeriodicRemove False &&
  condor_hold "$OSG_CLUSTER"
```

This scopes the change to that cluster. See [condor_qedit](https://htcondor.readthedocs.io/en/latest/man-pages/condor_qedit.html).
Manual holds otherwise remain exposed to the submit file's held-too-long removal
rule. Inspect space usage and remove only known inactive logs/build copies or
other disposable data; do not delete a slot containing unverified output. Free
enough room to renew the SSH certificate, check NERSC destination writability
and quota, then watch the drain complete successful passes.

An already running drain retries failed transfers and picks up renewed SSH keys
on subsequent connections. If it has exited, restart it. Once transfer and
space conditions are healthy, release the deliberately held cluster:

```bash
condor_release "$OSG_CLUSTER"
```

The live `PeriodicRemove=False` edit persists for that cluster; supervise its
remaining holds or deliberately restore the reviewed policy. New submissions
use the submit file. Account for every design active during the outage on
NERSC: removed jobs can leave gaps even after the sweep reports completion.
Restage missing events only after the corresponding prior jobs/transfers have
stopped. Keep the original fixed event target.

## Hand back to the NERSC workflow

On NERSC, load the selected campaign profile and run
`models/campaign.py --config "$CFG" status --details`. Inspect total terminal
events and blockers for every processed design;
`drained.done` counts track OSG commits, while the survey also checks the terminal
artifacts. Neither sweep completion nor drain idle exit establishes target completion.

Once those designs have no remaining NERSC/OSG writers or transfers, follow
[cache/publish and review/reap](nersc-campaign.md#3-cache-and-publish).
Only reap designs you intend to stop generating. A later AP drain refuses
`reaped.done`; it cannot append events to an already reaped design.
