#!/usr/bin/env python3
"""
cache_reap_one_design.py -- per-design cache/publish/reap orchestration

A CONDUCTOR, not a new instrument. It sequences the hardened scripts
    cache_one_design.py  ->  publish_cache_to_cfs.py  ->  reap_raw_particle_lists.py
for ONE design/system, interprets their outcomes, and writes a structured per-design record.
It does NOT reimplement fingerprint checks, CFS deep verification, raw-deletion safety, or
event classification -- those remain owned by the underlying scripts. Every safety gate stays
where it is; the wrapper only sequences, routes, checks for anomalies, and records.

Two explicit subcommands with a hard seam at the irreversible action:

  cache   (produce + verify; NEVER deletes)
     route: if a valid CFS cache.verified already exists (fingerprint matches frozen expected)
              -> skip cache+publish, go straight to the reaper dry-run
            elif CFS cache.verified exists but fingerprint-mismatched or incomplete
              -> QUARANTINE (do not auto-overwrite)
            elif a valid local cache exists
              -> reuse it, publish
            else
              -> build local cache, publish
     then: reaper DRY-RUN -> parse --bucket-json -> clean/quarantine verdict JSON

  reap    (delete; NEVER caches/publishes)
     require: a clean cache verdict for this exact design/system/fingerprint
     then: rerun reaper DRY-RUN now (authoritative; world may have changed)
           if still clean -> reaper --apply
           rerun reaper DRY-RUN after apply
           success iff: reaper exit 0, would_delete == 0, no anomalous buckets
     write apply-result JSON

"Clean" buckets are defined DENY-BY-DEFAULT: only {would_delete, already_absent_expected,
no_surface_nothing_to_reap} are good; ANY other nonzero bucket is anomalous. A new bucket
added to the reaper later is therefore treated as anomalous until consciously allowlisted.

Campaign-invariant config (frozen expected fingerprint, CFS root, PDG paths) comes from one
place (the campaign submitter forwards these flags). Per-design params (design, system, target) are explicit.
Shortfall is never default: n_events < target -> quarantine unless --accept-shortfall + reason.

Run the wrapper INSIDE a batch job / interactive allocation (it is the job). No Slurm
submission/monitoring here; use submit_cache_reap_campaign.py.

PRECONDITION (intentional scope boundary): this wrapper assumes a QUIESCENT, TERMINAL-READY
design -- production complete, and any repair / completion sweeps
(repair_stale_events.py, fill) already done. It does NOT run those and
does NOT finish incomplete designs. A non-terminal design is expected to QUARANTINE here via a
cache/publish failure or an anomalous reaper bucket, rather than be silently mishandled.
Finishing/repairing designs is a separate sweep upstream of this wrapper.
"""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args
import time

