# Initialization inputs

Inputs from which every design's bundle (`parameters.dat`, `music_input`, `initial.hdf`) is created. See the [physics reference](../../docs/reference/physics.md) for how each parameter enters the models.

To create a new Latin-hypercube table, prioritize a first batch, or inspect its coverage, follow the [design-generation guide](../../inference/DESIGN.md). Generation and inference share their parameter order and prior bounds in [`inference/design_parameters.py`](../../inference/design_parameters.py); use the exact same final table for both stages.

| File | Purpose |
|---|---|
| [design_params_combined500.dat](design_params_combined500.dat) | Compatibility link to the canonical [inference table](../../inference/model/design_params_combined500.dat): 500 rows of 17 named parameter columns. Row *n* is `design_NNN`; generation and inference read the same file |
| [base_parameters.dat](base_parameters.dat) | Base template for campaign rendering (`[files] params`): the fixed settings that no design row or system supplies |
| [music_input_template](music_input_template) | Fixed MUSIC settings with `{}` placeholders filled from each design's parameters (`[files] music_input_template`) |
| [parameters.dat](parameters.dat) | Standalone example for the [first-run guide](../../docs/getting-started.md): design row 0 rendered for AuAu200 exactly as `run_designs.py` renders a campaign design |

`run_designs.py` renders each design's `parameters.dat` from the base template, the design-table row, and the system constants in `SYSTEM_CONFIG` ([run_designs.py](../../models/run_designs.py)): species, normalization column, cross section (4.23 fm² for AuAu200) and transverse grid. Initialization then copies `parameters.dat` and the template into the design directory, renders `music_input`, and runs TRENTo to write `initial.hdf`. An existing bundle is never re-rendered when these templates change.

`nersc/configure_user.py` points new profiles at these files. Keep each model matrix paired with the design table that generated it; inference joins on the zero-based `design_index`.
