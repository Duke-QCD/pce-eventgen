# Standalone maintenance tools

See the [maintenance flag index](../../docs/reference/cli-maintenance.md) for
arguments and source locations, and [troubleshooting](../../docs/troubleshooting.md)
for the normal between-pass recovery path.

These are explicit, task-specific utilities. The standard campaign uses
`models/campaign.py repair` for its normal repair pass.

| Script | Purpose |
|---|---|
| `marker_census.py` | Inspect completion markers and report recovery guidance |
| `regen_stuck.py` | Quarantine selected stuck events so they can be regenerated from their initial conditions |
| `delete_archived_surfaces.py` | Gated deletion of surface archives using verified cache evidence and a deletion manifest |

All three default their raw root and archive root to the profile's
`HIC_RAW_ROOT` and `HIC_SURFACE_ARCHIVE_ROOT`, and locate archives relative to
`HIC_SURFACE_ARCHIVE_BASE` with the shared layout in `models/surface_archive.py`.
`--archive-base` overrides that source base for a scan. Source the campaign's
profile before maintenance, including after a handoff; see the
[archive layout and migration instructions](../../docs/reference/files.md#surface-archive).

Use each script's `--help` and introductory comments for its inputs and preview/
execution controls. Surface-archive deletion is independent of `campaign.py reap`,
which removes verified raw particle lists.
