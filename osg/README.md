# First-time OSG / OSPool setup

Start here before the [campaign runbook](../docs/osg-campaign.md). OSPool runs independent events on many workers. Its **access point (AP)** is where you stage inputs, submit HTCondor jobs and collect their returned output. The **container image** supplies the models and physics data on each worker; your AP Python environment supplies the staging/sweep/drain tools.

Choose the scope before starting:

- **Check an existing campaign without running events:** reuse its NERSC profile and initialized bundles, complete sections 3–4 in a separate AP directory, then follow [validation without copying inputs or running events](#validation-without-copying-inputs-or-running-events). Skip the initialization and live smoke commands.
- **Test a new installation end to end:** prepare the isolated two-event bundle in section 2, complete sections 3–4, then run the [first smoke campaign](../docs/osg-campaign.md#first-smoke-campaign). That procedure copies inputs, submits real jobs and transfers results.
- **Replace baked code or data:** build and validate a newly versioned image using section 5 before a fresh smoke run.

Helper options are in the [OSG flag index](../docs/reference/cli-osg.md). Commands below run in Bash; keep each section's shell open and stop on a failed command.

## 1. Obtain the accounts and matching inputs

You need an assigned AP, authorized OSPool project, NERSC access, the [checkout/production inputs](../docs/getting-started.md#1-obtain-code-and-physics-data) and an approved image accessible to your jobs. See [AP access](https://osg-htc.org/services/access-point/) and [project setup](https://portal.osg-htc.org/documentation/overview/account_setup/starting-project/). Copying submit settings does not grant access.

A NERSC **profile** is a set of configuration files, not another account or an isolated filesystem. Profiles may share the repository, installed software and static physics data. NERSC and the AP have separate filesystems: staging copies inputs to the AP, and draining copies event output back to NERSC.

Assign whole **design points** (rows such as `design_027`) to one generation platform. NERSC and OSG may work through the same design table from opposite ends and share one NERSC campaign profile/raw root, provided their point ranges do not overlap. Keep a stopping boundary: the staging guard detects terminal native NERSC events, not queued/running NERSC jobs. It also deliberately prevents mixing native and container-generated events within a point. Preserve each initialized bundle and fixed event bound. A separate smoke profile/raw root isolates test results from production.

| Location | What belongs there |
|---|---|
| NERSC | Initialized bundles, final drained raw events, cache/analysis and their campaign INI |
| AP home, e.g. `$HOME/osgrun` | Submit file, worker script, staged per-event inputs, returned outboxes and Condor logs |
| AP Python environment | NumPy/h5py and the staging/sweep/drain helpers |
| Assigned OSDF origin | Versioned image `.sif`, distributed to workers through its `osdf:///` URI |
| Worker sandbox | One event's inputs and model execution; HTCondor returns `outbox/` to its AP slot |

Inside each job the runner calls its sole event `0`. The containing AP slot retains its full-design identity, such as `0007`; the drain writes that slot's outbox into NERSC event `0007`. Do not rename slots or sort HDF5 datasets when splitting a bundle.

OSG helpers take CLI arguments, not the campaign INI. Match their NERSC raw root, system and full event target to that profile.

## 2. Prepare a two-event input bundle on NERSC

**Already initialized?** Reuse that campaign's profile, raw root, system and original full event count; skip the smoke-profile creation and initialization blocks below. On NERSC, activate the profile and record the settings with:

```bash
printf 'NERSC_RAW_ROOT=%s\n' "${HIC_RAW_ROOT:?Activate the NERSC campaign profile first}"
python "$REPO/models/campaign.py" --config "$CFG" events --print-config
```

`--print-config` runs no events. Some printed options retain `${HIC_RAW_ROOT}` literally; use the expanded value from `printf` for the AP. Select an initialized design point and check its bundle using the [read-only validation procedure](#validation-without-copying-inputs-or-running-events). Do not lower an existing campaign's event count to `2` for a preview: the original bound controls event identities and directory padding.

**New end-to-end smoke test:** finish [NERSC setup steps 1–5](../nersc/SETUP.md), with a working personal installation/profile and approved data. In that activated Bash shell, use the same `REPO`, `PROJECT`, `SOFTWARE`, `DATA`, `OUTPUT` and `WORK_SCRATCH` selections to create a **new** OSG-only smoke profile:

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

Repeat setup's directory/preflight block for this profile. It selects row 0, `design_000`, only. The following block **runs TRENTo on NERSC** to initialize the bundle, without submitting the MUSIC/iS3D/SMASH event workers. Skip it for validation without running models:

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

Expect the matching three-file bundle and two HDF5 datasets. `initialize` previews without `--apply`; it neither submits NERSC workers nor replaces existing initial conditions.

Record the **literal absolute** NERSC raw root using `printf '%s\n' "$HIC_RAW_ROOT"`, such as `/pscratch/sd/u/USERNAME/pce-osg-smoke`. The AP cannot expand NERSC's `$PSCRATCH`. For the cache/analysis handoff, apply the small resource settings and single `0 100` centrality bin from [NERSC setup section 6](../nersc/SETUP.md#6-run-the-isolated-two-event-smoke-campaign). Do not run that section's NERSC generation command (`events --submit`) for this OSG-owned profile.

## 3. Deploy the AP helpers and Python environment

From a machine with this checkout and SSH access to your assigned AP, create the directories and transfer all six files over one authenticated SSH connection. Then open one interactive SSH session for the remaining setup. This uses two authentications rather than separate connections for directory creation and each copy; it does not require SSH multiplexing.

For an independent setup alongside a running campaign, use a fresh directory such as `osgrun-test` instead of `osgrun` throughout this section and the runbook: change `work` in the remote block, `OSG_WORK` on the AP, and each `source .../campaign_env.sh` path. Keep its helpers, Python environment, staging tree, manifests and `campaign_env.sh` under that directory. Also change the test submit file's `log` to `$ENV(HOME)/osgrun-test/fanout.log` in section 4. The deployment below refuses an existing work directory; use a fresh name rather than overwriting a running setup. If the files have already been deployed successfully, skip the transfer and continue on the AP.

```bash
cd /path/to/pce-eventgen       # replace with this checkout's location
export AP_LOGIN=YOUR_AP_USERNAME@YOUR_ASSIGNED_AP
(
  set -o pipefail
  tar -C osg -cf - stage_osg_design.py split_design.py \
    sweep_osg_designs.py drain_osg_events_batched.py events_fanout.submit job.sh |
    ssh "$AP_LOGIN" '
      set -e
      work="$HOME/osgrun"
      mkdir "$work"
      mkdir "$work/helpers" "$work/stage"
      tar -xf - -C "$work/helpers"
      mv "$work/helpers/events_fanout.submit" "$work/helpers/job.sh" "$work/"
    '
)
# Stop if the transfer failed. Otherwise, authenticate once more to continue:
ssh "$AP_LOGIN"
```

Continue **on the AP** with Python >= 3.8. Reuse a supported environment or create one:

```bash
export OSG_WORK="$HOME/osgrun"
export OSG_SCRIPTS="$OSG_WORK/helpers"
export OSG_STAGE="$OSG_WORK/stage"
export PYTHONNOUSERSITE=1
python3 --version
python3 -c 'import sys; assert sys.version_info >= (3, 8), "Use Python 3.8 or later"'
python3 -m venv "$OSG_WORK/.venv" &&
  source "$OSG_WORK/.venv/bin/activate" &&
  python -m pip install 'numpy>=1.24,<2' 'h5py>=3.8,<4'
chmod u+x "$OSG_WORK/job.sh"
```

If venv/pip is unavailable, use the AP-supported environment. Pip selects releases compatible with the AP's Python; the Python 3.11 local/NERSC recipe need not be copied here. AP packages do not affect packages baked into the worker image. See the shared [dependency reference](../local/README.md#dependency-versions).

Create `$OSG_WORK/campaign_env.sh` using the **complete** [runbook environment block](../docs/osg-campaign.md#ap-environment-and-nersc-access), including its environment activation. Use your NERSC username and the literal raw root from step 2. For the new smoke test set `OSG_CAMPAIGN=pce-osg-smoke`, `OSG_EVENT_TARGET=2`, `OSG_DESIGN=000` and `OSG_SYSTEM=AuAu200`. For an existing campaign, use its actual system (for example `OO5360`), original full event count and selected design suffix instead. A different AP campaign name does not change the NERSC drain destination; that is selected by `NERSC_RAW_ROOT`.

Activate it in **every** new AP shell and tmux session. Check the deployment:

```bash
(
  set -e
  source "$HOME/osgrun/campaign_env.sh"
  python -c 'import sys, numpy, h5py; print(sys.executable); print("NumPy:", numpy.__version__); print("h5py:", h5py.__version__)'
  for tool in ssh rsync tmux condor_submit condor_q condor_history; do
    command -v "$tool"
  done
  for helper in stage_osg_design split_design sweep_osg_designs drain_osg_events_batched; do
    python "$OSG_SCRIPTS/$helper.py" --help >/dev/null
  done
  bash -n "$OSG_WORK/job.sh"
  test -x "$OSG_WORK/job.sh"
  test -w "$OSG_WORK/stage"
  echo 'AP helper checks passed.'
)
```

All checks must pass; they stop at the first failure. Expect Python under `$OSG_WORK/.venv/bin/` when using the venv recipe. A missing activation file means the environment-creation step has not succeeded; `/stage_osg_design.py` means `OSG_SCRIPTS` is unset. Restore the complete environment in each shell rather than installing into system Python; see [troubleshooting](../docs/troubleshooting.md#osg).

Next check [AP→NERSC access](../docs/osg-campaign.md#ap-environment-and-nersc-access) and complete section 4. Reuse valid existing credentials; renewal is needed when they expire, not every time a shell starts. For validation without events, follow the read-only procedure below and stop there. For an end-to-end test, continue to the live smoke campaign after sections 4–5 as needed.

## 4. Select your submit policy and image

Edit the two lines at the top of your AP copy of `$OSG_WORK/events_fanout.submit` with your approved image and authorized project:

```text
IMAGE   = osdf:///YOUR_APPROVED_IMAGE_LOCATION/heavy-ion-VERSION.sif
PROJECT = YOUR_AUTHORIZED_OSPOOL_PROJECT
```

The shared Condor log defaults to `$ENV(HOME)/osgrun/fanout.log` (HTCondor expands `$ENV(HOME)` at submission). Set it to your chosen work directory; changing `OSG_WORK` alone does not change the submit file's log. For `osgrun-test`, update only that copy:

```bash
source "$HOME/osgrun-test/campaign_env.sh" &&
  sed -i 's|^log[[:space:]]*=.*|log = $ENV(HOME)/osgrun-test/fanout.log|' \
    "$OSG_WORK/events_fanout.submit"
```

Older submit files may set `container_image` and `+ProjectName` directly instead of defining `IMAGE` and `PROJECT`. Read those entries to recover existing selections; apply the selections to the new test copy. An existing filename or successful AP file-read check does not establish that the image contains the current runner, models and physics data.

Keep `executable = job.sh`, `initialdir = stage/$(slot)`, the three transferred input filenames, `transfer_output_files = outbox`, and `queue slot from manifest.txt`. The stage tree must be immediately under the submit directory.

The worker uses one CPU; measure memory/disk during the smoke job. Changes to models, runner, converter or physics data require a newly versioned image; AP helper changes alone do not update it.

OSDF origin paths and access rights depend on your AP/account. For AP41, the usual personal local origin is `/ospool/ap41/data/USERNAME`, corresponding to `osdf:///ospool/ap41/data/USERNAME/...`. Another user's personal image may be inaccessible to your jobs. Confirm a shared image's permissions or copy an approved image into your assigned origin; see [OSDF guidance](https://portal.osg-htc.org/documentation/htc_workloads/managing_data/osdf/). Keep submit files, staged slots and Condor logs in AP home.

For a new or changed installation, complete the [first smoke campaign](../docs/osg-campaign.md#first-smoke-campaign) before a production sweep. An existing working campaign need not be recreated merely to check its AP configuration.

## Validation without copying inputs or running events

Reuse an initialized NERSC campaign and complete AP sections 3–4 in a separate directory. These setup steps copy helper scripts and install Python packages, but no campaign inputs. Do not run section 2's initialization or the runbook's live staging/submission commands.

On **NERSC**, with the existing campaign profile active, check the selected point's bundle. Replace `design` and `system` with the intended literal suffix and configured system:

```bash
(
  set -e
  design=000
  system=AuAu200
  bundle="${HIC_RAW_ROOT:?Activate the NERSC campaign profile first}/design_${design}/${system}"
  for name in initial.hdf parameters.dat music_input; do
    path="$bundle/$name"
    if test -f "$path" && test -s "$path" && test -r "$path"; then
      printf 'OK: %s\n' "$path"
    else
      printf 'FAIL: missing, empty, or unreadable: %s\n' "$path" >&2
      exit 1
    fi
  done
)
```

This checks file presence, nonzero size and read permissions, not HDF5 structure or physics contents. It writes nothing. Also confirm that the selected image is accessible at its assigned origin; for an AP41 personal image, for example:

```bash
ls -lh /ospool/ap41/data/YOUR_AP_USERNAME/heavy-ion-VERSION.sif
test -r /ospool/ap41/data/YOUR_AP_USERNAME/heavy-ion-VERSION.sif &&
  echo 'Image is readable on the AP.'
```

Run that image check on the **AP**, then follow the runbook's [read-only staging preview](../docs/osg-campaign.md#read-only-staging-preview). It requires the campaign's original full event count to calculate a gap; that argument is not a request to run those events. Keep `--dry-run` in the command.

Stop after those checks if that is your intended scope. Passing establishes AP setup, unattended SSH, file access and a staging plan. It does not validate worker-side image retrieval, input contents, model execution or output draining. A `condor_submit -dry-run` needs an actual manifest and staged inputs and writes job descriptions; it is part of the later smoke procedure, not required for this no-input-copy check.

## 5. Build a new image when the approved image needs updating

Skip this section for an existing matching image. Otherwise assemble a fresh build context with enough quota for source, builds, image and temporary files. Replace these paths:

```bash
export SOURCE_REPO=/path/to/pce-eventgen
export STATIC_DATA="$HOME/pce-eventgen-data"
python3 "$SOURCE_REPO/tools/download_physics_data.py" --directory "$STATIC_DATA"
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
rsync -a "$STATIC_DATA/" "$IMAGE_BUILD/data/"
python3 "$SOURCE_REPO/tools/check_physics_data.py" eos "$IMAGE_BUILD/data/EOS/PCE/strange_30_10000"
python3 "$SOURCE_REPO/tools/check_physics_data.py" is3d \
  "$IMAGE_BUILD/data/iS3D/deltaf_coefficients/vh/smash_pce_light_strange"
git -C "$IMAGE_BUILD/smash" describe --tags   # expect SMASH-3.0-1-g0985d6b3
```

The public bundle supplies [the canonical layout](../nersc/SETUP.md#2-obtain-the-checkout-and-production-inputs), including all EOS/coefficient tables and the attribution/license notices copied into the image. Keep matching SMASH particle/decay inputs. The definition uses Rocky Linux 9's GNU toolchain and native libraries (CMake 3.x, GSL 2.x and Eigen 3.4), plus compatible NumPy/h5py releases; do not replace these with unbounded latest-version installs. TRENTo is not built in the image, so its Boost dependency is unnecessary. The definition downloads Pythia 8.309; offline builds need its archive staged and the definition adapted.

Run the build **from the context directory**, because `%files` paths are relative to that directory. On an OSG-operated AP, first prepare the build environment following the current [OSPool build instructions](https://portal.osg-htc.org/documentation/htc_workloads/using_software/containers-singularity/):

```bash
mkdir -p "$HOME/tmp"
export TMPDIR="$HOME/tmp"
export APPTAINER_TMPDIR="$HOME/tmp"
export APPTAINER_CACHEDIR="$HOME/tmp"
cd "$IMAGE_BUILD"
apptainer build --ignore-proot heavy-ion-VERSION.sif heavy-ion.def
```

Choose a new `VERSION` and follow the build host's current resource/fakeroot policy. The definition uses two build processes and a portable x86-64-v2 baseline. Retain build logs and source/data provenance.

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
  test -s "$HIC_PDG_DIR/baryon_dict.dat"
  test -s "$HIC_IS3D_CONFIG"
  "$HIC_SMASH_EXEC" --version
'
```

Inspect the loader output for missing libraries; `ldd` can return zero while printing `not found`. The container helper exports runtime paths, not the analysis-only `HIC_BARYON_DICT`, so that table is checked through `HIC_PDG_DIR`. Publish the validated SIF to your assigned origin under its new filename and set `IMAGE` in the submit file. Confirm execution with a fresh smoke job: OSPool runs the transferred `job.sh`, not the definition's `%runscript`.
