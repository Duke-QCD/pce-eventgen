# NERSC

The [repository handbook](../docs/README.md) supplies the physics/workflow
orientation and searchable [command](../docs/reference/commands.md) and
[configuration](../docs/reference/configuration.md) references.

Start with **[New-user setup](SETUP.md)** for the current Perlmutter workflow.
It covers home/Global Common/CFS/scratch placement, reuse or independent copies
of production data, building your own executables, and personal configuration.

## Installation

Activate your conda environment under `/global/common/software`, load the
compiler and dependencies described in the setup guide, and run from the
repository root:

```bash
./nersc/install -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
```

To rebuild one model, supply its name, for example:

```bash
./nersc/install SMASH -- -DPythia_CONFIG_EXECUTABLE=/path/to/pythia8-config
./nersc/install iS3D
```

The NERSC entry point calls `local/install` using the loaded toolchain.
See [local model installation](../local/README.md)
for build options, installed layout, and runtime overrides. The production MUSIC
EOS and iS3D tables must be supplied as described in the setup guide.

## Campaigns

Source your generated `activate.sh`, then use `models/campaign.py` with your
profile's `calibration.ini`. The [campaign guide](../docs/nersc-campaign.md#select-your-profile)
covers generation, repair/fill, status, cache/publish, review/reap and analysis.
The batch launcher is your profile's `pce-eventgen`. Inference remains
separate.
