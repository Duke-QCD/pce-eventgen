#!/bin/bash
# =============================================================================
# container_paths.sh
# In-image analog of the NERSC paths file: every path points at a location baked
# into the image under /opt. Sourced by the container's %environment so
# run-events sees the same HIC_* variables as on NERSC. Only the per-event chain
# (MUSIC -> iS3D -> SMASH) runs in the image, so cache/analysis variables are absent.
# =============================================================================

# Software root inside the image
export HIC_SOFTWARE_ROOT=/opt

# Scripts / wrapper prefix the wrapper auto-discovers as VIRTUAL_ENV=/opt/hic
export HIC_CODE_HOME=/opt/hic

# Baked read-only physics data (canonical layout: iS3D/PDG/charge_dict.dat, ...)
export HIC_CFS_ROOT=/opt/data

export HIC_ZSTD_EXEC=zstd

# --- iS3D static data / config (baked) ---
export HIC_IS3D_DATA_ROOT=$HIC_CFS_ROOT/iS3D
export HIC_IS3D_CONFIG=$HIC_IS3D_DATA_ROOT/iS3D_parameters.dat
export HIC_PDG_DIR=$HIC_IS3D_DATA_ROOT/PDG
export HIC_IS3D_PDG_DIR=$HIC_PDG_DIR
export HIC_IS3D_TABLE_DIR=$HIC_IS3D_DATA_ROOT/tables
export HIC_IS3D_DELTAF_DIR=$HIC_IS3D_DATA_ROOT/deltaf_coefficients/vh
export HIC_IS3D_CHOSEN_PARTICLES=$HIC_PDG_DIR/chosen_particles.dat
# Used by iS3D_OSCAR.py to assign charges in the SMASH input.
export HIC_CHARGE_DICT=$HIC_PDG_DIR/charge_dict.dat

# --- Executables / configs (built into the image) ---
export HIC_MUSIC_EXEC=$HIC_SOFTWARE_ROOT/bin/MUSIChydro
export HIC_IS3D_EXEC=$HIC_SOFTWARE_ROOT/bin/iS3D
export HIC_IS3D_OSCAR_SCRIPT=$HIC_CODE_HOME/bin/iS3D_OSCAR.py
export HIC_SMASH_EXEC=$HIC_SOFTWARE_ROOT/SMASH/build/smash
export HIC_SMASH_CONFIG=$HIC_CODE_HOME/config/config.yaml

# --- MUSIC ---
export HIC_MUSIC_INPUT=../music_input
export HIC_EOS_PCE_DIR=$HIC_CFS_ROOT/EOS/PCE/strange_30_10000

# --- Surface archive ---
# On OSG there is no shared FS and surface.dat is produced+consumed locally in
# mode=both, so this is just a local throwaway dir. The job wrapper should set
# it to node-local scratch; default keeps it under the job sandbox.
export HIC_SURFACE_ARCHIVE_ROOT=${HIC_SURFACE_ARCHIVE_ROOT:-$PWD/_local_surface_archive}
