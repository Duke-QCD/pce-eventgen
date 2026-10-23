#!/usr/bin/env python3
"""
inspect_inventory.py

Read-only inspection of the per-PDG inventory carried in cache_one_design/v3 shard
.npz files. The inventory (arrays inv_sub / inv_pdg / inv_vals) records EVERY PDG that
appeared within |eta| < INVENTORY_ETA_MAX (default 2.0), including species that are NOT
in CORE_SPECIES and therefore get no rich per-species block. Each inventory row is a
per-(event, oversample) SUM over particles of one PDG, with 6 aggregate moments:

    inv_vals[:, :] = [N, sum_pT, sum_pT2, sum_ET_total, sum_B_sintheta, sum_BmPDG_sintheta]

This tool masks those arrays on a requested PDG and reports:
    - yield per HYDRO EVENT (equal-event weighting: mean_e [ (sum over event's
      subevents of N) / nsub_e ], matching per-design analysis's canonical estimator),
    - <pT>            = sum_pT  / N   (count-weighted, oversample-independent),
    - per-particle pT RMS width = sqrt(<pT^2> - <pT>^2),
    - <ET_total> per hydro event.

It does not write, move, or delete anything. It only reads inv_*, offsets, nsubs,
event_ids from each shard (never the large flow/histogram arrays).

USAGE
    # what PDGs are even in these shards (ranked by count)?  -- run this FIRST for
    # anything that isn't a core species (e.g. Sigma^0 is often EM-decayed and absent):
    python inspect_inventory.py /path/to/design_dir --list

    # one species across all shards of a design (dir is globbed for *.npz):
    python inspect_inventory.py /path/to/design_dir --pdg 3212           # Sigma^0

    # explicit shards; combine particle+antiparticle by |PDG|:
    python inspect_inventory.py shard_000.npz shard_001.npz --pdg 3212 --abs

    # machine-readable (loop over designs/species and collect):
    python inspect_inventory.py /path/to/design_dir --pdg 3122 --csv

NOTES
    - Published *.npz.zst shards and compressed metadata are supported using the
      same loaders as extraction/feeddown. Decompressing each published shard
      still reads the whole compressed file; only inventory arrays are materialized.
    - With the shared INI, use --design design_027 instead of repeating a cache
      path. The configured fingerprint is checked before inventory inspection.
    - Yields/ET are integrated over the inventory acceptance |eta| < INVENTORY_ETA_MAX
      (printed from the sibling metadata.json when found; default 2.0). The inventory
      carries NO pT/y-differential shape and NO flow -- only these 6 moments. For a
      differential or flow observable on a non-core species you must promote it into
      CORE_SPECIES and re-cache from raw (un-reaped designs only).
    - Zero-multiplicity events (nsub == 0) are real peripheral events kept by the cache;
      by default they are INCLUDED in the per-event mean (canonical, min-bias). Pass
      --exclude-zero-events to average only over events with at least one kept subevent.
    - Each row is a per-subevent SUM, not a particle list: individual kinematics of a
      single track are not recoverable from the inventory, only these moments.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import extract_observables as sx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args, expand_path

INV_COL = {"N": 0, "sum_pT": 1, "sum_pT2": 2,
           "sum_ET_total": 3, "sum_B_sintheta": 4, "sum_BmPDG_sintheta": 5}
REQUIRED_KEYS = ("inv_sub", "inv_pdg", "inv_vals", "offsets", "nsubs", "event_ids")


def expand_paths(paths):
    """Expand local or published shard directories; prefer plain over compressed copies."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            plain = glob.glob(os.path.join(p, "*.npz"))
            compressed = [name for name in glob.glob(os.path.join(p, "*.npz.zst"))
                          if not os.path.exists(name[:-4])]
            hits = sorted(plain + compressed)
            if not hits:
                sys.exit(f"ERROR: no *.npz or *.npz.zst shards found in directory: {p}")
            out.extend(hits)
        elif os.path.exists(p):
            out.append(p)
        else:
            sys.exit(f"ERROR: path does not exist: {p}")
    # de-dup preserving order
    seen, uniq = set(), []
    for p in out:
        rp = os.path.realpath(p)
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def load_inventory(path, zstd="zstd"):
    """Load only the inventory + indexing arrays from one shard. Fail-closed: a file
    missing any required key is named and aborts (it is not a v3 shard)."""
    z, temporary = sx._load_shard(path, str(path).endswith('.zst'), zstd=zstd)
    try:
        missing = [k for k in REQUIRED_KEYS if k not in z.files]
        if missing:
            sys.exit(f"ERROR: {path} is missing inventory keys {missing} "
                     f"-- not a cache_one_design/v3 shard?")
        return {
            "inv_sub": z["inv_sub"].astype(np.int64),
            "inv_pdg": z["inv_pdg"].astype(np.int64),
            "inv_vals": z["inv_vals"].astype(np.float64),
            "offsets": z["offsets"].astype(np.int64),
            "nsubs": z["nsubs"].astype(np.int64),
            "n_events": int(len(z["nsubs"])),
        }
    finally:
        z.close()
        if temporary is not None:
            os.unlink(temporary)


