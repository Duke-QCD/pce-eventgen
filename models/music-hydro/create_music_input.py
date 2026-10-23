#!/usr/bin/env python3
"""Render the design's MUSIC input, deriving its lattice from the TRENTo grid.

Called by models/initialize.py before TRENTo; this helper does not run models.
The template supplies fixed MUSIC settings and parameters.dat fills placeholders.
"""

import argparse
import math
import re
import sys
from pathlib import Path


# MUSIC transverse-lattice keys derived from the TRENTo grid (grid_max, grid_step).
# The TRENTo grid is the single source of truth: a conflicting entry for one of
# these keys in parameters.dat is ignored (with a warning), never honoured.
GRID_DERIVED_KEYS = (
    "Grid_size_in_x",
    "Grid_size_in_y",
    "X_grid_size_in_fm",
    "Y_grid_size_in_fm",
)


def load_parameters(path: Path):
    """
    Parse key = value lines from parameters.dat into a dict.
    Comments (# ...) and blank lines are ignored.
    Values are parsed as int/float when possible, otherwise left as strings.
    """
    params = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Strip inline comments
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue

        if "=" not in line:
            raise ValueError(f"Bad line in {path}: {raw}")

        key, val = [x.strip() for x in line.split("=", 1)]

        # Try to parse as integer or float
        if re.fullmatch(r"-?\d+", val):
            val = int(val)
        elif re.fullmatch(r"-?\d+(\.\d+)?([eE][-+]?\d+)?", val):
            val = float(val)

        params[key] = val
    return params


def derive_grid_parameters(params: dict) -> dict:
    """
    Derive the MUSIC transverse lattice from the TRENTo grid so the two cannot
    drift. Mirrors TRENTo's own arithmetic and operation order:

        nsteps      = ceil(2 * grid_max / grid_step)
        half_extent = 0.5 * nsteps * grid_step        # TRENTo's ACTUAL half-extent

    TRENTo rounds the requested grid_max UP to a whole number of cells, so the
    grid it writes may be slightly wider than requested; MUSIC must match the
    grid TRENTo actually wrote, not the value in parameters.dat.

    MUSIC convention (verified against the AuAu200 production template at
    grid_max = 15, grid_step = 0.1 -> Grid_size_in_x 300, X_grid_size_in_fm 30.0):
      Grid_size_in_x/y   = nsteps                 (cell COUNT)
      X/Y_grid_size_in_fm = 2 * half_extent       (FULL extent; MUSIC's spatial
                                                   range is [-size/2, +size/2],
                                                   so dx = size / count)

    Mutates nothing; returns the derived values (empty if the grid keys are absent).
    """
    if "grid_max" not in params or "grid_step" not in params:
        return {}

    try:
        grid_max = float(params["grid_max"])
        grid_step = float(params["grid_step"])
    except (TypeError, ValueError):
        raise ValueError(
            f"grid_max / grid_step must be numeric, got "
            f"{params['grid_max']!r} / {params['grid_step']!r}"
        )
    if grid_max <= 0 or grid_step <= 0:
        raise ValueError(
            f"grid_max and grid_step must be positive, got {grid_max} / {grid_step}"
        )

    nsteps = math.ceil(2.0 * grid_max / grid_step)      # TRENTo's order of operations
    half_extent = 0.5 * nsteps * grid_step
    full_extent = 2.0 * half_extent

    if abs(half_extent - grid_max) > 1e-9:
        print(
            f"[create_music_input] NOTE: grid_max {grid_max} is not a whole number of "
            f"{grid_step} fm cells; TRENTo will write a half-extent of {half_extent} fm "
            f"({nsteps} cells). MUSIC is being matched to the ACTUAL grid.",
            file=sys.stderr,
        )

    return {
        "Grid_size_in_x": nsteps,
        "Grid_size_in_y": nsteps,
        "X_grid_size_in_fm": full_extent,
        "Y_grid_size_in_fm": full_extent,
    }


def apply_derived(params: dict, derived: dict) -> dict:
    """
    Merge derived grid values into params. The DERIVED value always wins: these
    four keys are not independent inputs, they are a restatement of the TRENTo
    grid in MUSIC's units, and the only correct value is the one matching the
    grid TRENTo actually wrote. A conflicting entry in parameters.dat is ignored
    with a warning (same rule as run_designs.py stripping grid_step/grid_max
    from the base parameters template).
    """
    out = dict(params)
    for k in GRID_DERIVED_KEYS:
        if k not in derived:
            continue
        if k in out and out[k] != derived[k]:
            print(
                f"[create_music_input] WARNING: parameters.dat sets {k}={out[k]}, "
                f"which disagrees with the value derived from grid_max/grid_step "
                f"({derived[k]}). Using the derived value; delete {k} from "
                f"parameters.dat to silence this.",
                file=sys.stderr,
            )
        out[k] = derived[k]
    return out


def fill_template(template_text: str, params: dict) -> str:
    r"""
    Replace occurrences of 'NAME {}' with 'NAME <value_from_params>'.

    Pattern matched: (\w+)(\s*){}
      - group(1) = parameter name (letters/digits/underscore)
      - group(2) = whitespace between name and {}
    """
    pattern = re.compile(r"(\w+)(\s*)\{\}")

    def repl(match):
        name = match.group(1)
        space = match.group(2)
        if name not in params:
            raise KeyError(
                f"Template has placeholder for '{name}', "
                f"but it is not defined in parameters.dat"
            )
        return f"{name}{space}{params[name]}"

    result = pattern.sub(repl, template_text)

    return result


def main():
    ap = argparse.ArgumentParser(
        description="Generate MUSIC input from music_input_template and parameters.dat"
    )
    ap.add_argument("--params", required=True, help="path to parameters.dat")
    ap.add_argument("--template", required=True, help="path to music_input_template")
    ap.add_argument("--out", default="music_input", help="output file (default: music_input)")
    args = ap.parse_args()

    params_path = Path(args.params)
    template_path = Path(args.template)
    out_path = Path(args.out)

    if not params_path.is_file():
        print(f"[ERROR] parameters file not found: {params_path}", file=sys.stderr)
        sys.exit(1)
    if not template_path.is_file():
        print(f"[ERROR] template file not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    params = load_parameters(params_path)

    try:
        derived = derive_grid_parameters(params)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    if derived:
        print(
            f"[create_music_input] transverse lattice from grid_max="
            f"{params['grid_max']} grid_step={params['grid_step']}: "
            f"{derived['Grid_size_in_x']} x {derived['Grid_size_in_y']} cells, "
            f"{derived['X_grid_size_in_fm']} x {derived['Y_grid_size_in_fm']} fm"
        )
    params = apply_derived(params, derived)

    template_text = template_path.read_text()

    try:
        filled = fill_template(template_text, params)
    except KeyError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    out_path.write_text(filled)
    print(f"[create_music_input] Wrote MUSIC input to {out_path}")


if __name__ == "__main__":
    main()
