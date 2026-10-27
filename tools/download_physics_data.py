#!/usr/bin/env python3
"""Download and verify the frozen EOS/iS3D data bundle (Python 3.8+, no packages).

Run from the checkout: python3 tools/download_physics_data.py
Select another location with --directory. Attribution and license notices are
included automatically. Existing matching data are checked and reused.
"""

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

from check_physics_data import validate

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'data/physics-data.json'


def unpack(archive, destination):
    """Extract regular files/directories without allowing links or path escapes."""
    seen = set()
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            relative = PurePosixPath(member.name)
            if (relative.is_absolute() or '..' in relative.parts
                    or not (member.isfile() or member.isdir()) or member.name in seen):
                raise ValueError(f'Unexpected archive entry: {member.name}')
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with bundle.extractfile(member) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            target.chmod(0o644)
            seen.add(member.name)


def download(destination, manifest, archive=None):
    destination = Path(destination).expanduser().resolve()
    # Packaging and documentation can change without changing the physics inputs.
    data_manifest = {'files': [entry for entry in manifest['files']
                               if entry['name'].startswith(('EOS/', 'iS3D/'))
                               and not Path(entry['name']).name.lower().startswith(('readme', 'license'))]}
    if destination.exists():
        print(f'Checking existing data: {destination}', flush=True)
        errors = validate(destination, data_manifest)
        if errors:
            raise ValueError('Existing directory does not match this data release. '
                             'Choose a new --directory; existing files were not changed.\n'
                             + '\n'.join(errors[:10]))
        print(f'Physics data ready: {destination}')
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Keep partial downloads/extractions out of the final path and clean up on failure.
    with tempfile.TemporaryDirectory(prefix='.pce-download-', dir=destination.parent) as temporary:
        temporary = Path(temporary)
        if archive is None:
            archive = temporary / manifest['archive']
            print(f'Downloading {manifest["version"]} ({manifest["bytes"] / 1e6:.0f} MB)...', flush=True)
            request = urllib.request.Request(manifest['url'], headers={'User-Agent': 'pce-eventgen-data'})
            with urllib.request.urlopen(request, timeout=60) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        else:
            archive = Path(archive).expanduser().resolve()
        staged = temporary / 'data'
        staged.mkdir()
        print('Unpacking and checking physics data...', flush=True)
        unpack(archive, staged)
        errors = validate(staged, data_manifest)
        if errors:
            raise ValueError('Data verification failed:\n' + '\n'.join(errors[:10]))
        if destination.exists():
            raise ValueError(f'Destination appeared during download; refusing to replace it: {destination}')
        os.rename(staged, destination)
    print(f'Physics data ready: {destination}')
    print('EOS: ' + str(destination / 'EOS/PCE/strange_30_10000'))
    print('iS3D: ' + str(destination / 'iS3D'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'physics-data',
                        help='Data location (default: physics-data/ in this checkout).')
    parser.add_argument('--archive', type=Path,
                        help='Use an already downloaded release archive instead of the network.')
    args = parser.parse_args()
    try:
        manifest = json.loads(RELEASE.read_text())
        download(args.directory, manifest, args.archive)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            print('ERROR: The physics-data release is not available at the published URL yet. '
                  'The maintainer must upload the prepared release archive first. '
                  'An existing copy can be installed with --archive PATH.', file=sys.stderr)
        else:
            print(f'ERROR: Download failed: {error}', file=sys.stderr)
        return 1
    except (OSError, ValueError, tarfile.TarError, urllib.error.URLError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