def event_of_rows(inv_sub, offsets):
    """Event index for each inventory row, from the shard's subevent offsets."""
    if inv_sub.size == 0:
        return np.zeros(0, dtype=np.int64)
    return np.searchsorted(offsets, inv_sub, side="right") - 1


def per_event_reduce(sub_masked, val_masked, offsets, nsubs):
    """Per-event array: (sum of val over an event's subevents) / nsub_event.
    Zero-multiplicity events (nsub == 0) get 0 (no contribution, no divide)."""
    n_events = len(nsubs)
    acc = np.zeros(n_events, dtype=np.float64)
    if sub_masked.size:
        ev = event_of_rows(sub_masked, offsets)
        np.add.at(acc, ev, val_masked)
    out = np.zeros(n_events, dtype=np.float64)
    nz = nsubs > 0
    out[nz] = acc[nz] / nsubs[nz]
    return out


def find_inventory_eta_max(shard_paths, zstd="zstd"):
    """Best-effort read of the inventory acceptance from a sibling metadata.json.
    Returns a float or None; never raises."""
    checked = set()
    for sp in shard_paths:
        d = os.path.dirname(os.path.realpath(sp)) or "."
        if d in checked:
            continue
        checked.add(d)
        mp = os.path.join(d, "metadata.json")
        if os.path.exists(mp) or os.path.exists(mp + '.zst'):
            try:
                meta = json.loads(sx._read_text_maybe_zst(mp, zstd=zstd))
                v = meta.get("fingerprint", {}).get("inventory_eta_max")
                if v is not None:
                    return float(v)
            except (OSError, ValueError, KeyError, RuntimeError):
                pass
    return None


