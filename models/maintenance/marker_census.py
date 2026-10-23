#!/usr/bin/env python3
"""Census of completion markers in a design's event dirs, with a verdict per state.

    python3 marker_census.py design_046 design_047

For every non-terminal event it records which markers are present and classifies
the event by what run_afterburner_stage() will ACTUALLY do with it on the next
pass.  The states that matter are the ones that return False WITHOUT consuming
the afterburner retry budget -- those loop forever with no escalation and no
error, which is what "I ran it again and nothing changed" looks like.

Traced from run-events:

  afterburner.done                      -> terminal, returns True
  hydro.no_surface                      -> terminal zero-output, returns True
  no hydro.done                         -> "no hydro completion marker", returns False.
                                           hydro will NOT redo it either: run_one_event
                                           skips hydro whenever the dir exists.
                                           STUCK until repair deletes the dir.
  fresh afterburner.lock (<24h)         -> "locked by another worker", returns False
  ambiguous particle output             -> returns False, NO budget consumed. PERMANENT.
    (both .oscar and .oscar.unfinished, or a zero-byte one)
  particle output + archive failure     -> complete_afterburner_output() CLEARS
                                           afterburner.failed and the retry count,
                                           then fails archiving, writes archive.failed
                                           and returns False.  Next pass: identical.
                                           PERMANENT, and self-erasing.
  retry_count >= max                    -> "reached the afterburner retry budget", skip
  hydro.done, no surface, no archive    -> restore returns False, NO budget consumed

The archive path is reconstructed exactly as archive_relative_event_path() does
(event dir relative to HIC_SURFACE_ARCHIVE_BASE, mirrored under HIC_SURFACE_ARCHIVE_ROOT), so we
can tell "archive missing" from "archive present but rejected".
"""
import argparse
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from surface_archive import archived_surface_path, configure_archive_base  # noqa: E402

DONE, NOSURF, HYDRO = "afterburner.done", "hydro.no_surface", "hydro.done"
LOCK, FAILED, RETRY = "afterburner.lock", "afterburner.failed", "afterburner.retry_count"
ARCH_FAILED, SURFACE = "archive.failed", "surface.dat"
OSCAR, UNFIN = "particle_lists.oscar", "particle_lists.oscar.unfinished"
ALL = [DONE, NOSURF, HYDRO, SURFACE, OSCAR, UNFIN, LOCK, FAILED, RETRY, ARCH_FAILED,
       "results"]

VERDICTS = {
    "no_hydro_marker": (
        "dir exists but no hydro.done -- hydro SKIPS an existing dir, afterburner "
        "refuses without the marker",
        "campaign.py --config CFG repair --apply  (deletes safe-stale dirs "
        "so hydro regenerates them)"),
    "archive_failed": (
        "particle list IS present and valid; only the surface archive failed. "
        "complete_afterburner_output() clears afterburner.failed AND the retry count "
        "before failing, so this repeats forever and never escalates",
        "campaign.py --config CFG repair --apply  with [repair] finalize_archive_failed "
        "and archive_root set (re-archives in place and writes afterburner.done "
        "WITHOUT re-running the afterburner)"),
    "ambiguous_output": (
        "both particle variants present, or one is zero-byte -- returns False without "
        "consuming retry budget; invisible to --max-afterburner-retries",
        "inspect and delete the bad variant by hand, then re-run"),
    "no_surface_no_archive": (
        "hydro.done but surface.dat gone and no archive to restore -- afterburner can "
        "never start; no budget consumed",
        "the surface is unrecoverable: delete the event dir and let repair+hydro "
        "regenerate it"),
    "needs_restore": (
        "hydro.done, surface archived away, archive PRESENT -- runs only with "
        "mode=afterburner AND --restore-archives",
        "route it afterburner+restore (mode=both cannot restore)"),
    "budget_exhausted": (
        "afterburner.retry_count >= max -- skipped silently on every pass",
        "raise --max-afterburner-retries, or let repair reset it; note some failure "
        "paths never write afterburner.failed and are immune"),
    "locked": (
        "afterburner.lock present -- a live worker, or a dead job's lock <24h old",
        "if no job is running: repair passes --clear-stale-locks --lock-stale-hours 0"),
    "ready_for_afterburner": (
        "hydro.done + surface.dat, no output yet -- a normal pass will run this",
        "nothing; include in the next afterburner/both pass"),
}


