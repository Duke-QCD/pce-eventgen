#!/usr/bin/env python3
"""
summarize_cache_reap_records.py: the cache/reap checkpoint

Aggregates per-design cache_reap_one_design.py cache/reap JSON records into a classified
summary, and emits the reap manifest. This is the SAFETY-CRITICAL CHECKPOINT: at campaign
scale a human reviews the *triage*, not 2000 raw rows, so the classification must be exact and
DENY-BY-DEFAULT -- any quarantine reason that does not match a known pattern is 'novel' and is
surfaced loudly, never bucketed into a catch-all "other known".

It does NOT reimplement reaper logic; it only reads wrapper records. It never deletes anything.

Classification (exact predicates over record fields):
  cache (stored classification tokens retained):
    clean_ready_for_stage2 : verdict==clean AND dryrun_buckets.would_delete > 0
    clean_already_reaped   : verdict==clean AND would_delete==0 AND already_absent_expected > 0
    clean_zero_unexpected  : verdict==clean AND would_delete==0 AND already_absent_expected==0
                             (clean but nothing to do and no prior-reap evidence -- inspect)
    known_quarantine:<sub> : verdict==quarantined AND reason matches a known pattern
    novel_quarantine       : verdict==quarantined AND reason matches NOTHING known
  reap (stored classification tokens retained):
    applied_clean          : verdict==applied_clean
    stage2_stale_needs_restage1 : quarantined AND reason matches a *stale* pattern
    stage2_failed_or_anomalous  : quarantined AND not stale (incl. novel) -> inspect

Mass-misconfig guard: if a large fraction of cache quarantines are fingerprint/scope
mismatches, the likely cause is the frozen fingerprint file / cache roots, not the designs.
"""

import argparse
import json
import os
import sys
from collections import defaultdict

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_config import parse_args as parse_workflow_args


from workflow_records import (
    _import_good_buckets, load_records, classify_history, MASS_MISCONFIG_FRACTION,
)
from workflow_status import ACTIONS, add_output_args, record_status, report


