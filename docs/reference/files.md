# Files, layouts, and event states

[Handbook](../README.md) · [Workflow](../workflow.md) · [Troubleshooting](../troubleshooting.md)

## Raw design layout

```text
<raw-root>/                       # campaign root
  design_000/
    AuAu200/                      # run/system directory passed to runner
      parameters.dat
      music_input_template
      music_input
      initial.hdf
      data/
      campaign_state.json         # written by repair
      ready_to_cache.json         # readiness evidence when appropriate
      cache_bad_events.json       # full-parser rejection feedback, when written
      0000/                       # example for a 10,000-event bound
        hydro.done
        afterburner.done
        particle_lists.oscar     # OR particle_lists.oscar.unfinished
      smash_output/              # per-event transport working directories
      reaped.done                # after verified raw reaping
```

Not every file exists at every stage. A two-event run uses event directories
`0/` and `1/`, not four-digit names. Generated orchestration/repair reports and
logs can also be present. `initial.hdf` and rendered inputs are expensive
reproducibility inputs: do not treat them as disposable per-event output.

## Event-level files and markers

| Name | Meaning and how to interpret it |
|---|---|
| `surface.dat` | MUSIC particlization text, consumed by iS3D; nonempty with `hydro.done` means hydro succeeded |
| `hydro.done` | Hydro produced a usable surface; does not establish afterburner completion |
| `hydro.no_surface` | Valid zero-output hydro event, after successful hydro with an empty surface; must have no contradictory completion/particle artifacts |
| `results/` | iS3D samples and converted SMASH input; may be removed during successful runner cleanup |
| `average_thermodynamic_quantities.dat` | iS3D surface averages/byproduct; may be cleaned after completion |
| `particle_lists.oscar` | Final transported particle output, with OSCAR headers and event blocks |
| `particle_lists.oscar.unfinished` | Accepted alternative filename **when** nonempty, unambiguous and accompanied by `afterburner.done`; the suffix alone does not decide validity |
| `afterburner.done` | Afterburner terminal marker; combined with exactly one usable particle variant establishes a particle event |
| `afterburner.lock` | Claimed/in-progress event; can survive interruption; status does not query the scheduler |
| `afterburner.failed` | Failure context for an afterburner attempt; inspect contents and runner log |
| `afterburner.retry_count` | Persisted attempts across restarts; reaching retry cap blocks repeated work |
| `archive.failed` | Surface archival failed; completed transport may be recoverable through repair finalization |
| `drained.done` | NERSC destination committed by OSG drain after terminal output checks; needed for OSG ownership/resume |

An absent `surface.dat` after completion can be normal because the runner
archives it. An absent particle list after `afterburner.done` is ambiguous
unless verified reaping history establishes deletion. Two particle-list
variants, zero-byte output, or no-surface plus particle artifacts are conflicts.
Do not resolve these by guessing from a filename or manually adding markers.

The runner's return code describes the invocation; skipped or failed event
slots can still remain. Always inspect terminal counts and blocker reasons.

## Surface archive

Normal mirrored archive paths are:

```text
<archive-root>/<campaign>/<design>/<system>/<event>/surface.dat.zst
```

`[repair] archive_base` records the original source base, exported to event jobs
as `HIC_SURFACE_ARCHIVE_BASE`. The full event path relative to this base is
retained, including parents above the campaign directory. For example, with
`archive_base = /scratch/original-user`, an event at
`/scratch/original-user/projectA/campaign/design_000/AuAu200/0000` archives under
`<archive-root>/projectA/campaign/design_000/AuAu200/0000/`, which keeps
separate projects distinct. A configured base takes precedence over `PSCRATCH`
and `SCRATCH`; events outside it are rejected rather than mapped elsewhere.

Generated profiles save the base in both `calibration.ini` and `nersc_paths.sh`.
Keep those values identical for direct runner/maintenance calls and campaign
commands. To adopt an existing nested archive, use its **original** scratch root
as the base, even when the operator's own scratch root is different. Pass
`--archive-base /scratch/original-user` to profile generation, or add
`archive_base = /scratch/original-user` to `[repair]` and
`export HIC_SURFACE_ARCHIVE_BASE=/scratch/original-user` to the existing paths
file. Source the profile again and update deployed runners/launchers. This
selects existing archives in place; it does not move or rewrite them. If raw
data itself is relocated, preserve its path relative to the selected base.

