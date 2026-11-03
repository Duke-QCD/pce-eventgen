#!/usr/bin/env python3
"""Generate a Latin-hypercube parameter table for event generation and inference."""

import argparse

from design_tools import (generate_design, min_pairwise_distance, nonnegative_integer,
                          normalize_design, positive_integer, write_designs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="New design table path; existing tables/sidecars are refused.")
    parser.add_argument("--ndesign", type=positive_integer, default=500, help="Number of design rows.")
    parser.add_argument("--seed", type=nonnegative_integer, default=12345, help="Random seed.")
    parser.add_argument("--candidates", type=positive_integer, default=20,
                        help="Independent LHS candidates; keep the one with greatest minimum separation.")
    args = parser.parse_args(argv)
    try:
        values = generate_design(args.ndesign, args.seed, args.candidates)
        score = min_pairwise_distance(normalize_design(values))
        write_designs([(args.out, values, {"operation": "latin-hypercube", "seed": args.seed,
                       "candidates": args.candidates, "sampler": "scipy.stats.qmc.LatinHypercube",
                       "minimum_distance": score})])
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot create design: {error}\n")
    print(f"Wrote {args.ndesign} design rows and metadata: {args.out}")
    print("Use this same table for campaign generation and inference; row 0 is design_000.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
