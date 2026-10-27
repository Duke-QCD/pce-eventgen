# Measurement inputs

The public repository bundles AuAu200 reference measurements and an [O-O input schema](OO5360.md), not O-O study measurements or evidence. The synthetic walkthrough uses the AuAu200 reference tables and a generated model matrix. Supply your own measurements through `--data-dir` while preserving the selected adapter's file layout. Keep personal datasets in an external directory or ignored `config/users/<study>/`.

Available reference inputs:

- `AuAu200/STAR/` and `AuAu200/PHENIX/`: tabulated Au-Au measurements, including the PHENIX proton tables referenced by the Au-Au observable specifications.
- `OO5360.md`: the input contract for the O-O adapter. Its measurements are supplied separately; the `OO5360/` directory is an ignored local staging area for existing studies.

Sources, acceptance, uncertainty budgets, source images, derived covariance and dataset-specific audits belong with each supplied study. The presence of a compatible table is not a validation of its uncertainty model or physics assumptions.

The active `DATA_SPEC` in [inference.py](../inference.py) determines which files enter a fit. Do not load every table indiscriminately or add overlapping measurements as independent constraints. Use `--data-dir` for a deliberately selected replacement dataset while preserving the expected relative directory layout.
