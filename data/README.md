# Physics data release

Users download the complete frozen EOS/iS3D runtime data with one command from the checkout:

```bash
python3 tools/download_physics_data.py --directory "$HOME/pce-eventgen-data"
```

Python 3.8 or later is sufficient; no Python packages, Git LFS or NERSC account are required. The helper checks the extracted EOS/iS3D runtime files, keeps the included attribution/license notices and reuses an existing matching data directory. It never replaces an existing mismatched data directory. Allow about 1.2 GB for the unpacked files plus space for the compressed download during installation. See [first-run setup](../docs/getting-started.md).

## Prepare and publish

The archive is prepared locally in `dist/`. Before the public download command can work, attach `pce-eventgen-physics-data.tar.gz` as an asset to the repository's GitHub release tagged `physics-data`. Release assets are separate from the Git checkout: do not commit the archive to Git. The helper downloads it from that release URL, recorded in `physics-data.json`; publish the asset before directing users to download it.

On NERSC, run the download helper on a login node with `--directory "$DATA"` to install directly into your chosen CFS data directory. The initial source upload excludes `dist/` and downloaded data, so it does not transfer the archive or physics tables. For offline setup, transfer the archive separately and use `--archive` as shown below.

To reproduce the archive from the local verified datasets, choose a new output path:

```bash
python3 tools/package_physics_data.py --output /path/to/new/pce-eventgen-physics-data.tar.gz
```

Defaults select `models/iS3D` and `models/music-hydro/EOS/PCE/strange_30_10000`; `--is3d-source` and `--eos-source` select other copies. The builder first checks the original production manifests, includes runtime data and notices only, normalizes archive metadata and updates `data/physics-data.json`. That manifest records the runtime file checksums and archive metadata; installation verifies the runtime files against it.

For offline installation or testing before publication:

```bash
python3 tools/download_physics_data.py \
  --archive dist/pce-eventgen-physics-data.tar.gz --directory /path/to/new/physics-data
```

Publishing is a separate action; the builder and download helper never create a GitHub release or upload files. The archive carries [attribution](ATTRIBUTION.md), existing project/model license notices, and the original EOS/coefficient manifests. It does not assert that the retained generators can reproduce the frozen production bytes.
