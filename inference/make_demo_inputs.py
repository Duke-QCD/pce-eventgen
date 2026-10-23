#!/usr/bin/env python3
"""Create a small synthetic AuAu200 model matrix for the inference tutorial.

The generated values are arbitrary smooth functions, not simulator predictions.
The full-design format and observable labels are real pipeline interfaces. The
tutorial compares this toy matrix with the bundled experimental measurements
only to exercise the software; no inferred parameter has a physics interpretation.
"""
import argparse
import json
from pathlib import Path

import numpy as np

import inference as I


def make_inputs(out_dir, ndesign=16):
    """Write a self-contained design/matrix pair into a new directory."""
    I.use_system("AuAu200")
    full_design, _, _, _ = I.load_design(I.DESIGN_DAT)
    if not 8 <= ndesign <= len(full_design):
        raise ValueError(f"--ndesign must be between 8 and {len(full_design)}.")
    output = Path(out_dir).expanduser().resolve()
    # Refuse existing destinations, including empty directories, to prevent replacing inputs.
    output.mkdir(parents=True, exist_ok=False)
    design = full_design[:ndesign]
    names = [parameter[0] for parameter in I.PARAMS_ALL]
    lower = np.array([parameter[1] for parameter in I.PARAMS_ALL])
    upper = np.array([parameter[2] for parameter in I.PARAMS_ALL])
    unit = (design - lower) / (upper - lower)
    # Low-rank, deterministic toy responses keep PCA/GP training small.
    yield_factor = 0.8 + 0.2 * unit[:, names.index("norm200")] + 0.1 * unit[:, names.index("gamma_s_0")]
    pt_factor = 0.9 + 0.1 * unit[:, names.index("w")]
    flow_factor = 0.8 + 0.2 * unit[:, names.index("eta_min")]
    pairs = sorted({(spec["species"], spec["obs"]) for spec in I.DATA_SPEC_AUAU})
    columns, labels = [], []
    for species, observable in pairs:
        for low in range(80):
            center = low + 0.5
            if observable.startswith("c"):
                value = (0.002 + center * 0.00008) * flow_factor
            elif observable == "mean_pT":
                value = (0.5 + center * 0.001) * pt_factor
            else:
                value = 100 * np.exp(-center / 45) * yield_factor
            columns.append(value)
            labels.append(f"{species}__{low}_{low+1}__{observable}")
    values = np.column_stack(columns)
    np.savetxt(output / "design.dat", design, fmt="%.12g", header="Parameter " + " ".join(names))
    matrix = output / "model_matrix_demo.npz"
    np.savez_compressed(matrix, Y=values, Y_err=0.01 * values, feature_labels=np.array(labels),
                        design_index=np.arange(ndesign),
                        design_name=np.array([f"design_{row:03d}" for row in range(ndesign)]))
    matrix.with_suffix(".meta.json").write_text(json.dumps({
        "system": "AuAu200", "analysis_profile": "baseline", "synthetic": True,
        "purpose": "Software tutorial only; arbitrary toy values, not physics predictions.",
        "generator": I.file_identity(__file__), "ndesign": ndesign,
    }, indent=2) + "\n")
    print(f"Created SYNTHETIC tutorial inputs: {output}")
    print(f"  {ndesign} designs, {values.shape[1]} columns; use --design {output / 'design.dat'}")
    print("  The default AuAu200 selections (primary exclusions, no feed-down) apply.")
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", required=True, help="New directory for generated inputs (must not exist).")
    parser.add_argument("--ndesign", type=int, default=16, help="Number of design rows (8 to 500; default: 16).")
    args = parser.parse_args(argv)
    try:
        make_inputs(args.out_dir, args.ndesign)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot create tutorial inputs: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
