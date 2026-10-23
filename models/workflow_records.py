"""Shared cache/reap record loading and classification.

These are predicates over historical records, not fresh cache verification.
The wrapper/reaper remain responsible for all execution and deletion gates.
"""

import datetime
import glob
import importlib.util
import json
import os
import re

def _import_good_buckets(wrapper_script):
    """Import GOOD_BUCKETS from the wrapper so the summarizer's clean-record bucket check uses
    the SAME allowlist as the wrapper (one source of truth, no drifting copy). The wrapper
    imports cleanly (argparse only under main()). Refuse if the authoritative
    policy cannot be loaded; a copied fallback can silently drift."""
    try:
        spec = importlib.util.spec_from_file_location("_cache_reap_wrapper", wrapper_script)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return frozenset(mod.GOOD_BUCKETS), None
    except Exception as e:
        raise SystemExit(f"Cannot load wrapper bucket policy from {wrapper_script}: {e}")

# Known cache quarantine reason patterns -> subcategory. Start SMALL; add only as the
# campaign reveals real cases. Anything unmatched is novel_quarantine (deny-by-default).
KNOWN_CACHE_PATTERNS = [
    ("known_cfs_incomplete",        re.compile(r"state=incomplete", re.I)),
    ("known_cfs_mismatch",          re.compile(r"state=verified_mismatch|fingerprint.*mismatch|"
                                               r"target_events .* != invocation|design/system "
                                               r"\(.*\) != invocation", re.I)),
    ("known_fingerprint_config",    re.compile(r"could not compute expected fingerprint hash|"
                                               r"fingerprint_hash != expected", re.I)),
    ("known_shortfall_not_accepted", re.compile(r"shortfall.*not (passed|accepted)|"
                                                r"cached_events \d+ < target|"
                                                r"n_events \d+ < target", re.I)),
    ("known_count_exceeds_design",  re.compile(r"cached_events \d+ > target|n_events \d+ > target",
                                               re.I)),
    ("known_shortfall_reason_missing", re.compile(r"--accept-shortfall requires --shortfall-reason",
                                                  re.I)),
    ("known_lock_or_not_quiescent", re.compile(r"skipped_lock_present|smash\.lock|not quiescent",
                                               re.I)),
    ("known_cache_build_failed",    re.compile(r"cache build failed", re.I)),
    ("known_publish_failed",        re.compile(r"publish failed", re.I)),
    # NOTE: deliberately NO generic "anomalous buckets" pattern -- an anomalous-bucket quarantine
    # means the reaper hit a state the wrapper's allowlist did not expect, which is exactly what
    # must stay NOVEL (deny-by-default). If a SPECIFIC bucket later proves benign+recurrent, add
    # that specific bucket name here, never the generic wrapper string.
]

# Reap quarantine reasons that indicate a genuinely STALE cache verdict -- the design's CFS
# state or fingerprint CHANGED between cache and reap (e.g. republished). These are safely
# resolved by re-running cache. NOT included here (deliberately): path/record mismatches, which
# are operator/config errors (reap pointed at the wrong place), and route to inspect instead.
REAP_STALE_PATTERNS = [
    re.compile(r"!= verified_match against (?:cache|stage1) fingerprint", re.I),
    re.compile(r"current CFS state .* != verified_match", re.I),
]

# Fraction of cache quarantines that, if fingerprint/scope-mismatch, triggers the mass-
# misconfiguration warning.
MASS_MISCONFIG_FRACTION = 0.5


def load_records(record_dir):
    """Load cache/reap records (stored as *_stage1.json / *_stage2.json).

    Returns (cache_records, reap_records, duplicates, bad_records), keyed by
    (design, system). Unreadable/corrupt record files are collected
    and surfaced loudly by the caller (a corrupt record means a design silently vanishes from
    the summary, which breaks the checkpoint's promise to account for every design)."""
    cache_records, reap_records = {}, {}
    dups = []
    bad_records = []
    # Numeric filename and JSON classification tokens are persisted formats.
    pat = os.path.join(record_dir, '**', '*_stage[12].json')
    for path in sorted(glob.glob(pat, recursive=True)):
        stage, store = ('stage1', cache_records) if path.endswith('_stage1.json') else ('stage2', reap_records)
        try:
            with open(path) as stream:
                d = json.load(stream)
            if not isinstance(d, dict) or any(
                    not isinstance(d.get(k), str) or not d[k] for k in ('design', 'system')):
                raise ValueError('record must have nonempty design and system strings')
        except (ValueError, OSError) as e:
            bad_records.append((path, str(e)))
            continue
        key = (d.get("design"), d.get("system"))
        if key in store:
            dups.append((stage, key, path, store[key][1]))
        store[key] = (d, path)
    return cache_records, reap_records, dups, bad_records


