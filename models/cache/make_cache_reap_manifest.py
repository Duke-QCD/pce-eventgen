#!/usr/bin/env python3
"""
make_cache_reap_manifest.py

Produces the cache campaign manifest: one "design system" row per design that is
  (a) QUIESCENT -- no afterburner.lock/smash.lock in expected events, from the raw survey
  (b) TERMINAL-READY -- terminal events (finished particles + hydro.no_surface) meet threshold
      and no disqualifying raw-state anomalies are present
  (c) NOT already done -- excludes designs whose wrapper records show applied_clean and, by
      default, designs already cache-clean/produced

It requires BOTH inputs:
  - survey JSON from survey_raw_designs.py for raw readiness/quiescence
  - wrapper records for cache/reap history (applied_clean, cache clean, quarantines)

This tool only READS. It writes a manifest text file. It never caches, publishes, or deletes.
"""

import argparse
import json
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args
from workflow_records import _import_good_buckets, load_records
from workflow_status import add_output_args, annotate_row, report


def load_survey(survey_json):
    with open(survey_json) as f:
        doc = json.load(f)
    return doc.get("rows", []), doc


def terminal_frac(row):
    if row.get("terminal_frac") is not None:
        return row.get("terminal_frac")
    return row.get("finished_frac")


def ready_error(row, min_terminal_frac, target_events=None):
    """Return None if the survey row is ready for caching; otherwise a reason string.

    There are two intended modes:
      strict mode (min_terminal_frac >= 1.0): every expected event must be terminal and
        no raw-state anomalies may remain.
      shortfall mode (min_terminal_frac < 1.0): failed/incomplete/completed_zero/missing
        events lower the terminal fraction rather than hard-disqualifying. This lets the
        manifest select deliberate shortfall candidates, which the wrapper still requires
        explicit --accept-shortfall to process.

    Some states are always hard-disqualifying because they represent active/ambiguous or
    contradictory raw state rather than mere shortfall: locks, no-surface conflicts, both
    particle files, and zero-byte particle artifacts.
    """
    strict = min_terminal_frac >= 1.0

    if "error" in row:
        return f"survey_error: {row.get('error')}"
    if row.get("locks_present", row.get("running", 0)) > 0:
        return f"locks_present={row.get('locks_present', row.get('running', 0))}"

    # Always-disqualifying raw-state anomalies. These are not shortfall; they are unsafe or
    # contradictory states that should be repaired upstream before a cache/reap campaign.
    for name in ("no_surface_conflict", "both_files", "zero_byte_particle", "extra_event_dirs", "unreadable"):
        val = row.get(name, 0)
        if val and val > 0:
            return f"{name}={val}"

    # Strict-mode-only hard checks. In deliberate shortfall mode, these counts are reflected in
    # terminal_frac; the wrapper/publish step still requires explicit shortfall acceptance.
    if strict:
        for name in ("incomplete", "failed", "completed_zero"):
            val = row.get(name, 0)
            if val and val > 0:
                return f"{name}={val}"

    frac = terminal_frac(row)
    if frac is None or frac < min_terminal_frac:
        return f"terminal_frac={frac} < {min_terminal_frac}"

    if target_events is not None:
        terminal = row.get("terminal", row.get("finished"))
        if terminal is None:
            return "terminal count missing"
        if terminal < target_events and strict:
            return f"terminal={terminal} < target_events={target_events}"
        if row.get("expected_missing", 0) > 0 and strict:
            return f"expected_missing={row.get('expected_missing')}"

    # If the survey row has an explicit conservative ready flag, honor false only in strict
    # mode. In shortfall mode the caller is deliberately asking to consider incomplete designs.
    if row.get("raw_ready_for_cache", row.get("ready_for_cache_reap")) is False and strict:
        return "raw_ready_for_cache=false"
    return None

