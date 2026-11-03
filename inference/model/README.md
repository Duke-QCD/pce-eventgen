# Model inputs

Stage campaign products here for the default paths, or pass `inference.py --matrix /absolute/path/file.npz`. The canonical [500-row design table](design_params_combined500.dat) is bundled here, so inference needs no files outside its own folder. The surrounding event-generation repository links its legacy initialization-table path to this file. `design_index` in a matrix selects its rows using zero-based IDs. Use another table only when it is the exact one that generated your matrix; see the [input contract](../INPUTS.md).

Default matrix names are `model_matrix_AuAu200.npz` and `model_matrix_OO5360.npz`. Every NPZ requires its same-stem `.meta.json`. Rename/copy both together. O-O requires `analysis_profile = oo_cross_full_pt027_flat/v3` and the associated flow-sample-usage metadata. An older O-O baseline matrix is incompatible.

Feed-down inventories are only read when a fit passes `--feeddown`. Supply the matching `feeddown_yields[...].npz` and `.meta.json`, covering all selected designs; an inventory covering fewer designs than the matrix is rejected.

Generated matrices, inventories and their metadata are ignored by Git here. Preserve campaign identity, settings and provenance when staging the chosen inputs.
