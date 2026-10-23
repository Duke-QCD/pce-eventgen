#!/bin/bash
# Invoke from the event directory containing surface.dat.
set -e
is3d_source="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$is3d_source/paths.sh"
export OMP_NUM_THREADS="${1:-${OMP_NUM_THREADS:-1}}"
mkdir -p results
exec "${HIC_IS3D_EXEC:-${HIC_IS3D_BUILD_DIR:-$is3d_source/build-workflow}/src/cpp/iS3D}"
