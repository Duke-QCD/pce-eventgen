# Campaign configuration reference

[Handbook](../README.md) · [Commands](commands.md) · [Physics](physics.md)

The supported shared format is an INI file. Generate a personal one with `nersc/configure_user.py`, which fills in [config/templates/calibration.ini](../../config/templates/calibration.ini); [NERSC setup](../../nersc/SETUP.md) describes every required path and input.

## Resolution rules

- Explicit CLI values override INI values, then the backend's defaults apply. [Generated CLI tables](commands.md#complete-source-indexed-flag-tables) show declarations; they do not evaluate the selected environment or INI.
- Paths expand `~`, `$VAR`, and `${VAR}`. Relative paths resolve from the **launch directory**, including paths stored inside an absolute INI filename. Unresolved path variables are errors. Use absolute paths for shared profiles.
- Boolean INI values accept `true/false`, `yes/no`, `on/off`, `1/0`. Space-separated lists are used for bins, harmonics, and PIDs.
- Section and key validation is strict: an unknown section or key is an error. Do not invent a key by replacing a flag's hyphens with underscores. The tables below enumerate accepted keys.
- `--submit`, `--apply`, shortfall acceptance/reason, and similar execution controls stay explicit on the CLI; they are not INI keys. INI keys such as `[run] initialize` and `[repair] apply_repairs` are policy for a command that acts (`events --submit`); previews never initialize or apply repairs.
- Every command has its own selection. Generation rows do not limit survey, manifests, cache/reap, or analysis. A display filter does not narrow a manifest.

## `[files]`: generation source inputs

| Key | Meaning |
|---|---|
| `params` | Base `key = value` parameter template (`config/initialization/base_parameters.dat`); each design row supplies its sampled parameters and `SYSTEM_CONFIG` the system constants |
| `design` | Whitespace design table with `Parameter ...` header, optionally commented |
| `launcher` | Slurm generation launcher; your profile's `pce-eventgen` |
| `music_input_template` | MUSIC text template, including `{}` placeholders; otherwise the orchestrator uses `<directory>/music_input_template` |

## `[run]`: identity and initialized event bound

| Key | Type / meaning |
|---|---|
| `directory` | Raw campaign root, above `<prefix>_NNN/<system>/` |
| `system` | One of `AuAu200`, `PbPb2760`, `PbPb5020`, `OO5360`; selects source-defined system constants |
| `prefix` | Design directory prefix; script default `design` |
| `row_start`, `row_end` | Zero-based inclusive design table row range; script default start 0 and end last row |
| `nevents` | Fixed positive hydro event bound; required; determines event names and expected slots |
| `jobs_per_design` | Number of generation batch jobs submitted per selected design; script default 1 |
| `initialize` | Boolean: `events --submit` initializes new bundles before repair/submission; script default false |
| `refuse_existing` | Boolean: `events --submit` stops before acting if a selected design directory exists; script default false; generated profiles set true |

Generated profiles select row 0 and initialize new bundles. Resume inspected existing directories with `--allow-existing` (or `refuse_existing=false`); initialized parameters and `initial.hdf` remain unchanged.

## `[submit]`: generation modes, resources, and logs

| Key | Meaning |
|---|---|
| `mode` | `hydro`, `afterburner`, `both`, `auto`; script default `hydro`, generated INI `auto` |
| `restore_archives` | Boolean opt-in; valid with `afterburner` or orchestrator `auto` |
| `max_afterburner_retries` | Persistent per-event attempt cap; script/runner default 3 |
| `time`, `qos` | Slurm walltime `HH:MM:SS` and queue policy; omitted values use launcher's defaults |
| `nodes` | Slurm node request |
| `ntasks_per_node` | Independent runner processes per node |
| `cpus_per_task` | CPUs assigned to each runner process; affects threaded MUSIC/iS3D work |
| `slurm_log_root` | Batch stdout/stderr destination passed to `sbatch` |
| `event_log_root` | Runner log root exported to launcher as `LOG_ROOT`; rank-specific logs below it |

Generation's account is set in the launcher's `#SBATCH --account` (the profile helper writes it). There is **no** `[submit] account`. Cache jobs have their own `[cache] account`. `jobs_per_design`, tasks per node, CPUs per task, and `nevents` are different controls; none is the iS3D oversample count.

## `[repair]`: between-pass recovery and surface archives

`repair` alone is a dry-run audit. `repair --apply` applies repairs, and `events --submit` applies them when `apply_repairs = true`. Applied repairs require no queued/running workers or transfers for the selected designs: they clear locks regardless of age.

