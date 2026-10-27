#!/usr/bin/env python3
"""Prepare the versioned physics-data release archive and its download manifest.

Maintainer tool; users run download_physics_data.py instead. The checked-in
dataset checksums must pass before packaging. No upload or publication occurs.
"""

import argparse
import gzip
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile

from check_physics_data import MANIFESTS, sha256, validate

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'physics-data'
ARCHIVE = 'pce-eventgen-physics-data.tar.gz'
URL = 'https://github.com/Duke-QCD/pce-eventgen/releases/download/' + VERSION + '/' + ARCHIVE


def source_files(is3d, eos):
    for dataset, directory in (('eos', eos), ('is3d', is3d / 'deltaf_coefficients/vh/smash_pce_light_strange')):
        errors = validate(directory, json.loads(MANIFESTS[dataset].read_text()))
        if errors:
            raise ValueError('\n'.join(errors))
    files = {}
    for name in ('iS3D_parameters.dat', 'paths.sh'):
        files['iS3D/' + name] = is3d / name
    for name in ('PDG', 'tables', 'deltaf_coefficients'):
        directory = is3d / name
        if not directory.is_dir():
            raise ValueError(f'Missing iS3D directory: {directory}')
        for path in sorted(directory.rglob('*')):
            if (path.is_file() and '__pycache__' not in path.parts
                    and not path.name.endswith('~') and '_backup' not in path.name
                    and not path.name.startswith('.')):
                files['iS3D/' + path.relative_to(is3d).as_posix()] = path
    for name in ('PDG/pdg_smash_pce.dat', 'PDG/chosen_particles.dat',
                 'PDG/charge_dict.dat', 'PDG/baryon_dict.dat'):
        if 'iS3D/' + name not in files:
            raise ValueError(f'Missing iS3D metadata: {is3d / name}')
    for entry in json.loads(MANIFESTS['eos'].read_text())['files']:
        files['EOS/PCE/strange_30_10000/' + entry['name']] = eos / entry['name']
    files.update({
        'README.md': ROOT / 'data/ARCHIVE_README.md',
        'ATTRIBUTION.md': ROOT / 'data/ATTRIBUTION.md',
        'licenses/pce-eventgen-MIT.txt': ROOT / 'LICENSE',
        'licenses/iS3D-MIT.txt': ROOT / 'models/iS3D/LICENSE',
        'licenses/MUSIC-GPL-2.0.txt': ROOT / 'models/music-hydro/LICENSE',
        'manifests/music-eos.json': MANIFESTS['eos'],
        'manifests/is3d-coefficients.json': MANIFESTS['is3d'],
    })
    for path in files.values():
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f'Missing or empty data/release file: {path}')
    return files


def package(is3d, eos, output):
    output = Path(output).expanduser().resolve()
    if output.exists():
        raise ValueError(f'Archive already exists: {output}; choose a new output path.')
    print('Checking production data and preparing archive...', flush=True)
    files = source_files(Path(is3d).resolve(), Path(eos).resolve())
    entries = [{'name': name, 'bytes': path.stat().st_size, 'sha256': sha256(path)}
               for name, path in sorted(files.items())]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.pce-package-', dir=output.parent) as temporary:
        archive = Path(temporary) / ARCHIVE
        # Stable headers make the release reproducible from unchanged input bytes.
        with archive.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw,
                                                     mtime=0, compresslevel=6) as compressed:
            with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.USTAR_FORMAT) as bundle:
                for entry in entries:
                    info = tarfile.TarInfo(entry['name'])
                    info.size = entry['bytes']
                    info.mode = 0o644
                    with files[entry['name']].open('rb') as stream:
                        bundle.addfile(info, stream)
        manifest = {'version': VERSION, 'archive': ARCHIVE, 'url': URL,
                    'bytes': archive.stat().st_size, 'sha256': sha256(archive),
                    'unpacked_bytes': sum(entry['bytes'] for entry in entries), 'files': entries}
        if output.exists():
            raise ValueError(f'Archive appeared during packaging: {output}')
        os.rename(archive, output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--is3d-source', type=Path, default=ROOT / 'models/iS3D',
                        help='Complete verified iS3D data root (default: models/iS3D).')
    parser.add_argument('--eos-source', type=Path,
                        default=ROOT / 'models/music-hydro/EOS/PCE/strange_30_10000',
                        help='Verified EOS directory containing 0.dat through 29.dat.')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist' / ARCHIVE,
                        help='New archive path (default: dist/pce-eventgen-physics-data.tar.gz).')
    args = parser.parse_args()
    try:
        manifest = package(args.is3d_source, args.eos_source, args.output)
        (ROOT / 'data/physics-data.json').write_text(json.dumps(manifest, indent=2) + '\n')
    except (OSError, ValueError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    print(f'Archive: {args.output} ({manifest["bytes"] / 1e6:.0f} MB)')
    print('Download manifest: data/physics-data.json')
    print('Prepared only; upload the archive to the ' + VERSION + ' GitHub release to publish it.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
