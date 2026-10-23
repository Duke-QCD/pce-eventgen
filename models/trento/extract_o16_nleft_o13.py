#!/usr/bin/env python3
"""
extract_o16_nleft_o13.py

Build a TRENTo ManualNucleus configuration table for 16-O from Trajectum's
embedded NLEFT pinhole tables (nucleusstructure.cpp), reproducing Trajectum's
"O13" variant exactly:

  coordinates : Oxygen16NLEFTResolveAmbiguitiesList
                (lattice-periodicity ambiguities resolved)
  weights     : Oxygen16NLEFTWeightsList, leading column only
                (columns 1-4 are dw/dC2, dw/dC3, dw/dsL, dw/dsNL perturbative
                 derivatives for interaction-uncertainty reweighting -- unused)
  w <= 0      : configurations dropped (Trajectum's positive-only condition);
                survivors importance-resampled with replacement, p = w / sum(w),
                yielding an UNWEIGHTED ensemble equivalent to Trajectum's
                uniform-pick + per-event weight w
  de-lattice  : + U[0,1) per axis in lattice units, then * a
                (Trajectum's "smearing by 1 lattice site in width")
  recenter    : per-configuration center of mass subtracted AFTER the jitter
                (matching Trajectum's order of operations)
  rotation    : none stored -- TRENTo's ManualNucleus applies a Haar-uniform
                ZXZ rotation per sampled event (term-for-term identical to
                Trajectum's rotation matrix)

Output: ONE contiguous float32 dataset, shape (n_out, 16, 3), units fm
(ManualNucleus requires exactly one object in the file; uncompressed so
trento's per-event hyperslab reads stay trivial), with provenance stored in
dataset attributes and a JSON sidecar.

Provenance: Trajectum (G. Nijs, W. van der Schee); NLEFT pinhole
configurations per arXiv:2402.05995; a = hbar*c / 150 MeV ~= 1.31551 fm
(OXYGEN16NLEFT_LATTICESPACING, nucleusstructure.h; OXYGEN16NLEFT_LENGTH = 20000).

Usage:
  python3 extract_o16_nleft_o13.py --source /path/to/nucleusstructure.cpp
  # then copy the .hdf5 (+ .meta.json) to NERSC $HOME or CFS (not PSCRATCH) and
  #   trento /path/o16_nleft_o13.hdf5 /path/o16_nleft_o13.hdf5 <nevents> ...
"""

import argparse
import datetime
import json
import re
import sys

import numpy as np

