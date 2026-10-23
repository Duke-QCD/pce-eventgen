#!/bin/bash
# Interactive fill for designs near completion.  Run INSIDE an salloc.
#
#   source /path/to/personal/profile/activate.sh
#   python models/campaign.py --config "$HIC_CAMPAIGN_CONFIG" fill fill.plan
#
# Reads a plan file of "design ntasks mode restore short system" lines (from
# select_fill.py) and launches one srun step per design.  The collision system
# comes from the plan, never from a default; one system per plan.
#
# CPU layout: each task gets CPT logical CPUs (default 16 = 8 physical cores)
# and that many OpenMP threads -- the same layout as running
# `srun -c 16 run-events-wrapper ...` by hand.  The threads speed up MUSIC;
# SMASH is serial.  A step stays on one node, so a design gets at most
# CPUS_PER_NODE/CPT tasks (16 at the default) whatever the plan's ntasks says.
# A rank that finishes an event claims the next unclaimed one, so a design with
# more runnable events than that works through them in rounds.  Steps that do
# not fit the allocation QUEUE INSIDE IT and start as earlier steps finish --
# that is the cycling, and Slurm does it for you.  Dispatch logs looping
# "Job step creation temporarily disabled, retrying" is the queue working.
#
# Environment knobs [default]:
#   CPT [16]                 logical CPUs per task; keep it even (2 = one core)
#   CPT_AFTERBURNER [CPT]    CPUs per task on mode=afterburner lines, which run
#                            no MUSIC; 2 fits 8x more of them per node
#   OMP_THREADS [line CPT]   OpenMP threads per task
#   RAW_ROOT [$HIC_RAW_ROOT]  NEVENTS [required]  MAXRETRY [3]
#   LOG_ROOT [$SCRATCH/logs/fill_<timestamp>]
set -u

if [ -z "${BASH_VERSION:-}" ]; then
  echo "ERROR: run this with bash, not sh: bash $0 <plan-file>" >&2
  exit 1
fi

PLAN="${1:?usage: fill_wave.sh <plan-file>}"
[[ -f "$PLAN" ]] || { echo "ERROR: plan file not found: $PLAN" >&2; exit 1; }

# Outside an allocation, every srun below would request a separate job.
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
  echo "ERROR: SLURM_JOB_ID is not set; run this inside an salloc" >&2
  exit 1
fi

# Load the selected profile before choosing its software environment. The
# campaign dispatcher may override the profile's archive root/base from the INI.
requested_surface_archive_root="${HIC_SURFACE_ARCHIVE_ROOT:-}"
requested_surface_archive_base="${HIC_SURFACE_ARCHIVE_BASE:-}"
if [[ -z "${HIC_PATHS_FILE:-}" ]]; then
  echo "ERROR: HIC_PATHS_FILE is not set; source your profile's activate.sh first" >&2
  exit 1
fi
source "$HIC_PATHS_FILE" || exit 1
if [[ -n "$requested_surface_archive_root" ]]; then
  export HIC_SURFACE_ARCHIVE_ROOT="$requested_surface_archive_root"
fi
if [[ -n "$requested_surface_archive_base" ]]; then
  export HIC_SURFACE_ARCHIVE_BASE="$requested_surface_archive_base"
fi

module load conda || exit 1
conda activate "$HIC_SOFTWARE_ROOT" || exit 1
module load gsl cpu || exit 1
export XDG_DATA_HOME="$CONDA_PREFIX/share"
export PYTHONNOUSERSITE=1

export PMI_NO_FORK=1 PMI_NO_PREINITIALIZE=1 PMI_MMAP_SYNC_WAIT_TIME=300

# Thread counts are always set explicitly: OpenMP sizes an unset pool from
# whatever CPU mask it sees.  This OMP_NUM_THREADS=1 is only the default; each
# step gets its own count at dispatch, and --cpu-bind=cores keeps those threads
# on the task's own CPUs.  The library clamps only affect numpy in run-events.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1

RAW_ROOT="${RAW_ROOT:-${HIC_RAW_ROOT:?Set RAW_ROOT or HIC_RAW_ROOT (campaign.py fill sets it from the INI)}}"
NEVENTS="${NEVENTS:?Set NEVENTS (campaign.py fill sets it from the INI)}"  # event bound and dir padding
MAXRETRY="${MAXRETRY:-3}"
LOG_ROOT="${LOG_ROOT:-$SCRATCH/logs/fill_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$LOG_ROOT"