def run_list(shards, eta_max, zstd="zstd"):
    """Rank every PDG present across the shards by integrated count, with its
    equal-event-weighted mean yield/event."""
    numer = defaultdict(float)   # sum_e (N_e / nsub_e), accumulated across shards
    tot_n = defaultdict(float)   # integrated particle count (sum of N over all rows)
    e_total = 0
    for path in shards:
        d = load_inventory(path, zstd=zstd)
        e_total += d["n_events"]
        sub, pdg, vals, offs, nsubs = (d["inv_sub"], d["inv_pdg"], d["inv_vals"],
                                       d["offsets"], d["nsubs"])
        if sub.size == 0:
            continue
        # per-row 1/nsub of the row's own event (rows only ever belong to nsub>0 events)
        inv_ns = np.zeros(len(nsubs), dtype=np.float64)
        nz = nsubs > 0
        inv_ns[nz] = 1.0 / nsubs[nz]
        w = inv_ns[event_of_rows(sub, offs)]
        contrib = vals[:, INV_COL["N"]] * w          # N_row / nsub_event
        upd, inv_map = np.unique(pdg, return_inverse=True)
        grp_c = np.zeros(len(upd)); np.add.at(grp_c, inv_map, contrib)
        grp_n = np.zeros(len(upd)); np.add.at(grp_n, inv_map, vals[:, INV_COL["N"]])
        for j, code in enumerate(upd):
            numer[int(code)] += grp_c[j]
            tot_n[int(code)] += grp_n[j]

    if not tot_n:
        print("No inventory entries found in the given shards (all empty).")
        return
    acc = "?" if eta_max is None else f"{eta_max:g}"
    print(f"# PDGs in inventory  (|eta| < {acc};  {e_total} events, "
          f"{len(shards)} shard(s),  {len(tot_n)} distinct PDGs)")
    print(f"# {'PDG':>10}  {'count':>14}  {'yield/event':>12}")
    for code in sorted(tot_n, key=lambda c: tot_n[c], reverse=True):
        print(f"  {code:>10d}  {tot_n[code]:>14.0f}  "
              f"{(numer[code] / max(e_total, 1)):>12.5g}")


def run_species(shards, pdg, use_abs, exclude_zero, eta_max, as_csv, header, zstd="zstd"):
    """Full report for one PDG (or |PDG| with --abs) aggregated across shards."""
    ype_chunks, et_chunks, nsub_chunks = [], [], []
    sum_n = sum_p = sum_p2 = 0.0
    for path in shards:
        d = load_inventory(path, zstd=zstd)
        sub, allpdg, vals, offs, nsubs = (d["inv_sub"], d["inv_pdg"], d["inv_vals"],
                                          d["offsets"], d["nsubs"])
        if use_abs:
            m = np.abs(allpdg) == abs(pdg)
        else:
            m = allpdg == pdg
        sub_m, vals_m = sub[m], vals[m]
        ype_chunks.append(per_event_reduce(sub_m, vals_m[:, INV_COL["N"]], offs, nsubs))
        et_chunks.append(per_event_reduce(sub_m, vals_m[:, INV_COL["sum_ET_total"]], offs, nsubs))
        nsub_chunks.append(nsubs)
        sum_n += vals_m[:, INV_COL["N"]].sum()
        sum_p += vals_m[:, INV_COL["sum_pT"]].sum()
        sum_p2 += vals_m[:, INV_COL["sum_pT2"]].sum()

    yields = np.concatenate(ype_chunks) if ype_chunks else np.zeros(0)
    ets = np.concatenate(et_chunks) if et_chunks else np.zeros(0)
    nsub_all = np.concatenate(nsub_chunks) if nsub_chunks else np.zeros(0, dtype=np.int64)
    n_zero = int((nsub_all == 0).sum())
    n_total = len(yields)

    if exclude_zero:
        keep = nsub_all > 0
        yields, ets = yields[keep], ets[keep]

    label = f"|PDG| {abs(pdg)}" if use_abs else f"PDG {pdg}"
    acc = "?" if eta_max is None else f"{eta_max:g}"

    if sum_n == 0.0:
        # Absence is a valid finding (Sigma^0 is commonly EM-decayed out of the OSCAR
        # output; its daughters then sit in the Lambda inventory as feeddown).
        if as_csv:
            if header:
                print("pdg,abs,n_events,n_zero,yield_mean,yield_std,mean_pT,pT_rms,ET_mean")
            print(f"{pdg},{int(use_abs)},{n_total},{n_zero},,,,, ")
        else:
            print(f"{label}: NOT FOUND in the inventory of these shards "
                  f"({len(shards)} shard(s), {n_total} events, |eta| < {acc}).")
            print("  Run with --list to see which PDGs are present.")
        return

    mean_pt = sum_p / sum_n
    pt_rms = float(np.sqrt(max(sum_p2 / sum_n - mean_pt**2, 0.0)))
    y_mean = float(yields.mean()) if len(yields) else 0.0
    y_std = float(yields.std()) if len(yields) else 0.0
    et_mean = float(ets.mean()) if len(ets) else 0.0
    denom_events = len(yields)

    if as_csv:
        if header:
            print("pdg,abs,n_events,n_zero,yield_mean,yield_std,mean_pT,pT_rms,ET_mean")
        print(f"{pdg},{int(use_abs)},{denom_events},{n_zero},"
              f"{y_mean:.6g},{y_std:.6g},{mean_pt:.6g},{pt_rms:.6g},{et_mean:.6g}")
        return

    print(f"{label}   ({len(shards)} shard(s))")
    if exclude_zero:
        print(f"  events               : {denom_events}  "
              f"(excluded {n_zero} zero-multiplicity events)")
    else:
        print(f"  events               : {denom_events}  "
              f"(zero-multiplicity: {n_zero})")
    print(f"  yield / event        : mean {y_mean:.5g}   std {y_std:.5g}   "
          f"min {yields.min():.4g}   max {yields.max():.4g}   (|eta| < {acc})")
    print(f"  <pT>                 : {mean_pt:.5g} GeV   "
          f"(per-particle RMS {pt_rms:.4g})")
    print(f"  <ET_total> / event   : {et_mean:.5g} GeV")


