#!/usr/bin/env python3
"""Write a personal NERSC profile; never install, copy data, or submit jobs.

Renders the four files in config/templates/ (paths, launcher, campaign INI,
activation script) into a new profile directory. Existing profiles are never
overwritten. See nersc/SETUP.md for data requirements and installation.
"""

import argparse
import getpass
import os
from pathlib import Path
import re
import shlex


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'config/templates'


def absolute(value):
    expanded = os.path.expandvars(os.path.expanduser(str(value)))
    if re.search(r'\$(?:[A-Za-z_]\w*|\{[^}]+\})', expanded):
        raise ValueError(f'Unresolved variable in path: {value}')
    return Path(expanded).resolve()


def fill(template, values, quote):
    """Replace every {{NAME}} placeholder; refuse unknown or leftover placeholders."""
    text = (TEMPLATES / template).read_text()
    names = set(re.findall(r'\{\{(\w+)\}\}', text))
    missing = names - set(values)
    if missing:
        raise ValueError(f'{template} uses undefined placeholder(s): {", ".join(sorted(missing))}')
    for name in names:
        value = str(values[name])
        text = text.replace('{{' + name + '}}', shlex.quote(value) if quote else value)
    return text


def render(project, software, data, output, raw, profile, design, nevents, runtime_gcc_module,
           archive_base):
    """Render every file before writing anything; no NERSC access is required."""
    campaign = raw.name
    slurm_logs = raw / '_logs/slurm'
    values = {
        'ACCOUNT': project, 'CODE_HOME': ROOT, 'SOFTWARE_ROOT': software, 'DATA_ROOT': data,
        'RUNTIME_GCC_MODULE': runtime_gcc_module, 'RAW_ROOT': raw, 'OUTPUT_ROOT': output,
        'ARCHIVE_ROOT': output / 'surface_archive',
        'ARCHIVE_BASE': archive_base,
        'CACHE_STAGING_ROOT': output / 'cache_staging',
        'CACHE_PUBLISHED_ROOT': output / 'event_caches',
        'RECORDS': output / 'records' / campaign, 'CAMPAIGN': campaign, 'DESIGN': design,
        'NEVENTS': nevents, 'PATHS_FILE': profile / 'nersc_paths.sh',
        'CONFIG': profile / 'calibration.ini', 'LAUNCHER': profile / 'pce-eventgen',
        'SLURM_OUTPUT': slurm_logs / '%x-%j.out', 'SLURM_ERROR': slurm_logs / '%x-%j.err',
    }
    return {
        'nersc_paths.sh': fill('nersc_paths.sh', values, quote=True),
        'pce-eventgen': fill('pce-eventgen', values, quote=True),
        'calibration.ini': fill('calibration.ini', values, quote=False),
        'activate.sh': fill('activate.sh', values, quote=True),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, help='NERSC allocation/account, e.g. m2730.')
    parser.add_argument('--data-root', required=True, help='Static-data root containing iS3D/ and EOS/.')
    parser.add_argument('--software-root', help='Your conda/model installation prefix.')
    parser.add_argument('--runtime-gcc-module',
                        default=os.environ.get('HIC_RUNTIME_GCC_MODULE', 'gcc-native/14'),
                        help='GNU runtime module matching your build (default: '
                             'HIC_RUNTIME_GCC_MODULE or gcc-native/14). Pass an empty string '
                             'to disable the event wrapper\'s module load.')
    parser.add_argument('--output-root', help='Writable CFS root for this user’s archives, caches and records.')
    parser.add_argument('--raw-root', help='Scratch campaign root (default $PSCRATCH/pce-eventgen).')
    parser.add_argument('--archive-base', help='Original source base for surface archive paths. '
                        'Default: the PSCRATCH/SCRATCH root containing raw-root, or raw-root\'s parent. '
                        'For an existing campaign, select the base used to create its archives.')
    parser.add_argument('--out-dir', help='New profile directory; refuses to overwrite an existing directory.')
    parser.add_argument('--design-file', default=str(ROOT / 'config/initialization/design_params_combined500.dat'),
                        help='Design table; defaults to the campaign\'s 500-row table.')
    parser.add_argument('--nevents', type=int, default=10000,
                        help='Fixed event bound; use 2 only for a separate setup-smoke campaign.')
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', args.project) or args.nevents <= 0:
            raise ValueError('Project must be an account name and nevents must be positive')
        user = getpass.getuser()
        software = absolute(args.software_root or f'/global/common/software/{args.project}/{user}/pce-eventgen')
        output = absolute(args.output_root or f'/global/cfs/cdirs/{args.project}/{user}/pce-eventgen')
        scratch = os.environ.get('PSCRATCH') or os.environ.get('SCRATCH')
        if not args.raw_root and not scratch:
            raise ValueError('Set PSCRATCH/SCRATCH or pass --raw-root explicitly')
        raw = absolute(args.raw_root or str(Path(scratch) / 'pce-eventgen'))
        archive_base = absolute(args.archive_base) if args.archive_base else None
        if archive_base is None:
            for name in ('PSCRATCH', 'SCRATCH'):
                if os.environ.get(name):
                    candidate = absolute(os.environ[name])
                    if candidate == raw or candidate in raw.parents:
                        archive_base = candidate
                        break
            if archive_base is None:
                archive_base = raw.parent
        if archive_base != raw and archive_base not in raw.parents:
            raise ValueError(f'Raw root {raw} is outside archive base {archive_base}')
        profile = absolute(args.out_dir or ROOT / 'config/users' / user)
        design = absolute(args.design_file)
        if not design.is_file():
            raise ValueError(f'Design table not found: {design}')
        files = render(args.project, software, absolute(args.data_root), output, raw,
                       profile, design, args.nevents, args.runtime_gcc_module, archive_base)
        profile.mkdir(parents=True, exist_ok=False)
        for name, text in files.items():
            path = profile / name
            path.write_text(text)
            if name == 'pce-eventgen':
                path.chmod(0o750)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print(f'Personal configuration: {profile}')
    print(f'Software prefix: {software}')
    print(f'GCC runtime module: {args.runtime_gcc_module or "disabled (use the loaded runtime)"}')
    print(f'Durable output root: {output}')
    print(f'Raw campaign root: {raw}')
    print(f'Surface archive source base: {archive_base}')
    print('Only configuration files were created. No software installed, data copied, or jobs submitted.')
    print(f'After preparing the environment/data: source {shlex.quote(str(profile / "activate.sh"))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
