#!/bin/bash
# job.sh — runs INSIDE the container (container universe). One event, full chain.
set -euo pipefail

source /opt/hic/container_paths.sh
# Worker archives are disposable and local to this sandbox, independent of any
# NERSC campaign profile inherited by a container invocation.
export HIC_SURFACE_ARCHIVE_BASE="$PWD"

# Match the single CPU requested in events_fanout.submit.
export OMP_NUM_THREADS=1          # MUSIC's OpenMP (and any other OpenMP code)
export OPENBLAS_NUM_THREADS=1     # NumPy/SciPy if linked against OpenBLAS
export MKL_NUM_THREADS=1          # NumPy/SciPy if linked against MKL
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1

echo "PWD=$PWD ; ls of run dir:" >&2
pwd >&2
ls -la >&2
echo "running run-events with run dir: $PWD" >&2

# parameters.dat, music_input and initial.hdf arrive via transfer_input_files.
# The run dir MUST be ABSOLUTE: run-events resolves SMASH's input paths relative
# to it, and a relative "." launches SMASH from the wrong directory on the worker.
/opt/hic/bin/run-events-wrapper --nevents 1 --mode both "$PWD" || true

# Diagnostic only: rerun SMASH so its error reaches the job's stderr. A failure
# here must not stop the script, which still has to return outbox/ and markers.
if [ -f 0/afterburner.failed ]; then
  echo "=== SMASH rerun for diagnosis ===" >&2
  smash_status=0
  (cd 0 && "$HIC_SMASH_EXEC" -i "$HIC_SMASH_CONFIG" -o smash_debug > smash_debug.log 2>&1) \
    || smash_status=$?
  tail -40 0/smash_debug.log >&2 || true
  echo "SMASH diagnostic exit status: $smash_status" >&2
fi

echo "=== tree after run ===" >&2
find . -maxdepth 3 \( -name '*.dat' -o -name '*.oscar*' -o -name 'surface*' -o -name '*.done' -o -name '*.failed' \) -printf '%s\t%p\n' >&2
echo "=== last 40 lines of run output already above; sizes are what matter ===" >&2

# Assemble exactly what NERSC needs into outbox/, preserving native filenames.
# Transferring the DIRECTORY lets HTCondor return whichever files exist without
# faulting on a fixed name: the .oscar / .unfinished suffix is kept intact, and
# only the markers that were actually written come back. No status sidecar needed.
mkdir -p outbox

# the deliverable (whichever variant SMASH produced), name preserved:
for f in 0/particle_lists.oscar 0/particle_lists.oscar.unfinished; do
    [ -e "$f" ] && cp -a "$f" outbox/
done

# completion / progress markers NERSC uses to judge the event:
for m in hydro.done hydro.no_surface afterburner.done afterburner.failed afterburner.retry_count; do
    [ -e "0/$m" ] && cp -a "0/$m" outbox/
done

# Terminal-state validation, matching the maintained runner and raw survey:
#   (a) one nonempty particle list + afterburner.done         -> SUCCESS
#   (b) NO particle list but hydro.no_surface present          -> SUCCESS (valid
#       peripheral zero-output event; afterburner was correctly skipped). MUST be
#       transferred so the cache pipeline counts the peripheral bin -- dropping
#       these biases the centrality dependence.
#   (c) neither a particle list nor hydro.no_surface           -> REAL FAILURE
particle_files=0
for f in outbox/particle_lists.oscar outbox/particle_lists.oscar.unfinished; do
    if [ -e "$f" ]; then
        particle_files=$((particle_files + 1))
        if [ ! -s "$f" ]; then
            echo "ERROR: empty particle output: $f" >&2
            exit 1
        fi
    fi
done
if [ "$particle_files" = 1 ] && [ -f outbox/afterburner.done ] && [ ! -e outbox/hydro.no_surface ]; then
    : # success, including the accepted .unfinished variant
elif [ "$particle_files" = 0 ] && [ -f outbox/hydro.no_surface ] &&
     [ ! -e outbox/hydro.done ] && [ ! -e outbox/afterburner.done ]; then
    echo "no particle list, but hydro.no_surface present: valid terminal zero-output (peripheral) event" >&2
else
    echo "ERROR: incomplete or conflicting terminal output; event needs inspection/retry" >&2
    find . -maxdepth 3 >&2
    exit 1
fi
