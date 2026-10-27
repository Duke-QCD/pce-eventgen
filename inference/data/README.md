# Measurement inputs

These are bundled reference datasets, not mandatory inputs for every analysis. The synthetic walkthrough uses the AuAu200 reference tables; it requires no campaign matrix or O-O study files. Supply replacement measurements through `--data-dir` while preserving the selected adapter's file layout. Keep personal datasets outside these tracked reference directories, for example in an external directory or ignored `config/users/<study>/`.

Available reference inputs:

- `AuAu200/STAR/` and `AuAu200/PHENIX/`: tabulated Au-Au measurements, including the PHENIX proton tables referenced by the Au-Au observable specifications.
- `OO5360/centrality/`: O-O multiplicity, charged flow and charged mean-pT tables.
- `OO5360/derived/`: identified-hadron tables versus multiplicity with extracted errors where resolved and flagged assumed errors elsewhere, plus an alternative extrapolated charged mean-pT table for comparison tools.
- `OO5360/uncertainties/`: source metadata, separate uncertainty components and per-point digitization evidence.
- `OO5360/sources/`: native-resolution ALICE panels retained for reviewing the identified-error extraction.
- `OO5360/optional/`: assumed light-hadron yield proxies from projected ratios and the measured K0S normalization, with a joint assumed covariance; enabled only by `--oo-yield-proxies`.

Each dataset's notes describe its sources, acceptance and uncertainty conventions. The [O-O reference](OO5360/README.md) identifies preliminary/digitized values and remaining assumed uncertainties; it does not supply full experimental covariance matrices. Its data-only checker is `python audit_oo_data.py` from `inference/`; this is optional for other datasets and does not validate a campaign matrix or calibration.

The active `DATA_SPEC` in [inference.py](../inference.py) determines which files enter a fit. Do not load every table indiscriminately or add overlapping measurements as independent constraints. Use `--data-dir` for a deliberately selected replacement dataset while preserving the expected relative directory layout.
