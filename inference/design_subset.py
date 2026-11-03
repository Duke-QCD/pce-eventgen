#!/usr/bin/env python3
"""Select a space-filling subset, optionally writing its remainder and a reordered full table."""

import argparse

import numpy as np

from design_tools import (greedy_maximin_subset, nonnegative_integer, positive_integer,
                          read_design, source_identity, write_designs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, help="Source full design table.")
    parser.add_argument("--out", required=True, help="New selected-subset table path.")
    parser.add_argument("--n-select", type=positive_integer, required=True, help="Number of rows to select.")
    parser.add_argument("--n-starts", type=positive_integer, default=20, help="Greedy starts, capped at source row count.")
    parser.add_argument("--seed", type=nonnegative_integer, default=12345, help="Random seed for starting rows.")
    parser.add_argument("--remainder-out", help="Also write unselected rows in their original order.")
    parser.add_argument("--ordered-out", help="Also write the full design: selected rows first, then remainder.")
    args = parser.parse_args(argv)
    try:
        values = read_design(args.design)
        selected = greedy_maximin_subset(values, args.n_select, args.n_starts, args.seed)
        remaining = np.setdiff1d(np.arange(len(values)), selected)
        if args.remainder_out and not len(remaining):
            raise ValueError("Remainder is empty; omit --remainder-out when selecting all rows.")
        provenance = {"source": source_identity(args.design), "seed": args.seed,
                      "n_select": args.n_select, "n_starts": min(args.n_starts, len(values))}
        products = []
        for path, indices, role in (
            (args.out, selected, "maximin-subset"),
            (args.remainder_out, remaining, "subset-remainder"),
            (args.ordered_out, np.concatenate((selected, remaining)), "subset-first-full-design"),
        ):
            if path:
                # output row i = source row source_row_indices[i]; output IDs are
                # always local zero-based row numbers, never inherited source IDs.
                metadata = dict(provenance, operation=role, source_row_indices=indices.tolist())
                products.append((path, values[indices], metadata))
        write_designs(products, protected=(args.design,))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot select design: {error}\n")
    for path, rows, _ in products:
        print(f"Wrote {len(rows)} rows and source-row mapping: {path}")
    print("Choose the campaign table before simulation; reordered rows have new design_NNN IDs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