| Key | Meaning |
|---|---|
| `apply_repairs` | Boolean; whether `events --submit` applies repairs (true) or only audits (false) |
| `repair_script` | Repair backend; defaults to this checkout's `models/repair_stale_events.py` |
| `repair_log_root` | Repair logs; if omitted, logs live in each run directory |
| `finalize_archive_failed` | Boolean; with applied repairs and archive root, finish completed-but-unarchived events without rerunning transport |
| `archive_root` | Surface archive root; shared with submitted event jobs |
| `archive_base` | Original source base stripped from event paths to locate archives; shared with repair, event and fill jobs. CLI `--archive-base` overrides this value; if omitted, `HIC_SURFACE_ARCHIVE_BASE` applies. Keep fixed across handoffs; see [archive layout and migration](files.md#surface-archive) |
| `zstd_bin`, `zstd_level` | Optional compressor executable and integer compression level for repair finalization |
| `archive_full_verify` | Boolean; request decompression comparison during finalization |
| `archive_remove_local` | Boolean; remove local surface after verified finalization; requires applied repair |
| `afterburner_done_template` | Optional known-good completion marker template for finalization |

Inspect audit buckets and original logs before applying repairs; adding a marker does not resolve ambiguous output. The [runbook](../nersc-campaign.md#1-generate-or-resume-events) gives the audit/apply sequence.

## `[pipeline]`: data products and provenance paths

| Key | Meaning |
|---|---|
| `record_dir` | Per-design cache/reap verdict records and dispatch logs |
| `survey_json` | Full raw/history survey snapshot written by survey/status |
| `cache_manifest` | Candidate `design system` rows for cache stage |
| `reap_manifest` | Clean-only deletion candidates written by review; use a reap-named filename |
| `cache_local_root` | Cache build/reuse staging root; despite the name, can be persistent CFS staging |
| `cache_cfs_root` | Published cache root; per-design paths append `<design>/<system>` |
| `expected_fingerprint` | Frozen JSON for the intended observable scope and conventions |
| `pdg_mass`, `baryon_dict` | Particle pole-mass and baryon-number tables for provenance and analysis |
| `tmp_dir` | Scratch directory for decompressing cache shards during verification; keep it on scratch, not a node's `/tmp` |
| `slurm_log_root` | Cache batch log root; falls back to generation log setting where mapped |
| `analysis_out_dir` | Per-design statistics, matrix, and default feeddown output directory |

Use different campaign roots and unique campaign basenames for separate runs. Cache staging, published cache, raw scratch, archives, and analysis artifacts are distinct storage products. Reaping raw does not remove staging or archives.

## `[cache]`: cache policy and resources

| Key | Type / meaning |
|---|---|
| `nprocs` | Worker processes per design during cache construction |
| `validate_events` | Number of particle events used by raw-reference cache validation |
| `events_per_shard` | Hydro events packed per cache shard |
| `allow_malformed_rows` | Maximum malformed particle rows allowed to be skipped; generated INIs use 1000 |
| `max_dropped_events` | Maximum unsalvageable hydro events allowed to be excluded; generated INIs use 50 |
| `designs_per_task` | Designs packed into each batch array task |
| `throttle` | Maximum simultaneous cache array tasks |
| `cores` | Slurm CPU request for each cache array task; also limits design packing |
| `nodes_per_task` | Cache task node request; supported local packing uses one node |
| `account`, `constraint`, `qos`, `time` | Cache Slurm allocation, CPU constraint, queue policy, walltime |
| `jobs` | Simultaneous designs for local cache/reap execution inside an allocation |

These template values need sizing for your allocation. Interactive cache work requires approximately `jobs × nprocs` workers plus overhead on one node; batch packing is explained in [cache execution](../nersc-campaign.md#3-cache-and-publish).

Malformed rows drop particles; bad-event exclusions drop hydro events. Neither accepts unfinished generation. Use the [separate reviewed shortfall procedure](../nersc-campaign.md#configuration-and-selection) for deliberate short designs. Bare builder defaults are stricter than generated profiles.

## `[analysis_campaign]`: published-cache analysis

| Key | Meaning / generated INI value |
|---|---|
| `analysis_profile` | Observable definitions: `baseline` (default) or `oo_cross_full_pt027_flat/v3`; see [OO profile setup](../analysis.md#oo5360-observable-profile) |
| `jobs` | Concurrent extraction/feeddown designs; 8 |
| `estimator` | Cached centrality field; `cent_ch_eta10` (charged multiplicity within \|η\| < 1) |
| `bin_edges` | Centrality percentiles; `0 5 10 20 30 40 50 60 70 80` |
| `y_window` | Identified-yield rapidity half-width; 0.5 |
| `charged_window` | Charged scalar acceptance, `eta05` or `eta10`; `eta05` |
| `n_boot` | 0 for analytic jackknife/SEM uncertainties; positive count for hydro-event bootstrap |
| `et_eta_window` | ET extraction pseudorapidity half-width; 0.5 |
| `harmonics` | Harmonics required by matrix consistency validation; `2 3` |

Baseline aggregation without configured bins uses 20 five-percent bins across 0–100%; OO v3 defaults to 1% bins through 5%, then 5% through 80%. Explicit INI/CLI bins override these defaults; generated INIs use nine bins ending at 80%. Harmonics here select validation requirements, not new harmonics to calculate. Cache acceptance and Q-vector content come from the fingerprint and builder. See [analysis](../analysis.md) for what can be changed without rebuilding raw data.

## `[feeddown]`: separate inventory yields

| Key | Meaning |
|---|---|
| `pids` | Integer PDG codes; generated INIs use `3212 3122 2212 3222` |
| `combine_antiparticles` | Boolean, sum particles and antiparticles; example true |
| `out` | Optional output path; default through campaign config is `<analysis_out_dir>/feeddown_yields.npz` |

Shares estimator, bins, and concurrency with `[analysis_campaign]`. This is an inventory multiplicity product, not an automatic correction to the matrix.

## Changing a campaign

Changing systems requires coordinating the system key, design normalization column, grid and cross section, any nuclear table, all storage roots, expected fingerprint, centrality/acceptance choices, and runtime data. `--system OO5360` alone is insufficient. Existing bundles retain their initialized physics. Keep the exact design table and bundle provenance for a resumed campaign.
