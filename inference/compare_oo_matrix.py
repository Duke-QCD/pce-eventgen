#!/usr/bin/env python3
"""
Arrival check for a regenerated O-O matrix (Stage A profile inference.OO_PROFILE) against the
previous "baseline" matrix built from the SAME caches.
Run before any inference on the new matrix.

HARD checks (exit 1 on any failure):
  - meta: system, analysis_profile, estimator/windows/n_boot as in the handoff; cache fingerprint
    hash and centrality edges equal to the old matrix; identical feature-label list.
  - every old design is present in the new matrix (new-only designs are fine).
  - shared designs (joined on design_index, never row order): every column OUTSIDE the profile's
    redefined charged columns is identical in Y and Y_err (NaN-equal; --rtol, default 0 = exact).
    This also catches design misalignment and wrong caches.
  - charged mean_pT: new/old > 1 everywhere (0.27-5 GeV vs pT>0 must raise the mean).
  - charged v{2,3}_2 == sqrt(c{2,3}_gap) where c>0, NaN otherwise.
INFORMATIONAL (per centrality bin):
  - <pT>: new/old ratio vs the ATLAS fiducial/pT0 ratio; percentile of the ATLAS fiducial value among
    the new designs (data outside the design range = the emulator would extrapolate).
  - c2/c3: pulls (c_new - c_old)/sqrt(err_new^2 + err_old^2) -- the same hydro events feed both, so
    the true pull spread is somewhat below 1; the MEAN is the cross_full-vs-gap offset test, also
    given as mean(c_new - c_old) in % of the measured c (dc/c_dat).
    Error gain err_old/err_new, noise/spread median(Y_err)/sd_designs(Y) new (old), negative-c
    count, percentile of the ALICE c = v^2 among the new designs. Bins with c noise/spread above
    --noise-flag are listed as candidates for a resolution cut (cent_max).
  - k=1 same-sample flow fallback totals from the meta.

    python compare_oo_matrix.py --new PATH --old PATH
"""

import argparse, sys
import numpy as np
import inference as I

CHANGED = ("mean_pT", "c2_gap", "c3_gap", "v2_2", "v3_2")   # charged columns the profile redefines
EXPECT = {"system": "OO5360", "estimator": "cent_ch_eta10", "charged_window": "eta05",
          "y_window": 0.5, "n_boot": 0}
SAME_AS_OLD = ("cache_fingerprint_hash", "centrality_edges_pct")
DATA = {"mean_pT": str(I.ROOT / "data/OO5360/centrality/mean_pT_charged_ATLAS.dat"),
        "mean_pT_pT0": str(I.ROOT / "data/OO5360/derived/mean_pT_charged_ATLAS_pT0.dat"),
        "c2_gap": str(I.ROOT / "data/OO5360/centrality/v2_2_charged_ALICE.dat"),
        "c3_gap": str(I.ROOT / "data/OO5360/centrality/v3_2_charged_ALICE.dat")}


def load(path, profile):
    meta = I.check_system_meta(path, system="OO5360", profile=profile)
    z = I.read_npz(path)
    return (meta, np.asarray(z["Y"], float), np.asarray(z["Y_err"], float),
            [str(x) for x in z["feature_labels"]], np.asarray(z["design_index"], int))


def parse(label):
    sp, ce, ob = label.split("__")
    lo, hi = (float(x) for x in ce.split("_"))
    return sp, lo, hi, ob


def same(a, b, rtol):
    """Elementwise NaN-equal, exact when rtol == 0."""
    nan = np.isnan(a) & np.isnan(b)
    eq = (a == b) if rtol == 0 else np.isclose(a, b, rtol=rtol, atol=0.0)
    return nan | eq


def data_by_bin(path, square=False):
    lo, hi, y, _ = I.read_cent_file(path)
    return {(l, h): (v * v if square else v) for l, h, v in zip(lo, hi, y)}


def pct(x, sample):
    return 100.0 * np.mean(sample < x)


