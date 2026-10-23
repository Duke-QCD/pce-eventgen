#!/bin/bash
# Source this file to use the co-located iS3D data from any event directory.
# Explicit HIC_* overrides, including production data paths, always win.
_is3d_data_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HIC_IS3D_DATA_ROOT="${HIC_IS3D_DATA_ROOT:-$_is3d_data_dir}"
export HIC_IS3D_CONFIG="${HIC_IS3D_CONFIG:-$HIC_IS3D_DATA_ROOT/iS3D_parameters.dat}"
export HIC_IS3D_PDG_DIR="${HIC_IS3D_PDG_DIR:-$HIC_IS3D_DATA_ROOT/PDG}"
export HIC_IS3D_TABLE_DIR="${HIC_IS3D_TABLE_DIR:-$HIC_IS3D_DATA_ROOT/tables}"
export HIC_IS3D_DELTAF_DIR="${HIC_IS3D_DELTAF_DIR:-$HIC_IS3D_DATA_ROOT/deltaf_coefficients/vh}"
export HIC_IS3D_CHOSEN_PARTICLES="${HIC_IS3D_CHOSEN_PARTICLES:-$HIC_IS3D_PDG_DIR/chosen_particles.dat}"
export HIC_CHARGE_DICT="${HIC_CHARGE_DICT:-$HIC_IS3D_PDG_DIR/charge_dict.dat}"
export HIC_BARYON_DICT="${HIC_BARYON_DICT:-$HIC_IS3D_PDG_DIR/baryon_dict.dat}"
export HIC_PDG_MASS="${HIC_PDG_MASS:-$HIC_IS3D_PDG_DIR/pdg_smash_pce.dat}"
unset _is3d_data_dir
