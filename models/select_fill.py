#!/usr/bin/env python3
"""Emit an interactive-fill plan for designs near completion.

The shortfall is MEASURED on disk, never taken from campaign_state tallies:
terminal == afterburner.done or hydro.no_surface, the same definition
run_designs.count_terminal_events() and survey_raw_designs use.  This is what
--min-terminal-frac sees, so the two agree by construction.

The mode is also derived from disk, by simulating what run_one_event() will
actually do with each missing event:

  no event dir                       -> hydro must create it      -> MODE both
  dir, no hydro.done                 -> hydro SKIPS (dir exists)  -> STUCK, needs repair
  dir + hydro.done + surface.dat     -> afterburner, no restore   -> MODE afterburner
  dir + hydro.done, no surface.dat   -> afterburner WITH restore  -> MODE afterburner+restore

A design carrying both absent dirs and archived tails needs two passes, because
--restore-archives is illegal with mode=both.  The first pass is emitted now;
re-run this after the wave to get the follow-up.

stdout: "design ntasks mode restore short system", one per selected design.
stderr: an audit line for EVERY design considered.

Traversal is bounded: immediate children of --run-root matching design_NNN,
then one level into <design>/<system>, then only the missing events' dirs.
"""
import argparse
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REAPED = "reaped.done"
DONE = "afterburner.done"
NOSURF = "hydro.no_surface"
HYDRO_DONE = "hydro.done"
SURFACE = "surface.dat"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_config import parse_args as parse_workflow_args
from run_designs import read_campaign_state


def probe_design(run_dir: Path, nevents: int):
    """Measure terminal count and classify every missing event. Returns a dict."""
    present = {}
    try:
        it = os.scandir(run_dir)
    except FileNotFoundError:
        return None
    with it:
        for e in it:
            if not e.name.isdigit():
                continue
            idx = int(e.name)
            if 0 <= idx < nevents and e.is_dir():
                present[idx] = e.path

    terminal = 0
    pending = []
    for idx, path in present.items():
        p = Path(path)
        if (p / DONE).is_file() or (p / NOSURF).is_file():
            terminal += 1
        else:
            pending.append(p)

    # classify only the non-terminal events -- few, for a near-complete design
    stuck = ab_local = ab_archived = 0
    for p in pending:
        if not (p / HYDRO_DONE).is_file():
            stuck += 1                      # dir exists but hydro never finished
        elif (p / SURFACE).is_file():
            ab_local += 1
        else:
            ab_archived += 1

    return {
        "terminal": terminal,
        "short": nevents - terminal,
        "absent": nevents - len(present),
        "stuck": stuck,
        "ab_local": ab_local,
        "ab_archived": ab_archived,
    }


def read_state_route(run_dir: Path, system: str, nevents: int):
    """Route only -- tallies are deliberately NOT trusted for counting."""
    payload = read_campaign_state(run_dir, system, nevents)
    return payload.get("route") if payload is not None else None


