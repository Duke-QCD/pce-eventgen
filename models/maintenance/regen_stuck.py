#!/usr/bin/env python3
"""Quarantine (or delete) event dirs that are unrecoverable, so hydro regenerates them.

Target state: hydro.done present, surface.dat absent, and NO archived
surface.dat.zst.  The afterburner can never start (nothing to feed iS3D) and
hydro never retries (run_one_event skips whenever the dir exists), so the event
is stuck forever.  Removing the dir makes it "absent", and the next mode=both
pass regenerates it from the SAME initial condition -- iter_initial_conditions
pairs dataset POSITION with event number, and initial.hdf is untouched, so this
is a faithful re-run, not a new sample.

DRY RUN BY DEFAULT.  --apply moves the dirs to a quarantine directory beside the
system dir (default) or deletes them outright with --hard-delete.

Four independent safety checks run per directory immediately before it is
touched, regardless of what the classifier said:
  - no afterburner.done          (never touch a completed event)
  - no hydro.no_surface          (never touch a terminal zero-output event)
  - no nonempty particle list    (never destroy usable SMASH output)
  - no nonempty surface.dat      (that event is recoverable; leave it alone)
  - no valid archived surface    (ditto -- it only needs --restore-archives)
Anything failing a check is refused and reported, not skipped silently.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from surface_archive import archived_surface_path, configure_archive_base  # noqa: E402

DONE, NOSURF, HYDRO, SURFACE = ("afterburner.done", "hydro.no_surface",
                                "hydro.done", "surface.dat")
OSCAR, UNFIN = "particle_lists.oscar", "particle_lists.oscar.unfinished"


def nonempty(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def safety_refusals(p: Path, archive_root: Path):
    """Return a list of reasons this dir must NOT be touched. Empty == safe."""
    bad = []
    if (p / DONE).exists():
        bad.append("afterburner.done present (completed event)")
    if (p / NOSURF).exists():
        bad.append("hydro.no_surface present (terminal zero-output)")
    for n in (OSCAR, UNFIN):
        if nonempty(p / n):
            bad.append(f"{n} is nonempty (usable SMASH output)")
    if nonempty(p / SURFACE):
        bad.append("surface.dat is nonempty (afterburner can still run)")
    if nonempty(archived_surface_path(archive_root, p)):
        bad.append("archived surface exists (needs --restore-archives, not regeneration)")
    return bad


def is_target(p: Path, archive_root: Path) -> bool:
    return ((p / HYDRO).exists()
            and not nonempty(p / SURFACE)
            and not nonempty(archived_surface_path(archive_root, p))
            and not (p / DONE).exists()
            and not (p / NOSURF).exists()
            and not nonempty(p / OSCAR)
            and not nonempty(p / UNFIN))


def process(run_dir: Path, nevents: int, design: str, archive_root: Path,
            apply: bool, hard_delete: bool, quarantine_root: Path, limit):
    print(f"\n=== {design}  {run_dir}")
    if not run_dir.is_dir():
        print("  run dir missing"); return 0, 0

    targets = []
    with os.scandir(run_dir) as it:
        for e in it:
            if not (e.name.isdigit() and e.is_dir()):
                continue
            if 0 <= int(e.name) < nevents and is_target(Path(e.path), archive_root):
                targets.append(Path(e.path))
    targets.sort()
    if limit:
        targets = targets[:limit]

    print(f"  {len(targets)} unrecoverable event dir(s) identified")
    if not targets:
        return 0, 0

    sample = " ".join(p.name for p in targets[:8])
    print(f"  e.g. {sample}{' ...' if len(targets) > 8 else ''}")

    moved = refused = 0
    qdir = quarantine_root / f"{design}_stuck_{time.strftime('%Y%m%d_%H%M%S')}"
    for p in targets:
        bad = safety_refusals(p, archive_root)
        if bad:
            refused += 1
            print(f"  REFUSED {p.name}: {'; '.join(bad)}")
            continue
        if not apply:
            moved += 1
            continue
        try:
            if hard_delete:
                shutil.rmtree(p)
            else:
                qdir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), str(qdir / p.name))
            moved += 1
        except Exception as exc:
            refused += 1
            print(f"  ERROR {p.name}: {exc.__class__.__name__}: {exc}")

    verb = ("deleted" if hard_delete else "quarantined") if apply else "WOULD remove"
    print(f"  {verb}: {moved}   refused/errored: {refused}")
    if apply and not hard_delete and moved:
        print(f"  quarantine: {qdir}")
    if not apply:
        print("  (dry run -- re-run with --apply)")
    return moved, refused


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("designs", nargs="+")
    ap.add_argument("--run-root", default=os.environ.get("HIC_RAW_ROOT"), required="HIC_RAW_ROOT" not in os.environ,
                    help="Raw campaign root (default: HIC_RAW_ROOT from your profile).")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--nevents", type=int, default=10000)
    ap.add_argument("--archive-root", default=os.environ.get("HIC_SURFACE_ARCHIVE_ROOT"),
                    required="HIC_SURFACE_ARCHIVE_ROOT" not in os.environ,
                    help="Surface archive root (default: HIC_SURFACE_ARCHIVE_ROOT from your profile).")
    ap.add_argument("--apply", action="store_true", help="actually move/delete")
    ap.add_argument("--archive-base", default=None,
                    help="Original source base for archive paths (default: HIC_SURFACE_ARCHIVE_BASE).")
    ap.add_argument("--hard-delete", action="store_true",
                    help="rmtree instead of moving to quarantine (irreversible)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap dirs touched per design; use --limit 1 for a pilot")
    a = ap.parse_args()

    root = Path(os.path.expandvars(os.path.expanduser(a.run_root)))
    try:
        configure_archive_base(a.archive_base, root)
    except ValueError as error:
        ap.error(str(error))
    arch = Path(os.path.expandvars(os.path.expanduser(a.archive_root)))
    print(f"run root: {root}\narchive root: {arch}")
    if a.apply and a.hard_delete:
        print("MODE: HARD DELETE (irreversible)")
    elif a.apply:
        print("MODE: quarantine (reversible -- dirs are moved, not destroyed)")

    tot_m = tot_r = 0
    for d in a.designs:
        d = d if d.startswith("design_") else f"design_{int(d):03d}"
        m, r = process(root / d / a.system, a.nevents, d, arch, a.apply,
                       a.hard_delete, root / d, a.limit)
        tot_m += m; tot_r += r
    print(f"\ntotal: {tot_m} removed, {tot_r} refused")
    if a.apply and tot_m:
        print("next: campaign.py --config CFG events --submit (mode auto or both) "
              "to regenerate them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