def main():
    ap = argparse.ArgumentParser(description="Review cache/reap records and prepare a clean-only reap manifest.")
    ap.add_argument("--record-dir", required=True)
    ap.add_argument('--system', help='Review only this system (shared config supplies it).')
    ap.add_argument("--wrapper-script", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "cache", "cache_reap_one_design.py"),
                    help="Path to cache_reap_one_design.py (source of the GOOD_BUCKETS allowlist "
                         "used to validate clean records).")
    ap.add_argument("--reap-manifest", default=None,
                    help="Write clean-only ready-to-reap designs (design system) to this path.")
    ap.add_argument("--show", default="all",
                    choices=["all", "quarantines", "novel", "ready"],
                    help="Which rows to print.")
    ap.add_argument("--json", help="Write full classified records and reasons as JSON.")
    add_output_args(ap)
    args = parse_workflow_args(ap, "summary")
    if args.max_rows < 0:
        ap.error("--max-rows must be nonnegative")
    diagnostic_limit = args.max_rows if args.details else 10

    good_buckets, gb_note = _import_good_buckets(args.wrapper_script)
    if gb_note:
        print(f"*** WARNING: {gb_note}", file=sys.stderr)

    cache_records, reap_records, dups, bad_records = load_records(args.record_dir)
    if args.system:
        cache_records = {key: val for key, val in cache_records.items() if key[1] == args.system}
        reap_records = {key: val for key, val in reap_records.items() if key[1] == args.system}
        dups = [item for item in dups if item[1][1] == args.system]
    if not cache_records and not reap_records and not bad_records:
        raise SystemExit(f"No cache/reap records under {args.record_dir}")

    # surface duplicate keys (last-loaded wins; a campaign record-dir should have each once)
    if dups:
        print(f"*** WARNING: {len(dups)} duplicate (design,system) record(s) -- only the "
              f"last-loaded is used. A campaign record-dir should have each design once. ***",
              file=sys.stderr)
        for stage, key, newp, oldp in (dups[:diagnostic_limit] if diagnostic_limit else dups):
            print(f"    {stage} {key[0]}/{key[1]}: {newp} overrides {oldp}", file=sys.stderr)

    # surface unreadable/corrupt records LOUDLY -- a dropped record means a design silently
    # vanishes from the summary, which breaks the checkpoint's account-for-every-design promise
    if bad_records:
        print(f"\n*** {len(bad_records)} UNREADABLE/CORRUPT RECORD FILE(S) -- STOP: the summary "
              f"below does NOT account for these designs. ***", file=sys.stderr)
        for path, err in (bad_records[:diagnostic_limit] if diagnostic_limit else bad_records):
            print(f"    {path}: {err}", file=sys.stderr)

    cats = defaultdict(list)   # category -> list of (design, system, detail)
    known_subcounts = defaultdict(int)
    rows = []
    duplicate_keys = {item[1] for item in dups}

    keys = sorted(set(cache_records) | set(reap_records))
    for key in keys:
        design, system = key
        s1rec = cache_records.get(key, (None, None))[0]
        final, surfaced_reason = classify_history(cache_records.get(key), reap_records.get(key), good_buckets)
        cat, _, sub = final.partition(':')
        cats[cat].append((design, system, surfaced_reason))
        if cat == 'known_quarantine':
            known_subcounts[sub] += 1
        state = record_status(final)
        if key in duplicate_keys:
            state, surfaced_reason = 'review', 'Duplicate records; history is ambiguous'
        rows.append(dict(design=design, system=system, classification=final,
                         status=state, next_action=ACTIONS[state],
                         cache_action=(s1rec or {}).get('cache_action'),
                         would_delete=((s1rec or {}).get('dryrun_buckets') or {}).get('would_delete'),
                         status_reason=surfaced_reason or final.replace('_', ' '),
                         record_paths=[item[1] for item in (cache_records.get(key), reap_records.get(key)) if item]))

    # ---- table ----
    def want(final):
        if args.show == "all":
            return True
        if args.show == "ready":
            return final == "clean_ready_for_stage2"
        if args.show == "quarantines":
            return "quarantine" in final or "stage2_failed" in final or "stale" in final
        if args.show == "novel":
            return "novel" in final or final == "stage2_failed_or_anomalous"
        return True

    report([r for r in rows if want(r['classification'])], 'Cache/reap review',
           args.only, args.details, args.max_rows)
    print('Recorded readiness only; the reap command independently verifies again before deletion.')
    if args.details:
        print('Record classifications: ' + ', '.join(f'{cat}={len(items)}' for cat, items in sorted(cats.items())))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        tmp = args.json + '.tmp'
        with open(tmp, 'w') as stream:
            json.dump(dict(rows=rows, bad_records=bad_records, duplicate_records=dups), stream, indent=2)
        os.replace(tmp, args.json)
        print(f'Full review: {args.json}')

    # ---- mass-misconfiguration guard ----
    cache_quarantines = [k for k in cats if k in ("known_quarantine", "novel_quarantine")]
    n_quar = sum(len(cats[k]) for k in cache_quarantines)
    n_scope = (known_subcounts.get("known_cfs_mismatch", 0)
               + known_subcounts.get("known_fingerprint_config", 0))
    if n_quar and n_scope / n_quar >= MASS_MISCONFIG_FRACTION:
        print(f"\n*** WARNING: {n_scope}/{n_quar} cache quarantines are fingerprint/scope "
              f"mismatches. The likely cause is campaign CONFIGURATION (frozen expected "
              f"fingerprint file / cache roots / HIC_EXPECTED_FINGERPRINT_V3), NOT the "
              f"individual designs. Verify config before taking design-level action or "
              f"passing --overwrite-cfs. ***", file=sys.stderr)

    # ---- novel quarantine loud surfacing ----
    novel = cats.get("novel_quarantine", []) + cats.get("stage2_failed_or_anomalous", [])
    if novel:
        print(f"\n*** {len(novel)} NOVEL / unclassified quarantine(s) -- STOP and inspect: ***",
              file=sys.stderr)
        for design, system, reason in (novel[:diagnostic_limit] if diagnostic_limit else novel):
            print(f"    {design}/{system}: {reason}", file=sys.stderr)

    # ---- reap manifest from the persisted clean_ready_for_stage2 classification ----
    if args.reap_manifest:
        if bad_records or dups or novel:
            print("Refusing to write a reap manifest from an incomplete or ambiguous summary.", file=sys.stderr)
            sys.exit(4 if bad_records or dups else 3)
        ready = cats.get("clean_ready_for_stage2", [])
        Path(args.reap_manifest).parent.mkdir(parents=True, exist_ok=True)
        tmp = args.reap_manifest + ".tmp"
        with open(tmp, "w") as f:
            for design, system, _ in ready:
                f.write(f"{design} {system}\n")
        os.replace(tmp, args.reap_manifest)
        print(f"\nReap manifest: {len(ready)} ready_to_reap design(s) -> {args.reap_manifest}")

    # ---- exit codes for campaign-driver gating (bad records take precedence) ----
    if bad_records or dups:
        sys.exit(4)   # corrupt/unreadable records: summary is incomplete, STOP
    if novel:
        sys.exit(3)   # novel/unclassified quarantines: STOP and inspect


if __name__ == "__main__":
    main()