# --- CPU layout --------------------------------------------------------------
# Slurm counts hyperthreads as CPUs: 256 per Perlmutter CPU node.
_cpn=${SLURM_JOB_CPUS_PER_NODE:-}            # e.g. "256(x4)"
CPUS_PER_NODE=${_cpn%%[!0-9]*}
CPUS_PER_NODE=${CPUS_PER_NODE:-256}
NNODES=${SLURM_JOB_NUM_NODES:-${SLURM_NNODES:-1}}
CPT="${CPT:-16}"
CPT_AFTERBURNER="${CPT_AFTERBURNER:-$CPT}"
for _v in CPT CPT_AFTERBURNER; do
  if [[ ! ${!_v} =~ ^[1-9][0-9]*$ ]] || (( ${!_v} > CPUS_PER_NODE )); then
    echo "ERROR: $_v=${!_v}: need an integer in 1..$CPUS_PER_NODE" >&2
    exit 1
  fi
done
if [[ -n "${OMP_THREADS:-}" && ! $OMP_THREADS =~ ^[1-9][0-9]*$ ]]; then
  echo "ERROR: OMP_THREADS=$OMP_THREADS: need a positive integer" >&2
  exit 1
fi

# --- read the plan into an array ---------------------------------------------
# Plain redirect, no process substitution: `done < <(...)` is a bashism that dies
# under sh/POSIX mode, and nothing inside the dispatch loop may touch stdin.
PLAN_LINES=()
while IFS= read -r _line || [[ -n "$_line" ]]; do
  [[ -z "${_line//[[:space:]]/}" ]] && continue     # blank
  [[ "${_line#"${_line%%[![:space:]]*}"}" == \#* ]] && continue   # comment
  PLAN_LINES+=("$_line")
done < "$PLAN"

if [[ ${#PLAN_LINES[@]} -eq 0 ]]; then
  echo "ERROR: no usable lines in $PLAN" >&2
  exit 1
fi

# --- system comes from the plan: 6 fields per line, one system per wave -------
PLAN_SYSTEM=""
for _l in "${PLAN_LINES[@]}"; do
  read -r -a _f <<< "$_l"
  if [[ ${#_f[@]} -ne 6 ]]; then
    echo "ERROR: expected 'design ntasks mode restore short system', got: $_l" >&2
    echo "       regenerate the plan with the current select_fill.py" >&2
    exit 1
  fi
  if [[ ! ${_f[1]} =~ ^[1-9][0-9]*$ ]]; then
    echo "ERROR: ntasks is not a positive integer in: $_l" >&2
    exit 1
  fi
  if [[ -z $PLAN_SYSTEM ]]; then
    PLAN_SYSTEM=${_f[5]}
  elif [[ ${_f[5]} != "$PLAN_SYSTEM" ]]; then
    echo "ERROR: plan mixes systems ($PLAN_SYSTEM, ${_f[5]}); one system per wave" >&2
    exit 1
  fi
done
if [[ -n ${SYSTEM:-} && $SYSTEM != "$PLAN_SYSTEM" ]]; then
  echo "ERROR: SYSTEM=$SYSTEM in environment, but plan is for $PLAN_SYSTEM" >&2
  exit 1
fi
SYSTEM=$PLAN_SYSTEM
echo "[fill] system=$SYSTEM (from plan)"

# --- size the steps ----------------------------------------------------------
# One node per step caps a design at CPUS_PER_NODE/cpt tasks.  Missing and
# reaped designs are dropped here, so the CPU tally counts only real work.
STEPS=()
_tasks=0
_cpus=0
for _l in "${PLAN_LINES[@]}"; do
  read -r D NT MODE RESTORE SHORT _ <<< "$_l"
  RUNDIR="$RAW_ROOT/$D/$SYSTEM"
  if [[ ! -d "$RUNDIR" ]]; then
    echo "[fill] SKIP $D: no $RUNDIR" >&2
    continue
  fi
  if [[ -f "$RUNDIR/reaped.done" ]]; then
    echo "[fill] SKIP $D: $RUNDIR is reaped" >&2
    continue
  fi
  if [[ $MODE == afterburner ]]; then cpt=$CPT_AFTERBURNER; else cpt=$CPT; fi
  max_nt=$(( CPUS_PER_NODE / cpt ))
  nt=$(( NT < max_nt ? NT : max_nt ))
  STEPS+=("$D $nt $cpt ${OMP_THREADS:-$cpt} $MODE $RESTORE $SHORT $NT")
  _tasks=$(( _tasks + nt ))
  _cpus=$(( _cpus + nt * cpt ))
done

_alloc=$(( NNODES * CPUS_PER_NODE ))
echo "[fill] plan=$PLAN log_root=$LOG_ROOT"
echo "[fill] nodes=$NNODES cpus/node=$CPUS_PER_NODE CPT=$CPT CPT_AFTERBURNER=$CPT_AFTERBURNER OMP_THREADS=${OMP_THREADS:-per-line CPT}"
echo "[fill] ${#STEPS[@]} of ${#PLAN_LINES[@]} designs to run: $_tasks tasks, $_cpus of $_alloc CPUs"
if (( _cpus > _alloc )); then
  echo "[fill] NOTE: more than the allocation holds at once; later steps queue inside it"
fi

# --- dispatch ----------------------------------------------------------------
#   OMP_NUM_THREADS=   this step's thread count (MUSIC fills the task's CPUs)
#   -c / --exact       exactly that many CPUs per task
#   --cpu-bind=cores   threads stay on the task's own cores
#   --mem=0            use the job's memory without reserving any of it, so
#                      steps share a node by CPU count alone; a per-step memory
#                      request inherited from the salloc could otherwise hold a
#                      whole node for one small step
STEP_PIDS=()
STEP_DESIGNS=()
if (( ${#STEPS[@]} )); then
  for _s in "${STEPS[@]}"; do
    read -r D NT CPTL OMPL MODE RESTORE SHORT NTPLAN <<< "$_s"
    RUNDIR="$RAW_ROOT/$D/$SYSTEM"
    _cap=""
    (( NT < NTPLAN )) && _cap=" (plan $NTPLAN, one-node cap)"
    echo "[fill] launch $D ntasks=$NT$_cap cpt=$CPTL omp=$OMPL mode=$MODE restore=$RESTORE short=$SHORT"
    OMP_NUM_THREADS="$OMPL" srun -N1 -n "$NT" -c "$CPTL" --exact --cpu-bind=cores --mem=0 -K0 \
      run-events-wrapper \
        --nevents "$NEVENTS" \
        --mode "$MODE" \
        --restore-archives "$RESTORE" \
        --max-afterburner-retries "$MAXRETRY" \
        --rankvar SLURM_PROCID --rankfmt "{:04d}" \
        --logfile "$LOG_ROOT/$D.log" \
        "$RUNDIR" \
      < /dev/null > "$LOG_ROOT/dispatch_$D.out" 2>&1 &
    STEP_PIDS+=("$!")
    STEP_DESIGNS+=("$D")
    sleep 2      # stagger: every worker stats all 10k event dirs on start
  done
fi

failed=0
for i in "${!STEP_PIDS[@]}"; do
  if ! wait "${STEP_PIDS[$i]}"; then
    echo "[fill] FAILED ${STEP_DESIGNS[$i]}: see $LOG_ROOT/dispatch_${STEP_DESIGNS[$i]}.out" >&2
    failed=1
  fi
done
echo "[fill] all steps finished"

# --- report ------------------------------------------------------------------
echo "[fill] terminal-event count by design:"
for _l in "${PLAN_LINES[@]}"; do
  read -r D NT MODE RESTORE SHORT _ <<< "$_l"
  if [[ ! -d "$RAW_ROOT/$D/$SYSTEM" || -f "$RAW_ROOT/$D/$SYSTEM/reaped.done" ]]; then
    printf "  %-12s not run (missing or reaped)\n" "$D"; continue
  fi
  n=$(find "$RAW_ROOT/$D/$SYSTEM" -mindepth 2 -maxdepth 2 \
        \( -name afterburner.done -o -name hydro.no_surface \) 2>/dev/null | wc -l)
  printf "  %-12s %5d/%s terminal  (was %s short)\n" "$D" "$n" "$NEVENTS" "$SHORT"
done
exit "$failed"
