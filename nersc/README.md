# NERSC

The [repository handbook](../docs/README.md) supplies the physics/workflow orientation and searchable [command](../docs/reference/commands.md) and [configuration](../docs/reference/configuration.md) references.

Start with **[New-user setup](SETUP.md)** for the current Perlmutter workflow. It covers home/Global Common/CFS/scratch placement, downloading physics data, building executables, and generating a personal profile.

## Installation

Download EOS/iS3D runtime inputs directly into CFS with `python3 tools/download_physics_data.py --directory "$DATA"`; verification and attribution are automatic. [`stage.py`](stage.py) remains available for uploading source without Git history/builds or copying an existing data tree. Its commands preview until `--apply`. See [setup step 2](SETUP.md#2-obtain-the-checkout-and-production-inputs) for storage selections and complete commands.

Activate your Conda environment, load the compiler and dependencies described in the setup guide, and run from the repository root. The guide creates the environment in `/global/common/software`; the installer writes into that active environment.

```bash
./nersc/install -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

To rebuild one model, supply its name, for example:

```bash
./nersc/install SMASH -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
./nersc/install iS3D
```

The NERSC entry point calls `local/install` using the loaded toolchain. See [local model installation](../local/README.md) for build options, installed layout, and runtime overrides. The production MUSIC EOS and iS3D tables must be supplied as described in the setup guide.

## Campaigns

Source your generated `activate.sh`, then use `models/campaign.py` with your profile's `calibration.ini`. The [campaign guide](../docs/nersc-campaign.md#select-your-profile) covers generation, repair/fill, status, cache/publish, review/reap and analysis. The batch launcher is your profile's `pce-eventgen`. Inference remains separate.
