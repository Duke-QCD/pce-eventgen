#!/usr/bin/env python3
"""
feeddown_census.py -- Rapidity-shape census to decide which observables are
exposed to the iS3D resonance longitudinal-sampling bug.

WHAT THIS DOES (and does NOT do)
--------------------------------
It does NOT produce a literal primary-vs-feed-down decomposition: the final
particle_lists.oscar carries no parentage, and its resonances are only the
survivors at output time, so a branching-ratio accounting from the cache would
undercount short-lived feeders. A true split needs SMASH ancestry output or an
iS3D-primary vs SMASH-final re-run.

Instead it answers the EXCLUSION decision empirically. The bug throws resonances
(finite-width, particlization-time) to a forward |y| shell while stable hadrons
fill midrapidity correctly. So:
  * For each OBSERVABLE species: its own midrapidity rapidity shape. A flat
    plateau (health ~1) means it is primary-dominated at midrapidity and the bug
    does not reach it. A forward shell (health ~0) means it IS a mis-placed
    resonance.
  * For each FEEDER resonance: whether it is shelled. An observable's midrapidity
    feed-down is disrupted ONLY if its feeders are shelled. The exposure section
    cross-references the two: a stable observable with shelled feeders is where
    feed-down has been displaced away from midrapidity.

health = N(|y|<0.5) / max_|y|bin N   (0.5-wide |y| bins, each Delta_y = 1.0, so
bins are equal-acceptance). Plateau -> midrapidity is near the peak (~1); shell ->
midrapidity ~0.

Reads only the final OSCAR. No re-run, no parentage.
"""

import argparse
import glob
import math
import sys

import numpy as np

# (label, [pdg codes]) -- observable species on the analysis list.
OBSERVABLES = [
    ("pi+", [211]), ("pi-", [-211]),
    ("K+", [321]), ("K-", [-321]),
    ("K0S(K0/K0bar)", [311, -311]),     # SMASH propagates 311; cache uses |pdg|==311
    ("p", [2212]), ("pbar", [-2212]),
    ("Lambda", [3122]), ("antiLambda", [-3122]),
    ("Xi-", [3312]), ("antiXi+", [-3312]),
    ("Omega-", [3334]), ("antiOmega+", [-3334]),
]

# (label, [pdg codes]) -- resonance / unstable feeders to check for the shell.
FEEDERS = [
    ("rho0", [113]), ("rho+/-", [213, -213]),
    ("omega", [223]),
    ("K*0", [313, -313]), ("K*+/-", [323, -323]),
    ("phi", [333]),
    ("eta", [221]), ("etaprime", [331]),
    ("Delta", [1114, 2114, 2214, 2224, -1114, -2114, -2214, -2224]),
    ("Sigma0", [3212, -3212]),
    ("Sigma+/-", [3222, 3112, -3222, -3112]),
    ("Sigma*(1385)", [3114, 3214, 3224, -3114, -3214, -3224]),
    ("Xi*(1530)", [3314, 3324, -3314, -3324]),
    ("Lambda(1520)", [3124, -3124]),
]

# Which feeders feed which observable (approximate dominant channels), for the
# exposure cross-reference only.
FEEDS = {
    "pi+": ["rho0", "rho+/-", "omega", "K*0", "K*+/-", "eta", "etaprime",
            "Delta", "Sigma*(1385)"],
    "pi-": ["rho0", "rho+/-", "omega", "K*0", "K*+/-", "eta", "etaprime",
            "Delta", "Sigma*(1385)"],
    "K+": ["K*0", "K*+/-", "phi"],
    "K-": ["K*0", "K*+/-", "phi"],
    "K0S(K0/K0bar)": ["K*0", "K*+/-", "phi"],
    "p": ["Delta", "Sigma*(1385)", "Lambda(1520)"],
    "pbar": ["Delta", "Sigma*(1385)", "Lambda(1520)"],
    "Lambda": ["Sigma0", "Sigma*(1385)", "Lambda(1520)"],
    "antiLambda": ["Sigma0", "Sigma*(1385)", "Lambda(1520)"],
    "Xi-": ["Xi*(1530)"],
    "antiXi+": ["Xi*(1530)"],
    "Omega-": [],          # essentially primary
    "antiOmega+": [],
}

YMAX = 6.0
DY = 0.5
NBINS = int(round(YMAX / DY))   # |y| bins [0,0.5),[0.5,1.0),...


def ybin(ay):
    b = int(ay / DY)
    return b if 0 <= b < NBINS else -1


def census(files, max_files=None):
    pdgs_wanted = set()
    for _, codes in OBSERVABLES + FEEDERS:
        pdgs_wanted.update(codes)
    # per-pdg accumulators
    yhist = {p: np.zeros(NBINS) for p in pdgs_wanted}
    mid_n = {p: 0.0 for p in pdgs_wanted}
    mid_sumpt = {p: 0.0 for p in pdgs_wanted}
    # charged aggregate (by charge field)
    ch_yhist = np.zeros(NBINS); ch_mid_n = 0.0; ch_mid_sumpt = 0.0
    n_lines = 0

    used = files if max_files is None else files[:max_files]
    for fn in used:
        with open(fn) as f:
            for line in f:
                if not line or line[0] == "#":
                    continue
                parts = line.split()
                if len(parts) != 12:
                    continue
                try:
                    E = float(parts[5]); px = float(parts[6])
                    py = float(parts[7]); pz = float(parts[8])
                    pdg = int(float(parts[9])); charge = int(float(parts[11]))
                except ValueError:
                    continue
                n_lines += 1
                if not (E > abs(pz)):     # y undefined / non-physical
                    continue
                y = 0.5 * math.log((E + pz) / (E - pz))
                ay = abs(y)
                b = ybin(ay)
                if b < 0:
                    continue
                pt = math.hypot(px, py)
                if charge != 0:
                    ch_yhist[b] += 1.0
                    if ay < 0.5:
                        ch_mid_n += 1.0; ch_mid_sumpt += pt
                if pdg in yhist:
                    yhist[pdg][b] += 1.0
                    if ay < 0.5:
                        mid_n[pdg] += 1.0; mid_sumpt[pdg] += pt
    return {
        "yhist": yhist, "mid_n": mid_n, "mid_sumpt": mid_sumpt,
        "ch_yhist": ch_yhist, "ch_mid_n": ch_mid_n, "ch_mid_sumpt": ch_mid_sumpt,
        "n_files": len(used), "n_lines": n_lines,
    }


