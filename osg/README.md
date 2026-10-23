# First-time OSG / OSPool setup

Start here before the [campaign runbook](../docs/osg-campaign.md).
OSPool runs independent events on many workers. Its **access point (AP)** is
where you stage inputs, submit HTCondor jobs and collect their returned output.
The **container image** supplies the models and physics data on each worker;
your AP Python environment supplies the staging/sweep/drain tools.

Local checks exercised staging, worker and drain logic. Use the smoke campaign
below to verify AP credentials, the actual image, HTCondor and storage transfers.
The [OSG flag index](../docs/reference/cli-osg.md) lists every helper's options.

## 1. Obtain the accounts and matching inputs

You need an OSPool account and assigned access point, an authorized OSPool
project name, NERSC access, this repository (with its SMASH submodule) and the production data, and a
maintainer-approved image accessible to **your** jobs. See the official
[access-point overview](https://osg-htc.org/services/access-point/) and
[project setup](https://portal.osg-htc.org/documentation/overview/account_setup/starting-project/).
The submit file's `IMAGE` and `PROJECT` values are per-account deployment values;
copying them does not grant access.

Agree which complete designs belong to OSG. Never submit NERSC event workers
and OSG workers for the same design. Keep the original design table, initialized
`initial.hdf` / `parameters.dat` / `music_input` bundle, collision system, and
fixed event bound. A small AP smoke campaign needs its own NERSC raw root and
profile, so it cannot drain into production output.

| Location | What belongs there |
|---|---|
| NERSC | Initialized bundles, final drained raw events, cache/analysis and their campaign INI |
| AP home, e.g. `$HOME/osgrun` | Submit file, worker script, staged per-event inputs, returned outboxes and Condor logs |
| AP Python environment | NumPy/h5py and the staging/sweep/drain helpers |
| Assigned OSDF origin | Versioned image `.sif`, distributed to workers through its `osdf:///` URI |
| Worker sandbox | One event's inputs and model execution; HTCondor returns `outbox/` to its AP slot |

Inside each job the runner calls its sole event `0`. The containing AP slot
retains its full-design identity, such as `0007`; the drain writes that slot's
outbox into NERSC event `0007`. Do not rename slots or sort HDF5 datasets when
splitting a bundle.

The OSG helpers use command-line arguments, **not** the campaign INI. Their raw
root, system and full event target must match the NERSC profile when you hand
the output back to the cache/analysis workflow.

## 2. Prepare a two-event input bundle on NERSC

Finish [NERSC setup steps 1–5](../nersc/SETUP.md), with a working personal
installation/profile and approved data. In that activated Bash shell, use the
same `REPO`, `PROJECT`, `SOFTWARE`, `DATA`, `OUTPUT` and `WORK_SCRATCH` selections
to create a **new** OSG-only smoke profile:

```bash
cd "$REPO"
python nersc/configure_user.py \
  --project "$PROJECT" --software-root "$SOFTWARE" --data-root "$DATA" \
  --runtime-gcc-module "$HIC_RUNTIME_GCC_MODULE" \
  --output-root "$OUTPUT" --raw-root "$WORK_SCRATCH/pce-osg-smoke" \
  --out-dir "$REPO/config/users/$USER-osg-smoke" --nevents 2
source "$REPO/config/users/$USER-osg-smoke/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
```

Repeat setup's directory/preflight block for this profile. It selects row 0,
`design_000`, only. Initialize without submitting NERSC event workers:

```bash
salloc --nodes 1 --qos interactive --time 00:30:00 --constraint cpu --account "$PROJECT"
cd "$REPO"
source "$REPO/config/users/$USER-osg-smoke/activate.sh"
export CFG="$HIC_CAMPAIGN_CONFIG"
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
srun --nodes=1 --ntasks=1 --cpus-per-task=2 --cpu-bind=cores \
  python models/campaign.py --config "$CFG" initialize --apply
exit  # release the initialization allocation
```

`initialize --apply` only initializes; it never repairs or submits NERSC
workers. Expected: the three-file bundle exists and `initial.hdf` has two
datasets. Run `initialize` without `--apply` to preview. Initialization never
replaces an existing `initial.hdf`; do not change the event bound of an
initialized design.

Record the **literal absolute** NERSC raw root printed by the profile, such as
`/pscratch/sd/u/USERNAME/pce-osg-smoke`. The AP cannot expand NERSC's `$PSCRATCH`.
For the cache/analysis handoff, apply the small resource settings and single
`0 100` centrality bin from [NERSC setup section 6](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign).
Do not run that section's NERSC generation command (`events --submit`) for this
OSG-owned profile.

## 3. Deploy the AP helpers and Python environment

From a machine with this checkout and SSH access to your assigned AP:

```bash
cd /path/to/pce-eventgen       # replace with this checkout's location
export AP_LOGIN=YOUR_AP_USERNAME@YOUR_ASSIGNED_AP
ssh "$AP_LOGIN" 'mkdir -p "$HOME/osgrun/stage"'
scp osg/stage_osg_design.py osg/split_design.py \
  osg/sweep_osg_designs.py osg/drain_osg_events_batched.py "$AP_LOGIN:"
scp osg/events_fanout.submit osg/job.sh "$AP_LOGIN:osgrun/"
ssh "$AP_LOGIN"
```

The remaining setup commands run **on the AP**. Use Python 3.8 or later (the
helpers use `shlex.join`); Python 3.11 is a suitable choice if provided. An
existing supported environment is fine. Otherwise create one once:

```bash
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3, 8), "Use Python 3.8 or later"'
python3 -m venv "$HOME/.venvs/pce-osg"
source "$HOME/.venvs/pce-osg/bin/activate"
python -m pip install numpy h5py
export PYTHONNOUSERSITE=1
chmod u+x "$HOME/osgrun/job.sh"
```

If `venv`/pip is unavailable, use the AP-supported environment supplied by its
staff. Do not install into the AP's system Python. This environment does not
change packages inside an existing worker image.

Create `$HOME/osgrun/campaign_env.sh` using the
[runbook's environment block](../docs/osg-campaign.md#ap-environment-and-nersc-access).
Use your NERSC username and the literal raw root from step 2; for the smoke test
set `OSG_CAMPAIGN=pce-osg-smoke`, `OSG_EVENT_TARGET=2`, `OSG_DESIGN=000` and
`OSG_SYSTEM=AuAu200`. Add your AP environment activation to that file, for example:

```bash
source "$HOME/.venvs/pce-osg/bin/activate"
export PYTHONNOUSERSITE=1
```

Activate it in **every** new AP shell and tmux session. Check the deployment:

```bash
source "$HOME/osgrun/campaign_env.sh"
(
  set -e
  python3 -c 'import sys, numpy, h5py; print(sys.executable)'
  for tool in ssh rsync tmux condor_submit condor_q condor_history; do
    command -v "$tool"
  done
  for helper in stage_osg_design split_design sweep_osg_designs drain_osg_events_batched; do
    python3 "$OSG_SCRIPTS/$helper.py" --help >/dev/null
  done
  bash -n "$OSG_WORK/job.sh"
  test -x "$OSG_WORK/job.sh"
  test -w "$OSG_WORK/stage"
  echo 'AP helper checks passed.'
)
```

Every check must pass. The subprocess stops at its first failure; it does not
validate SSH credentials, destination writability, submit-file parsing or model
execution. Complete the runbook's SSH certificate setup next, authenticate
interactively, and check the actual certificate expiry before unattended work.

## 4. Select your submit policy and image

Edit the two lines at the top of your AP copy of `$OSG_WORK/events_fanout.submit`
with your approved image and authorized project:

```text
IMAGE   = osdf:///YOUR_APPROVED_IMAGE_LOCATION/heavy-ion-VERSION.sif
PROJECT = YOUR_AUTHORIZED_OSPOOL_PROJECT
```

The shared Condor log is `$ENV(HOME)/osgrun/fanout.log` (HTCondor expands
`$ENV(HOME)` at submission). Keep `executable = job.sh`, `initialdir = stage/$(slot)`,
the three transferred input filenames, `transfer_output_files = outbox`, and
`queue slot from manifest.txt`. The stage tree must be immediately under the
submit directory.

The maintained worker uses one CPU. Start with the supplied resource requests
and inspect actual memory/disk usage during the smoke job. The model binaries,
runner, converter, configuration and physics data must all be from the approved
image version. A new AP helper or `job.sh` does not update anything baked into
the SIF. Use a new versioned filename after rebuilding.

OSDF origin paths and access rights depend on your AP/account. For AP41, the
usual personal local origin is `/ospool/ap41/data/USERNAME`, corresponding to
`osdf:///ospool/ap41/data/USERNAME/...`. Another user's personal image may be
inaccessible to your jobs. Confirm a shared image's permissions or copy an
approved image into your assigned origin; see
[OSDF guidance](https://portal.osg-htc.org/documentation/htc_workloads/managing_data/osdf/).
Keep submit files, staged slots and Condor logs in AP home.

Continue with the runbook's [first smoke campaign](../docs/osg-campaign.md#first-smoke-campaign).
It stages just the two-event design, parses the submit file without submitting,
then checks returned and drained events before a production sweep.

## 5. Build a new image when the approved image needs updating

This is a maintainer task; skip it when a matching image is already available.
On a build host with this repository (including its SMASH submodule) and the approved data, assemble
a fresh context. Choose a directory with enough quota for source, build trees,
the image and temporary files. Replace the three paths below:

```bash
export SOURCE_REPO=/path/to/pce-eventgen
export STATIC_DATA=/path/to/approved/production_data
export IMAGE_BUILD=/path/to/new/image-build-directory
mkdir "$IMAGE_BUILD"              # must be a fresh directory
mkdir -p "$IMAGE_BUILD/data"
cp "$SOURCE_REPO/osg/heavy-ion.def" "$SOURCE_REPO/osg/container_paths.sh" "$IMAGE_BUILD/"
cp "$SOURCE_REPO/models/run-events" "$SOURCE_REPO/models/run-events-wrapper" "$IMAGE_BUILD/"
cp "$SOURCE_REPO/models/iS3D/iS3D_OSCAR.py" "$SOURCE_REPO/config/config.yaml" "$IMAGE_BUILD/"
rsync -a --exclude='/build/' --exclude='/build-workflow/' \
  "$SOURCE_REPO/models/music-hydro/" "$IMAGE_BUILD/music-hydro/"
rsync -a --exclude='/build/' --exclude='/build-workflow/' \
  "$SOURCE_REPO/models/iS3D/" "$IMAGE_BUILD/iS3D/"
# A clone keeps SMASH's Git metadata, which the build checks.
git clone --quiet "$SOURCE_REPO/models/SMASH" "$IMAGE_BUILD/smash"
git -C "$IMAGE_BUILD/smash" checkout --quiet --detach 0985d6b32fb801404c933d70c86c23759d60ff1f
rsync -a "$STATIC_DATA/iS3D/" "$IMAGE_BUILD/data/iS3D/"
rsync -a "$STATIC_DATA/EOS/" "$IMAGE_BUILD/data/EOS/"
python3 "$SOURCE_REPO/tools/check_physics_data.py" eos "$IMAGE_BUILD/data/EOS/PCE/strange_30_10000"
python3 "$SOURCE_REPO/tools/check_physics_data.py" is3d \
  "$IMAGE_BUILD/data/iS3D/deltaf_coefficients/vh/smash_pce_light_strange"
git -C "$IMAGE_BUILD/smash" describe --tags   # expect SMASH-3.0-1-g0985d6b3
```

`STATIC_DATA` must have the layout in
[NERSC setup](../nersc/SETUP.md#2-obtain-the-checkout-and-production-inputs). The particle/decay inputs
must agree; sigma is included in SMASH. Keep all ten coefficient tables and the
30 EOS binaries.
The build downloads pinned Pythia 8.309; an offline build needs that archive
staged and the definition adapted before building.

Run the build **from the context directory**, because `%files` paths are
relative to that directory. On an OSG-operated AP, first prepare the build
environment following the current
[OSPool build instructions](https://portal.osg-htc.org/documentation/htc_workloads/using_software/containers-singularity/):

```bash
mkdir -p "$HOME/tmp"
export TMPDIR="$HOME/tmp"
export APPTAINER_TMPDIR="$HOME/tmp"
export APPTAINER_CACHEDIR="$HOME/tmp"
cd "$IMAGE_BUILD"
apptainer build --ignore-proot heavy-ion-VERSION.sif heavy-ion.def
```

Replace `VERSION` with a new label. Check your AP's current build policy and
available resources first; other build hosts may require a different fakeroot
setup. The definition defaults to two build processes per model. It compiles a
portable x86-64-v2 baseline rather than tuning the image to the build host.
Keep the complete build log and source/data provenance.

Before publishing, run a runtime preflight:

```bash
apptainer exec --cleanenv "$IMAGE_BUILD/heavy-ion-VERSION.sif" bash -c '
  set -e
  source /opt/hic/container_paths.sh
  python3 -c "import numpy, h5py"
  for executable in "$HIC_MUSIC_EXEC" "$HIC_IS3D_EXEC" "$HIC_SMASH_EXEC"; do
    test -x "$executable"
    ldd "$executable"
  done
  test -s "$HIC_CHARGE_DICT"
  test -s "$HIC_BARYON_DICT"
  test -s "$HIC_IS3D_CONFIG"
  "$HIC_SMASH_EXEC" --version
'
```

Inspect the loader output for missing libraries; `ldd` can return zero while
printing `not found`. The model/image test is the real smoke job, not this path
check alone. Publish the validated SIF to your assigned origin under its new
filename, update `container_image`, and submit a fresh smoke job. OSPool runs
the submit file's `job.sh`; the definition's `%runscript` is not the job entry point.