# The ONLY buckets that are acceptable in a clean reaper dry-run. Everything else the reaper
# might emit (now or in future versions) is treated as anomalous -> quarantine.
# MUST stay aligned with the reaper's TERMINAL_OK_BUCKETS: the reaper removes whole event dirs on
# a clean reap, so AFTER an apply every reaped event classifies as skipped_no_event_dir (its dir
# is gone). That is the normal post-sweep state, hence allowlisted here. A genuinely incomplete
# reap is still caught independently by the post-apply would_delete != 0 check, not by this set.
GOOD_BUCKETS = frozenset({
    "would_delete", "already_absent_expected", "skipped_no_event_dir", "no_surface_nothing_to_reap"})


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def import_cache_fingerprint_hash(cache_script):
    """Dynamically import fingerprint_hash from the exact cache_one_design.py supplied, so the
    wrapper uses the SAME canonical hash function as the cache/publish/reaper -- one source of
    truth, no drifting copy. cache_one_design.py is import-safe (definitions only at module
    scope; work under main()/__main__)."""
    spec = importlib.util.spec_from_file_location("_hic_cache_one_design", cache_script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.fingerprint_hash


def compute_expected_fp_hash(cache_script, expected_fingerprint_path, explicit_hash=None):
    """Compute the expected fingerprint hash via the imported canonical function (single source
    of truth). --expected-fingerprint-hash, if supplied, is a CROSS-CHECK ONLY (not a fallback):
    it must match the computed value or this raises. Import failure is FATAL by design -- if the
    wrapper cannot import the cache script's hash function, it cannot trust that same script to
    build a correct cache, so there is intentionally no explicit-hash fallback; the caller
    quarantines."""
    fh = import_cache_fingerprint_hash(cache_script)
    computed = fh(load_json(expected_fingerprint_path))
    if explicit_hash is not None and explicit_hash != computed:
        raise ValueError(f"--expected-fingerprint-hash {explicit_hash} != computed {computed}")
    return computed


def load_json(path):
    with open(path) as f:
        return json.load(f)


def run(cmd, log_path):
    """Run a subprocess, tee stdout+stderr to a log file, return (returncode, combined_text)."""
    with open(log_path, "w") as logf:
        logf.write(f"# {now_iso()}  CMD: {' '.join(cmd)}\n")
        logf.flush()
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        logf.write(proc.stdout or "")
    return proc.returncode, (proc.stdout or "")


def anomalous_buckets(buckets):
    """Return the dict of nonzero buckets that are NOT in the good set (deny-by-default)."""
    return {k: v for k, v in buckets.items() if v and k not in GOOD_BUCKETS}


def buckets_or_none(doc):
    """Return the buckets dict from a reaper bucket-json doc, or None if missing/malformed.
    Callers quarantine on None rather than silently treating missing buckets as {}."""
    b = doc.get("buckets")
    return b if isinstance(b, dict) else None


def load_bucket_json_or_quarantine(path, quarantine, label):
    """Load a reaper bucket-json, quarantining (not crashing) on unreadable/corrupt JSON or a
    missing dict-valued 'buckets'. Returns (doc, buckets)."""
    if not os.path.isfile(path):
        quarantine(f"{label}: no bucket-json at {path}")
    try:
        doc = load_json(path)
    except (json.JSONDecodeError, OSError) as e:
        quarantine(f"{label}: bucket-json unreadable: {e}")
    buckets = buckets_or_none(doc)
    if buckets is None:
        quarantine(f"{label}: bucket-json missing dict-valued 'buckets'")
    return doc, buckets


def event_count_policy_error(cached_events, target_events, accept_shortfall,
                             n_dropped_bad_events=0):
    """SINGLE source of truth for the completeness/shortfall policy, applied identically
    whether we built a local cache or are skipping to an existing CFS cache. Returns an error
    string if the cache violates current policy, else None.
      cached == target            -> ok
      cached <  target            -> ok IF the deficit is fully explained by events DROPPED as
                                     non-salvageable at cache time (cached + n_dropped >= target,
                                     i.e. the design reached target terminal events); otherwise
                                     requires accept_shortfall (a fresh, explicit decision)
      cached >  target            -> always rejected (cache does not match design definition)
    Note: this enforces the CURRENT invocation's policy, NOT whatever a prior publish recorded,
    so an existing accepted-shortfall cache cannot bypass a wrapper run that did not opt in. The
    drop-explained acceptance mirrors publish_cache_to_cfs.py and reads n_dropped_bad_events from
    the same metadata/marker, so the wrapper does not quarantine what publish would accept."""
    if cached_events is None:
        return "cached_events missing"
    if cached_events > target_events:
        return f"cached_events {cached_events} > target {target_events} (cache exceeds design)"
    if cached_events < target_events:
        drop_explained = (n_dropped_bad_events > 0
                          and (cached_events + n_dropped_bad_events) >= target_events)
        if not drop_explained and not accept_shortfall:
            return (f"cached_events {cached_events} < target {target_events} and --accept-shortfall "
                    f"not passed this invocation")
    return None


# ========================================================================================
# Local-cache validity (cheap sanity checks only; publish remains the real gate).
# ========================================================================================
def local_cache_valid(local_cache, expected_fp_hash, raw_design, target_events,
                      accept_shortfall, malformed_threshold):
    """Return (ok: bool, reason: str|None, metadata: dict|None). Cheap checks against the
    local metadata.json; does NOT reimplement publish verification."""
    meta_path = os.path.join(local_cache, "metadata.json")
    if not os.path.isfile(meta_path):
        return False, "no metadata.json", None
    try:
        md = load_json(meta_path)
    except (json.JSONDecodeError, OSError) as e:
        return False, f"metadata.json unreadable: {e}", None

    if md.get("fingerprint_hash") != expected_fp_hash:
        return False, "fingerprint_hash != expected frozen scope", md
    # system_dir match using the SAME realpath semantics the reaper uses
    md_sysdir = md.get("system_dir")
    if not md_sysdir or os.path.realpath(md_sysdir) != os.path.realpath(raw_design):
        return False, f"system_dir {md_sysdir!r} != raw_design {raw_design!r}", md
    # validation must have actually happened (publish would reject otherwise; fail early/clearly)
    val = md.get("validation", {}) or {}
    if not val.get("raw_reference_enabled") or not (val.get("raw_reference_events", 0) > 0):
        return False, "cache not raw-reference-validated", md
    if md.get("unknown_visible_pdgs"):
        return False, f"unknown_visible_pdgs present: {md['unknown_visible_pdgs']}", md
    if md.get("missing_mass_baryon_pdgs"):
        return False, f"missing_mass_baryon_pdgs present: {md['missing_mass_baryon_pdgs']}", md
    mal = (md.get("malformed_oscar_rows", {}) or {}).get("total", 0)
    if mal > malformed_threshold:
        return False, f"malformed_oscar_rows.total {mal} > threshold {malformed_threshold}", md
    ec_err = event_count_policy_error(md.get("n_events"), target_events, accept_shortfall,
                                      md.get("n_dropped_bad_events", 0))
    if ec_err:
        return False, ec_err, md
    return True, None, md


def cfs_marker_state(cfs_cache, expected_fp_hash):
    """Classify the CFS destination: 'verified_match' | 'verified_mismatch' | 'incomplete'
    | 'absent' | 'unreadable'. Cheap routing check only; the reaper dry-run is authoritative."""
    if os.path.exists(os.path.join(cfs_cache, "cache.incomplete")):
        return "incomplete", None
    marker = os.path.join(cfs_cache, "cache.verified")
    if not os.path.isfile(marker):
        return "absent", None
    try:
        m = load_json(marker)
    except (json.JSONDecodeError, OSError):
        return "unreadable", None
    if m.get("fingerprint_hash") == expected_fp_hash and m.get("cache_valid") is True:
        return "verified_match", m
    return "verified_mismatch", m


# ========================================================================================
# Cache and publish.
# ========================================================================================
def cache_design(args):
    rec = {
        "stage": "stage1", "design": args.design, "system": args.system,
        "raw_design": os.path.abspath(args.raw_design),
        "local_cache": os.path.abspath(args.local_cache),
        "cfs_cache": os.path.abspath(args.cfs_cache),
        "target_events": args.target_events,
        "expected_fingerprint": os.path.abspath(args.expected_fingerprint),
        "timestamps": {"start": now_iso()},
        "commands": [], "logs": {},
        "cache_action": None, "verdict": None, "quarantine_reason": None,
    }
    os.makedirs(args.record_dir, exist_ok=True)

    # Compute the expected fingerprint hash via the cache script's own canonical function
    # (single source of truth). Import failure with no explicit fallback -> quarantine.
    try:
        expected_fp_hash = compute_expected_fp_hash(
            args.cache_script, args.expected_fingerprint,
            getattr(args, "expected_fingerprint_hash", None))
    except Exception as e:
        rec["verdict"] = "quarantined"
        rec["quarantine_reason"] = f"could not compute expected fingerprint hash: {e}"
        rec["timestamps"]["end"] = now_iso()
        write_record(args, rec, "stage1")
        print(f"QUARANTINE (cache): {rec['quarantine_reason']}", file=sys.stderr)
        sys.exit(2)
    rec["expected_fingerprint_hash"] = expected_fp_hash

    # effective malformed-row acceptance threshold (current policy, NOT the cache's build-time
    # allowed_threshold which is mere provenance): default to the build allowance if omitted.
    effective_malformed_threshold = (args.malformed_threshold
                                     if args.malformed_threshold is not None
                                     else args.allow_malformed_rows)
    rec["malformed_threshold"] = effective_malformed_threshold

    def quarantine(reason):
        rec["verdict"] = "quarantined"
        rec["quarantine_reason"] = reason
        rec["timestamps"]["end"] = now_iso()
        write_record(args, rec, "stage1")
        print(f"QUARANTINE (cache): {reason}", file=sys.stderr)
        sys.exit(2)

    # ---- preflight: shortfall acceptance must carry a reason (matches stated policy) ----
    if args.accept_shortfall and not args.shortfall_reason:
        quarantine("--accept-shortfall requires --shortfall-reason")

    # ---- route based on CFS state ----
    cfs_state, cfs_marker = cfs_marker_state(args.cfs_cache, expected_fp_hash)
    rec["cfs_state"] = cfs_state
    if cfs_state == "verified_match":
        # MUST enforce THIS invocation's design/system/target/shortfall policy before honoring
        # the skip -- an existing accepted-shortfall cache must NOT bypass a run that did not
        # opt in. Same policy as the build path (one decision, one code path).
        if cfs_marker.get("design_id") != args.design or cfs_marker.get("system") != args.system:
            quarantine(f"CFS cache.verified design/system "
                       f"({cfs_marker.get('design_id')}/{cfs_marker.get('system')}) "
                       f"!= invocation ({args.design}/{args.system})")
        if cfs_marker.get("target_events") != args.target_events:
            quarantine(f"CFS cache.verified target_events {cfs_marker.get('target_events')} "
                       f"!= invocation --target-events {args.target_events}")
        ec_err = event_count_policy_error(
            cfs_marker.get("cached_events"), args.target_events, args.accept_shortfall,
            cfs_marker.get("n_dropped_bad_events", 0))
        if ec_err:
            quarantine(f"CFS cache violates current event-count/shortfall policy: {ec_err}")
        rec["cache_action"] = "skip_cache_publish_cfs_already_verified"
        rec["cached_events"] = cfs_marker.get("cached_events")
        rec["fingerprint_hash"] = cfs_marker.get("fingerprint_hash")
    elif cfs_state in ("verified_mismatch", "incomplete", "unreadable"):
        if not args.overwrite_cfs:
            quarantine(f"CFS cache exists but state={cfs_state}; refusing to auto-overwrite. "
                       f"Inspect and pass --overwrite-cfs to replace.")
        rec["cache_action"] = f"overwrite_cfs_{cfs_state}"
    # ---- if not already verified on CFS, ensure a local cache then publish ----
    if cfs_state != "verified_match":
        ok, reason, md = local_cache_valid(
            args.local_cache, expected_fp_hash, args.raw_design, args.target_events,
            args.accept_shortfall, effective_malformed_threshold)
        if ok and not args.force_rebuild:
            rec["cache_action"] = rec["cache_action"] or "reuse_local_cache"
        else:
            if not ok and os.path.isfile(os.path.join(args.local_cache, "metadata.json")) \
                    and not args.force_rebuild and reason and "n_events" not in reason \
                    and "shortfall" not in reason:
                # an existing-but-invalid local cache (wrong scope/raw/validation) is a flag
                quarantine(f"existing local cache invalid: {reason}")
            # build
            rec["cache_action"] = "build_local_cache"
            cache_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_cache.log")
            cmd = [sys.executable, args.cache_script,
                   "--input-design", args.raw_design,
                   "--output-cache", args.local_cache,
                   "--num-events", str(args.num_events),
                   "--max-events", str(args.max_events),
                   "--start-index", str(args.start_index),
                   "--validate-events", str(args.validate_events),
                   "--nprocs", str(args.nprocs),
                   "--events-per-shard", str(args.events_per_shard),
                   "--overwrite", "--max-dropped-events", str(args.max_dropped_events)]
            if args.allow_malformed_rows:
                cmd += ["--allow-malformed-rows", str(args.allow_malformed_rows)]
            rec["commands"].append(" ".join(cmd)); rec["logs"]["cache"] = cache_log
            code, _ = run(cmd, cache_log)
            if code != 0:
                quarantine(f"cache build failed (rc={code}); see {cache_log}")
            ok, reason, md = local_cache_valid(
                args.local_cache, expected_fp_hash, args.raw_design, args.target_events,
                args.accept_shortfall, effective_malformed_threshold)
            if not ok:
                quarantine(f"freshly built local cache invalid: {reason}")

        # record cache facts from metadata
        if md:
            rec["cached_events"] = md.get("n_events")
            rec["fingerprint_hash"] = md.get("fingerprint_hash")
            rec["status_counts"] = md.get("status_counts")
            rec["malformed_oscar_rows"] = md.get("malformed_oscar_rows", {}).get("total")
            rec["n_zero_records"] = md.get("n_zero_records")
            rec["n_nonzero_records"] = md.get("n_nonzero_records")

        # ---- publish ----
        publish_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_publish.log")
        cmd = [sys.executable, args.publish_script,
               "--local-cache", args.local_cache,
               "--cfs-cache", args.cfs_cache,
               "--system", args.system,
               "--design-id", args.design,
               "--expected-fingerprint", args.expected_fingerprint,
               "--target-events", str(args.target_events),
               "--cache-script", args.cache_script]
        if args.pdg_mass:
            cmd += ["--pdg-mass", args.pdg_mass]
        if args.baryon_dict:
            cmd += ["--baryon-dict", args.baryon_dict]
        if args.accept_shortfall:
            cmd += ["--accept-shortfall"]
            if args.shortfall_reason:
                cmd += ["--shortfall-reason", args.shortfall_reason]
        if cfs_state in ("verified_mismatch", "incomplete", "unreadable") and args.overwrite_cfs:
            cmd += ["--overwrite"]
        rec["commands"].append(" ".join(cmd)); rec["logs"]["publish"] = publish_log
        code, _ = run(cmd, publish_log)
        if code != 0:
            quarantine(f"publish failed (rc={code}); see {publish_log}")

    # ---- reaper dry-run ----
    bucket_json = os.path.join(args.record_dir, f"{args.design}_{args.system}_dryrun_buckets.json")
    dry_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_dryrun.log")
    cmd = [sys.executable, args.reaper_script,
           "--cfs-cache", args.cfs_cache,
           "--raw-design", args.raw_design,
           "--expected-fingerprint", args.expected_fingerprint,
           "--bucket-json", bucket_json]
    if args.tmp_dir:
        cmd += ["--tmp-dir", args.tmp_dir]
    rec["commands"].append(" ".join(cmd)); rec["logs"]["dryrun"] = dry_log
    code, _ = run(cmd, dry_log)
    if code != 0:
        quarantine(f"reaper dry-run failed (rc={code}); see {dry_log}")
    bdoc, dry_buckets = load_bucket_json_or_quarantine(bucket_json, quarantine, "reaper dry-run")
    rec["dryrun_buckets"] = dry_buckets
    rec["dryrun_fingerprint_hash"] = bdoc.get("fingerprint_hash")
    anomalies = anomalous_buckets(dry_buckets)
    if anomalies:
        quarantine(f"reaper dry-run has anomalous buckets: {anomalies}")

    rec["verdict"] = "clean"
    rec["timestamps"]["end"] = now_iso()
    write_record(args, rec, "stage1")
    print(f"CACHE CLEAN: {args.design}/{args.system}. "
          f"would_delete={dry_buckets.get('would_delete', 0)}. "
          f"Verdict written. Review, then run reap --apply.")


# ========================================================================================
# Reap raw events.
# ========================================================================================
def reap_design(args):
    rec = {
        "stage": "stage2", "design": args.design, "system": args.system,
        "raw_design": os.path.abspath(args.raw_design),
        "cfs_cache": os.path.abspath(args.cfs_cache),
        "timestamps": {"start": now_iso()},
        "commands": [], "logs": {}, "verdict": None, "quarantine_reason": None,
    }
    os.makedirs(args.record_dir, exist_ok=True)

    def quarantine(reason):
        rec["verdict"] = "quarantined"
        rec["quarantine_reason"] = reason
        rec["timestamps"]["end"] = now_iso()
        write_record(args, rec, "stage2")
        print(f"QUARANTINE (reap): {reason}", file=sys.stderr)
        sys.exit(2)

    # ---- require a clean cache verdict for this exact design/system ----
    cache_record_path = record_path(args, "stage1")
    if not os.path.isfile(cache_record_path):
        quarantine(f"no cache record at {cache_record_path}; run cache first")
    cache_record = load_json(cache_record_path)
    rec["stage1_verdict_file"] = cache_record_path
    if cache_record.get("verdict") != "clean":
        quarantine(f"cache verdict is not clean: {cache_record.get('verdict')}")
    if cache_record.get("design") != args.design or cache_record.get("system") != args.system:
        quarantine("cache record design/system mismatch")
    # cheap clarity prechecks: the invocation paths must match what cache recorded (the fresh
    # reaper dry-run is still authoritative; this just makes a misconfigured run fail clearly)
    for key, cur in (("raw_design", args.raw_design), ("cfs_cache", args.cfs_cache),
                     ("expected_fingerprint", args.expected_fingerprint)):
        cached_value = cache_record.get(key)
        if cached_value and os.path.realpath(cached_value) != os.path.realpath(cur):
            quarantine(f"cache record {key} ({cached_value}) != invocation ({cur})")
    cache_fingerprint = cache_record.get("dryrun_fingerprint_hash") or cache_record.get("fingerprint_hash")

    # ---- cheap pre-check: current CFS marker fingerprint matches cache's ----
    cfs_state, cfs_marker = cfs_marker_state(args.cfs_cache, cache_fingerprint)
    rec["cfs_state"] = cfs_state
    if cfs_state != "verified_match":
        quarantine(f"current CFS state {cfs_state} != verified_match against cache fingerprint "
                   f"(cache changed since verification?)")

    # ---- authoritative: rerun reaper dry-run NOW ----
    pre_json = os.path.join(args.record_dir, f"{args.design}_{args.system}_pre_apply_buckets.json")
    pre_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_pre_apply.log")
    cmd = [sys.executable, args.reaper_script,
           "--cfs-cache", args.cfs_cache, "--raw-design", args.raw_design,
           "--expected-fingerprint", args.expected_fingerprint, "--bucket-json", pre_json]
    if args.tmp_dir:
        cmd += ["--tmp-dir", args.tmp_dir]
    rec["commands"].append(" ".join(cmd)); rec["logs"]["pre_apply_dryrun"] = pre_log
    code, _ = run(cmd, pre_log)
    if code != 0:
        quarantine(f"pre-apply dry-run failed (rc={code}); see {pre_log}")
    pre, pre_buckets = load_bucket_json_or_quarantine(pre_json, quarantine, "pre-apply dry-run")
    rec["pre_apply_buckets"] = pre_buckets
    pre_anom = anomalous_buckets(pre_buckets)
    if pre_anom:
        quarantine(f"pre-apply dry-run no longer clean: {pre_anom}")
    pre_would = pre_buckets.get("would_delete", 0)

    # ---- apply ----
    apply_json = os.path.join(args.record_dir, f"{args.design}_{args.system}_apply_buckets.json")
    apply_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_apply.log")
    cmd = [sys.executable, args.reaper_script,
           "--cfs-cache", args.cfs_cache, "--raw-design", args.raw_design,
           "--expected-fingerprint", args.expected_fingerprint, "--bucket-json", apply_json,
           "--apply", "--confirm-design", args.design, "--confirm-system", args.system]
    if args.tmp_dir:
        cmd += ["--tmp-dir", args.tmp_dir]
    rec["commands"].append(" ".join(cmd))
    rec["logs"]["apply"] = apply_log
    rec["logs"]["apply_buckets"] = apply_json
    code, _ = run(cmd, apply_log)
    rec["apply_returncode"] = code
    if os.path.isfile(apply_json):
        try:
            rec["apply_buckets"] = load_json(apply_json).get("buckets")
        except (json.JSONDecodeError, OSError):
            pass
    if code != 0:
        quarantine(f"reaper --apply failed (rc={code}); see {apply_log}")

    # ---- post-apply dry-run ----
    post_json = os.path.join(args.record_dir, f"{args.design}_{args.system}_post_apply_buckets.json")
    post_log = os.path.join(args.record_dir, f"{args.design}_{args.system}_post_apply.log")
    cmd = [sys.executable, args.reaper_script,
           "--cfs-cache", args.cfs_cache, "--raw-design", args.raw_design,
           "--expected-fingerprint", args.expected_fingerprint, "--bucket-json", post_json]
    if args.tmp_dir:
        cmd += ["--tmp-dir", args.tmp_dir]
    rec["commands"].append(" ".join(cmd)); rec["logs"]["post_apply_dryrun"] = post_log
    code, _ = run(cmd, post_log)
    if code != 0:
        quarantine(f"post-apply dry-run failed (rc={code}); see {post_log}")
    post, post_buckets = load_bucket_json_or_quarantine(post_json, quarantine, "post-apply dry-run")
    rec["post_apply_buckets"] = post_buckets

    # ---- core success criterion (simple, authoritative): exit 0, no would_delete, no anomalies ----
    post_anom = anomalous_buckets(post_buckets)
    post_would = post_buckets.get("would_delete", 0)
    if post_would != 0:
        quarantine(f"post-apply would_delete={post_would} (apply did not finish as expected)")
    if post_anom:
        quarantine(f"post-apply anomalous buckets: {post_anom}")

    # ---- transition observation: NON-GATING warning only (avoid fragile overconstraint) ----
    # After a clean reap the reaper removes whole event dirs, so events that were would_delete
    # pre-apply now classify as skipped_no_event_dir (dir gone), NOT already_absent_expected
    # (which was the pre-dir-sweep signature of a reaped event). Count both so the warning fires
    # only on a genuine shortfall, not on the normal dir-swept outcome.
    post_reaped = (post_buckets.get("skipped_no_event_dir", 0)
                   + post_buckets.get("already_absent_expected", 0))
    rec["transition_check"] = {
        "pre_apply_would_delete": pre_would,
        "post_apply_reaped_event_dirs": post_reaped,
        "consistent": (post_reaped >= pre_would),
    }
    if post_reaped < pre_would:
        rec["transition_warning"] = (
            f"post reaped-event count ({post_reaped}) < pre would_delete ({pre_would}); "
            f"fewer events show as reaper-deleted than were planned. Investigate, but this is "
            f"NOT a hard failure (core criterion satisfied).")
        print("WARNING:", rec["transition_warning"], file=sys.stderr)

    rec["verdict"] = "applied_clean"
    rec["timestamps"]["end"] = now_iso()
    write_record(args, rec, "stage2")
    print(f"REAP APPLIED CLEAN: {args.design}/{args.system}. "
          f"would_delete now 0; raw reaped. Record written.")


# ========================================================================================
# Records.
# ========================================================================================
# Record files keep the persisted "stage1" (cache) / "stage2" (reap) tokens so that
# existing campaign records stay readable.


def record_path(args, stage):
    return os.path.join(args.record_dir, f"{args.design}_{args.system}_{stage}.json")


def write_record(args, rec, stage):
    p = record_path(args, stage)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rec, f, indent=2, sort_keys=True)
    os.replace(tmp, p)


