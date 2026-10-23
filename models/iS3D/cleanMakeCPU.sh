#!/bin/bash
# Reconfigure the current CPU sources without clearing any event output.
set -e
is3d_source="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
is3d_build="${HIC_IS3D_BUILD_DIR:-$is3d_source/build-workflow}"
cmake -S "$is3d_source" -B "$is3d_build" -DCMAKE_BUILD_TYPE=Release
cmake --build "$is3d_build" --parallel "${HIC_BUILD_JOBS:-$(nproc)}"
