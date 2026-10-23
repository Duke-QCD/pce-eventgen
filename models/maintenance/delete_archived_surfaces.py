#!/usr/bin/env python3
"""
delete_archived_surfaces.py — safe, gated deletion of CFS surface.dat.zst archives.

DRY-RUN BY DEFAULT.  Nothing is deleted unless you pass --apply.

Why this is safe to run:
  * Every invocation does a FRESH scan of the raw/scratch tree and re-verifies the
    safety net before touching a design's archives.  It does not trust any earlier
    survey snapshot, so a stale list cannot cause a bad delete.
  * It only ever removes files whose name is exactly 'surface.dat.zst'.  It never
    rm -rf's a directory and never touches particle lists, markers, or anything else.
  * Both deletion paths refuse any path that does not resolve under the archive root.
  * It refuses to run if the archive root is missing, and warns loudly if it finds
    zero archives (which almost always means the archive path is wrong).

Two categories of deletable design:

  READY  — a design is deletable if its VERIFIED-GOOD events reach --ready-threshold
           (default 0.99) of target.  An event is verified-good iff it has
           afterburner.done AND a nonempty particle list, OR is a legitimate
           hydro.no_surface event.  A merely-nonempty particle file with no
           afterburner.done does NOT count as good — it is recognized as un-verified
           and counted against the threshold, and the per-design shortfall is
           reported.  NOTE: verified-good means afterburner COMPLETED cleanly; it is
           not the same as having passed the full cache parser.  Deleting surfaces
           for ready-but-uncached designs sacrifices the cheap afterburner-rerun
           fallback if cache later rejects events, so it requires --allow-uncached-ready.
           With threshold < 1.0, up to (1-threshold) of a design's events may be
           un-verified and have their surfaces deleted (cheap, bounded, but a wrong
           one needs a HYDRO rerun, since the surface is afterburner's input).

  REAPED — particle lists were already cached and removed from scratch, so these
           have afterburner.done markers but no local particle files.  This state
           is INDISTINGUISHABLE by scan from 'never produced particles', so reaped
           designs must be named explicitly with --reaped and the script will only
           confirm they match the reaped signature (particles == 0, and
           ab_done + no_surface >= --ready-threshold of target).  The real safety
           net for these is YOUR knowledge that they were cached; the script says so
           loudly for each one.  Reaped designs delete without --allow-uncached-ready.

Typical use:
  1. Dry run (default) — verifies the safety net and writes a manifest:
       python delete_archived_surfaces.py \
         --raw-root $HIC_RAW_ROOT \
         --archive-root $HIC_SURFACE_ARCHIVE_ROOT \
         --system AuAu200
     (writes surface_delete_manifest.json with the exact verified plan)

  2a. Apply with a FRESH re-scan (safest, slower — re-verifies everything live):
       python delete_archived_surfaces.py ... --apply

  2b. Apply from the manifest the dry run just wrote (faster — skips the expensive
      re-scan, deletes exactly the verified file list).  Refuses a stale manifest:
       python delete_archived_surfaces.py --from-manifest surface_delete_manifest.json --apply

  The manifest path is the speed/safety trade you asked for: 2b trusts the dry run's
  live verification instead of repeating it, so it is only valid for a short window
  (default 120 min) and refuses if older.  Run it right after the dry run.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from surface_archive import archived_surface_path, configure_archive_base  # noqa: E402

PARTICLE_FILES = ("particle_lists.oscar", "particle_lists.oscar.unfinished")
SURFACE_ARCHIVE_NAME = "surface.dat.zst"


def expand(s: Optional[str]) -> Optional[Path]:
    if s is None:
        return None
    return Path(os.path.expandvars(os.path.expanduser(s)))


def event_dirs(system_dir: Path):
    """Yield existing numeric event directories under a design/system dir."""
    try:
        with os.scandir(system_dir) as it:
            for e in it:
                if e.is_dir() and e.name.isdigit():
                    yield Path(e.path)
    except OSError:
        return


def has_nonempty_particle(ev: Path, names: set) -> bool:
    for pf in PARTICLE_FILES:
        if pf in names:
            try:
                if (ev / pf).is_file() and (ev / pf).stat().st_size > 0:
                    return True
            except OSError:
                pass
    return False


def scan_design(system_dir: Path) -> Tuple[int, int, int, int, int]:
    """Fresh scan -> (n_event_dirs, n_particles, n_no_surface, n_ab_done, n_good).

    n_good is the count of VERIFIED-complete events: afterburner.done AND a nonempty
    particle list (or a legitimate hydro.no_surface event), the same terminal-event
    standard as the campaign survey, so a nonempty-but-incomplete particle file does
    NOT count as good and is correctly recognized as incomplete.
    """
    n_dirs = n_particles = n_no_surface = n_ab_done = n_good = 0
    for ev in event_dirs(system_dir):
        n_dirs += 1
        try:
            names = {x.name for x in os.scandir(ev)}
        except OSError:
            continue
        has_part = has_nonempty_particle(ev, names)
        has_done = "afterburner.done" in names
        has_nosurf = "hydro.no_surface" in names
        if has_part:
            n_particles += 1
        if has_done:
            n_ab_done += 1
        if has_nosurf:
            n_no_surface += 1
        # Verified-good: completed afterburner with real output, or legit no-surface.
        if (has_done and has_part) or has_nosurf:
            n_good += 1
    return n_dirs, n_particles, n_no_surface, n_ab_done, n_good


def classify(system_dir: Path, target: int, reaped_set: set, design: str, threshold: float) -> Tuple[str, str, Tuple]:
    """Return (decision, reason, stats).  decision in {ready, reaped, skip}.

    'good' events (afterburner.done + nonempty particle, or hydro.no_surface) must
    reach `threshold` of target.  A design with up to (1-threshold) of its events
    not-verified is still deletable, but those un-verified events are counted and
    reported, not hidden -- so 'incomplete is recognized as incomplete' holds.
    """
    n_dirs, n_particles, n_no_surface, n_ab_done, n_good = scan_design(system_dir)
    stats = (n_dirs, n_particles, n_no_surface, n_ab_done, n_good)
    need = math.ceil(threshold * target)
    pct = int(round(threshold * 100))

    if design in reaped_set:
        # Reaped: particle lists were cached and removed.  Require near-full accounting
        # of afterburner.done + no_surface (tightened from a loose 90% floor).  100%
        # would wrongly reject designs like 007 (9996 ab_done), so use the threshold.
        accounted = n_ab_done + n_no_surface
        if n_particles == 0 and accounted >= need:
            return "reaped", (f"reaped signature ok (ab_done={n_ab_done}, no_surface={n_no_surface}, "
                             f"accounted={accounted} >= {need}={pct}%, particles=0)"), stats
        return "skip", (f"in --reaped list but signature off "
                        f"(particles={n_particles}, ab_done+no_surface={accounted} < {need}={pct}%)"), stats

    # READY: verified-good events must reach the threshold.
    short = target - n_good
    if n_good >= need and n_good > 0:
        return "ready", (f"verified good={n_good}/{target} "
                        f"(ab_done+particle, or no_surface); {short} un-verified, "
                        f"within {100 - pct}% tolerance"), stats

    return "skip", (f"not deletable: verified good={n_good}/{target} (need {need}={pct}%); "
                    f"particles={n_particles}, ab_done={n_ab_done}, no_surface={n_no_surface}"), stats


def find_archives(archive_design_dir: Path) -> List[Path]:
    """All surface.dat.zst files under a design's archive subtree (by exact name)."""
    out: List[Path] = []
    if not archive_design_dir.is_dir():
        return out
    for root, _dirs, files in os.walk(archive_design_dir):
        for fn in files:
            if fn == SURFACE_ARCHIVE_NAME:
                out.append(Path(root) / fn)
    return out


