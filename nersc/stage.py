#!/usr/bin/env python3
"""Stage a source copy or approved runtime data for NERSC setup.

Both subcommands preview by default; --apply performs the copy. Software is
built separately with nersc/install. No jobs are submitted or destination files
deleted. See nersc/SETUP.md for storage selections and complete setup.
"""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_physics_data import MANIFESTS, validate


# Explicit rules rather than Git's ignore files: rsync and Git interpret some
# patterns differently, and source uploads must work without Git metadata.
CODE_EXCLUDES = (
    '.git', '.recovery/', '.aws/', '.ssh/', '.codex/', '.agents/',
    '.env', '.env.*', '.netrc', '.pypirc',
    '/personal/', '/private/', '/scratch/',
    '/multisystem-calibration-paper/', '/multisystem-calibration-paper-*', '/multisystem-calibration-dissertation-*',
    '.venv/', 'venv/', '__pycache__/', '.pytest_cache/', '.matplotlib/',
    '*.py[cod]', '*.o', '*~', '.DS_Store',
    'build/', 'build-*/',
    '/config/users/', '/example-output/', '/install.log', '/physics-data/', '/dist/',
    '/models/music-hydro/utilities/submit_full_job.pbs',
    '/models/music-hydro/EOS/PCE/*',
    '/models/iS3D/deltaf_coefficients/vh/smash_pce_light_strange/',
    '/models/iS3D/iS3D', '/models/iS3D/src/cpp/iS3D.e',
    '/models/iS3D/generate_delta_f_coefficients/*/df_vh_dimensionless/deltaf_table',
    '/models/iS3D/results/', '/models/iS3D/results_test/', '/models/iS3D/input/',
    '/models/iS3D/surface.dat', '/models/iS3D/average_thermodynamic_quantities.dat',
    '/models/events.dat', '/models/results', '/music_input',
    '/models/analysis/stats_pilots/', '/models/analysis/stats_*.csv',
    '/models/analysis/osg_vs_nersc.png', '/osg/manifest*.txt',
    '/inference/model/*.npz', '/inference/model/*.meta.json',
    '/inference/studies/',
    '/inference/plots*', '/inference/scan_*', '/inference/*.log',
)
IS3D_FILES = (
    'iS3D_parameters.dat', 'PDG/pdg_smash_pce.dat', 'PDG/chosen_particles.dat',
    'PDG/charge_dict.dat', 'PDG/baryon_dict.dat',
)
IS3D_DIRS = ('PDG', 'tables', 'deltaf_coefficients')


def separate_paths(source, destination):
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError(f'Source and destination must be separate trees: {source}, {destination}')


def copy_code(source, destination, apply=False):
    source = Path(source).expanduser().resolve()
    for name in ('local/install', 'nersc/configure_user.py', 'models/SMASH/CMakeLists.txt'):
        if not (source / name).is_file():
            raise ValueError(f'Missing source file: {source / name}. '
                             'Use the complete checkout, including the initialized SMASH submodule.')
    if not destination or destination.startswith('-') or '\n' in destination or '\r' in destination:
        raise ValueError('Supply a destination directory or USER@HOST:directory.')
    if ':' in destination:
        if not re.fullmatch(r'(?:[\w.-]+@)?[\w.-]+:[^\r\n]+', destination):
            raise ValueError('Remote destination must be USER@HOST:directory (or HOST:directory). '
                             'Use a home-relative or absolute remote path.')
    else:
        local_destination = Path(destination).expanduser().resolve()
        separate_paths(source, local_destination)
        destination = str(local_destination)
    if not shutil.which('rsync'):
        raise ValueError('Source copying requires rsync 3 or newer locally and at the destination.')
    command = ['rsync', '-a', '--no-owner', '--no-group', '--checksum',
               '--protect-args', '--itemize-changes', '--stats']
    if not apply:
        command.append('--dry-run')
    command.append('--include=.env.example')
    for name in ('README.md', 'eos-strange.py'):
        command.append('--include=/models/music-hydro/EOS/PCE/' + name)
    command.extend('--exclude=' + pattern for pattern in CODE_EXCLUDES)
    command.extend(['--', str(source) + '/', destination.rstrip('/') + '/'])
    print(('Copying source' if apply else 'Preview only (rsync dry run)') + ':', flush=True)
    print(shlex.join(command), flush=True)
    subprocess.run(command, check=True)
    print('Source copy includes local edits and SMASH source; Git history and physics datasets '
          'are excluded. Build software and stage physics data separately.')


