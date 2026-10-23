#!/usr/bin/env python3
"""Validate the charged cumulant columns in the model matrix.

Run this on a model_matrix.npz from run_analysis.py, which carries both v_n{2}
and c{n}_gap for charged particles. Three checks:

  (1) CONSISTENCY  -- v_n{2} == sqrt(c{n}_gap) wherever c_n > 0. They derive from the same event
      sums (v = sqrt(c_ref), c = c_ref), so this must hold to ~float precision. A real deviation
      means a wiring/aggregation bug, not a statistics effect.

  (2) NaN PROVENANCE -- every NaN v_n{2} cell must have a NONPOSITIVE c{n}_gap, i.e. the sqrt is
      the SOLE cause of the NaN. A NaN v_n sitting on a positive c_n would mean something else is
      nulling the cell.

  (3) RED FLAG    -- the negative c_n should be SMALL relative to their own statistical sigma
      (c_n/sigma_c near 0, i.e. consistent with zero). A c_n that is many sigma below zero is a
      physics/model/statistics smell, not a square-root artifact; those bins deserve a look (and
      should be cut from the likelihood regardless).

Usage:
  python check_cumulant_consistency.py model_matrix.npz
  python check_cumulant_consistency.py model_matrix.npz --harmonics 2 3 --flag-sigma 3
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args
import numpy as np


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("matrix", help="model_matrix.npz (regenerated with cumulant columns)")
    ap.add_argument("--harmonics", type=int, nargs="+", default=[2, 3, 4])
    ap.add_argument("--rtol", type=float, default=1e-6,
                    help="relative tolerance for v_n == sqrt(c_n) on positive c_n (default 1e-6)")
    ap.add_argument("--flag-sigma", type=float, default=3.0,
                    help="negative c_n with c_n/sigma_c < -flag_sigma is flagged (default 3.0)")
    a = parse_workflow_args(ap, "validate")

    z = np.load(a.matrix, allow_pickle=False)
    Y, E = z["Y"].astype(float), z["Y_err"].astype(float)
    L = [str(x) for x in z["feature_labels"]]
    parts = [s.split("__") for s in L]
    sp = np.array([p[0] for p in parts])
    ce = np.array([p[1] for p in parts])
    ob = np.array([p[2] for p in parts])
    cents = sorted(set(ce[sp == "charged"]), key=lambda c: float(c.split("_")[0]))
    if not cents:
        print("FAIL: matrix has no charged centrality columns")
        return 1

    def col(c, o):
        i = np.where((sp == "charged") & (ce == c) & (ob == o))[0]
        return i[0] if len(i) else None

    ok = True
    for n in a.harmonics:
        vkey, ckey = f"v{n}_2", f"c{n}_gap"
        if col(cents[0], ckey) is None:
            print(f"[n={n}] no '{ckey}' columns -- regenerate the matrix with the patched "
                  f"run_analysis.py first. FAIL.")
            ok = False
            continue

        # (0) populated: the cumulant columns must actually carry values. Catches a present-but-
        # all-NaN column, which checks (1)/(2) below would otherwise PASS vacuously (consistency
        # runs over zero positive cells; provenance finds no positive c). This is the silent-
        # corruption case the aggregate-side guard is meant to prevent.
        ccols = [col(c, ckey) for c in cents]
        cfin = float(np.isfinite(Y[:, ccols]).mean())
        sfin = float(np.isfinite(E[:, ccols]).mean())
        c0 = cfin > 0.0
        print(f"\n=== n={n}  ({vkey} vs {ckey}) ===")
        print(f"(0) populated: {ckey} finite frac = {cfin:.3f} (sigma {sfin:.3f})  "
              f"-> {'OK' if c0 else 'FAIL (all-NaN column: reduce not emitting / aggregation bug)'}")
        ok &= c0

        max_rel, n_pos = 0.0, 0
        n_nan_v, n_nan_badprov = 0, 0
        neg_ratios = []
        per_bin = []
        for c in cents:
            v = Y[:, col(c, vkey)]
            cc = Y[:, col(c, ckey)]
            sc = E[:, col(c, ckey)]

            # (1) consistency on resolved (positive) cumulants
            m = np.isfinite(v) & np.isfinite(cc) & (cc > 0)
            if m.any():
                rel = np.abs(v[m] - np.sqrt(cc[m])) / np.maximum(np.sqrt(cc[m]), 1e-300)
                max_rel = max(max_rel, float(rel.max()))
                n_pos += int(m.sum())

            # (2) NaN provenance: NaN v_n with a POSITIVE c_n is unexplained
            vnan = np.isnan(v)
            n_nan_v += int(vnan.sum())
            n_nan_badprov += int((vnan & np.isfinite(cc) & (cc > 0)).sum())

            # (3) red flag: negative c_n normalized by its own sigma
            negm = np.isfinite(cc) & (cc <= 0) & np.isfinite(sc) & (sc > 0)
            if negm.any():
                neg_ratios.append(cc[negm] / sc[negm])

            per_bin.append((c, int(vnan.sum()), int((np.isfinite(cc) & (cc <= 0)).sum())))

        c1 = max_rel <= a.rtol
        print(f"(1) consistency v_n==sqrt(c_n) on c_n>0: max rel dev = {max_rel:.2e} "
              f"over {n_pos} cells  -> {'OK' if c1 else 'FAIL (wiring/aggregation bug)'}")
        ok &= c1

        c2 = n_nan_badprov == 0
        print(f"(2) NaN provenance: {n_nan_v} NaN {vkey} cells; "
              f"with positive c_n (unexplained) = {n_nan_badprov}  "
              f"-> {'OK (all NaN from sqrt of c_n<=0)' if c2 else 'FAIL (NaN not from the sqrt)'}")
        ok &= c2

        if neg_ratios:
            r = np.concatenate(neg_ratios)
            n_flag = int((r < -a.flag_sigma).sum())
            verdict = ("OK (small, ~consistent with zero)" if n_flag == 0
                       else f"CHECK: {n_flag} cell(s) below -{a.flag_sigma:g}sigma -- not a sqrt artifact")
            print(f"(3) negative c_n: {r.size} cells; c_n/sigma_c "
                  f"median={np.median(r):+.2f} min={r.min():+.2f}  -> {verdict}")
        else:
            print(f"(3) negative c_n: none.")

        print("    per-bin [cent : #NaN_v / #neg_c]:")
        print("    " + "  ".join(f"{c}:{nv}/{nb}" for c, nv, nb in per_bin))

    print(f"\nOVERALL: {'PASS' if ok else 'FAIL -- investigate above'}  "
          f"(check 3 is advisory, not part of PASS/FAIL)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