def choose_pass(m):
    """(mode, restore, note) for the NEXT productive pass, or (None, None, reason)."""
    if m["absent"] > 0:
        note = ""
        if m["ab_archived"] > 0:
            note = (f"pass 1 of 2: {m['ab_archived']} archived tail(s) need a later "
                    f"afterburner+restore pass")
        return "both", "false", note
    if m["ab_archived"] > 0:
        return "afterburner", "true", ""
    if m["ab_local"] > 0:
        return "afterburner", "true", ""      # restore is a no-op when surface.dat is present
    return None, None, "nothing runnable: all missing events are stuck (need repair)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-root", default=os.environ.get("HIC_RAW_ROOT"),
                    help="Raw campaign root (campaign.py supplies [run] directory).")
    ap.add_argument("--system", required=True,
                    help="collision system (e.g. AuAu200, OO5360); written into every plan line")
    ap.add_argument("--nevents", type=int, default=10000)
    ap.add_argument("--prefix", default="design", help="Design directory prefix (shared with run_designs).")
    ap.add_argument("--max-short", type=int, default=500)
    ap.add_argument("--max-tasks", type=int, default=128)
    ap.add_argument("--jobs", type=int, default=16, help="parallel design probes (I/O bound)")
    ap.add_argument("--quiet", action="store_true")
    args = parse_workflow_args(ap, "fill")

    if not args.run_root:
        ap.error("--run-root is required (or run through models/campaign.py fill-plan)")
    run_root = Path(os.path.expandvars(os.path.expanduser(args.run_root)))
    if not run_root.is_dir():
        print(f"ERROR: run root not found: {run_root}", file=sys.stderr)
        return 1

    with os.scandir(run_root) as it:
        entries = sorted((e for e in it if re.fullmatch(re.escape(args.prefix) + r"_\d+", e.name) and e.is_dir()),
                         key=lambda e: e.name)

    targets = []
    audit, skipped_reaped = [], 0
    for e in entries:
        run_dir = Path(e.path) / args.system
        if not run_dir.is_dir():
            continue
        if (run_dir / REAPED).is_file():
            skipped_reaped += 1
            continue
        targets.append((e.name, run_dir))

    def work(item):
        name, run_dir = item
        return name, run_dir, probe_design(run_dir, args.nevents)

    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        results = list(ex.map(work, targets))

    plan, notes = [], []
    for name, run_dir, m in results:
        if m is None:
            continue
        route = read_state_route(run_dir, args.system, args.nevents)
        if m["short"] <= 0:
            audit.append((name, m, "-", "-", "skip: complete"))
            continue
        if m["short"] >= args.max_short:
            audit.append((name, m, route or "-", "-", f"skip: short>={args.max_short}"))
            continue

        mode, restore, note = choose_pass(m)
        if mode is None:
            audit.append((name, m, route or "-", "-", f"SKIP: {note}"))
            notes.append(f"{name}: {note}")
            continue

        runnable = m["absent"] + (m["ab_local"] + m["ab_archived"]
                                  if mode == "afterburner" else m["ab_local"])
        runnable = max(1, min(runnable if runnable else m["short"], m["short"]))
        ntasks = max(1, min(runnable, args.max_tasks))
        plan.append((name, ntasks, mode, restore, m["short"]))

        disp = f"include {mode}"
        if route and route != mode:
            disp += f"  [campaign_state says '{route}']"
        audit.append((name, m, route or "-", mode, disp))
        if note:
            notes.append(f"{name}: {note}")
        if m["stuck"]:
            notes.append(f"{name}: {m['stuck']} event dir(s) exist without hydro.done -- "
                         f"hydro SKIPS an existing dir, so these advance only after "
                         f"campaign.py repair --apply deletes them")

    plan.sort(key=lambda r: -r[4])
    for name, ntasks, mode, restore, short in plan:
        print(f"{name} {ntasks} {mode} {restore} {short} {args.system}")

    if not args.quiet:
        print(f"\n{'design':<12}{'term':>7}{'short':>7}{'absent':>8}{'stuck':>7}"
              f"{'ab_loc':>8}{'ab_arch':>9}  {'state':<13}disposition", file=sys.stderr)
        for name, m, route, mode, disp in audit:
            print(f"{name:<12}{m['terminal']:>7}{m['short']:>7}{m['absent']:>8}{m['stuck']:>7}"
                  f"{m['ab_local']:>8}{m['ab_archived']:>9}  {str(route):<13}{disp}",
                  file=sys.stderr)

    tot_tasks = sum(p[1] for p in plan)
    tot_short = sum(p[4] for p in plan)
    print(f"\n[select] scanned {len(results)} designs ({skipped_reaped} reaped, skipped)",
          file=sys.stderr)
    print(f"[select] SELECTED {len(plan)}, {tot_short} events, {tot_tasks} tasks "
          f"({tot_tasks / 128:.2f} nodes at 128 cores)", file=sys.stderr)
    for n in notes:
        print(f"[select] NOTE {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