Without this setting, legacy callers still try `PSCRATCH`, then `SCRATCH`,
then the last four event-path components. That fallback depends on the current
environment and should not be used for a campaign handoff. Campaigns using the
four-component layout must have distinct names within a shared archive root.

Compression uses a temporary path and verifies before
removing the local surface. When an archive already exists, the runner compares
its decompressed bytes with any local surface and preserves both on a mismatch.
`--restore-archives` explicitly restores missing surfaces for afterburner-only
work. Archive finalization is a separate repair action for completed transport
whose archive step failed. The [maintenance archive deletion tool](../../models/maintenance/README.md)
is independent of raw reaping.

## Cache products and records

```text
<cache-local-root>/<design>/<system>/
  metadata.json
  <shards>.npz

<cache-cfs-root>/<design>/<system>/
  metadata.json
  <shards>.npz.zst
  cache.verified

<record-dir>/
  <design>_<system>_stage1.json     # cache/publish/reaper-audit verdict
  <design>_<system>_stage2.json     # reap verdict
  <design>_<system>_cache_dispatch.log
  <design>_<system>_reap_dispatch.log
  _submit_cache.sbatch
```

Shard filenames are listed in `metadata.json`; consumers should use metadata,
not assume an invented filename pattern. The local cache packs selected
per-hydro-event/per-oversample statistics. Publication compresses it, checks
payloads/provenance, and writes `cache.verified` only after successful checks.
Cache schema is `cache_one_design/v3`.

`cache.verified` is a JSON verification record, not permission by itself to
delete raw data. The wrapper adds event-count/policy checks and a reaper audit.
Review combines records and writes a clean-only reap selection; actual reaping
rechecks current payload and raw state independently. Preserve the entire
published cache, fingerprint, metadata and records together.

## Manifests and snapshots

Cache and reap manifests contain whitespace-separated `design system` rows,
for example `design_027 AuAu200`. The cache manifest selects candidates;
`reap_ready.manifest` selects reviewed clean deletion candidates. Do not
substitute one for the other. Empty manifests can mean a valid no-work result.

The survey JSON includes full per-design status, counts, errors, raw readiness,
and historical record context. `terminal_frac` counts terminal expected hydro
slots; valid no-surface events count. With a fixed target, missing directories
count against completeness. Display filters and row limits do not truncate
the saved snapshot or narrow emitted manifests.

Status meanings and next commands are in
[campaign usage](../nersc-campaign.md#2-survey-and-select-cache-candidates).
Survey/history labels are metadata-level observations, not a deep payload check.
Designs not yet created and without any record do not appear in a raw survey;
generation row selection controls their initialization.

## Analysis products

| Product | Contents |
|---|---|
| `<design>_<system>_stageA.npz` | Per-design event statistics extracted from published cache |
| Matching `.meta.json` | Schema, fingerprint, source, and extraction scope |
| `analysis_failures.txt` | Extraction failures for follow-up |
| `model_matrix.npz` | `Y`, `Y_err`, `feature_labels`, `design_index`, `design_name` |
| `model_matrix.meta.json` | Scope, bins, reduction/uncertainty settings, skipped designs and scientific notes |
| `model_matrix_long.csv` | Human-readable long form unless `--no-csv` |
| `feeddown_yields.npz` | Separate per-PID inventory yields; not calibration matrix columns |

Read [analysis](../analysis.md) before consuming these, then use the
[analysis-input reference](analysis-inputs.md) for array/label contracts,
uncertainties and inventory acceptance. Array row positions are not design IDs.
`Y_err` contains diagonal standard errors, not covariance.

## OSG staging and transfer

The AP slot is `<ap-stage-root>/<campaign>/<design>/<system>/<event>/` with
`initial.hdf`, `parameters.dat`, `music_input`, and later `outbox/`. HTCondor
returns only `outbox/`; drain transfers/flattens terminal artifacts into the
NERSC event directory, commits `drained.done`, then removes the AP copy when
enabled. The NERSC and AP campaign names need not match. Read the
[OSG runbook](../osg-campaign.md) for root-argument differences,
identity preservation, ownership, and retry behavior.