def main(new_path, old_path, rtol, noise_flag):
    fails = []
    def hard(ok, msg):
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
        if not ok:
            fails.append(msg)

    print(f"new: {new_path}\nold: {old_path}")
    mN, YN, EN, LN, dN = load(new_path, I.OO_PROFILE)
    mO, YO, EO, LO, dO = load(old_path, "baseline")

    print("== meta ==")
    hard(True, f"profiles: new {I.OO_PROFILE!r}, old 'baseline' (checked at load)")
    for k, v in EXPECT.items():
        hard(mN.get(k) == v, f"new {k} = {mN.get(k)!r} (want {v!r})")
    for k in SAME_AS_OLD:
        hard(mN.get(k) == mO.get(k), f"new {k} equals old")
    hard(LN == LO, f"feature labels identical ({len(LN)} columns)")
    nfail = int(mN.get("n_failed", -1))
    hard(nfail == 0, f"n_failed = {nfail}" + (f": {[f[0] for f in mN.get('failed', [])][:10]}" if nfail else ""))
    if LN != LO:
        print("\nlabel lists differ -- column comparison impossible"); return 1

    for tag, d in (("new", dN), ("old", dO)):
        hard(len(np.unique(d)) == len(d), f"{tag} design_index unique")
    shared = np.intersect1d(dN, dO)
    posN, posO = {int(d): i for i, d in enumerate(dN)}, {int(d): i for i, d in enumerate(dO)}
    rN, rO = np.array([posN[int(d)] for d in shared]), np.array([posO[int(d)] for d in shared])
    only_old = np.setdiff1d(dO, dN)
    print(f"== designs: new {len(dN)}, old {len(dO)}, shared {len(shared)}; "
          f"new-only {len(np.setdiff1d(dN, dO))}; old-only {len(only_old)}"
          + (f" {only_old[:10].tolist()}" if len(only_old) else ""))
    hard(len(shared) > 0, "at least one shared design")
    hard(len(only_old) == 0, "every old design is present in the new matrix (a lost design = an upstream "
                             "extraction/aggregation failure)" + (f"; missing {only_old[:10].tolist()}"
                                                                   if len(only_old) else ""))
    if not len(shared):
        return 1

    parsed = [parse(l) for l in LN]
    changed = np.array([sp == "charged" and ob in CHANGED for sp, _, _, ob in parsed])
    bins = sorted({(lo, hi) for _, lo, hi, _ in parsed})
    per_bin = {b: int(sum(changed[j] for j, p in enumerate(parsed) if (p[1], p[2]) == b)) for b in bins}
    hard(set(per_bin.values()) == {len(CHANGED)},
         f"{len(CHANGED)} redefined charged columns in each of {len(bins)} bins ({len(LN) - changed.sum()} unchanged)")

    print("== unchanged columns on shared designs ==")
    u = ~changed
    okY = same(YN[rN][:, u], YO[rO][:, u], rtol)
    okE = same(EN[rN][:, u], EO[rO][:, u], rtol)
    badcol = ~(okY.all(0) & okE.all(0))
    if badcol.any():
        ulab = np.array(LN)[u]
        obs = sorted({parse(l)[0] + ":" + parse(l)[3] for l in ulab[badcol]})
        bad_designs = shared[~(okY.all(1) & okE.all(1))]
        a, b = YN[rN][:, u], YO[rO][:, u]
        nz = np.isfinite(a) & np.isfinite(b) & (b != 0)
        rel = np.max(np.abs(a[nz] / b[nz] - 1.0)) if nz.any() else np.nan   # Y only, nonzero old
        print(f"    {badcol.sum()} columns differ, in {len(bad_designs)} designs "
              f"(first {bad_designs[:8].tolist()}); max |rel diff| {rel:.3g}; obs: {obs[:12]}")
    hard(not badcol.any(), f"{u.sum()} unchanged columns identical in Y and Y_err "
                           f"({'exact' if rtol == 0 else f'rtol={rtol:g}'})")

    def cols(ob):
        """{(lo,hi): column index} for a charged observable."""
        return {(lo, hi): j for j, (sp, lo, hi, o) in enumerate(parsed) if sp == "charged" and o == ob}

    print("== charged v_n{2} vs c_n consistency (new) ==")
    for n in (2, 3):
        cc, vc = cols(f"c{n}_gap"), cols(f"v{n}_2")
        c = YN[:, [cc[b] for b in bins]]; v = YN[:, [vc[b] for b in bins]]
        want = np.where(c > 0, np.sqrt(np.where(c > 0, c, 0.0)), np.nan)
        ok = (np.isnan(v) & np.isnan(want)) | np.isclose(v, want, rtol=1e-12, atol=0.0)
        hard(ok.all(), f"v{n}_2 == sqrt(c{n}_gap) where c>0, NaN otherwise")

    print("== charged <pT> ==")
    mc = cols("mean_pT")
    dfid, dpt0 = data_by_bin(DATA["mean_pT"]), data_by_bin(DATA["mean_pT_pT0"])
    hard(all((YN[rN, mc[b]] > YO[rO, mc[b]]).all() for b in bins), "new/old > 1 in every design and bin")
    print("    bin       new/old [min med max]   ATLAS fid/pT0   ATLAS fid pct-of-designs")
    for b in bins:
        r = YN[rN, mc[b]] / YO[rO, mc[b]]
        atl = dfid[b] / dpt0[b] if b in dfid and b in dpt0 else np.nan
        p = pct(dfid[b], YN[:, mc[b]]) if b in dfid else np.nan
        print(f"    {b[0]:g}-{b[1]:g}%".ljust(12) + f"{r.min():.3f} {np.median(r):.3f} {r.max():.3f}"
              f"        {atl:.3f}          {p:5.1f}")

    print("== charged c_n (new cross_full vs old same-gap; pulls on shared designs) ==")
    flags = []
    for n in (2, 3):
        ob = f"c{n}_gap"; cc = cols(ob); dat = data_by_bin(DATA[ob], square=True)
        print(f"  c{n}:  bin      pull mean+-sem  pull sd  dc/c_dat%  err gain  noise/spread new (old)  n<0  data pct")
        for b in bins:
            if b not in dat:
                continue
            j = cc[b]
            cN, eN, cO, eO = YN[rN, j], EN[rN, j], YO[rO, j], EO[rO, j]
            pull = (cN - cO) / np.sqrt(eN**2 + eO**2)
            pull = pull[np.isfinite(pull)]
            dcc = 100.0 * np.mean(cN - cO) / dat[b]          # offset as % of the measured c
            gain = np.median(eO / eN)
            nsN = np.median(EN[rN, j]) / np.std(YN[rN, j]); nsO = np.median(EO[rO, j]) / np.std(YO[rO, j])
            nneg = int((YN[:, j] < 0).sum())
            p = pct(dat[b], YN[:, j])
            sem = pull.std() / np.sqrt(len(pull))
            mark = " <" if nsN > noise_flag else ""
            if nsN > noise_flag:
                flags.append(f"c{n} {b[0]:g}-{b[1]:g}% noise/spread {nsN:.2f}")
            if abs(pull.mean()) > 3 * sem:
                flags.append(f"c{n} {b[0]:g}-{b[1]:g}% mean pull {pull.mean():+.2f} (>3 sem)")
            print(f"       {b[0]:g}-{b[1]:g}%".ljust(14) + f"{pull.mean():+6.2f}+-{sem:.2f}   {pull.std():5.2f}  "
                  f"{dcc:+7.1f}    {gain:6.2f}     {nsN:5.2f} ({nsO:4.2f}){mark:2s}       {nneg:3d}   {p:5.1f}")

    usage = mN.get("charged_flow_sample_usage")
    if usage is None:
        hard(False, "meta has charged_flow_sample_usage")
    else:
        nev = sum(int(d["same_full_fallback_events_total"]) for d in usage)
        fr = [b["same_full_pair_weight_fraction"] for d in usage for b in d["bins"]
              if b["same_full_pair_weight_fraction"] is not None]
        print(f"== k=1 same-sample fallback: {nev} events over {len(usage)} designs; "
              f"max bin pair-weight share {max(fr, default=0.0):.3g} (inference gate: FLOW_FALLBACK_MAX="
              f"{I.FLOW_FALLBACK_MAX:g})")

    print("\n== summary ==")
    print(f"  hard checks: {'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
    for f in fails:
        print(f"    FAIL: {f}")
    print(f"  informational flags ({len(flags)}): noise/spread > {noise_flag:g} -> resolution-cut candidate; "
          f"|mean pull| > 3 sem -> systematic new-vs-old offset")
    for f in flags:
        print(f"    {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Arrival check: regenerated O-O matrix vs the baseline one")
    ap.add_argument("--new", required=True, help="Regenerated O-O matrix NPZ.")
    ap.add_argument("--old", required=True, help="Baseline O-O matrix NPZ to compare against.")
    ap.add_argument("--rtol", type=float, default=0.0,
                    help="relative tolerance for the unchanged-column identity (default 0 = exact)")
    ap.add_argument("--noise-flag", type=float, default=0.5,
                    help="flag c_n bins whose median Y_err / design sd exceeds this (default %(default)s)")
    a = ap.parse_args()
    sys.exit(main(a.new, a.old, a.rtol, a.noise_flag))
