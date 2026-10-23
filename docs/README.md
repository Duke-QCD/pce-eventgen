# pce-eventgen handbook

This repository simulates relativistic heavy-ion collisions with a custom
TRENTo → MUSIC → iS3D → SMASH chain, then turns particle output into observables
for calibration. This handbook assumes basic Python and heavy-ion physics,
but no experience running this software or a large computing campaign.
Terms specific to this workflow are defined in the [glossary](glossary.md).

## Learn and run

1. **[Understand the workflow](workflow.md).** Learn what each model does,
   what an event or design means here, and which files connect the stages.
2. **[Get your first result](getting-started.md).** Choose a local or NERSC
   installation, check the external data, and run a small isolated example.
3. **[Operate a campaign](nersc-campaign.md).** Generate many designs,
   monitor progress, recover failures, cache results, and extract observables.
4. **[Read the results](analysis.md).** Understand caches, statistical units,
   centrality, uncertainties, and the calibration matrix.

## Look things up

| I need to… | Read |
|---|---|
| Look up a term (design, oversample, reap, fingerprint, …) | [Glossary](glossary.md) |
| Find a command, flag, argument type, or parser default | [Command reference](reference/commands.md) |
| Understand or change an INI setting | [Configuration reference](reference/configuration.md) |
| Select executables, data, archives, or thread counts | [Environment reference](reference/environment.md) |
| Change physics parameters or model input options | [Physics and model inputs](reference/physics.md) |
| Identify an output file, marker, or archive | [Files and event states](reference/files.md) |
| Diagnose installation, event, cache, or analysis failures | [Troubleshooting](troubleshooting.md) |
| Locate a script or understand the source tree | [Repository and developer guide](repository.md) |
| Run on Perlmutter with personal paths | [NERSC setup](../nersc/SETUP.md) |
| Generate on OSG and transfer to NERSC | [OSG runbook](osg-campaign.md) |
| Set up an OSG account's workflow and test it first | [OSG setup](../osg/README.md) |
| Read matrix labels, statistical errors, design alignment or feed-down inventory | [Analysis-input reference](reference/analysis-inputs.md) |
| Use the matrix in inference | [Standalone inference](../inference/README.md) |
| Try a small inference run without campaign products | [Student inference walkthrough](../inference/GETTING_STARTED.md) |
| Check robustness and injected-truth recovery on small inputs | [Inference scan walkthrough](../inference/SCANS.md) |

The code and approved production inputs define behavior. User names, accounts,
paths and design ranges in the runbooks are examples, not settings for your
campaign. The command tables are generated from source without
running jobs; see [maintaining this handbook](repository.md#maintaining-this-handbook).

## Conventions used throughout

Commands run in Bash from the repository root unless a step explicitly says
otherwise. `python` means the Python in your activated environment. A `$NAME`
is a shell variable: set it in the same shell before using it. Replace paths
such as `/path/to/...` with real paths; they are not provided directories.
`CFG` is the absolute path to the selected campaign INI. `REPO` is your checkout.

Commands that submit jobs, change event directories or delete data only
**preview** unless you add `--submit` (`events`, `cache`, `reap`, `analysis run`)
or `--apply` (`repair`, `initialize`). A preview may still write reports,
scripts or manifests.

Use editor search or `rg` to look up an exact spelling, for example:

```bash
rg -n -- 'max-afterburner-retries' docs models/run-events
rg -n 'events_per_shard' docs config
python models/campaign.py --help
python models/campaign.py --config "$CFG" analysis aggregate --help
```