def check_data(is3d, eos):
    errors = []
    for name in IS3D_FILES:
        path = is3d / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f'Missing or empty iS3D input: {path}')
    for name in IS3D_DIRS:
        directory = is3d / name
        if not directory.is_dir() or not any(p.is_file() for p in directory.rglob('*')):
            errors.append(f'Missing or empty iS3D data directory: {directory}')
    for dataset, directory in (('eos', eos), ('is3d', is3d / 'deltaf_coefficients/vh/smash_pce_light_strange')):
        manifest = json.loads(MANIFESTS[dataset].read_text())
        errors.extend(validate(directory, manifest))
    if errors:
        raise ValueError('\n'.join(errors))


def copy_data(is3d, eos, destination, apply=False):
    is3d, eos, destination = (Path(p).expanduser().resolve() for p in (is3d, eos, destination))
    separate_paths(is3d, destination)
    separate_paths(eos, destination)
    if destination.exists():
        raise ValueError(f'Data root already exists: {destination}. Reuse the approved tree '
                         'in your profile or choose a new data root; existing data is never replaced.')
    print('Checking source EOS and coefficient checksums and required iS3D inputs...', flush=True)
    check_data(is3d, eos)
    print(f'iS3D runtime inputs: {is3d} -> {destination / "iS3D"}')
    print(f'EOS tables: {eos} -> {destination / "EOS/PCE/strange_30_10000"}')
    if not apply:
        print('Preview only. No directories created or files copied; add --apply to copy.')
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Verify a private staging tree before exposing the selected data root.
    with tempfile.TemporaryDirectory(prefix='.pce-data-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'data'
        sampler = staged / 'iS3D'
        sampler.mkdir(parents=True)
        shutil.copy2(is3d / 'iS3D_parameters.dat', sampler)
        if (is3d / 'paths.sh').is_file():
            shutil.copy2(is3d / 'paths.sh', sampler)
        for name in IS3D_DIRS:
            # Follow links so the new tree is independent of the source location.
            shutil.copytree(is3d / name, sampler / name)
        eos_copy = staged / 'EOS/PCE/strange_30_10000'
        shutil.copytree(eos, eos_copy)
        print('Checking copied EOS and coefficient checksums...', flush=True)
        check_data(sampler, eos_copy)
        if destination.exists():
            raise ValueError(f'Data root appeared while copying; refusing to replace it: {destination}')
        os.rename(staged, destination)
    print(f'Verified data root ready: {destination}')
    print('Use this path as --data-root when running nersc/configure_user.py.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    code = subparsers.add_parser('code', help='Copy source with rsync; preview by default.')
    code.add_argument('--source', type=Path, default=ROOT, help='Complete local source tree (default: this checkout).')
    code.add_argument('--destination', required=True, help='Local directory or USER@HOST:directory; remote paths may be home-relative or absolute. Parent must exist.')
    code.add_argument('--apply', action='store_true', help='Copy files; default is an rsync dry run (connects to remote host).')
    data = subparsers.add_parser('data', help='Copy approved runtime data locally on NERSC; preview by default.')
    data.add_argument('--is3d-source', type=Path, required=True, help='Complete approved iS3D data root, including production coefficients.')
    data.add_argument('--eos-source', type=Path, required=True, help='Approved EOS directory containing 0.dat through 29.dat.')
    data.add_argument('--data-root', type=Path, required=True, help='New local CFS data root; refuses an existing path.')
    data.add_argument('--apply', action='store_true', help='Copy and verify data; default checks sources and prints the plan.')
    args = parser.parse_args()
    try:
        if args.command == 'code':
            copy_code(args.source, args.destination, args.apply)
        else:
            copy_data(args.is3d_source, args.eos_source, args.data_root, args.apply)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
