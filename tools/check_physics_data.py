#!/usr/bin/env python3
"""Verify externally managed production physics data against checked-in manifests.

The large MUSIC EOS tables and iS3D delta-f coefficient tables are not stored in
Git. Each dataset has a small manifest with file names, sizes and SHA-256
checksums; this tool confirms that a copy matches it exactly.

  eos   MUSIC EOS 18 tables, the directory holding 0.dat ... 29.dat
        (the value of HIC_EOS_PCE_DIR)
  is3d  iS3D hrg_eos=6 coefficient tables, the smash_pce_light_strange/
        directory (below HIC_IS3D_DELTAF_DIR)
"""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = {
    'eos': ROOT / 'models/music-hydro/data/pce-strange-30-10000.json',
    'is3d': ROOT / 'models/iS3D/data/smash-pce-light-strange.json',
}


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def validate(directory, manifest):
    errors = []
    for entry in manifest['files']:
        path = Path(directory) / entry['name']
        if not path.is_file():
            errors.append(f'missing {path}')
        elif path.stat().st_size != entry['bytes']:
            errors.append(f'wrong size: {path}')
        elif sha256(path) != entry['sha256']:
            errors.append(f'checksum mismatch: {path}')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('dataset', choices=sorted(MANIFESTS), help='Which dataset to check.')
    parser.add_argument('directory', type=Path, help='Directory containing the dataset files.')
    parser.add_argument('--manifest', type=Path, help='Alternative manifest (default: the checked-in one).')
    args = parser.parse_args()
    manifest = json.loads((args.manifest or MANIFESTS[args.dataset]).read_text())
    errors = validate(args.directory, manifest)
    if errors:
        for error in errors:
            print(error)
        return 1
    total = sum(f['bytes'] for f in manifest['files'])
    print(f"Verified {len(manifest['files'])} {manifest['dataset']} files ({total:,} bytes).")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