def write_manifest(path: Path, args, plan) -> None:
    """Persist the verified plan so --apply --from-manifest can delete without re-scanning."""
    now = time.time()
    entries = []
    total = 0
    for design, decision, reason, n_zst, archive_design_dir, stats, zst in plan:
        n_dirs, n_particles, n_no_surface, n_ab_done, n_good = stats
        entries.append({
            "design": design,
            "category": decision,
            "reason": reason,
            "stats": {"event_dirs": n_dirs, "particles": n_particles,
                      "no_surface": n_no_surface, "ab_done": n_ab_done, "good": n_good},
            "archive_design_dir": str(archive_design_dir),
            "n_zst": n_zst,
            "zst": [str(p) for p in zst],
        })
        total += n_zst
    payload = {
        "schema": "surface_delete_manifest.v1",
        "created_at": datetime.datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "created_epoch": now,
        "raw_root": str(expand(args.raw_root)),
        "archive_root": str(expand(args.archive_root)),
        "archive_base": os.environ.get("HIC_SURFACE_ARCHIVE_BASE"),
        "system": args.system,
        "target": args.target,
        "reaped": sorted({d.strip() for d in args.reaped.split(",") if d.strip()}),
        "total_zst": total,
        "entries": entries,
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)


def apply_from_manifest(args) -> None:
    """Delete exactly the files recorded in a manifest, after staleness/consistency gates."""
    mpath = expand(args.from_manifest)
    if mpath is None or not mpath.is_file():
        raise SystemExit(f"ERROR: manifest not found: {mpath}")
    try:
        m = json.loads(mpath.read_text())
    except Exception as exc:
        raise SystemExit(f"ERROR: cannot parse manifest {mpath}: {exc}")
    if m.get("schema") != "surface_delete_manifest.v1":
        raise SystemExit(f"ERROR: unexpected manifest schema in {mpath}")

    created = float(m.get("created_epoch", 0))
    age_min = (time.time() - created) / 60.0
    archive_root = Path(m.get("archive_root", ""))
    total_zst = int(m.get("total_zst", 0))
    entries = m.get("entries", [])

    print("=== delete_archived_surfaces: APPLY FROM MANIFEST ===")
    print(f"manifest     : {mpath}")
    print(f"created_at   : {m.get('created_at')}  ({age_min:.1f} min ago)")
    print(f"archive-root : {archive_root}")
    print(f"system       : {m.get('system')}   target events: {m.get('target')}")
    print(f"designs      : {len(entries)}   total surface.dat.zst recorded: {total_zst}")
    print("-" * 78)

    # Gate 1: staleness. The manifest skips live re-verification, so it is only valid
    # briefly. Refuse if older than the limit unless explicitly overridden.
    if age_min > args.max_manifest_age_min and not args.allow_stale_manifest:
        raise SystemExit(
            f"ERROR: manifest is {age_min:.0f} min old (limit {args.max_manifest_age_min}). "
            f"The safety net was verified that long ago and is not being re-checked. "
            f"Re-run the dry run to refresh it, or pass --allow-stale-manifest to override.")

    # Gate 2: the archive root must still exist.
    if not archive_root.is_dir():
        raise SystemExit(f"ERROR: archive-root from manifest does not exist: {archive_root}. Refusing.")

    # Gate 3: if the caller also passed --archive-root, it must match (catch wrong manifest).
    if args.archive_root is not None:
        passed = expand(args.archive_root)
        if str(passed) != str(archive_root):
            raise SystemExit(
                f"ERROR: --archive-root ({passed}) does not match manifest archive_root "
                f"({archive_root}). Refusing in case this is the wrong manifest.")

    if total_zst == 0:
        print("Manifest records zero archives to delete. Nothing to do.")
        return

    if not args.apply:
        print("Loaded manifest OK and passed staleness/consistency gates.")
        print("This is the from-manifest path; add --apply to actually delete.")
        return

    print("--apply: deleting surface.dat.zst files recorded in manifest...")
    archive_root_resolved = archive_root.resolve()
    deleted = already_gone = errors = refused = 0
    for e in entries:
        design = e.get("design", "?")
        paths = e.get("zst", [])
        d_ok = d_gone = 0
        for s in paths:
            p = Path(s)
            if p.name != SURFACE_ARCHIVE_NAME:   # paranoia: never delete anything else
                continue
            # Path containment: refuse anything that does not resolve under the archive
            # root, protecting against a wrong/edited manifest deleting files elsewhere.
            try:
                p.resolve().relative_to(archive_root_resolved)
            except ValueError:
                refused += 1
                print(f"  REFUSING path outside archive root: {p}", file=sys.stderr)
                continue
            try:
                p.unlink()
                d_ok += 1
            except FileNotFoundError:
                d_gone += 1
            except OSError as exc:
                errors += 1
                print(f"  ERROR deleting {p}: {exc}", file=sys.stderr)
        deleted += d_ok
        already_gone += d_gone
        note = f"  ({d_gone} already gone)" if d_gone else ""
        print(f"  {design:12s} {e.get('category',''):7s}  deleted {d_ok}/{len(paths)}{note}")

    print("-" * 78)
    print(f"DONE. deleted {deleted} surface.dat.zst file(s); {already_gone} already absent.")
    if refused:
        print(f"  {refused} path(s) REFUSED for being outside the archive root.", file=sys.stderr)
    if errors:
        print(f"  {errors} deletion error(s) — see above.", file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw-root", default=os.environ.get("HIC_RAW_ROOT"),
                    help="Raw campaign root holding design_*/SYSTEM/<event dirs> (for safety-net "
                         "verification). Default: HIC_RAW_ROOT. REQUIRED.")
    ap.add_argument("--archive-root", default=os.environ.get("HIC_SURFACE_ARCHIVE_ROOT"),
                    help="Surface archive root (the same root run-events archives to); archives are "
                         "found relative to the configured archive base, including nested campaigns. Default: "
                         "HIC_SURFACE_ARCHIVE_ROOT. REQUIRED.")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--archive-base", default=None,
                    help="Original source base for archive paths (default: HIC_SURFACE_ARCHIVE_BASE); "
                         "only used for scans, since a saved manifest contains exact archive paths.")
    ap.add_argument("--target", type=int, default=10000, help="Expected events per design.")
    ap.add_argument("--reaped", default="",
                    help="Comma-separated design names to treat as reaped (deleted on your assertion "
                         "they were cached, after signature check). Default: none.")
    ap.add_argument("--designs", default=None,
                    help="Optional comma-separated subset of design names to consider (default: all under raw-root).")
    ap.add_argument("--ready-threshold", type=float, default=0.99,
                    help="Fraction of events that must be verified-good (ab_done+particle or no_surface) "
                         "for a design to be deletable. Default 0.99. Applies to ready and reaped.")
    ap.add_argument("--allow-uncached-ready", action="store_true",
                    help="Permit deleting surfaces for READY-but-not-cached designs. These have verified "
                         "particle output but have NOT passed the full cache parser, so deleting their "
                         "surfaces sacrifices the cheap afterburner-rerun fallback if cache later rejects "
                         "events. Reaped designs delete without this flag; ready designs require it.")
    ap.add_argument("--apply", action="store_true", help="Actually delete. Without this flag, dry-run only.")
    ap.add_argument("--manifest", default="surface_delete_manifest.json",
                    help="Where the dry run/scan writes the verified plan. Default: ./surface_delete_manifest.json")
    ap.add_argument("--no-manifest", action="store_true", help="Do not write a manifest during the scan.")
    ap.add_argument("--from-manifest", default=None,
                    help="Delete using a previously written manifest instead of re-scanning (faster). "
                         "Skips live safety-net re-verification; gated by manifest age.")
    ap.add_argument("--max-manifest-age-min", type=float, default=120.0,
                    help="Refuse a --from-manifest older than this many minutes (default 120).")
    ap.add_argument("--allow-stale-manifest", action="store_true",
                    help="Override the manifest age limit (use only if you know nothing has changed).")
    args = ap.parse_args()

    if args.from_manifest is not None:
        apply_from_manifest(args)
        return

    raw_root = expand(args.raw_root)
    archive_root = expand(args.archive_root)
    reaped_set = {d.strip() for d in args.reaped.split(",") if d.strip()}
    subset = {d.strip() for d in args.designs.split(",")} if args.designs else None

    if raw_root is None or not raw_root.is_dir():
        raise SystemExit(f"raw-root is not a directory: {raw_root}")
    try:
        configure_archive_base(args.archive_base, raw_root)
    except ValueError as error:
        ap.error(str(error))
    if archive_root is None:
        raise SystemExit("ERROR: --archive-root is required (or set HIC_SURFACE_ARCHIVE_ROOT). "
                         "This is the CFS path holding surface.dat.zst files. Refusing to guess.")
    if not archive_root.is_dir():
        raise SystemExit(f"ERROR: archive-root does not exist: {archive_root}. Refusing to proceed.")

    mode = "APPLY (deleting)" if args.apply else "DRY-RUN (no deletions)"
    print(f"=== delete_archived_surfaces: {mode} ===")
    print(f"raw-root     : {raw_root}")
    print(f"archive-root : {archive_root}")
    print(f"system       : {args.system}   target events: {args.target}")
    print(f"ready threshold  : {int(round(args.ready_threshold*100))}% verified-good per design")
    print(f"uncached ready   : {'ALLOWED (--allow-uncached-ready)' if args.allow_uncached_ready else 'withheld (reaped only)'}")
    print(f"reaped (explicit): {sorted(reaped_set)}")
    print("-" * 78)

    design_dirs = sorted(p for p in raw_root.glob("design_*") if (p / args.system).is_dir())
    if subset is not None:
        design_dirs = [p for p in design_dirs if p.name in subset]

    plan = []  # (design, decision, reason, n_zst, archive_design_dir, stats, zst)
    held = []  # ready-but-uncached designs withheld for lack of --allow-uncached-ready
    total_zst = 0
    for ddir in design_dirs:
        design = ddir.name
        system_dir = ddir / args.system
        decision, reason, stats = classify(system_dir, args.target, reaped_set, design, args.ready_threshold)
        if decision == "skip":
            continue
        if decision == "ready" and not args.allow_uncached_ready:
            held.append((design, reason))
            continue
        archive_design_dir = archived_surface_path(archive_root, system_dir / "0").parent.parent
        zst = find_archives(archive_design_dir)
        plan.append((design, decision, reason, len(zst), archive_design_dir, stats, zst))
        total_zst += len(zst)

    # Report
    deletable = [p for p in plan if p[3] > 0]
    no_archive = [p for p in plan if p[3] == 0]

    print(f"{'design':12s} {'category':7s} {'zst':>7s}  detail")
    for design, decision, reason, n_zst, _adir, _stats, _zst in plan:
        flag = "" if n_zst > 0 else "  [NO ARCHIVES FOUND]"
        loud = "  <-- on your cache assertion" if decision == "reaped" else ""
        print(f"{design:12s} {decision:7s} {n_zst:7d}  {reason}{flag}{loud}")

    print("-" * 78)
    print(f"designs selected: {len(plan)}   with archives: {len(deletable)}   "
          f"total surface.dat.zst: {total_zst}")
    if held:
        print(f"\n{len(held)} READY-but-uncached design(s) WITHHELD (pass --allow-uncached-ready "
              f"to include): {', '.join(d for d, _ in held)}")
        print("  These have verified particle output but have not passed the full cache parser;")
        print("  deleting their surfaces sacrifices the cheap afterburner-rerun fallback.")

    if total_zst == 0:
        print("\n*** WARNING: zero surface.dat.zst found for any selected design. ***")
        print("    The --archive-root path is almost certainly wrong, or surfaces are not")
        print("    archived where expected. NOT proceeding with any deletion. Check the path:")
        print(f"      find {archive_root} -name '{SURFACE_ARCHIVE_NAME}' | head")
        return

    if no_archive:
        print(f"\nNote: {len(no_archive)} selected design(s) had no archives under "
              f"{archive_root}/<design> — skipped, nothing to delete there.")

    # Persist the verified plan (audit record + input for --from-manifest), unless suppressed.
    if not args.no_manifest:
        mpath = expand(args.manifest)
        try:
            write_manifest(mpath, args, plan)
            print(f"\nWrote manifest: {mpath}  ({total_zst} archives across {len(deletable)} design(s))")
            if not args.apply:
                print("To delete using this exact verified list without re-scanning:")
                print(f"  python {Path(sys.argv[0]).name} --from-manifest {mpath} --apply")
        except OSError as exc:
            print(f"\nWARNING: could not write manifest {mpath}: {exc}", file=sys.stderr)

    if not args.apply:
        print("\nDRY-RUN complete. Nothing deleted.")
        print("Review the list above. If it is correct, re-run with --apply (fresh scan) "
              "or --from-manifest (faster) to delete.")
        return

    # APPLY: delete, per design, only exact surface.dat.zst files.
    print("\n--apply: deleting surface.dat.zst files...")
    archive_root_resolved = archive_root.resolve()
    deleted = 0
    errors = 0
    refused = 0
    for design, decision, _reason, n_zst, _adir, _stats, zst in plan:
        if n_zst == 0:
            continue
        d_ok = 0
        for f in zst:
            if f.name != SURFACE_ARCHIVE_NAME:   # paranoia: never delete anything else
                continue
            # Containment: refuse anything not resolving under the archive root
            # (e.g. archive_root/design is a symlink pointing elsewhere). Mirrors
            # the from-manifest guard so both deletion paths are symmetric.
            try:
                f.resolve().relative_to(archive_root_resolved)
            except ValueError:
                refused += 1
                print(f"  REFUSING path outside archive root: {f}", file=sys.stderr)
                continue
            try:
                f.unlink()
                d_ok += 1
            except OSError as exc:
                errors += 1
                print(f"  ERROR deleting {f}: {exc}", file=sys.stderr)
        deleted += d_ok
        print(f"  {design:12s} {decision:7s}  deleted {d_ok}/{n_zst}")

    print("-" * 78)
    print(f"DONE. deleted {deleted} surface.dat.zst file(s) across {len(deletable)} design(s).")
    if refused:
        print(f"  {refused} path(s) REFUSED for being outside the archive root.", file=sys.stderr)
    if errors:
        print(f"  {errors} deletion error(s) — see above.", file=sys.stderr)


if __name__ == "__main__":
    main()