def classify(p: Path, archive_ok, max_retries):
    has = {m: (p / m).exists() for m in ALL}

    def size(name):
        try:
            return (p / name).stat().st_size
        except OSError:
            return None

    if has[DONE]:
        return "terminal_done", has
    if has[NOSURF]:
        return "terminal_nosurf", has

    nonempty = [n for n in (OSCAR, UNFIN) if has[n] and (size(n) or 0) > 0]
    zeroed = [n for n in (OSCAR, UNFIN) if has[n] and (size(n) or 0) == 0]

    if not has[HYDRO]:
        return "no_hydro_marker", has
    if zeroed or len(nonempty) > 1:
        return "ambiguous_output", has
    if nonempty:
        # a usable particle list exists; only archiving stands between it and done
        return ("archive_failed" if (has[ARCH_FAILED] or not (has[SURFACE] or archive_ok))
                else "ready_to_finalize"), has
    if has[LOCK]:
        return "locked", has

    retries = 0
    if has[RETRY]:
        try:
            txt = (p / RETRY).read_text()
            for line in txt.splitlines():
                if line.strip().startswith("count"):
                    retries = int(line.split("=", 1)[1]); break
            else:
                retries = int(txt.strip())
        except Exception:
            retries = 0
    if retries >= max_retries:
        return "budget_exhausted", has

    if has[SURFACE] and (size(SURFACE) or 0) > 0:
        return "ready_for_afterburner", has
    return ("needs_restore" if archive_ok else "no_surface_no_archive"), has


def run(run_dir: Path, nevents: int, design: str, archive_root: Path,
        max_retries: int, examples: int):
    print(f"\n{'='*76}\n{design}   {run_dir}\n{'='*76}")
    if not run_dir.is_dir():
        print("  run dir missing"); return

    width = max(1, len(str(nevents - 1)))
    present = {}
    with os.scandir(run_dir) as it:
        for e in it:
            if e.name.isdigit() and e.is_dir():
                i = int(e.name)
                if 0 <= i < nevents:
                    present[i] = Path(e.path)

    absent = nevents - len(present)
    states, sigs, ex = Counter(), Counter(), defaultdict(list)
    for i, p in present.items():
        # only pay for the archive stat when it can change the answer
        quick_terminal = (p / DONE).exists() or (p / NOSURF).exists()
        arch_ok = False
        if not quick_terminal:
            arch_ok = archived_surface_path(archive_root, p).is_file()
        st, has = classify(p, arch_ok, max_retries)
        states[st] += 1
        if not st.startswith("terminal"):
            sig = ", ".join(m for m in ALL if has[m]) or "(no markers)"
            sigs[sig] += 1
            if len(ex[st]) < examples:
                ex[st].append(f"{i:0{width}d}")

    terminal = states["terminal_done"] + states["terminal_nosurf"]
    print(f"  event dirs {len(present)}/{nevents}   absent {absent}   "
          f"terminal {terminal}   shortfall {nevents - terminal}")
    print(f"  ({states['terminal_done']} afterburner.done, "
          f"{states['terminal_nosurf']} hydro.no_surface)")

    if absent:
        print(f"\n  {absent} event dir(s) DO NOT EXIST -- only mode=both creates these.")

    nonterm = {k: v for k, v in states.items() if not k.startswith("terminal")}
    if not nonterm:
        print("\n  every existing dir is terminal.")
    else:
        print(f"\n  NON-TERMINAL EVENTS BY STATE:")
        for st, c in sorted(nonterm.items(), key=lambda kv: -kv[1]):
            why, fix = VERDICTS.get(st, ("", ""))
            print(f"\n    {c:>6}  {st}   e.g. {' '.join(ex[st])}")
            if why:
                print(f"            what happens: {why}")
                print(f"            fix: {fix}")

    if sigs:
        print(f"\n  RAW MARKER SIGNATURES (non-terminal only):")
        for sig, c in sigs.most_common(12):
            print(f"    {c:>6}  {sig}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("designs", nargs="+")
    ap.add_argument("--run-root", default=os.environ.get("HIC_RAW_ROOT"), required="HIC_RAW_ROOT" not in os.environ,
                    help="Raw campaign root (default: HIC_RAW_ROOT from your profile).")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--nevents", type=int, default=10000)
    ap.add_argument("--max-retries", type=int, default=3)
    ap.add_argument("--archive-root", default=os.environ.get("HIC_SURFACE_ARCHIVE_ROOT"),
                    required="HIC_SURFACE_ARCHIVE_ROOT" not in os.environ,
                    help="Surface archive root (default: HIC_SURFACE_ARCHIVE_ROOT from your profile).")
    ap.add_argument("--examples", type=int, default=6)
    ap.add_argument("--archive-base", default=None,
                    help="Original source base for archive paths (default: HIC_SURFACE_ARCHIVE_BASE).")
    a = ap.parse_args()
    root = Path(os.path.expandvars(os.path.expanduser(a.run_root)))
    try:
        configure_archive_base(a.archive_base, root)
    except ValueError as error:
        ap.error(str(error))
    arch = Path(os.path.expandvars(os.path.expanduser(a.archive_root)))
    print(f"archive root: {arch}")
    for d in a.designs:
        d = d if d.startswith("design_") else f"design_{int(d):03d}"
        run(root / d / a.system, a.nevents, d, arch, a.max_retries, a.examples)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
