# Analysis tools

New users should read [From particle output to a calibration matrix](../../docs/analysis.md).
The [analysis flag index](../../docs/reference/cli-analysis.md) lists all
declared options with source links; the [file reference](../../docs/reference/files.md)
explains artifacts and markers.

These scripts analyze event output across the model chain. The main NERSC
commands remain in [the campaign guide](../../docs/nersc-campaign.md) and use the
shared campaign INI.

## Campaign analysis

| Script | Role |
|---|---|
| `run_analysis.py` | Campaign extraction, auditing, aggregation, and feeddown-yield export; called by `campaign.py analysis` |
| `analyze_design.py` | Single-design extraction/reduction; called by `campaign.py analysis-design` |
| `extract_observables.py` | Read published v3 caches and build per-event observable artifacts |
| `reduce_observables.py` | Reduce those artifacts to observables and uncertainties |
| `inspect_inventory.py` | Cache inventory inspection; called by `campaign.py inventory` |
| `check_cumulant_consistency.py` | Matrix validation; called by `campaign.py validate` |
| `check_cache_profiles.py` | Synthetic end-to-end regression check for the AuAu200 and OO5360 cache profiles |

Run the profile regression from the repository root in the analysis environment:

```bash
python models/analysis/check_cache_profiles.py
```

## Diagnostics

| Script | Role |
|---|---|
| `feeddown_census.py` | Rapidity-shape census of observable species and their resonance feeders, read directly from raw particle lists; separate from the campaign's feed-down yield export |

These tools are not part of the calibration matrix or inference.