def classify_history(first, second, good_buckets):
    """Return final (classification, reason), using one chronology policy everywhere."""
    if restaged_after_quarantine(first, second):
        second = None
    if second:
        record = second[0]
        category, reason = classify_reap(record, good_buckets)
    elif first:
        record = first[0]
        category, reason = classify_cache(record, good_buckets)
        if category == 'known_quarantine':
            category += ':' + reason
            reason = None
    else:
        return None, None
    return category, reason or record.get('quarantine_reason')


def classify_cache_quarantine(reason):
    if not reason:
        return "novel_quarantine", None
    for sub, pat in KNOWN_CACHE_PATTERNS:
        if pat.search(reason):
            return "known_quarantine", sub
    return "novel_quarantine", None


def is_reap_stale(reason):
    if not reason:
        return False
    return any(p.search(reason) for p in REAP_STALE_PATTERNS)


def classify_cache(rec, good_buckets):
    v = rec.get("verdict")
    if v == "clean":
        b = rec.get("dryrun_buckets") or {}
        # Consistency check: a 'clean' record must not carry any anomalous (non-good) nonzero
        # bucket. If it does, the record is internally inconsistent (bug / partial write / hand
        # edit) -> treat as novel, do NOT propagate 'clean' into the reap manifest.
        bad = {k: val for k, val in b.items() if val and k not in good_buckets}
        if bad:
            return "novel_quarantine", f"clean record has anomalous buckets: {bad}"
        wd = b.get("would_delete", 0)
        aae = b.get("already_absent_expected", 0)
        nsr = b.get("no_surface_nothing_to_reap", 0)
        if wd > 0:
            return "clean_ready_for_stage2", None
        if aae > 0:
            return "clean_already_reaped", None
        if nsr > 0:
            # would_delete==0, no already-reaped evidence, but everything is no-surface: benign
            # (a peripheral/no-surface design), distinct from a truly unexplained zero.
            return "clean_nothing_to_reap", None
        return "clean_zero_unexpected", None
    if v == "quarantined":
        cat, sub = classify_cache_quarantine(rec.get("quarantine_reason"))
        return cat, sub
    return "novel_quarantine", None  # unexpected verdict -> treat as novel


def classify_reap(rec, good_buckets):
    v = rec.get("verdict")
    if v == "applied_clean":
        # Consistency check: applied_clean must have post-apply would_delete==0 and no anomalous
        # buckets, else the record contradicts its own verdict -> inspect.
        pb = rec.get("post_apply_buckets") or {}
        bad = {k: val for k, val in pb.items() if val and k not in good_buckets}
        pwd = pb.get("would_delete", 0)
        if pwd != 0:
            return "stage2_failed_or_anomalous", f"applied_clean but post would_delete={pwd}"
        if bad:
            return "stage2_failed_or_anomalous", f"applied_clean but anomalous post buckets: {bad}"
        return "applied_clean", None
    if v == "quarantined":
        if is_reap_stale(rec.get("quarantine_reason")):
            return "stage2_stale_needs_restage1", None
        return "stage2_failed_or_anomalous", None
    return "stage2_failed_or_anomalous", None


def restaged_after_quarantine(cache_record, reap_record):
    """A fresh clean cache record permits another attempt after a failed reap.

    A successful reap always remains final. Missing/invalid chronology is left for
    review. File mtimes resolve records written within the same timestamp second.
    The per-design wrapper still re-verifies the cache independently before reap.
    """
    if not cache_record or not reap_record:
        return False
    first, first_path = cache_record
    second, second_path = reap_record
    if first.get('verdict') != 'clean' or second.get('verdict') != 'quarantined':
        return False
    try:
        t1 = datetime.datetime.fromisoformat(first['timestamps']['end'].replace('Z', '+00:00'))
        t2 = datetime.datetime.fromisoformat(second['timestamps']['end'].replace('Z', '+00:00'))
        return t1 > t2 or (t1 == t2 and os.stat(first_path).st_mtime_ns > os.stat(second_path).st_mtime_ns)
    except (KeyError, TypeError, ValueError, OSError):
        return False

