#!/usr/bin/env python3
"""
analyze_design.py -- single entry point for per-design observable analysis.

Thin wrapper over the tested extract_observables / reduce_observables modules (which remain the
implementation and are not modified). Subcommands:

  run       Extract one design's artifact, optionally validate it and/or reduce it to JSON.
            This is the per-design unit the campaign launcher calls. Resumable: skips a design
            whose artifact already exists unless --force.
  validate  Round-trip validate an existing artifact against its cache (the acceptance gate).
  reduce    Reduce an existing artifact to an observables JSON.

Paths resolve from --cache-root/<design>/<system> and --out-dir/<design>_<system>_stageA,
so the common case is just `--cache-root R --design design_XXX --out-dir O`. The analysis is
read-only and applies no cuts of its own -- all acceptances are baked into the cache and are
verified against this code's constants by the extractor's fingerprint gate.
"""

import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args

import extract_observables as sx
import reduce_observables as sr

DEFAULT_SYSTEM = os.environ.get("HIC_CACHE_SYSTEM", "AuAu200")
DEFAULT_CACHE_ROOT = None  # campaign.py supplies [pipeline] cache_cfs_root


def _cache_dir(cache_root, design, system):
    return os.path.join(cache_root, design, system)


def _prefix(out_dir, design, system):
    return os.path.join(out_dir, f"{design}_{system}_stageA")


def _resolve_design_system(a):
    """Resolve (cache_dir, design, system) from either --cache-dir or --cache-root+--design."""
    if getattr(a, "cache_dir", None):
        cache_dir = a.cache_dir
        system = os.path.basename(os.path.normpath(cache_dir))
        if a.system and a.system != system:
            sys.exit(f"error: --system {a.system} disagrees with --cache-dir system {system}")
        design = a.design or os.path.basename(os.path.dirname(os.path.normpath(cache_dir)))
    else:
        if not a.design:
            sys.exit("error: --cache-root requires --design")
        if not a.cache_root:
            sys.exit("error: --cache-root is empty (set HIC_EVENT_CACHE_CFS_ROOT or pass --cache-root)")
        system = a.system or DEFAULT_SYSTEM
        design = a.design
        cache_dir = _cache_dir(a.cache_root, design, system)
    return cache_dir, design, system


def cmd_run(a):
    cache_dir, design, system = _resolve_design_system(a)
    expected = sx.load_expected_fingerprint(a.expected_fingerprint)
    sx.validate_cache_scope(cache_dir, expected, a.zstd)
    prefix = _prefix(a.out_dir, design, system)
    # "done" requires BOTH files: write_artifact creates .npz then .meta.json as separate atomic
    # steps, so an interruption can leave a .npz with no metadata. Requiring both makes a partial
    # artifact re-extract (and overwrite) instead of being silently skipped.
    done = os.path.exists(prefix + ".npz") and os.path.exists(prefix + ".meta.json")
    if done and not a.force:
        sx.validate_artifact_scope(prefix, expected)
        print(f"[analyze_design] {design}/{system}: artifact exists, skipping extraction (use --force to redo)")
    else:
        out, meta = sx.extract_design(cache_dir, et_eta_window=a.et_eta_window,
                                      require_verified=not a.allow_unverified, zstd=a.zstd,
                                      expected_fingerprint=expected)
        sx.write_artifact(out, meta, prefix)
        print(f"[analyze_design] {design}/{system}: {meta['n_events']} events "
              f"({meta['n_zero_events']} zero) -> {prefix}.npz")

    if a.validate:
        ok = sr.validate_against_cache(prefix, cache_dir, zstd=a.zstd)
        if not ok:
            return 2

    if a.reduce_json:
        rout, rmeta = sr.load_artifact(prefix)
        res = sr.reduce_all(rout, rmeta, estimator=a.estimator, edges_pct=a.bin_edges, n_boot=a.n_boot,
                            weighting=a.weighting, y_window=a.y_window, charged_window=a.charged_window)
        with open(a.reduce_json, "w") as f:
            json.dump(res, f, indent=2, default=sr._json_default)
        print(f"[analyze_design] reduced -> {a.reduce_json}")
    return 0


def cmd_validate(a):
    cache_dir, design, system = _resolve_design_system(a)
    prefix = a.artifact or _prefix(a.out_dir, design, system)
    expected = sx.load_expected_fingerprint(a.expected_fingerprint)
    sx.validate_cache_scope(cache_dir, expected, a.zstd)
    sx.validate_artifact_scope(prefix, expected)
    ok = sr.validate_against_cache(prefix, cache_dir, zstd=a.zstd)
    return 0 if ok else 1


