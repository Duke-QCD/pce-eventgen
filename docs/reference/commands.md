# Command and flag reference

[Handbook](../README.md) · [INI settings](configuration.md) · [Environment](environment.md)

Use `python models/campaign.py --config "$CFG" COMMAND ...` for the maintained
NERSC campaign. `CFG` must point to your selected personal INI. The dispatcher
forwards additional arguments to the backend. Most subcommand help is available
as `python models/campaign.py --config "$CFG" analysis run --help` or directly
as `python models/analysis/run_analysis.py run --help`.

## Complete source-indexed flag tables

The tables include positional arguments, aliases, parser defaults, choices,
types, help text, and source locations. Search for exact spellings in your
editor or with `rg`. They cover repository workflow tools and custom model
helpers; upstream model internals have their own documentation below.
Parser-context columns identify directly declared subcommands or the shared
helper where an option originates.

| Area | Flags and arguments |
|---|---|
| Campaign, runners, setup, initialization, shared configuration/status helpers, checks | [Generation index](cli-generation.md) |
| Build, publish, survey/select, and reap caches | [Cache index](cli-cache.md) |
| Extract, reduce, aggregate, inspect, validate, diagnostics | [Analysis index](cli-analysis.md) |
| Independent Stage B inference, recovery scans and posterior plots | [Inference index](cli-inference.md) |
| Event migration, markers, stuck events, archive deletion | [Maintenance index](cli-maintenance.md) |
| OSG stage, split, sweep, and both drain implementations | [OSG index](cli-osg.md) |
| MUSIC input/surface/EOS helpers, TRENTo extraction, iS3D scripts/converter | [Model-helper index](cli-model-tools.md) |

An index includes all subcommands' declarations in one script table. A flag
declared for `aggregate` is not necessarily accepted by `run`. A declared
default is also not necessarily the resolved default: `run_designs.py` merges
its own defaults and INI, and shared-config backends apply typed INI defaults.
Use `events --print-config` to inspect resolved generation settings. Some older
plotting scripts use fixed paths or another argument parser; their index entry
links the source rather than inventing a command interface.

## Campaign commands and their effects

Stage B inference has independent entry points in
[inference/](../../inference/README.md). It consumes the selected matrix and
does not run through the campaign dispatcher or inherit its INI settings.

| Command | Backend | Effect when invoked | Explicit execution boundary |
|---|---|---|---|
| `events` | `run_designs.py` | Preview: list designs to initialize, audit repair, print routes and `sbatch` commands | `--submit` initializes (if enabled), repairs (applied if enabled) and submits; `--print-config` prints options and exits |
| `repair` | `run_designs.py --repair-only` | Dry-run repair audit; writes audit logs and `campaign_state.json` | `--apply` applies repairs; never initializes or submits |
| `initialize` | `run_designs.py --initialize-only` | List uninitialized designs | `--apply` initializes them (runs TRENTo); never repairs or submits |
| `status` | `cache/survey_raw_designs.py` | Scan raw metadata/history and write configured survey JSON | No generation or cache verification |
| `manifest` | `cache/make_cache_reap_manifest.py` | Write candidate cache manifest using survey/history | No cache execution or deletion |
| `prepare-cache` | `status` then `manifest` | Refresh both using INI settings | No extra CLI selection arguments; invoke the two commands separately for overrides |
| `cache` | `submit_cache_reap_campaign.py --stage cache` | Preview, render batch script and create record/log directories | `--submit` launches batch work; with `--execution local --submit`, executes in the current allocation |
| `review` | `summarize_cache_reap_records.py` | Classify records and write clean-only reap manifest | No fresh deep cache verification or deletion |
| `reap` | `submit_cache_reap_campaign.py --stage reap` | Preview local reapers | `--submit` executes reapers inside an allocation and deletes eligible raw particle lists (and event directories once a design is fully reaped) |
| `analysis run` | `analysis/run_analysis.py run` | Preview extraction of eligible caches | `--submit` actually extracts; `--force` re-extracts matching existing artifacts |
| `analysis audit` | `analysis/run_analysis.py audit` | Inspect artifacts, scope, and failures | Runs immediately; missing artifacts alone need not cause a failing exit |
| `analysis aggregate` | `analysis/run_analysis.py aggregate` | Reduce artifacts and write matrix/metadata/optional CSV | Writes immediately; inspect skipped designs and nonzero exits |
| `analysis feeddown` | `analysis/run_analysis.py feeddown` | Write separate PID-inventory yield product | Writes immediately; does not alter matrix |
| `analysis-design` | `analysis/analyze_design.py` | Single-design commands; choose its subcommand | Inspect the selected subcommand's help |
| `inventory` | `analysis/inspect_inventory.py` | Read cache species inventory | Runs immediately |
| `validate` | `analysis/check_cumulant_consistency.py` | Check matrix cumulants, roots, and requested harmonics | Runs immediately; this is a focused consistency check |
| `fill-plan` | `select_fill.py` | Print near-complete-design plan to stdout, diagnostics to stderr | Redirect stdout to a plan file |
| `fill PLAN` | `fill_wave.sh` | Run one-node Slurm steps consuming that plan | Executes immediately inside an allocation |