HBARC_MEV_FM = 197.3269804          # CODATA; Trajectum: 1/CONST_MEV_TO_INV_FM
A_NUCLEONS = 16
NUM_RE = re.compile(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?")


def find_initializer(text, name):
    """Return the raw text between '{' and '}' of `name`'s array definition."""
    m = re.search(r"\b" + re.escape(name) + r"\s*\[[^\]]*\]\s*=\s*\{", text)
    if m is None:
        sys.exit(f"ERROR: no array definition found for '{name}'.")
    start = m.end()
    end = text.find("}", start)
    if end < 0:
        sys.exit(f"ERROR: unterminated initializer for '{name}'.")
    return text[start:end], text[m.start():m.end() + 60].replace("\n", " ")


def parse_numbers(body, name, expected, dtype):
    toks = NUM_RE.findall(body)
    if len(toks) != expected:
        sys.exit(
            f"ERROR: '{name}' parsed {len(toks)} numbers, expected {expected}.\n"
            f"Initializer body starts with: {body[:120]!r}\n"
            f"Send this preview back for a parser fix."
        )
    return np.array(toks, dtype=dtype)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--source", required=True,
                    help="path to Trajectum's nucleusstructure.cpp")
    ap.add_argument("--out", default="o16_nleft_o13.hdf5",
                    help="output HDF5 path (extension must be one trento's "
                         "filename_is_hdf5 accepts, e.g. .hdf5)")
    ap.add_argument("--n-out", type=int, default=50000,
                    help="stored realizations M (fresh jitter each; default 50000)")
    ap.add_argument("--seed", type=int, default=20260720)
    ap.add_argument("--length", type=int, default=20000,
                    help="OXYGEN16NLEFT_LENGTH from nucleusstructure.h")
    ap.add_argument("--lattice-spacing", type=float, default=HBARC_MEV_FM / 150.0,
                    help="fm; default hbar*c/150 MeV per nucleusstructure.h")
    ap.add_argument("--coords-name", default="Oxygen16NLEFTResolveAmbiguitiesList")
    ap.add_argument("--weights-name", default="Oxygen16NLEFTWeightsList")
    ap.add_argument("--force", action="store_true",
                    help="overwrite existing outputs (default: refuse)")
    args = ap.parse_args()

    import h5py  # deferred so parse errors don't hide a missing h5py

    meta_path = args.out + ".meta.json"
    if not args.force:
        import os
        for p in (args.out, meta_path):
            if os.path.exists(p):
                sys.exit(f"ERROR: {p} exists; re-run with --force to overwrite.")

    text = open(args.source, "r", errors="replace").read()
    N, A = args.length, A_NUCLEONS
    a_fm = args.lattice_spacing

    body, decl = find_initializer(text, args.coords_name)
    print(f"[coords ] matched: {decl[:100]} ...")
    coords = parse_numbers(body, args.coords_name, 3 * A * N, np.float64)
    coords = coords.reshape(N, A, 3)          # Trajectum layout: [cfg][nucleon][xyz]

    body, decl = find_initializer(text, args.weights_name)
    print(f"[weights] matched: {decl[:100]} ...")
    w5 = parse_numbers(body, args.weights_name, 5 * N, np.float64).reshape(N, 5)
    w = w5[:, 0]                               # leading MC weight; cols 1-4 unused

    cmin, cmax = coords.min(axis=(0, 1)), coords.max(axis=(0, 1))
    pos_mask = w > 0.0                         # Trajectum O13: skip w <= 0
    n_pos = int(pos_mask.sum())
    wpos = w[pos_mask]
    ess = wpos.sum() ** 2 / (wpos ** 2).sum()
    neg_share = -w[w < 0].sum() / np.abs(w).sum() if (w < 0).any() else 0.0

    print(f"[source ] N = {N} configs; lattice coords span "
          f"x/y/z min {cmin.astype(int)} max {cmax.astype(int)} (lattice units)")
    print(f"[weights] w>0: {n_pos}/{N} ({n_pos / N:.1%});  "
          f"negative-weight share of sum|w|: {neg_share:.2%}")
    print(f"[weights] ESS of p propto w: {ess:.0f} effective configs "
          f"(max single-config prob {wpos.max() / wpos.sum():.2e})")

    rng = np.random.default_rng(args.seed)
    M = args.n_out
    idx = rng.choice(np.flatnonzero(pos_mask), size=M, replace=True,
                     p=wpos / wpos.sum())
    pos = coords[idx] + rng.random((M, A, 3))  # jitter: +U[0,1) per axis...
    pos *= a_fm                                # ...then scale to fm
    pos -= pos.mean(axis=1, keepdims=True)     # CM to origin, after jitter

    r = np.linalg.norm(pos, axis=2)
    r_rms, r_max = float(np.sqrt((r ** 2).mean())), float(r.max())
    print(f"[output ] M = {M} realizations ({M / ess:.1f}x ESS, fresh jitter each); "
          f"a = {a_fm:.5f} fm")
    print(f"[output ] r_rms = {r_rms:.4f} fm   r_max = {r_max:.3f} fm "
          f"(r_max feeds ManualNucleus's b-range estimate)")

    attrs = {
        "description": "16O nucleon configurations for TRENTo ManualNucleus "
                       "(species token = path to this file)",
        "variant": "Trajectum O13-equivalent: NLEFT pinhole, periodicity "
                   "ambiguities resolved, w>0 only, importance-resampled "
                   "to an unweighted ensemble",
        "source_file": args.source,
        "coords_array": args.coords_name,
        "weights_array": args.weights_name + " (column 0)",
        "n_source_configs": N,
        "n_positive_weight": n_pos,
        "negative_weight_share_of_abs_sum": neg_share,
        "importance_ess": ess,
        "n_out": M,
        "resample_seed": args.seed,
        "lattice_spacing_fm": a_fm,
        "hbarc_mev_fm": HBARC_MEV_FM,
        "jitter": "uniform [0,1) per axis in lattice units, before CM subtraction",
        "frame": "per-configuration CM at origin; no rotation stored "
                 "(ManualNucleus rotates Haar-uniformly per event)",
        "units": "fm",
        "r_rms_fm": r_rms,
        "r_max_fm": r_max,
        "references": "Trajectum (Nijs & van der Schee); NLEFT configurations "
                      "arXiv:2402.05995",
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "script": "extract_o16_nleft_o13.py",
    }

    with h5py.File(args.out, "w") as f:      # existence already gated above
        d = f.create_dataset("configs", data=pos.astype(np.float32))
        for k, v in attrs.items():
            d.attrs[k] = v
    with open(meta_path, "w") as f:
        json.dump(attrs, f, indent=2)

    print(f"[done   ] wrote {args.out} (float32 ({M}, {A}, 3), single dataset) "
          f"+ {meta_path}")
    print(f"[next   ] copy to $HOME/CFS (not PSCRATCH); smoke test:\n"
          f"          trento {args.out} {args.out} 1000 ...")


if __name__ == "__main__":
    main()