def cmd_reduce(a):
    if a.artifact:
        prefix = a.artifact
    else:
        if not (a.out_dir and a.design):
            sys.exit("error: reduce needs --artifact, or --out-dir and --design")
        prefix = _prefix(a.out_dir, a.design, a.system)
    sx.validate_artifact_scope(prefix, sx.load_expected_fingerprint(a.expected_fingerprint))
    out, meta = sr.load_artifact(prefix)
    edges = a.bin_edges
    res = sr.reduce_all(out, meta, estimator=a.estimator,
                        edges_pct=edges, n_boot=a.n_boot, weighting=a.weighting,
                        y_window=a.y_window, charged_window=a.charged_window)
    if a.json:
        with open(a.json, "w") as f:
            json.dump(res, f, indent=2, default=sr._json_default)
        print(f"wrote {a.json}")
    else:
        for b in res["bins"]:
            o = b["observables"]
            ch = o["charged"]
            print(f"{b['cent_low']:>2.0f}-{b['cent_high']:<2.0f}%  N={b['n_events']:5d}  "
                  f"dNch/deta={ch['dNch_deta'][0]:7.2f}+/-{ch['dNch_deta'][1]:.2f}  "
                  f"v2{{2}}={ch['v2_2'][0]:.4f}+/-{ch['v2_2'][1]:.4f}  "
                  f"pi+<pT>={o['pi+']['mean_pT'][0]:.4f}")
    return 0


def _add_common_io(p, with_artifact=False):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--cache-dir", help="Full path to <...>/<design>/<system>.")
    g.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT,
                   help="Cache root; with --design gives <root>/<design>/<system> "
                        "(campaign.py supplies [pipeline] cache_cfs_root).")
    p.add_argument("--design", help="e.g. design_123.")
    p.add_argument("--system", default=None,
                   help="Default: infer from --cache-dir, otherwise HIC_CACHE_SYSTEM or AuAu200.")
    p.add_argument("--out-dir", help="Artifact output directory.")
    if with_artifact:
        p.add_argument("--artifact", help="Artifact prefix (overrides --out-dir/--design).")
    p.add_argument("--zstd", default="zstd")


def parse_bin_edges(value):
    """Accept the original comma form and the shared INI's space-separated edges."""
    return [float(item) for item in value.replace(',', ' ').split()]


def _add_reduce_opts(p):
    p.add_argument("--bin-edges", type=parse_bin_edges, default=None,
                   help="Comma-separated percentile edges (or a quoted space-separated list).")
    p.add_argument("--estimator", default="cent_ch_eta05", choices=sx.CENTRALITY_LABELS)
    p.add_argument("--charged-window", default="eta05", choices=["eta05", "eta10"])
    p.add_argument("--y-window", type=float, default=0.5)
    p.add_argument("--weighting", default="equal_event", choices=["equal_event", "all_oversample"])
    p.add_argument("--n-boot", type=int, default=200)


def main():
    ap = argparse.ArgumentParser(description="per-design analysis per-design analysis (single entry point).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("run", help="Extract one design (+ optional validate / reduce).")
    _add_common_io(rp)
    rp.add_argument("--et-eta-window", type=float, default=0.5)
    rp.add_argument("--allow-unverified", action="store_true")
    rp.add_argument("--force", action="store_true", help="Re-extract even if the artifact exists.")
    rp.add_argument("--validate", action="store_true", help="Round-trip validate after extracting.")
    rp.add_argument("--reduce-json", help="Also reduce to this JSON path after extracting.")
    _add_reduce_opts(rp)
    rp.set_defaults(func=cmd_run)

    vp = sub.add_parser("validate", help="Round-trip validate an artifact against its cache.")
    _add_common_io(vp, with_artifact=True)
    vp.set_defaults(func=cmd_validate)

    dp = sub.add_parser("reduce", help="Reduce an artifact to observables.")
    dp.add_argument("--artifact", help="Artifact prefix.")
    dp.add_argument("--out-dir", help="With --design, locates the artifact.")
    dp.add_argument("--design")
    dp.add_argument("--system", default=DEFAULT_SYSTEM)
    dp.add_argument("--json", default=None)
    _add_reduce_opts(dp)
    dp.set_defaults(func=cmd_reduce)

    for parser in (rp, vp, dp):
        parser.add_argument("--expected-fingerprint",
                            default=os.environ.get("HIC_EXPECTED_FINGERPRINT_V3"),
                            help="Selected scope; defaults to HIC_EXPECTED_FINGERPRINT_V3.")
    a = parse_workflow_args(ap, "analysis-design")
    if a.cmd in ("run",) and not a.out_dir:
        ap.error("run requires --out-dir")
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
