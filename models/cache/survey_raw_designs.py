#!/usr/bin/env python3
"""
survey_raw_designs.py

Campaign/raw-state survey for cache/reap readiness. Scans PSCRATCH design/system directories by
stat-ing small marker files only -- it NEVER reads particle_lists.oscar contents -- and writes
per-design JSON for make_cache_reap_manifest.py.

This survey is conservative and campaign-oriented:
  - hydro.no_surface is counted as a terminal/cacheable no-particle event.
  - afterburner.lock and smash.lock are counted independently of event status.
  - when --target-events is supplied, expected event directories are enumerated explicitly, so
    missing event dirs count against readiness rather than disappearing from the denominator.
  - terminal = finished particle events + hydro_no_surface events.
  - completed_zero / failed / incomplete / missing / locks / conflicts are surfaced separately.

Layout assumed: <root>/design_XXX/<System>/<NNNN>/{afterburner.done, hydro.no_surface,
particle_lists.oscar, ...}

Reads raw metadata and small cache/reap records. Writes only the requested JSON
snapshot; never changes events or reads particle-list contents.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workflow_config import parse_args as parse_workflow_args
from workflow_records import _import_good_buckets, load_records
from workflow_status import add_output_args, annotate_row, report
import argparse
import functools
import fnmatch
import glob
import json
import multiprocessing as mp
import os

PARTICLE_NAMES = ("particle_lists.oscar", "particle_lists.oscar.unfinished")
LOCK_NAMES = ("afterburner.lock", "smash.lock")


def expected_event_names(start_index, target_events, event_width=None):
    """Return expected event directory names for a target-event scan."""
    last = start_index + target_events - 1
    width = event_width if event_width is not None else len(str(last))
    return [str(i).zfill(width) for i in range(start_index, start_index + target_events)]


def nonempty_file(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def classify_event(entry_names, event_path):
    """Classify a single event dir and return (status, flags).

    Mutually-exclusive status values:
      finished               afterburner.done + one nonempty particle file
      hydro_no_surface       hydro.no_surface marker present, clean no-surface terminal event
      completed_zero         afterburner.done but no nonempty particle file
      incomplete_no_done     nonempty particle file exists but afterburner.done absent
      failed                 afterburner.failed present and no terminal output
      missing_or_not_started no terminal/failure markers/output
      no_surface_conflict    hydro.no_surface paired with contradictory terminal/output markers

    Independent flags:
      locks_present          afterburner.lock or smash.lock present
      both_particle_files    both nonempty OSCAR variants present
      zero_byte_particle     particle file exists but is zero-byte / unreadable as nonempty
    """
    done = "afterburner.done" in entry_names
    hydro_done = "hydro.done" in entry_names
    no_surface = "hydro.no_surface" in entry_names
    locks_present = any(nm in entry_names for nm in LOCK_NAMES)

    nonempty_particles = []
    zero_byte_particle = False
    for nm in PARTICLE_NAMES:
        p = os.path.join(event_path, nm)
        if nm in entry_names:
            if nonempty_file(p):
                nonempty_particles.append(nm)
            else:
                zero_byte_particle = True

    both_particle_files = len(nonempty_particles) == 2

    if no_surface:
        # Clean convention is hydro.no_surface alone (zero-byte surface may be left behind).
        # Contradictions should not be fed to cache/reap campaign automation.
        if done or hydro_done or nonempty_particles or zero_byte_particle or nonempty_file(
                os.path.join(event_path, "surface.dat")):
            status = "no_surface_conflict"
        else:
            status = "hydro_no_surface"
    elif nonempty_particles:
        status = "finished" if done else "incomplete_no_done"
    elif done:
        status = "completed_zero"
    elif "afterburner.failed" in entry_names:
        status = "failed"
    else:
        status = "missing_or_not_started"

    flags = {
        "locks_present": locks_present,
        "both_particle_files": both_particle_files,
        "zero_byte_particle": zero_byte_particle,
        "needs_hydro": status in ("failed", "missing_or_not_started") and not hydro_done,
        "needs_afterburner": status in ("failed", "missing_or_not_started") and hydro_done,
    }
    return status, flags


def scan_design(system_dir, target_events=None, start_index=0, event_width=None):
    """Scan one design/system directory. Returns a dict of counts."""
    statuses = ("finished", "hydro_no_surface", "completed_zero", "incomplete_no_done",
                "failed", "missing_or_not_started", "no_surface_conflict")
    counts = {k: 0 for k in statuses}
    counts.update({
        "locks_present": 0,
        "afterburner_locks": 0,
        "smash_locks": 0,
        "both_files": 0,
        "zero_byte_particle": 0,
        "n_event_dirs": 0,
        "expected_missing": 0,
        "extra_event_dirs": 0,
        "unreadable": 0,
        "needs_hydro": 0,
        "needs_afterburner": 0,
    })

    # The maintained runner also treats this as design-level completion. Avoid
    # thousands of pointless event metadata calls after a completed reap.
    if os.path.isfile(os.path.join(system_dir, 'reaped.done')):
        counts.update(reaped_marker=True, terminal=0, scanned_expected=target_events or 0,
                      terminal_frac=None, finished_frac=None, attempted=target_events or 0)
        return counts

    try:
        with os.scandir(system_dir) as it:
            numeric_dirs = {e.name: e.path for e in it if e.is_dir() and e.name.isdigit()}
    except FileNotFoundError as e:
        if target_events is None:
            return {'_error': str(e)}
        numeric_dirs = {}  # A discovered design with no system dir has not started.
    except OSError as e:
        return {"_error": str(e)}

    counts["n_event_dirs"] = len(numeric_dirs)

    if target_events is not None:
        # If no width is supplied, infer it from existing numeric dirs when possible. This keeps
        # small tests and legacy padded layouts working, while 10000-event production naturally
        # infers width 4 from 0000..9999. Fall back to the mathematically minimal width only when
        # there are no dirs to inspect.
        inferred_width = event_width
        if inferred_width is None and numeric_dirs:
            inferred_width = max(len(name) for name in numeric_dirs)
        scan_names = expected_event_names(start_index, target_events, inferred_width)
        expected_set = set(scan_names)
        counts["extra_event_dirs"] = len(set(numeric_dirs) - expected_set)
    else:
        scan_names = sorted(numeric_dirs, key=lambda x: int(x))
        expected_set = set(scan_names)

    for name in scan_names:
        path = numeric_dirs.get(name)
        if path is None:
            counts["missing_or_not_started"] += 1
            counts["expected_missing"] += 1
            counts["needs_hydro"] += 1
            continue
        try:
            with os.scandir(path) as it2:
                names = {x.name for x in it2}
        except OSError:
            counts["missing_or_not_started"] += 1
            counts["unreadable"] += 1
            continue

        status, flags = classify_event(names, path)
        counts[status] += 1
        counts['needs_hydro'] += int(flags['needs_hydro'])
        counts['needs_afterburner'] += int(flags['needs_afterburner'])
        if flags["locks_present"]:
            counts["locks_present"] += 1
        if "afterburner.lock" in names:
            counts["afterburner_locks"] += 1
        if "smash.lock" in names:
            counts["smash_locks"] += 1
        if flags["both_particle_files"]:
            counts["both_files"] += 1
        if flags["zero_byte_particle"]:
            counts["zero_byte_particle"] += 1

    terminal = counts["finished"] + counts["hydro_no_surface"]
    scanned_expected = len(scan_names)
    counts["terminal"] = terminal
    counts["scanned_expected"] = scanned_expected
    counts["terminal_frac"] = terminal / scanned_expected if scanned_expected else None
    # Backward-compatible aliases used by older display code / manifest versions.
    counts["finished_frac"] = counts["terminal_frac"]
    counts["attempted"] = scanned_expected
    return counts


def _scan_worker(sd, target_events, start_index, event_width):
    """Top-level (picklable) wrapper around scan_design for multiprocessing. Returns the
    counts dict; on any unexpected exception returns an _error dict so one bad design dir
    cannot kill the whole pool."""
    try:
        return scan_design(sd, target_events=target_events, start_index=start_index,
                           event_width=event_width)
    except Exception as e:  # noqa: BLE001 - isolate per-design failures
        return {"_error": str(e)}


def main():
    ap = argparse.ArgumentParser(
        description="Survey RAW PSCRATCH designs for cache/reap campaign readiness "
                    "(stat markers only, no OSCAR reads).")
    ap.add_argument("--raw-root", required=True,
                    help="e.g. $PSCRATCH/my_campaign")
    ap.add_argument("--system", default=None, help="Filter to one system (e.g. AuAu200).")
    ap.add_argument("--design-glob", default="design_*",
                    help="Glob for design dirs (default design_*). Narrow it to limit the scan.")
    ap.add_argument("--max-designs", type=int, default=None,
                    help="Stop after scanning this many design/system dirs (for a quick look).")
    ap.add_argument("--target-events", type=int, default=None,
                    help="Expected number of event dirs. If supplied, missing expected dirs count "
                         "against readiness. Strongly recommended for campaign manifests.")
    ap.add_argument("--start-index", type=int, default=0,
                    help="First expected event index when --target-events is supplied.")
    ap.add_argument("--event-width", type=int, default=None,
                    help="Zero-padding width for expected event dirs. Default derives from "
                         "start_index+target_events-1 (10000 -> width 4 for 0000..9999).")
    ap.add_argument("--min-terminal-frac", dest="min_terminal_frac",
                    type=float, default=None,
                    help="Only show designs whose terminal/(expected) >= this.")
    # Preserve the former threshold and sort spellings without documenting them.
    ap.add_argument("--min-finished-frac", dest="min_terminal_frac", type=float,
                    default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    ap.add_argument("--sort", default="terminal_frac",
                    type=lambda value: "terminal_frac" if value == "finished_frac" else value,
                    choices=["terminal_frac", "terminal", "finished", "design", "edge"],
                    help="Sort key ('edge' = most edge-case events first).")
    ap.add_argument("--suggest", action="store_true",
                    help="Suggest a pipeline-branch-spanning subset for testing.")
    ap.add_argument("--json", default=None, metavar="PATH",
                    help="Also write all per-design scan results (including errors) as JSON to PATH.")
    ap.add_argument("--nprocs", type=int, default=16,
                    help="Parallel worker processes for the per-design scan (default 16). The scan is "
                         "metadata-bound, so run on an salloc node; set 1 to force sequential.")
    ap.add_argument('--record-dir', help='Combine raw state with cache/reap history (from shared config).')
    ap.add_argument('--cache-cfs-root', help='Published root; only small marker existence is checked.')
    ap.add_argument('--quiet', action='store_true', help='Write the snapshot without a terminal report.')
    add_output_args(ap)
    args = parse_workflow_args(ap, "survey")
    if args.target_events is not None and args.target_events <= 0:
        ap.error('--target-events must be positive')
    if args.max_rows < 0:
        ap.error('--max-rows must be nonnegative')

    cache_records, reap_records, duplicates, bad_records = load_records(args.record_dir) if args.record_dir else ({}, {}, [], [])
    duplicate_keys = {item[1] for item in duplicates}
    good_buckets, _ = _import_good_buckets(str(Path(__file__).resolve().with_name('cache_reap_one_design.py')))

    pattern = os.path.join(args.raw_root, args.design_glob, "*")
    sysdirs = sorted(p for p in glob.glob(pattern) if os.path.isdir(p))
    if args.system:
        sysdirs = [os.path.join(p, args.system) for p in glob.glob(os.path.join(args.raw_root, args.design_glob))
                   if os.path.isdir(p)]
    # Record-only designs must not disappear simply because raw directories were
    # removed. Scope them by the same system/glob and validate recorded paths later.
    recorded_paths = [os.path.join(args.raw_root, design, system) for design, system in set(cache_records) | set(reap_records)
                      if fnmatch.fnmatch(design, args.design_glob) and (not args.system or system == args.system)]
    sysdirs = sorted(set(sysdirs) | set(recorded_paths))
    if not sysdirs:
        raise SystemExit(f"No design/system dirs under {args.raw_root} matching {args.design_glob}"
                         + (f" / {args.system}" if args.system else ""))

    # max_designs applies to the scan set (glob order), as before.
    if args.max_designs:
        sysdirs = sysdirs[:args.max_designs]

    # The slow part is scan_design's per-event scandir; it is independent per design, so run
    # it across a process pool. Row assembly (edge/ready) below is cheap and stays sequential.
    worker = functools.partial(_scan_worker, target_events=args.target_events,
                               start_index=args.start_index, event_width=args.event_width)
    if args.nprocs and args.nprocs > 1 and len(sysdirs) > 1:
        with mp.Pool(min(args.nprocs, len(sysdirs))) as pool:
            scan_results = pool.map(worker, sysdirs)
    else:
        scan_results = [worker(sd) for sd in sysdirs]

    rows = []
    scanned = 0
    for sd, c in zip(sysdirs, scan_results):
        system = os.path.basename(sd)
        design = os.path.basename(os.path.dirname(sd))
        scanned += 1
        if "_error" in c:
            rows.append({"design": design, "system": system, "error": c["_error"], "path": sd})
            continue

        edge = (c["completed_zero"] + c["incomplete_no_done"] + c["failed"]
                + c["missing_or_not_started"] + c["no_surface_conflict"]
                + c["locks_present"] + c["both_files"] + c["zero_byte_particle"])
        ready = (c["terminal"] == c["scanned_expected"] and c["locks_present"] == 0
                 and c['scanned_expected'] > 0 and not c.get('reaped_marker')
                 and c['unreadable'] == 0
                 and c["both_files"] == 0 and c["zero_byte_particle"] == 0
                 and c["no_surface_conflict"] == 0
                 # Unexpected numeric event dirs outside the expected set block readiness:
                 # downstream tools may scan all numeric dirs, so an uncached stray dir could
                 # be reaped. Quarantine for a human look rather than auto-reap.
                 and c["extra_event_dirs"] == 0)
        rows.append({
            "design": design, "system": system,
            "path": sd,
            "target_events": args.target_events,
            "start_index": args.start_index,
            "event_width": args.event_width,
            "finished": c["finished"],
            "hydro_no_surface": c["hydro_no_surface"],
            "terminal": c["terminal"],
            "attempted": c["scanned_expected"],
            "scanned_expected": c["scanned_expected"],
            "terminal_frac": c["terminal_frac"],
            "finished_frac": c["terminal_frac"],
            "completed_zero": c["completed_zero"],
            "incomplete": c["incomplete_no_done"],
            "failed": c["failed"],
            "missing": c["missing_or_not_started"],
            "expected_missing": c["expected_missing"],
            "locks_present": c["locks_present"],
            "afterburner_locks": c["afterburner_locks"],
            "smash_locks": c["smash_locks"],
            "both_files": c["both_files"],
            "zero_byte_particle": c["zero_byte_particle"],
            "no_surface_conflict": c["no_surface_conflict"],
            "n_event_dirs": c["n_event_dirs"],
            "extra_event_dirs": c["extra_event_dirs"],
            "edge_total": edge,
            "raw_ready_for_cache": ready,
            "ready_for_cache_reap": ready,  # compatibility alias; never permission to reap
            "reaped_marker": c.get('reaped_marker', False),
            "unreadable": c['unreadable'],
            "needs_hydro": c['needs_hydro'],
            "needs_afterburner": c['needs_afterburner'],
        })

    for row in rows:
        key = (row['design'], row['system'])
        annotate_row(row, cache_records.get(key), reap_records.get(key), good_buckets,
                     ambiguous=key in duplicate_keys or bool(bad_records),
                     cache_cfs_root=args.cache_cfs_root, target_events=args.target_events)

    good = [r for r in rows if "error" not in r]
    errors = [r for r in rows if "error" in r]

    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        tmp = args.json + ".tmp"
        with open(tmp, "w") as jf:
            json.dump({"raw_root": os.path.abspath(args.raw_root),
                       "system_filter": args.system,
                       "target_events": args.target_events,
                       "start_index": args.start_index,
                       "event_width": args.event_width,
                       "scanned": scanned, "record_dir": args.record_dir,
                       "cache_cfs_root": args.cache_cfs_root,
                       "bad_records": bad_records, "duplicate_records": duplicates,
                       "rows": rows}, jf, indent=2, sort_keys=True)
        os.replace(tmp, args.json)

    if args.min_terminal_frac is not None:
        good = [r for r in good if (r["terminal_frac"] or 0) >= args.min_terminal_frac]

    keymap = {
        "terminal_frac": lambda r: -(r["terminal_frac"] or 0),
        "terminal": lambda r: -(r["terminal"] or 0),
        "finished": lambda r: -(r["finished"] or 0),
        "design": lambda r: r["design"],
        "edge": lambda r: -(r["edge_total"] or 0),
    }
    good.sort(key=keymap[args.sort])

    if args.quiet:
        if args.json:
            print(f'Survey snapshot: {args.json} ({len(rows)} designs)')
        return

    report(good + errors, 'Campaign status', args.only, args.details, args.max_rows)
    print('Cache/reap states describe recorded history or markers, not fresh cache verification.')
    if not args.record_dir:
        print('No record directory supplied: cache/reap history is unavailable; use --config or --record-dir.')
    if bad_records or duplicates:
        print(f'Record history needs review: {len(bad_records)} unreadable, {len(duplicates)} duplicate records.')
    if args.json:
        print(f'Full snapshot: {args.json}')

    def pct(x):
        return f"{100*x:5.1f}%" if isinstance(x, (int, float)) else "   -  "

    if args.suggest:
        print("\n==== Suggested pipeline-branch-spanning subset ====")
        quiescent = [r for r in good if r["locks_present"] == 0]

        def show(label, r, why):
            if r:
                print(f"  {label:26s} {r['design']}/{r['system']:<8} "
                      f"(term={r['terminal']}, term%={pct(r['terminal_frac'])}, "
                      f"nosurf={r['hydro_no_surface']}, both={r['both_files']}, "
                      f"incmp={r['incomplete']})  <- {why}")
            else:
                print(f"  {label:26s} (none) <- {why}: axis untested, consider why")

        if not quiescent:
            print("  (all candidate designs have locks; none safe to cache+reap yet)")
        else:
            most_complete = max(quiescent, key=lambda r: (r["terminal_frac"] or 0))
            show("most-complete", most_complete, "clean happy-path end-to-end")
            partial = [r for r in quiescent if (r["terminal_frac"] or 0) < 0.99 and r["terminal"] > 0]
            partial.sort(key=lambda r: -(r["failed"] + r["missing"]))
            show("high-failure/shortfall", partial[0] if partial else None,
                 "tests explicit shortfall paths if deliberately accepted")
            nosurf = [r for r in quiescent if r["hydro_no_surface"] > 0]
            nosurf.sort(key=lambda r: -r["hydro_no_surface"])
            show("has hydro_no_surface", nosurf[0] if nosurf else None,
                 "tests no-surface terminal path")
            both = [r for r in quiescent if r["both_files"] > 0]
            both.sort(key=lambda r: -r["both_files"])
            show("has both-files events", both[0] if both else None,
                 "tests reaper both-files SKIP / should not enter campaign manifest")
            by_term = sorted((r for r in quiescent if r["terminal"]), key=lambda r: r["terminal"])
            show("smallest terminal count", by_term[0] if by_term else None,
                 "tests small-design timing / few shards")
            show("largest terminal count", by_term[-1] if by_term else None,
                 "tests large-design timing / many shards / size")


if __name__ == "__main__":
    main()
