#!/usr/bin/env python3
"""
run_analysis.py -- campaign launcher for per-design observable analysis.

Runs analyze_design.py over many designs in PARALLEL via a local thread pool,
inside an `salloc`, sharing the reaper's local execution model. Analysis is
metadata/IO-bound. Because it runs in the allocation shell, there is no sbatch
script, no PYTHONPATH wiring, and no env-propagation to get wrong.

DRY-RUN BY DEFAULT: lists the selected designs and an example command, runs nothing. Add --submit
to execute. Resumable: a design whose artifact already exists (BOTH .npz and .meta.json) is
skipped unless --force; --force is propagated to analyze_design.py so it actually re-extracts.

Design selection uses --pattern (default 'design_') plus
optional --start-index/--end-index on the trailing integer, restricted to designs that have a
verified published cache (cache.verified present).

Subcommands:
  run     discover -> (dry-run | --submit parallel extract) -> report + failures file
  audit   report which designs have complete artifacts, which are missing/partial, and failures

Typical use (from the repository root, inside an allocation with the profile active):
  python models/campaign.py --config "$HIC_CAMPAIGN_CONFIG" analysis run
  python models/campaign.py --config "$HIC_CAMPAIGN_CONFIG" analysis run --submit
  python models/campaign.py --config "$HIC_CAMPAIGN_CONFIG" analysis audit
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import extract_observables as sx

DEFAULT_SYSTEM = os.environ.get("HIC_CACHE_SYSTEM", "AuAu200")
DEFAULT_CACHE_ROOT = None  # campaign.py supplies [pipeline] cache_cfs_root
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ANALYZE = os.path.join(SCRIPT_DIR, "analyze_design.py")

_print_lock = threading.Lock()


def _say(*args):
    with _print_lock:
        print(*args, flush=True)


def _natkey(name):
    m = re.search(r"(\d+)$", name)
    return (0, int(m.group(1))) if m else (1, name)


def _design_index(name):
    m = re.search(r"(\d+)$", name)
    return int(m.group(1)) if m else None


def discover_designs(cache_root, system, pattern):
    """Designs with a verified published cache: <cache_root>/<pattern>*/<system>/cache.verified."""
    hits = glob.glob(os.path.join(cache_root, f"{pattern}*", system, "cache.verified"))
    return sorted({os.path.basename(os.path.dirname(os.path.dirname(p))) for p in hits}, key=_natkey)


def artifact_prefix(out_dir, design, system):
    return os.path.join(out_dir, f"{design}_{system}_stageA")


def is_done(out_dir, design, system):
    """Complete requires BOTH files (a .npz without .meta.json is a partial/interrupted write)."""
    p = artifact_prefix(out_dir, design, system)
    return os.path.exists(p + ".npz") and os.path.exists(p + ".meta.json")


def _require_cache_root(a):
    if not a.cache_root or not os.path.isabs(a.cache_root) or not os.path.isdir(a.cache_root):
        sys.exit(f"--cache-root is not an existing absolute path: {a.cache_root!r}\n"
                 "Source your env file (so $HIC_EVENT_CACHE_CFS_ROOT is set) or pass --cache-root explicitly.")


def select(a):
    discovered = discover_designs(a.cache_root, a.system, a.pattern)
    if not discovered:
        sys.exit(f"no verified caches under {a.cache_root}/{a.pattern}*/{a.system}/cache.verified "
                 "(is the env sourced / --cache-root correct?)")
    if a.designs:
        # An explicit list is authoritative and validated against the full verified set: a typo or
        # an unpublished design must ERROR, not be silently dropped. This is the path used for the
        # sample-validation gate, where silently skipping a design would let you believe you
        # validated something you did not. Explicit list wins over --start/--end-index.
        missing_req = sorted(set(a.designs) - set(discovered), key=_natkey)
        if missing_req:
            sys.exit("requested designs have no verified cache (typo or not yet published): "
                     + " ".join(missing_req))
        designs = [d for d in discovered if d in set(a.designs)]
    else:
        designs = discovered
        if a.start_index is not None:
            designs = [d for d in designs if (_design_index(d) if _design_index(d) is not None else -1) >= a.start_index]
        if a.end_index is not None:
            designs = [d for d in designs if (_design_index(d) if _design_index(d) is not None else 1 << 30) <= a.end_index]
    # Check selected source scopes even when a pre-existing artifact would skip
    # extraction. Otherwise an entirely wrong-scope campaign can look complete.
    for d in designs:
        sx.validate_cache_scope(os.path.join(a.cache_root, d, a.system), a.expected_scope, a.zstd)
        if not a.force and is_done(a.out_dir, d, a.system):
            sx.validate_artifact_scope(artifact_prefix(a.out_dir, d, a.system), a.expected_scope)
    if not a.force:
        designs = [d for d in designs if not is_done(a.out_dir, d, a.system)]
    return designs


def _cmd(a, design):
    c = [sys.executable, ANALYZE, "run",
         "--cache-root", a.cache_root, "--design", design, "--system", a.system,
         "--out-dir", os.path.abspath(a.out_dir), "--zstd", a.zstd,
         "--et-eta-window", str(a.et_eta_window),
         "--expected-fingerprint", a.expected_fingerprint]
    if a.force:
        c.append("--force")       # propagate force, else analyze_design skips existing artifacts
    if a.validate:
        c.append("--validate")
    if a.allow_unverified:
        c.append("--allow-unverified")
    return c


def run_one(cmd, design):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        out_last = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        err_last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        return design, r.returncode, out_last, err_last
    except Exception as e:
        return design, -1, "", str(e)


def cmd_run(a):
    _require_cache_root(a)
    if a.jobs < 1:
        sys.exit("--jobs must be >= 1")
    if not os.path.exists(ANALYZE):
        sys.exit(f"analyze_design.py not found next to this script: {ANALYZE}\n"
                 "Keep analyze_design.py, extract_observables.py, reduce_observables.py in this directory.")
    os.makedirs(a.out_dir, exist_ok=True)
    designs = select(a)

    _say(f"Designs to process : {len(designs)}  (jobs={a.jobs}, validate={a.validate}, force={a.force})")
    _say(f"Cache root         : {a.cache_root}")
    _say(f"Output             : {os.path.abspath(a.out_dir)}")
    if designs:
        _say(f"First / last       : {designs[0]} ... {designs[-1]}")
    else:
        _say("Nothing to do: all eligible designs already have artifacts (use --force to redo).")
        return 0
    if not a.submit:
        _say("\nDRY RUN -- nothing executed. Example per-design command:")
        _say("  " + " ".join(_cmd(a, designs[0])))
        _say("\nRe-run with --submit to execute.")
        return 0

    fails, done = [], 0
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(run_one, _cmd(a, d), d): d for d in designs}
        for fut in as_completed(futs):
            design, rc, out_last, err_last = fut.result()
            done += 1
            if rc == 0:
                _say(f"[{done}/{len(designs)}] OK   {design}  {out_last}")
            else:
                fails.append(design)
                _say(f"[{done}/{len(designs)}] FAIL {design}  rc={rc}  {err_last or out_last}")

    fpath = os.path.join(a.out_dir, "analysis_failures.txt")
    if fails:
        with open(fpath, "w") as f:
            f.write("\n".join(sorted(set(fails), key=_natkey)) + "\n")
        _say(f"\n{len(fails)}/{len(designs)} FAILED -> {fpath}")
        _say("  " + " ".join(sorted(set(fails), key=_natkey)))
        _say("Re-run the same command to retry failures (succeeded designs are skipped).")
    else:
        if os.path.exists(fpath):
            os.remove(fpath)   # clear a stale failures list so audit can't report fixed designs
        _say(f"\nAll {len(designs)} designs succeeded.")
    _say("Run `python run_analysis.py audit --out-dir ...` to confirm.")
    return 1 if fails else 0


def cmd_audit(a):
    _require_cache_root(a)
    designs = discover_designs(a.cache_root, a.system, a.pattern)
    complete, missing, suspect = [], [], []
    for d in designs:
        p = artifact_prefix(a.out_dir, d, a.system)
        npz, meta = os.path.exists(p + ".npz"), os.path.exists(p + ".meta.json")
        if not (npz and meta):
            if npz and not meta:
                suspect.append(f"{d}: .npz present but .meta.json missing (partial write)")
            else:
                missing.append(d)
            continue
        try:
            m = sx.validate_artifact_scope(p, a.expected_scope)
            ne = int(m.get("n_events", 0))
            complete.append((d, ne, int(m.get("n_zero_events", 0))))
            if ne <= 0:
                suspect.append(f"{d}: n_events={ne}")
        except Exception as e:
            suspect.append(f"{d}: meta unreadable ({e})")

    print(f"eligible designs : {len(designs)}")
    print(f"complete         : {len(complete)}")
    print(f"missing          : {len(missing)}")
    if missing:
        s = missing[:40]
        print("  " + " ".join(s) + (f" ... (+{len(missing) - len(s)} more)" if len(missing) > len(s) else ""))
    if suspect:
        print("SUSPECT (re-run with --force to fix):")
        for x in suspect:
            print("  " + x)
    fpath = os.path.join(a.out_dir, "analysis_failures.txt")
    if os.path.exists(fpath):
        fails = [x for x in open(fpath).read().split() if x]
        if fails:
            print(f"\nlast recorded failures ({fpath}): {' '.join(sorted(set(fails), key=_natkey))}")
    if a.verbose and complete:
        print("\nn_events (events / zero):")
        for d, ne, nz in complete:
            print(f"  {d}: {ne} / {nz}")
    return 1 if suspect else 0


def _artifact_name_index(npz_path, system):
    """design_067_AuAu200_stageA.npz -> ('design_067', 67)."""
    base = os.path.basename(npz_path)
    tail = f"_{system}_stageA.npz"
    name = base[:-len(tail)] if base.endswith(tail) else base
    m = re.search(r"(\d+)$", name)
    return name, (int(m.group(1)) if m else None)


def _feature_groups(species_yhist, combo_names):
    """Fixed (group, [observables]) order used to flatten every design identically.
    charged carries dNch_deta; identified species + combos carry dN_dy; global_ET its two columns.
    Identified v_n are the differential-vs-charged-reference estimator; no v1 (cache has q2/q3/q4).

    Charged flow carries BOTH v_n{2} = sqrt(c_n) AND the signed two-particle cumulant c{n}_gap
    (= v_n{2}^2 where positive). The cumulants are included so calibration can run in cumulant
    space and avoid the sqrt-NaN that small higher-harmonic c_n hit when a finite-statistics
    estimate fluctuates below zero; each c{n}_gap sits next to its v{n}_2. This is purely additive
    -- nothing is removed -- and which of v_n / c_n / harmonics enters the likelihood (e.g. use
    c2/c3, drop v4) is a downstream selection, not baked in here. Identified-species cumulants are
    intentionally omitted (identified flow is the differential estimator and is deferred)."""
    charged_flow = ["v2_2", "c2_gap", "v3_2", "c3_gap", "v4_2", "c4_gap"]
    groups = [("charged", ["dNch_deta", "mean_pT"] + charged_flow)]
    for sp in list(species_yhist) + list(combo_names):
        groups.append((sp, ["dN_dy", "mean_pT", "v2_2", "v3_2", "v4_2"]))
    groups.append(("global_ET", ["dET_total_visible_deta", "dET_hadronic_deta"]))
    return groups


def cmd_aggregate(a):
    import numpy as np
    import json as _json
    import csv as _csv
    from reduce_observables import reduce_all, COMBOS, load_artifact

    out_dir = os.path.abspath(a.out_dir)
    arts = sorted(glob.glob(os.path.join(out_dir, f"*_{a.system}_stageA.npz")))
    designs = []
    for npz in arts:
        meta = npz[:-len(".npz")] + ".meta.json"
        if not os.path.exists(meta):
            continue                                   # partial artifact; skip (audit flags it)
        name, idx = _artifact_name_index(npz, a.system)
        designs.append(((idx if idx is not None else 1 << 30), name, npz, meta))
    designs.sort()
    if a.start_index is not None:
        designs = [d for d in designs if d[0] >= a.start_index]
    if a.end_index is not None:
        designs = [d for d in designs if d[0] <= a.end_index]
    if not designs:
        sys.exit(f"no per-design analysis artifacts (.npz + .meta.json) under {out_dir}")

    # Different fingerprints can have identical array shapes. Reject mixed
    # scopes before reduction rather than silently combine different flow cuts.
    scope_hash, scope_fingerprint = None, None
    from extract_observables import _canonical_fingerprint_hash
    for _, name, _, meta_path in designs:
        with open(meta_path) as f:
            artifact_meta = _json.load(f)
        fp = artifact_meta.get("cache_fingerprint")
        if not isinstance(fp, dict):
            sys.exit(f"{name}: artifact lacks a cache fingerprint; re-extract")
        digest = _canonical_fingerprint_hash(fp)
        if artifact_meta.get("cache_fingerprint_hash") != digest:
            sys.exit(f"{name}: artifact fingerprint/hash mismatch; re-extract")
        if scope_hash is not None and digest != scope_hash:
            sys.exit(f"{name}: mixed cache fingerprints in one model matrix; use separate output directories")
        sx.assert_expected_scope(fp, a.expected_scope, name)
        scope_hash, scope_fingerprint = digest, fp

    edges = list(a.bin_edges) if a.bin_edges else list(range(0, 105, 5))   # 20 x 5% bins, 0-100%
    combo_names = list(COMBOS.keys())

    labels, bin_pairs = None, None
    Y, Yerr, didx, dname, failures = [], [], [], [], []
    n = len(designs)
    _say(f"Aggregating {n} design(s) | estimator={a.estimator} | "
         f"{len(edges)-1} centrality bins | n_boot={a.n_boot}")
    for i, (idx, name, npz, meta) in enumerate(designs, 1):
        _say(f"[{i}/{n}] {name}")          # printed BEFORE load, so a hang shows the culprit design
        try:
            out, m = load_artifact(npz[:-len(".npz")])   # materializes arrays into a dict; an
                                                         # NpzFile re-reads every array on each
                                                         # access, which is fatal in the bootstrap.
            red = reduce_all(out, m, estimator=a.estimator, edges_pct=edges, n_boot=a.n_boot,
                             weighting="equal_event", y_window=a.y_window,
                             charged_window=a.charged_window)
            groups = _feature_groups(m["species_yhist"], combo_names)
            # Charged cumulants must be emitted by reduce_all; a missing key would otherwise become
            # a silent all-NaN column that poisons inference. Derive the requirement FROM the
            # whitelist so it auto-tracks if a c{n}_gap is added/removed (one source of truth).
            required = {(g, o) for g, obs in groups for o in obs
                        if g == "charged" and o.endswith("_gap")}
            if labels is None:
                bin_pairs = [(b["cent_low"], b["cent_high"]) for b in red["bins"]]
                labels = [f"{g}__{lo}_{hi}__{o}"
                          for (lo, hi) in bin_pairs for g, obs in groups for o in obs]
            row, erow = [], []
            for b in red["bins"]:
                ob = b["observables"]
                for g, obs in groups:
                    gd = ob.get(g, {})
                    for o in obs:
                        v = gd.get(o)
                        if v is None and (g, o) in required:
                            raise KeyError(
                                f"charged cumulant '{o}' absent from reduce_all output "
                                "(reduce_observables.py not emitting it? version mismatch); "
                                "refusing to write an all-NaN cumulant column")
                        row.append(float(v[0]) if v is not None else np.nan)
                        erow.append(float(v[1]) if v is not None else np.nan)
            if len(row) != len(labels):
                raise ValueError(f"feature count {len(row)} != expected {len(labels)} "
                                 "(centrality binning differs across designs?)")
            Y.append(row); Yerr.append(erow); didx.append(idx); dname.append(name)
        except Exception as ex:
            failures.append([name, str(ex)])
            _say(f"  SKIP {name}: {ex}")

    if not Y:
        sys.exit("no designs reduced successfully")
    Y = np.asarray(Y, float); Yerr = np.asarray(Yerr, float)
    didx = np.asarray(didx, int); dname = np.asarray(dname)
    labels = np.asarray(labels)

    mout = os.path.abspath(a.matrix_out) if a.matrix_out else os.path.join(out_dir, "model_matrix.npz")
    if not mout.endswith(".npz"):
        mout += ".npz"          # keep np.savez output and the meta/CSV path-slicing consistent
    np.savez(mout, Y=Y, Y_err=Yerr, feature_labels=labels, design_index=didx, design_name=dname)

    meta_out = {
        "schema": "model_matrix/v1",
        "cache_fingerprint_hash": scope_hash,
        "cache_fingerprint": scope_fingerprint,
        "n_designs": int(Y.shape[0]), "n_features": int(Y.shape[1]),
        "row_order": "rows aligned to design_index / design_name (sorted ascending by design number)",
        "estimator": a.estimator, "centrality_edges_pct": edges, "centrality_bins": bin_pairs,
        "y_window": a.y_window, "charged_window": a.charged_window,
        "weighting": "equal_event", "n_boot": a.n_boot,
        "feature_label_format": "{species}__{cent_lo}_{cent_hi}__{observable}",
        "Y_err_is": ("diagonal per-observable statistical sigma ("
                     + ("analytic jackknife/SEM" if a.n_boot == 0 else f"bootstrap, n_boot={a.n_boot}")
                     + ") -- NOT a covariance"),
        "sigma_method": ("jackknife_sem" if a.n_boot == 0 else "bootstrap"),
        "covariance_note": ("cross-observable/cross-centrality covariance requires a design-level "
                            "shared-replicate bootstrap from the per-event .npz; build separately"),
        "excluded_from_likelihood": ["p", "pbar", "p_tot", "phi"],
        "vn_note": ("identified v_n = differential vs charged reference; charged v_n = two-subevent "
                    "gap, v_n = sqrt(c_n) where c_n>0 else NaN; charged c{n}_gap = the SIGNED "
                    "two-particle cumulant c_n{2} (= v_n^2 when positive), included so calibration "
                    "can run in cumulant space and avoid sqrt-NaN; no v1"),
        "flow_calibration_note": ("recommended: calibrate charged flow on c2_gap and c3_gap "
                    "(cumulant space; compare to data c_n = (v_n_data)^2 with sigma_c ~= 2*v*sigma_v, "
                    "a delta-method approx accurate where v >> sigma_v). Drop v4/c4 (unresolved at "
                    "present statistics) and peripheral bins where c_n is not resolved -- cumulant "
                    "space removes NaNs but does NOT make low-S/N bins informative. These are "
                    "likelihood-time selections, NOT baked into the matrix. Match the target observable "
                    "definition (eta gap, pT range, centrality estimator, event weighting) when "
                    "comparing; the central-value map c=v^2 is exact, the error map is delta-method"),
        "nan_policy": "undefined observables stored as NaN; handle per-column downstream",
        "source_artifacts_dir": out_dir, "system": a.system,
        "n_failed": len(failures), "failed": failures,
    }
    with open(mout[:-len(".npz")] + ".meta.json", "w") as f:
        _json.dump(meta_out, f, indent=2)

    if not a.no_csv:
        csv_path = mout[:-len(".npz")] + "_long.csv"
        with open(csv_path, "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["design_index", "design_name", "species", "cent_low", "cent_high",
                        "observable", "value", "sigma"])
            for r in range(Y.shape[0]):
                for c, lab in enumerate(labels):
                    sp, cent, ob = lab.split("__")
                    lo, hi = cent.split("_")
                    w.writerow([didx[r], dname[r], sp, lo, hi, ob, Y[r, c], Yerr[r, c]])

    _say(f"\nModel matrix: {Y.shape[0]} designs x {Y.shape[1]} features -> {mout}")
    _say(f"  + {mout[:-len('.npz')]}.meta.json" + ("" if a.no_csv else f"  + {mout[:-len('.npz')]}_long.csv"))
    if failures:
        _say(f"  {len(failures)} design(s) skipped (recorded in .meta.json 'failed').")
    return 1 if failures else 0


def _read_json_resilient(read_fn, what, tries=4, base_sleep=0.15):
    """Read+parse a small cache-side JSON (metadata.json / cache.verified) with a bounded retry.

    These are read straight from CFS while the sweep is also forking many zstd subprocesses for
    shard decompression. On older interpreters a fork in one thread can momentarily disrupt a plain
    read in flight in another (observed as a UnicodeDecodeError mid-file on a file that is
    byte-identical and clean at rest), and a shared filesystem can also hand back a transient
    bad/short read under heavy parallel I/O. Both are transient -- a re-read essentially always
    succeeds -- so retry on decode/parse/OS errors with backoff. Raise (failing THIS design closed,
    not the whole campaign) only if it is still unreadable after `tries`, which indicates genuine
    on-disk corruption rather than a transient."""
    import time
    last = None
    for k in range(tries):
        try:
            return read_fn()
        except (UnicodeDecodeError, json.JSONDecodeError, OSError) as e:
            last = e
            if k + 1 < tries:
                time.sleep(base_sleep * (2 ** k))
    raise RuntimeError(f"{what}: unreadable after {tries} attempts "
                       f"({type(last).__name__}: {last}). Transient read faults clear on retry, so "
                       "persisting past this points at genuine cache corruption -- re-run / rebuild "
                       "this design's cache.")


def _feeddown_read_design(cache_dir, pids, combine, estimator, edges, zstd):
    """Read one verified v3 cache and reduce it to per-(PID, centrality) inventory multiplicities.

    Returns (n_events, row_vals, row_errs, eta_max). row_vals/row_errs are the flattened
    (bin-outer, pid-inner) equal-event mean multiplicity per hydro event and its SEM.

    The per-PID count is sourced from the cache INVENTORY (inv_sub/inv_pdg/inv_vals) integrated
    over |eta| < INVENTORY_ETA_MAX -- NOT the y-hist -- so it works for arbitrary PIDs (including
    ones with no rich per-species block). Centrality uses cent#<estimator>, meaned over oversamples
    exactly as extract_observables does. The reduction (equal-event mean via _event_mean, SEM via
    _mean_se) is imported from reduce_observables, so these yields carry the IDENTICAL statistical
    treatment as the model-matrix yields. Fail-closed: any structural/schema problem raises."""
    import numpy as np
    from extract_observables import (_read_text_maybe_zst, _resolve_shard_path, _load_shard,
                                 EXPECTED_CACHE_SCHEMA)
    from reduce_observables import centrality_classes, _event_mean, _mean_se

    def _read_meta():
        return json.loads(_read_text_maybe_zst(os.path.join(cache_dir, "metadata.json"), zstd=zstd))
    meta = _read_json_resilient(_read_meta, f"{cache_dir}/metadata.json")
    n_events = int(meta["n_events"])
    eta_max = float((meta.get("fingerprint") or {}).get("inventory_eta_max", 2.0))

    # Discovery already required cache.verified to EXIST; verify its CONTENTS (valid + schema).
    mpath = os.path.join(cache_dir, "cache.verified")
    if os.path.exists(mpath):
        def _read_marker():
            with open(mpath, "rb") as fh:        # bytes+decode so a transient partial read retries as a unit
                return json.loads(fh.read().decode("utf-8"))
        mk = _read_json_resilient(_read_marker, f"{cache_dir}/cache.verified")
        if mk.get("cache_valid") is not True:
            raise RuntimeError(f"{cache_dir}: cache.verified cache_valid is not True")
        if mk.get("schema_version") != EXPECTED_CACHE_SCHEMA:
            raise RuntimeError(f"{cache_dir}: cache.verified schema_version "
                             f"{mk.get('schema_version')!r} != {EXPECTED_CACHE_SCHEMA!r}")
        if "n_events" in mk and int(mk["n_events"]) != n_events:
            raise RuntimeError(f"{cache_dir}: cache.verified n_events disagrees with metadata.json")

    shard_names = meta.get("shards")
    if not shard_names:
        raise RuntimeError(f"{cache_dir}: metadata.json has no 'shards' list")

    cent_key = f"cent#{estimator}"
    npid = len(pids)
    counts = np.zeros((n_events, npid), dtype=np.float64)   # per-event, oversample-summed, |eta|<eta_max
    cent = np.zeros(n_events, dtype=np.float64)
    nsub = np.zeros(n_events, dtype=np.int64)
    apids = [abs(int(p)) for p in pids]

    row = 0
    for base in shard_names:
        sp, is_zst = _resolve_shard_path(cache_dir, base)
        z, tmp = _load_shard(sp, is_zst, zstd=zstd)
        try:
            need = ["event_ids", "offsets", "inv_sub", "inv_pdg", "inv_vals", cent_key]
            missing = [k for k in need if k not in z.files]
            if missing:
                raise RuntimeError(f"{cache_dir}/{base}: shard missing keys {missing} "
                                 "(not a v3 inventory shard?)")
            offs = z["offsets"]; ev = z["event_ids"]; ne = len(ev)
            cc = z[cent_key]
            inv_sub = z["inv_sub"]; inv_pdg = z["inv_pdg"]; inv_vals = z["inv_vals"]
            if row + ne > n_events:
                raise RuntimeError(f"{cache_dir}/{base}: event overflow vs metadata n_events={n_events}")
            # nsub + centrality mean over oversamples (0 for zero-events -> ranks most peripheral)
            for i in range(ne):
                a, b = int(offs[i]), int(offs[i + 1])
                nsub[row + i] = b - a
                if b > a:
                    seg = cc[a:b]
                    cent[row + i] = float(np.nanmean(seg)) if seg.size else 0.0
            # per-event inventory counts per pid, vectorized across this shard's rows
            if inv_sub.size:
                ev_of_row = np.searchsorted(offs, inv_sub, side="right") - 1
                gidx = row + ev_of_row
                apdg = np.abs(inv_pdg)
                for j, (pid, apid) in enumerate(zip(pids, apids)):
                    m = (apdg == apid) if combine else (inv_pdg == pid)
                    if m.any():
                        np.add.at(counts, (gidx[m], j), inv_vals[m, 0])
            row += ne
        finally:
            try:
                z.close()
            except Exception:
                pass
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    if row != n_events:
        raise RuntimeError(f"{cache_dir}: summed shard events {row} != metadata n_events {n_events}")

    # Reduce per centrality class: equal-event mean multiplicity + SEM (mirrors the yield path).
    classes = centrality_classes(cent, edges)
    row_vals, row_errs = [], []
    for (lo, hi, idx) in classes:
        n_ev = len(idx)
        ns = nsub[idx].astype(float)
        nz = ns > 0
        for j in range(npid):
            per_ev = np.zeros(n_ev)
            per_ev[nz] = counts[idx, j][nz] / ns[nz]
            row_vals.append(_event_mean(per_ev, n_ev))
            row_errs.append(_mean_se(per_ev, n_ev))
    return n_events, row_vals, row_errs, eta_max


def cmd_feeddown(a):
    """Survey per-PID inventory multiplicities into a SEPARATE design_index-keyed file
    (feeddown_yields.npz). Reads the caches directly (the inventory is not in the per-design analysis
    artifacts); does NOT touch model_matrix or any per-design analysis artifact."""
    import numpy as np
    _require_cache_root(a)
    if a.jobs < 1:
        sys.exit("--jobs must be >= 1")

    # Selection: verified caches via discovery + start/end/explicit filters. NO artifact-skip --
    # this is a single combined output (like aggregate), so every selected design is processed.
    discovered = discover_designs(a.cache_root, a.system, a.pattern)
    if not discovered:
        sys.exit(f"no verified caches under {a.cache_root}/{a.pattern}*/{a.system}/cache.verified "
                 "(is the env sourced / --cache-root correct?)")
    if a.designs:
        missing_req = sorted(set(a.designs) - set(discovered), key=_natkey)
        if missing_req:
            sys.exit("requested designs have no verified cache (typo or not yet published): "
                     + " ".join(missing_req))
        designs = [d for d in discovered if d in set(a.designs)]
    else:
        designs = discovered
        if a.start_index is not None:
            designs = [d for d in designs
                       if (_design_index(d) if _design_index(d) is not None else -1) >= a.start_index]
        if a.end_index is not None:
            designs = [d for d in designs
                       if (_design_index(d) if _design_index(d) is not None else 1 << 30) <= a.end_index]
    if not designs:
        sys.exit("no designs selected")

    for d in designs:
        sx.validate_cache_scope(os.path.join(a.cache_root, d, a.system), a.expected_scope, a.zstd)

    pids = [int(p) for p in a.pids]
    edges = [float(x) for x in a.bin_edges] if a.bin_edges \
        else [0., 5., 10., 20., 30., 40., 50., 60., 70., 80.]
    if len(edges) < 2:
        sys.exit("--bin-edges needs at least 2 edges")
    combine = bool(a.combine_antiparticles)
    bin_pairs = list(zip(edges[:-1], edges[1:]))
    tok = (lambda p: f"pidpm{p}") if combine else (lambda p: f"pid{p}")

    _say(f"Feeddown survey | {len(designs)} design(s) | pids={pids} | combine_anti={combine} "
         f"| estimator={a.estimator} | {len(bin_pairs)} bins | jobs={a.jobs}")

    def work(d):
        cache_dir = os.path.join(a.cache_root, d, a.system)
        return _feeddown_read_design(cache_dir, pids, combine, a.estimator, edges, a.zstd)

    results, failures = {}, []
    with ThreadPoolExecutor(max_workers=a.jobs) as ex:
        futs = {ex.submit(work, d): d for d in designs}
        done = 0
        for fut in as_completed(futs):
            d = futs[fut]; done += 1
            try:
                n_ev, row, erow, eta_max = fut.result()
                results[d] = (row, erow, n_ev, eta_max)
                _say(f"[{done}/{len(designs)}] {d}: n_events={n_ev}")
            except Exception as ex2:
                failures.append([d, str(ex2)])
                _say(f"[{done}/{len(designs)}] SKIP {d}: {ex2}")

    if not results:
        sys.exit("no designs read successfully")
    etas = {round(v[3], 6) for v in results.values()}
    if len(etas) != 1:
        sys.exit(f"inconsistent inventory_eta_max across designs (mixed caches?): {sorted(etas)}")
    eta_max = etas.pop()
    obs = f"N_eta{eta_max:g}"
    labels = [f"{tok(p)}__{lo}_{hi}__{obs}" for (lo, hi) in bin_pairs for p in pids]
    nfeat = len(labels)

    ordered = sorted(results.keys(),
                     key=lambda d: (_design_index(d) if _design_index(d) is not None else 1 << 30, d))
    Y, Yerr, didx, dname = [], [], [], []
    for d in ordered:
        row, erow, n_ev, _ = results[d]
        if len(row) != nfeat:
            failures.append([d, f"feature count {len(row)} != {nfeat}"])
            continue
        Y.append(row); Yerr.append(erow)
        di = _design_index(d)
        didx.append(di if di is not None else -1); dname.append(d)
    if not Y:
        sys.exit("no designs produced a consistent-width row")
    Y = np.asarray(Y, float); Yerr = np.asarray(Yerr, float)
    didx = np.asarray(didx, int); dname = np.asarray(dname)
    labels = np.asarray(labels)

    out = os.path.abspath(a.out)
    if not out.endswith(".npz"):
        out += ".npz"
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, Y=Y, Y_err=Yerr, feature_labels=labels, design_index=didx, design_name=dname)

    meta_out = {
        "schema": "feeddown_yields/v1",
        "purpose": "per-PID inventory multiplicities for feeddown corrections; NOT a calibration target",
        "n_designs": int(Y.shape[0]), "n_features": int(Y.shape[1]),
        "row_order": "rows aligned to design_index / design_name (ascending by design number)",
        "pids": pids, "combine_antiparticles": combine,
        "estimator": a.estimator, "centrality_edges_pct": edges, "centrality_bins": bin_pairs,
        "feature_label_format": ("{pidTOKEN}__{cent_lo}_{cent_hi}__N_eta{etamax} "
                                 "(TOKEN = pid<code> single, pidpm<code> = particle+antiparticle summed)"),
        "value_is": (f"equal-event mean multiplicity per hydro event within |eta| < {eta_max:g} "
                     f"(inventory acceptance); zero-events included as 0. NOT dN/deta -- divide by "
                     f"{2 * eta_max:g} for a pseudorapidity density. Raw counts, for forming ratios downstream."),
        "Y_err_is": "diagonal per-observable SEM of the equal-event mean (analytic; matches n_boot=0 path)",
        "sigma_method": "sem",
        "inventory_eta_max": eta_max,
        "source": "per-PID inventory (inv_sub/inv_pdg/inv_vals) in cache_one_design/v3 caches; NOT the y-hist",
        "acceptance_note": ("inventory is |eta|<eta_max integrated with NO rapidity resolution; valid for "
                            "same-window ratios where acceptance cancels, NOT a substitute for dN/dy"),
        "ratio_sigma_note": ("dividing two of these yield columns treats them as independent and OVERESTIMATES "
                             "the ratio sigma (parent/product are event-correlated); reduce the ratio directly "
                             "if its uncertainty enters the likelihood"),
        "system": a.system, "n_failed": len(failures), "failed": failures,
        "join_note": "join to X / model_matrix on design_index (NON-contiguous), never a range()",
    }
    meta_path = out[:-len(".npz")] + ".meta.json"
    with open(meta_path, "w") as fh:
        json.dump(meta_out, fh, indent=2, default=str)
    _say(f"Wrote {out}  shape={tuple(Y.shape)}  ({len(failures)} failed)")
    _say(f"Meta:  {meta_path}")
    return 0


def _add_select_args(p):
    p.add_argument("--out-dir", required=True, help="Artifact output directory.")
    p.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT,
                   help="Published cache root (campaign.py supplies [pipeline] cache_cfs_root).")
    p.add_argument("--system", default=DEFAULT_SYSTEM)
    p.add_argument("--pattern", default="design_", help="Design dir name prefix (default 'design_').")
    p.add_argument("--start-index", type=int, default=None, help="Min trailing integer (inclusive).")
    p.add_argument("--end-index", type=int, default=None, help="Max trailing integer (inclusive).")
    p.add_argument("--designs", nargs="*", help="Restrict to these explicit design names.")


def main():
    ap = argparse.ArgumentParser(description="Per-design analysis campaign (local thread pool; dry-run by default).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("run", help="Discover and (with --submit) extract designs in parallel.")
    _add_select_args(rp)
    rp.add_argument("--jobs", type=int, default=8, help="Parallel designs (CFS-friendly; raise if I/O allows).")
    rp.add_argument("--force", action="store_true", help="Re-extract designs that already have artifacts.")
    rp.add_argument("--validate", action="store_true", help="Validate each design in-line (doubles cache I/O).")
    rp.add_argument("--allow-unverified", action="store_true")
    rp.add_argument("--et-eta-window", type=float, default=0.5)
    rp.add_argument("--zstd", default="zstd")
    rp.add_argument("--submit", action="store_true", help="Actually execute (otherwise dry-run).")
    rp.set_defaults(func=cmd_run)

    aup = sub.add_parser("audit", help="Report complete / missing / partial artifacts and failures.")
    _add_select_args(aup)
    aup.add_argument("--verbose", action="store_true")
    aup.set_defaults(func=cmd_audit)

    agp = sub.add_parser("aggregate",
                         help="Reduce all per-design analysis artifacts and stack into the emulator model matrix.")
    agp.add_argument("--out-dir", required=True,
                     help="Directory containing <design>_<system>_stageA.npz artifacts.")
    agp.add_argument("--system", default=DEFAULT_SYSTEM)
    agp.add_argument("--matrix-out", default=None,
                     help="Output .npz path (default <out-dir>/model_matrix.npz).")
    agp.add_argument("--estimator", default="cent_ch_eta10",
                     help="Centrality estimator column (default cent_ch_eta10, |eta|<1.0).")
    agp.add_argument("--bin-edges", type=float, nargs="*", default=None,
                     help="Centrality percentile edges (default 0,5,...,100 = 20 bins).")
    agp.add_argument("--y-window", type=float, default=0.5, help="Identified dN/dy half-window.")
    agp.add_argument("--charged-window", default="eta05", choices=["eta05", "eta10"])
    agp.add_argument("--n-boot", type=int, default=0,
                     help="Bootstrap resamples for diagonal sigma "
                          "(0 = analytic jackknife/SEM, fast; >0 = slower event bootstrap).")
    agp.add_argument("--start-index", type=int, default=None)
    agp.add_argument("--end-index", type=int, default=None)
    agp.add_argument("--no-csv", action="store_true", help="Skip the long-format inspection CSV.")
    agp.set_defaults(func=cmd_aggregate)

    fdp = sub.add_parser("feeddown",
                         help="Survey per-PID inventory multiplicities into a separate "
                              "design_index-keyed file (feeddown_yields.npz); does NOT touch model_matrix.")
    fdp.add_argument("--cache-root", default=DEFAULT_CACHE_ROOT,
                     help="Published cache root (campaign.py supplies [pipeline] cache_cfs_root).")
    fdp.add_argument("--system", default=DEFAULT_SYSTEM)
    fdp.add_argument("--pattern", default="design_", help="Design dir name prefix (default 'design_').")
    fdp.add_argument("--start-index", type=int, default=None, help="Min trailing integer (inclusive).")
    fdp.add_argument("--end-index", type=int, default=None, help="Max trailing integer (inclusive).")
    fdp.add_argument("--designs", nargs="*", help="Restrict to these explicit design names.")
    fdp.add_argument("--pids", type=int, nargs="+", required=True,
                     help="PDG codes to survey, e.g. --pids 3212 3122 2212 3222.")
    fdp.add_argument("--combine-antiparticles", action="store_true",
                     help="Sum each PID with its antiparticle (|PDG| match).")
    fdp.add_argument("--no-combine-antiparticles", dest="combine_antiparticles", action="store_false",
                     help="Keep signed PIDs separate, overriding shared config.")
    fdp.add_argument("--estimator", default="cent_ch_eta10",
                     help="Centrality estimator column (default cent_ch_eta10; matches model_matrix).")
    fdp.add_argument("--bin-edges", type=float, nargs="*", default=None,
                     help="Centrality percentile edges (default STAR 0,5,10,20,30,40,50,60,70,80). "
                          "Use the SAME edges as your model_matrix aggregate for bin-aligned ratios.")
    fdp.add_argument("--out", default="feeddown_yields.npz",
                     help="Output .npz path (default ./feeddown_yields.npz).")
    fdp.add_argument("--jobs", type=int, default=8, help="Parallel designs (CFS-I/O bound).")
    fdp.add_argument("--zstd", default="zstd")
    fdp.set_defaults(func=cmd_feeddown)

    for parser in (rp, aup, agp, fdp):
        parser.add_argument("--expected-fingerprint",
                            default=os.environ.get("HIC_EXPECTED_FINGERPRINT_V3"),
                            help="Selected scope; defaults to HIC_EXPECTED_FINGERPRINT_V3.")
    a = parse_workflow_args(ap, "analysis")
    a.expected_scope = sx.load_expected_fingerprint(a.expected_fingerprint)
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