Generated profiles set `refuse_existing = true`: add `--allow-existing` to
`events --submit` when resuming an existing initialized design. A between-pass
audit is plain `repair`. Check for active writers before anything that acts.

The dispatcher forwards `--help` to the selected Python backend/subcommand
without reading the INI or doing campaign work:

```bash
python models/campaign.py cache --help
python models/campaign.py analysis run --help
python models/campaign.py analysis aggregate --help
```

`campaign.py --help`, `prepare-cache --help` and `fill --help` show dispatcher
help. For fill's shell environment controls, use the
[campaign guide](../nersc-campaign.md#allocations-and-optional-event-filling).

`--print-commands` on the dispatcher prints backend command lines without
invoking them. The INI must still be valid. For example:

```bash
python models/campaign.py --config "$CFG" --print-commands cache
python models/campaign.py --config "$CFG" events --print-config
python models/campaign.py --config "$CFG" status --only review --details
```

`status --only`, `review --show`, and output row limits affect the display;
they do not restrict JSON snapshots, emitted manifests, or later jobs. Generation
`row_start`/`row_end` do not restrict downstream tools either. For one extraction,
use `analysis run --designs design_027 --submit`. For cache/reap selection, supply
a separately reviewed manifest. Details and shortfall examples are in
[campaign usage](../nersc-campaign.md#configuration-and-selection).

## Direct event runner

```bash
run-events-wrapper --mode both --nevents 10000 /absolute/path/design_000/AuAu200
run-events-wrapper --mode afterburner --nevents 10000 \
  --restore-archives --max-afterburner-retries 3 /absolute/path/design_000/AuAu200
```

The positional directory is required even though it is hidden in the parser's
help. The wrapper accepts the runner's arguments and chooses its sibling
`run-events`. Defaults: mode `hydro`, `nevents=10`, log level `info`, retry cap
3, restoration disabled, logs to stdout. `--logfile` writes a log; the Slurm
launcher adds `--rankvar SLURM_PROCID` and rank formatting to separate task logs.
Do not pass `auto` directly to the runner.

Restoration accepts an optional Boolean value, including `--restore-archives=false`.

## Shell entry points and scheduler files

| Entry | Usage / controls | What it does |
|---|---|---|
| `local/install`, `nersc/install` | `[MODEL ...] [-- SMASH_CMAKE_OPTIONS ...]`; `--help` | Build the selected models (default: trento, music-hydro, iS3D, SMASH) into the active environment; trailing CMake arguments apply to SMASH |
| `models/run-events-wrapper` | Runner arguments above | Resolve sibling runner, co-located data and NERSC runtime module |
| Profile `pce-eventgen` | Selected by INI `[files] launcher`; rendered from `config/templates/pce-eventgen` | Slurm generation launcher using `RUNDIR`, `NEVENTS`, `MODE`, retry/archive/log exports |
| `models/fill_wave.sh` | `bash models/fill_wave.sh PLAN`; normally `campaign.py fill PLAN` | Consume six-field plan and launch `srun` steps; see its environment controls in [environment reference](environment.md#threads-and-fill) |
| `osg/events_fanout.submit` | `condor_submit` from the directory containing `stage/` and the reviewed manifest | One container job per slot; set `IMAGE` and `PROJECT` at the top for your account |
| `osg/job.sh` | Transferred by HTCondor; not a normal interactive CLI | Run the container's event chain and collect terminal output in `outbox/` |
| `models/iS3D/runCPU.sh` | `bash /path/to/runCPU.sh THREADS` from an event directory | Standalone CPU sampling helper; prefer the maintained runner for complete events |
| `models/iS3D/cleanMakeCPU.sh` | `bash /path/to/cleanMakeCPU.sh` | Build standalone CPU iS3D; shared installation is documented separately |

The standalone fixed-chemistry, plotting, legacy make, and upstream example
scripts can carry site-specific paths and physics choices. They are discoverable
in [the repository guide](../repository.md) but are not automatically part of
the main campaign. Do not assume their defaults match a personal profile.

## Model executables

| Program | Reference |
|---|---|
| `trento --help` | [Usage and all upstream-style options](../../models/trento/doc/usage.rst); [initialization](../../models/initialize.py) shows the actual custom invocation |
| `MUSIChydro` | [MUSIC workflow README](../../models/music-hydro/README.md), [input defaults](model-input-defaults.md), and [parameter reader](../../models/music-hydro/src/read_in_parameters.cpp) |
| `iS3D` | [Custom workflow and supported modes](../../models/iS3D/README.md), [parameter file](../../models/iS3D/iS3D_parameters.dat), and [input defaults](model-input-defaults.md) |
| `smash --help` | [SMASH installation](../../models/SMASH/INSTALL.md), [upstream user guide](https://github.com/smash-transport/smash/tree/SMASH-3.0), and repository [workflow YAML](../../config/config.yaml) |

Start with the wrapper rather than invoking models by hand. It supplies the
working directories, custom data, per-event SMASH overrides, conversion, and
completion/archive transitions that individual executables do not coordinate.
