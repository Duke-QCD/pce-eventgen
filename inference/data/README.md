# Measurement inputs

These tables supply the experimental inputs selected by the inference pipeline:

- `AuAu200/STAR/` and `AuAu200/PHENIX/`: tabulated Au-Au measurements, including
  the PHENIX proton tables referenced by the Au-Au observable specifications.
- `OO5360/centrality/`: O-O multiplicity, charged flow and charged mean-pT tables.
- `OO5360/derived/`: provisional identified-hadron tables versus multiplicity
  and an alternative extrapolated charged mean-pT table for comparison tools.
- `OO5360/uncertainties/`: source metadata and separate uncertainty components
  for the tabulated ALICE flow and ATLAS mean-pT measurements.

The [O-O data notes](OO5360/README.md) describe sources, acceptance and uncertainty
assumptions. The identified-hadron errors are assumed test uncertainties, not
published experimental errors. These inputs do not include full experimental
covariance matrices.

The active `DATA_SPEC` in [inference.py](../inference.py) determines which files
enter a fit. Do not load every table indiscriminately or add overlapping
measurements as independent constraints. Use `--data-dir` for a deliberately
selected replacement dataset while preserving the expected relative directory
layout.
