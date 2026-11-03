#!/usr/bin/env python3
"""Validate design bounds and inspect coverage or selected zero-based rows."""

import argparse
import csv
from pathlib import Path

import numpy as np

from design_tools import (LOWER, PARAM_NAMES, UPPER, min_pairwise_distance,
                          normalize_design, read_design)


def parse_rows(text, nrows):
    """Expand comma-separated row numbers and inclusive ranges, preserving order."""
    result = []
    for part in text.split(","):
        parts = part.strip().split("-")
        if len(parts) == 1:
            rows = [int(parts[0])]
        elif len(parts) == 2:
            low, high = map(int, parts)
            if low > high:
                raise ValueError(f"Reversed row range: {part}")
            # Check bounds before expanding a potentially enormous range.
            if low < 0 or high >= nrows:
                raise ValueError(f"Rows must be between 0 and {nrows - 1}: {part}")
            rows = range(low, high + 1)
        else:
            raise ValueError(f"Invalid row selection: {part}")
        for row in rows:
            if row < 0 or row >= nrows:
                raise ValueError(f"Rows must be between 0 and {nrows - 1}: {row}")
            if row not in result:
                result.append(row)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, help="Design table to validate and summarize.")
    parser.add_argument("--rows", help="Zero-based row IDs, e.g. 0,3-6,12; ranges are inclusive.")
    parser.add_argument("--out", help="Optional new CSV for selected rows; requires --rows.")
    parser.add_argument("--show-normalized", action="store_true", help="Show selected values in unit-prior coordinates.")
    args = parser.parse_args(argv)
    if args.out and not args.rows:
        parser.error("--out requires --rows")
    try:
        values = read_design(args.design)
        unit = normalize_design(values)
        rows = parse_rows(args.rows, len(values)) if args.rows else []
        if args.out:
            output = Path(args.out).expanduser()
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["design_index", "design_name", *PARAM_NAMES])
                writer.writerows([row, f"design_{row:03d}", *values[row]] for row in rows)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot inspect design: {error}\n")
    print(f"Valid design: {len(values)} rows, {values.shape[1]} parameters; zero-based design IDs.")
    print(f"{'parameter':14s} {'prior min':>12s} {'prior max':>12s} {'observed min':>12s} {'observed max':>12s}")
    for column, name in enumerate(PARAM_NAMES):
        print(f"{name:14s} {LOWER[column]:12g} {UPPER[column]:12g} "
              f"{values[:, column].min():12g} {values[:, column].max():12g}")
    separation = min_pairwise_distance(unit)
    print(f"Minimum separation (unit prior): {separation if separation is not None else 'n/a (one row)'}")
    print(f"Duplicate rows: {len(values) - len(np.unique(values, axis=0))}")
    if rows:
        print("\nSelected rows" + (" (unit-prior coordinates):" if args.show_normalized else ":"))
        print("design_index " + " ".join(PARAM_NAMES))
        shown = unit if args.show_normalized else values
        for row in rows:
            print(str(row) + " " + " ".join(f"{value:.8g}" for value in shown[row]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