def main():
    ap = argparse.ArgumentParser(description="Build the cache cache/reap campaign manifest.")
    ap.add_argument("--survey-json", required=True,
                    help="JSON from survey_raw_designs.py --json (readiness/quiescence).")
    ap.add_argument("--record-dir", required=True,
                    help="Wrapper record dir (to exclude already-done designs).")
    ap.add_argument("--out", required=True, help="Manifest path to write (design system rows).")
    ap.add_argument("--system", default=None, help="Restrict to one system.")
    ap.add_argument('--raw-root', help='Require the survey to describe this campaign root (from config).')
    ap.add_argument('--cache-cfs-root', help='Validate record cache paths against this root (from config).')
    ap.add_argument("--target-events", type=int, default=None,
                    help="Expected target events. Defaults to survey JSON target_events if present.")
    ap.add_argument("--start-index", type=int, default=None,
                    help="Expected start index. Defaults to survey JSON start_index if present. Used "
                         "only to validate that the survey and manifest agree.")
    ap.add_argument("--min-terminal-frac", dest="min_terminal_frac",
                    type=float, default=1.0,
                    help="Minimum terminal/expected fraction to count as ready (default 1.0).")
    # Retain the former flag without listing it in generated CLI documentation.
    ap.add_argument("--min-finished-frac", dest="min_terminal_frac", type=float,
                    default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ap.add_argument("--redo-clean", action="store_true",
                    help="Include designs already cache-clean (re-produce). Default excludes them.")
    ap.add_argument("--force-applied", action="store_true",
                    help="Include designs already applied_clean. DANGER: raw is already reaped; only "
                         "for deliberate re-verification. Default excludes them.")
    ap.add_argument("--allow-bad-records", action="store_true",
                    help="Do not stop on unreadable record files. Unsafe for deletion-gating; useful "
                         "only for diagnostics.")
    add_output_args(ap)
    args = parse_workflow_args(ap, "manifest")
    if not 0 < args.min_terminal_frac <= 1:
        ap.error('--min-terminal-frac must be in (0, 1]')
    if args.max_rows < 0:
        ap.error('--max-rows must be nonnegative')

    rows, doc = load_survey(args.survey_json)
    if args.raw_root and doc.get('raw_root') and os.path.realpath(args.raw_root) != os.path.realpath(doc['raw_root']):
        raise SystemExit('Survey raw_root differs from this campaign; rerun prepare-cache before selecting work.')
    survey_target_events = doc.get("target_events")
    survey_start_index = doc.get("start_index")
    target_events = args.target_events if args.target_events is not None else survey_target_events
    start_index = args.start_index if args.start_index is not None else survey_start_index

    # The survey denominator must match the manifest denominator. A survey run without
    # --target-events cannot know about absent expected event dirs, so do not use it for a
    # deletion-campaign manifest that supplies target_events here.
    if target_events is None:
        raise SystemExit("target_events is required either in --target-events or in the survey JSON. "
                         "Rerun survey_raw_designs.py with --target-events for campaign use.")
    if survey_target_events is None:
        raise SystemExit("survey JSON has target_events=null. Rerun survey_raw_designs.py with "
                         "--target-events matching this manifest.")
    if int(survey_target_events) != int(target_events):
        raise SystemExit(f"survey target_events={survey_target_events} != manifest "
                         f"target_events={target_events}; rerun survey or fix invocation.")
    if survey_start_index is not None and start_index is not None and int(survey_start_index) != int(start_index):
        raise SystemExit(f"survey start_index={survey_start_index} != manifest start_index={start_index}; "
                         f"rerun survey or fix invocation.")

    cache_records, reap_records, duplicates, bad = load_records(args.record_dir)
    good_buckets, _ = _import_good_buckets(str(Path(__file__).with_name('cache_reap_one_design.py')))
    if duplicates:
        raise SystemExit('Duplicate cache/reap records: refusing to select designs from ambiguous history; run review.')
    if bad:
        print(f"*** ERROR: {len(bad)} unreadable record(s); the already-done exclusion is "
              f"incomplete. Refusing to write a campaign manifest. ***", file=sys.stderr)
        for p, e in bad:
            print(f"    {p}: {e}", file=sys.stderr)
        if not args.allow_bad_records:
            raise SystemExit(4)
        print("WARNING: --allow-bad-records set; continuing despite incomplete record history.",
              file=sys.stderr)

    selected = []
    report_rows = []
    quarantined = set()
    excluded = {
        "not_ready": [], "applied": [], "produced": [], "survey_error": [],
        "system_filter": [], "record_issue": [], "maybe_reaped_missing_record": [],
    }

    for r in rows:
        design, system = r["design"], r["system"]
        key = (design, system)
        if args.system and system != args.system:
            excluded["system_filter"].append(key)
            continue

        annotate_row(r, cache_records.get(key), reap_records.get(key), good_buckets,
                     cache_cfs_root=args.cache_cfs_root or doc.get('cache_cfs_root'),
                     target_events=target_events)
        report_rows.append(r)
        category = r.get('record_classification')
        if r.get('record_issue'):
            excluded['record_issue'].append((design, system, r['record_issue']))
            continue
        if category and ('quarantine' in category or category.startswith('stage2_')):
            quarantined.add(key)
        # Already done? Apply before readiness filtering so already-applied/reproduced designs
        # don't clutter not_ready counts after raw reaping.
        if r['status'] == 'reaped' and not args.force_applied:
            excluded["applied"].append(key)
            continue
        if 'error' in r:
            excluded['survey_error'].append((design, system, r['error']))
            continue
        if category in ('clean_ready_for_stage2', 'clean_nothing_to_reap', 'clean_already_reaped') and not args.redo_clean:
            excluded["produced"].append(key)
            continue

        if r.get('reaped_marker'):
            excluded['not_ready'].append((design, system, 'reaped.done present; no raw cache input'))
            continue
        err = ready_error(r, args.min_terminal_frac, target_events=target_events)
        if err:
            excluded["not_ready"].append((design, system, err))
            cz = r.get("completed_zero", 0) or 0
            # A fully reaped design with a missing/lost reap record often appears in raw state
            # as all completed_zero (afterburner.done remains, particle files were reaped). This
            # is safely excluded, but call it out because the diagnostic otherwise looks like a
            # production anomaly.
            if target_events and cz >= 0.9 * target_events:
                excluded["maybe_reaped_missing_record"].append((design, system, cz))
            continue

        selected.append((design, system, r.get("terminal", r.get("finished")), terminal_frac(r)))
        r['selected_for_cache'] = True

    selected.sort()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out + ".tmp"
    with open(tmp, "w") as f:
        for design, system, _terminal, _frac in selected:
            f.write(f"{design} {system}\n")
    os.replace(tmp, args.out)

    report(report_rows, 'Cache selection', args.only, args.details, args.max_rows)
    print(f"\nCache manifest: {len(selected)} selected -> {args.out}")
    if any(excluded.values()):
        print('Excluded: ' + ', '.join(f'{name}={len(items)}' for name, items in excluded.items() if items))
    if quarantined:
        print(f'{len(quarantined)} design(s) have quarantined history; use review --details for reasons.')
    if args.min_terminal_frac < 1:
        print('Shortfall selection only: caching still requires --accept-shortfall and a reason.')
    if args.details and excluded['not_ready']:
        limit = args.max_rows or len(excluded['not_ready'])
        print('Cache selection blockers:')
        for design, system, reason in excluded['not_ready'][:limit]:
            print(f'  {design}/{system}: {reason}')
    if not selected:
        print("\nNo designs selected. Check survey readiness, system filter, target-events, or "
              "whether all ready designs are already done.", file=sys.stderr)


if __name__ == "__main__":
    main()