# ========================================================================================
# CLI.
# ========================================================================================
def add_common_base(ap):
    """Args shared by both actions (reap does not need --local-cache)."""
    ap.add_argument("--design", required=True)
    ap.add_argument("--system", required=True)
    ap.add_argument("--raw-design", required=True)
    ap.add_argument("--cfs-cache", required=True)
    ap.add_argument("--expected-fingerprint", required=True)
    ap.add_argument("--expected-fingerprint-hash", default=None,
                    help="Optional diagnostic override. If given, must match the hash the "
                         "wrapper computes from --expected-fingerprint via the cache script's "
                         "canonical function; mismatch is a hard error. Normally omit it.")
    ap.add_argument("--record-dir", required=True)
    ap.add_argument("--reaper-script", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "reap_raw_particle_lists.py"))
    ap.add_argument("--cache-script", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache_one_design.py"),
                    help="Path to cache_one_design.py (also the source of the canonical "
                         "fingerprint_hash the wrapper imports).")
    ap.add_argument("--tmp-dir", default=None)


def main():
    ap = argparse.ArgumentParser(description="Per-design cache+publish+reap orchestration.")
    sub = ap.add_subparsers(dest="mode", required=True, metavar="{cache,reap}")

    p1 = sub.add_parser("cache", help="produce + verify (never deletes)")
    add_common_base(p1)
    p1.add_argument("--local-cache", required=True)
    p1.add_argument("--publish-script", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "publish_cache_to_cfs.py"))
    p1.add_argument("--target-events", type=int, required=True)
    p1.add_argument("--num-events", type=int, default=10000)
    p1.add_argument("--max-events", type=int, default=10000)
    p1.add_argument("--start-index", type=int, default=0)
    p1.add_argument("--validate-events", type=int, default=50)
    p1.add_argument("--nprocs", type=int, default=16)
    p1.add_argument("--events-per-shard", type=int, default=50)
    p1.add_argument("--pdg-mass", default=os.environ.get("HIC_PDG_MASS"))
    p1.add_argument("--baryon-dict", default=os.environ.get("HIC_BARYON_DICT"))
    p1.add_argument("--malformed-threshold", type=int, default=None,
                    help="Max malformed_oscar_rows.total for a local cache to be reusable "
                         "(current policy). Defaults to --allow-malformed-rows if omitted. The "
                         "cache's recorded build-time allowed_threshold is provenance, not used "
                         "as the acceptance gate.")
    p1.add_argument("--allow-malformed-rows", type=int,
                    default=int(os.environ.get("HIC_ALLOW_MALFORMED_ROWS", "0")),
                    help="Max malformed OSCAR rows to tolerate; used both as this wrapper's "
                         "local-cache acceptance gate (via --malformed-threshold default) and "
                         "passed to the cache script when building. Defaults to the "
                         "HIC_ALLOW_MALFORMED_ROWS environment variable (else 0).")
    p1.add_argument("--force-rebuild", action="store_true",
                    help="Rebuild the local cache even if a valid one exists.")
    p1.add_argument("--overwrite-cfs", action="store_true",
                    help="Permit overwriting a mismatched/incomplete CFS cache (else quarantine).")
    p1.add_argument("--accept-shortfall", action="store_true")
    p1.add_argument("--shortfall-reason", default=None)

    p2 = sub.add_parser("reap", help="delete (never caches/publishes)")
    add_common_base(p2)
    p2.add_argument("--apply", action="store_true",
                    help="Required to actually delete. Without it, reap refuses.")

    p1.add_argument("--max-dropped-events", type=int,
                    default=int(os.environ.get("HIC_MAX_DROPPED_EVENTS", "0")))

    args = parse_workflow_args(ap, "design-cache")
    if args.mode == "cache":
        # Provenance-table selections must also reach the cache parser, which
        # reads these environment variables when its subprocess starts.
        for key, name in (("pdg_mass", "HIC_PDG_MASS"), ("baryon_dict", "HIC_BARYON_DICT")):
            value = getattr(args, key)
            if value:
                value = os.path.abspath(os.path.expandvars(os.path.expanduser(value)))
                setattr(args, key, value)
                os.environ[name] = value
        cache_design(args)
    elif args.mode == "reap":
        if not args.apply:
            raise SystemExit("reap requires --apply (it is the irreversible delete stage).")
        reap_design(args)


if __name__ == "__main__":
    main()