def agg(labelcodes, data):
    """Sum a label's pdg codes into one profile."""
    h = np.zeros(NBINS); mn = 0.0; msp = 0.0
    for p in labelcodes:
        if p in data["yhist"]:
            h += data["yhist"][p]; mn += data["mid_n"][p]; msp += data["mid_sumpt"][p]
    return h, mn, msp


def health(h):
    peak = h.max()
    if peak <= 0:
        return float("nan")
    return h[0] / peak


def flag_for(hl, total):
    if total < 50:
        return "low-stat"
    if math.isnan(hl):
        return "empty"
    if hl >= 0.5:
        return "plateau (clean)"
    if hl < 0.1:
        return "SHELL (bug-affected)"
    return "intermediate"


def row(label, h, mn, msp):
    total = float(h.sum())
    hl = health(h)
    meanpt = (msp / mn) if mn > 0 else float("nan")
    return label, total, mn, meanpt, hl, flag_for(hl, total)


def print_table(title, rows):
    print(f"\n{title}")
    print(f"  {'species':<18}{'total':>10}{'mid N':>10}{'<pT>_mid':>10}"
          f"{'health':>9}  flag")
    print("  " + "-" * 70)
    for label, total, mn, meanpt, hl, flag in rows:
        pt = f"{meanpt:.3f}" if not math.isnan(meanpt) else "  -  "
        h = f"{hl:.3f}" if not math.isnan(hl) else " nan "
        print(f"  {label:<18}{total:>10.0f}{mn:>10.0f}{pt:>10}{h:>9}  {flag}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--glob", required=True,
                    help="Glob for particle_lists.oscar files, e.g. "
                         "'$HIC_RAW_ROOT/design_001/AuAu200/*/particle_lists.oscar' "
                         "(quote it).")
    ap.add_argument("--max-files", type=int, default=None,
                    help="Cap number of event files (speed). Default: all.")
    ap.add_argument("--profile", default=None,
                    help="Optional: print the full |y| profile for this species label.")
    args = ap.parse_args()

    files = sorted(glob.glob(args.glob))
    if not files:
        sys.exit(f"no files match: {args.glob}")
    data = census(files, args.max_files)
    print(f"files: {data['n_files']}   particle rows parsed: {data['n_lines']}")

    obs_rows, obs_health = [], {}
    for label, codes in OBSERVABLES:
        h, mn, msp = agg(codes, data)
        obs_rows.append(row(label, h, mn, msp))
        obs_health[label] = health(h)
    # charged
    chl = health(data["ch_yhist"])
    ch_total = float(data["ch_yhist"].sum())
    ch_pt = (data["ch_mid_sumpt"] / data["ch_mid_n"]) if data["ch_mid_n"] > 0 else float("nan")
    charged_row = ("charged(all)", ch_total, data["ch_mid_n"], ch_pt, chl,
                   flag_for(chl, ch_total))
    print_table("OBSERVABLES (your analysis list)", [charged_row] + obs_rows)

    feed_rows, feed_health = [], {}
    for label, codes in FEEDERS:
        h, mn, msp = agg(codes, data)
        feed_rows.append(row(label, h, mn, msp))
        feed_health[label] = (health(h), float(h.sum()))
    print_table("FEEDERS (resonances -- expect SHELL if the bug is present)", feed_rows)

    # ---- exposure cross-reference ----
    print("\nFEED-DOWN EXPOSURE  (observable <- its feeders' shapes)")
    print("  an observable is exposed only if it has feeders that are SHELL-shaped")
    print("  " + "-" * 70)
    for label, _ in OBSERVABLES:
        feeders = FEEDS.get(label, [])
        ohl = obs_health.get(label, float("nan"))
        own = ("SHELL" if (not math.isnan(ohl) and ohl < 0.1)
               else "plateau" if (not math.isnan(ohl) and ohl >= 0.5) else "?")
        shelled = []
        for fl in feeders:
            hl, tot = feed_health.get(fl, (float("nan"), 0.0))
            if tot >= 50 and not math.isnan(hl) and hl < 0.1:
                shelled.append(fl)
        if own == "SHELL":
            verdict = "EXCLUDE: observable is itself a mis-placed resonance"
        elif not feeders:
            verdict = "safe: primary-dominated, no significant feeders"
        elif shelled:
            verdict = (f"feed-down displaced via shelled feeders [{', '.join(shelled)}]"
                       " -> check midrapidity yield vs data")
        else:
            verdict = "safe: own shape is plateau and feeders not shelled"
        print(f"  {label:<18} own={own:<8} {verdict}")

    if args.profile:
        codes = dict(OBSERVABLES + FEEDERS).get(args.profile)
        if codes is None:
            print(f"\n(unknown --profile label {args.profile!r})")
        else:
            h, _, _ = agg(codes, data)
            print(f"\n|y| profile for {args.profile}:")
            for b in range(NBINS):
                print(f"  |y| [{b*DY:.1f},{(b+1)*DY:.1f}): {h[b]:.0f}")


if __name__ == "__main__":
    main()
