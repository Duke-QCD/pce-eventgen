# PCE event generation physics data

This is the `physics-data` runtime input bundle for https://github.com/Duke-QCD/pce-eventgen. Use the matching source release and its setup guide to build and run the models.

The bundle contains `EOS/PCE/strange_30_10000/` and the complete `iS3D/` runtime data tree. It contains no executables, environment, campaign output or nuclear configurations for OO5360. The uncompressed inputs occupy about 1.2 GB.

The repository's `tools/download_physics_data.py` downloads, checks and extracts this bundle in one step. It retains `ATTRIBUTION.md`, `licenses/` and the production dataset manifests automatically. The data location can be a personal directory or a shared project directory; it does not depend on the original maintainer's NERSC storage.

Follow the repository's first-run guide for a local installation, or pass this bundle's directory as `--data-root` when generating a NERSC profile. The EOS directory itself contains `0.dat` through `29.dat`; the iS3D root contains `iS3D_parameters.dat`, `paths.sh`, `PDG/`, `tables/` and `deltaf_coefficients/`.