def main():
    ap = argparse.ArgumentParser(
        description="Query the per-PDG inventory in cache_one_design/v3 shard .npz files.")
    ap.add_argument("paths", nargs="*",
                    help="Shard .npz file(s) and/or directories (globbed for *.npz).")
    ap.add_argument("--design", help="Design to inspect under the configured published cache root.")
    ap.add_argument("--cache-root", default=None)
    ap.add_argument("--system", default=os.environ.get('HIC_CACHE_SYSTEM', 'AuAu200'))
    ap.add_argument("--expected-fingerprint", default=None)
    ap.add_argument("--zstd", default="zstd")
    ap.add_argument("--pdg", type=int, default=None,
                    help="PDG code to report (e.g. 3212 = Sigma^0). "
                         "Use --pdg=-3212 for the antiparticle, or --abs to combine.")
    ap.add_argument("--abs", action="store_true",
                    help="Combine particle + antiparticle by |PDG|.")
    ap.add_argument("--list", action="store_true",
                    help="List every PDG present (ranked by count) and exit.")
    ap.add_argument("--exclude-zero-events", action="store_true",
                    help="Average per-event yield only over events with nsub > 0.")
    ap.add_argument("--csv", action="store_true",
                    help="Emit a single CSV row (for looping over designs/species).")
    ap.add_argument("--no-header", action="store_true",
                    help="With --csv, suppress the header line.")
    args = parse_workflow_args(ap, "inventory")

    if not args.list and args.pdg is None:
        ap.error("give --pdg CODE (report a species) or --list (enumerate PDGs).")

    if args.paths and args.design:
        ap.error("use either explicit paths or --design")
    if not args.paths:
        if not args.design or not args.cache_root:
            ap.error("give shard paths, or --design with --config/--cache-root")
        args.paths = [os.path.join(args.cache_root, args.design, args.system)]
    shards = expand_paths([expand_path(path) for path in args.paths])
    if args.expected_fingerprint:
        expected = sx.load_expected_fingerprint(args.expected_fingerprint)
        for folder in {os.path.dirname(os.path.realpath(path)) for path in shards}:
            sx.validate_cache_scope(folder, expected, args.zstd)
    eta_max = find_inventory_eta_max(shards, zstd=args.zstd)

    if args.list:
        run_list(shards, eta_max, zstd=args.zstd)
    else:
        run_species(shards, args.pdg, args.abs, args.exclude_zero_events,
                    eta_max, args.csv, not args.no_header, zstd=args.zstd)


if __name__ == "__main__":
    main()
