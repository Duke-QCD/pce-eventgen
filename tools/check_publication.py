#!/usr/bin/env python3
"""Audit one publication ref, including deleted files in its reachable history.

Checks local-only paths, large Git blobs, legacy LFS pointers and recognizable
credential formats without printing secret values. Does not inspect other refs,
submodule history or untracked files, and never uploads or changes anything.
"""

import argparse
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ROOTS = (
    '.recovery', '.aws', '.ssh', '.codex', '.agents', 'personal', 'private',
    'scratch', 'dist', 'physics-data', 'example-output',
)
LEGACY_FILES = {
    'models/music-hydro/utilities/submit_full_job.pbs',
    'nersc/examples/simple',
    'models/music-hydro/EOS/PCE/PCE_eos_smooth_e0p25spacing.dat',
}
SECRET_PATTERNS = (
    re.compile(rb'-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----'),
    re.compile(rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b'),
    re.compile(rb'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b'),
    re.compile(rb'\bxox[baprs]-[A-Za-z0-9-]{20,}'),
)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def local_only(path):
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    return (
        parts[0] in LOCAL_ROOTS
        or parts[0].startswith('multisystem-calibration-paper')
        or path.startswith('config/users/')
        or path in LEGACY_FILES
        or any(part in {'.venv', 'venv', '__pycache__', '.matplotlib', '.pytest_cache'} for part in parts)
        or any(part == '.env' or (part.startswith('.env.') and part != '.env.example') for part in parts)
        or parts[-1] in {'.netrc', '.pypirc', 'id_rsa', 'id_ed25519'}
        or path.endswith(('.pyc', '.pyo'))
        or (path.startswith('inference/') and len(parts) > 1
            and parts[1].startswith(('plots', 'scan_')))
        or (path.startswith('inference/model/') and path.endswith(('.npz', '.meta.json')))
    )


def audit(ref):
    # Resolve once, so a moving branch cannot change the target during the audit.
    commit = git('rev-parse', '--verify', ref + '^{commit}').decode().strip()
    commits = git('rev-list', commit).decode().splitlines()
    paths = set()
    for revision in commits:
        paths.update(p.decode('utf-8', errors='replace')
                     for p in git('ls-tree', '-r', '-z', '--name-only', revision).split(b'\0') if p)
    failures = {'local-only path in history: ' + path for path in paths if local_only(path)}
    objects = git('rev-list', '--objects', commit).decode().splitlines()
    blob_count = 0
    with subprocess.Popen(['git', 'cat-file', '--batch'], cwd=ROOT,
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE) as reader:
        for entry in objects:
            oid, _, path = entry.partition(' ')
            reader.stdin.write((oid + '\n').encode())
            reader.stdin.flush()
            header = reader.stdout.readline().split()
            if len(header) != 3 or header[0].decode() != oid:
                raise ValueError('Missing or unreadable Git object: ' + oid)
            kind, size = header[1], int(header[2])
            if size > 100 * 1024 * 1024:
                failures.add('Git blob exceeds 100 MiB: ' + path)
                remaining = size
                while remaining:
                    block = reader.stdout.read(min(remaining, 1024 * 1024))
                    if not block:
                        raise ValueError('Truncated Git object: ' + oid)
                    remaining -= len(block)
                data = b''
            else:
                data = reader.stdout.read(size)
                if len(data) != size:
                    raise ValueError('Truncated Git object: ' + oid)
            if reader.stdout.read(1) != b'\n':
                raise ValueError('Invalid Git object framing: ' + oid)
            if kind != b'blob':
                continue
            blob_count += 1
            if data.startswith(b'version https://git-lfs.github.com/spec/v1\n'):
                failures.add('Git LFS pointer in history: ' + path)
            if any(pattern.search(data) for pattern in SECRET_PATTERNS):
                failures.add('Possible credential in history (value withheld): ' + path)
        reader.stdin.close()
        if reader.wait():
            raise ValueError('Git object reader failed')
    print(f'Audited {commit[:12]}: {len(commits)} commits, {len(paths)} historical paths, '
          f'{blob_count} blobs.')
    for failure in sorted(failures):
        print('FAIL: ' + failure, file=sys.stderr)
    if not failures:
        print('Publication checks passed. Review source and data permissions separately.')
    return bool(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ref', default='HEAD', help='Commit or branch to audit (default: HEAD).')
    args = parser.parse_args()
    try:
        return int(audit(args.ref))
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('ERROR: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
