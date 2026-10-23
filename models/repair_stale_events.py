#!/usr/bin/env python3
"""Audit and repair stale per-event directories in a run (results) folder.

A "stale hydro" directory is one created for an event that never produced a
usable hydro surface: the event directory exists, there is no hydro.done marker,
no surface.dat, no afterburner markers/outputs, no lock, and every file present
is a known initial-condition/setup or MUSIC byproduct file.  Such a directory
holds no irreproducible data -- the initial condition is regenerated
deterministically from initial.hdf -- so the safe repair is to DELETE it and let
the next pass reinitialize the event.  No backup is kept on purpose: backups
would accumulate on scratch, and a directory only qualifies as safe-stale when
it contains nothing worth preserving.

Anything that does not cleanly match the safe-stale pattern (a partial
surface.dat, a particle list without afterburner.done, an active lock,
unexpected files, ...) is reported for manual inspection and never deleted.

Dry-run by default; pass --apply to mutate event state. Dry-run still writes
route summaries and audit logs.

With --finalize-archive-failed and --archive-root, completed particle output can
be finalized without rerunning afterburner. The archive layout is
<archive-root>/<campaign>/<design>/<system>/<event>/surface.dat.zst, with any
additional parents preserved relative to HIC_SURFACE_ARCHIVE_BASE (or the legacy
scratch-root fallback when unset). Explicit
archive filenames in failure markers must agree with that derived destination.
--archive-remove-local also cleans retained surfaces on later passes and always
compares decompressed archive bytes with the original before deleting it.
Interrupted done/failed marker transitions are retryable. Known cache-rejected
output and structurally incomplete output are never finalized.

Concurrent repair scans of the same run directory are excluded by flock; event
workers must still be stopped separately. Operational archive errors/refusals
are logged and cause exit status 2, allowing run_designs.py to stop submission.
Incomplete outputs remain blocked in the route summary without an error exit.

Intended to be run BETWEEN event-running passes (e.g. orchestrated by
run_designs.py before Slurm submission), when no job is writing the event
directories.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from surface_archive import (SURFACE_ARCHIVE_NAME, archive_relative_path, archived_surface_path,
                             configure_archive_base)


HYDRO_DONE = "hydro.done"
HYDRO_NO_SURFACE = "hydro.no_surface"
SURFACE = "surface.dat"
AFTERBURNER_DONE = "afterburner.done"
AFTERBURNER_FAILED = "afterburner.failed"
AFTERBURNER_RETRY_COUNT = "afterburner.retry_count"
ARCHIVE_FAILED = "archive.failed"
AFTERBURNER_LOCK = "afterburner.lock"
RESULTS_DIR = "results"
AVERAGE_THERMO = "average_thermodynamic_quantities.dat"
READY_TO_CACHE = "ready_to_cache.json"
# Written by reap_raw_particle_lists.py after a fully verified reap. Its presence means the
# design is complete and its raw is gone (per-event dirs may be removed to reclaim inodes), so
# repair must NOT scan or re-route its events -- it would misread a reaped design as stale.
REAPED_MARKER = "reaped.done"

# Archive-finalize recovery: an event whose afterburner COMPLETED but whose surface
# archive to CFS failed (archive.failed, possibly alongside afterburner.done) can be
# finalized in place -- re-archive the local surface, verify, write afterburner.done,
# clear archive.failed -- without re-running afterburner.  Gated by
# --finalize-archive-failed + --archive-root; fail-closed if anything is off.
# SMASH event-end footers carry trailing fields, e.g.
#   # event 7 end 0 impact -1.000 scattering_projectile_target no
# so match the 'event N end' prefix with a word boundary, NOT anchored at end-of-line.
_EVENT_END_RE = re.compile(r"^#\s*event\s+\d+\s+end\b")
# Pull the intended .zst target out of the archive.failed marker's error line.
_MARKER_PATH_RE = re.compile(r"(/\S*?surface\.dat\.zst)(?:\.tmp\.\d+\.\d+)?")
# Written by cache_one_design.py when its full parse rejects events (mid-file gaps the cheap
# head/tail check here cannot see).  Repair consumes it: blocks readiness for the listed events
# and, under --apply, cleans them for rerun.
CACHE_BAD_EVENTS = "cache_bad_events.json"

# Always-written per-design route summary consumed by run_designs.py --mode auto.
# This is the single authoritative classification: it is computed from the same
# pass that performs lock-clearing/stale-deletion, so the route reflects the
# post-repair on-disk state rather than a second, possibly-divergent scan.
CAMPAIGN_STATE = "campaign_state.json"

# Bucket -> route-class mapping for mode=auto.  Any classify_event() bucket NOT
# named in one of these sets (and not failed_afterburner_exhausted) is treated as
# blocked: route never guesses a production mode for an ambiguous state.
ROUTE_READY_BUCKETS = {
    "complete",
    "done_with_surface_present",
    "hydro_no_surface",
    # An archive-failed event finalized in place (surface re-archived + verified,
    # afterburner.done written) is now terminal-complete like done_with_surface_present.
    "archive_failed_finalized",
    "archive_surface_removed",
}
ROUTE_HYDRO_INCOMPLETE_BUCKETS = {
    "safe_stale_hydro",
    "safe_stale_hydro_would_repair",
    "safe_stale_hydro_repaired",
    # Orphaned surface with no hydro.done: repair deletes the dir (--apply) and
    # mode=both regenerates hydro.  Both the applied and dry-run buckets are
    # hydro-incomplete so the design routes 'both'.
    "ambiguous_surface_no_hydro_done",
    "ambiguous_surface_no_hydro_done_would_repair",
    "ambiguous_surface_no_hydro_done_repaired",
}
ROUTE_AFTERBURNER_BUCKETS = {
    "afterburner_retryable",
    "failed_afterburner_retryable",
    # A broken particle list with a local surface is cleaned by repair (--apply)
    # and becomes a fresh afterburner attempt next pass; route it to afterburner.
    "broken_particle_list",
    "broken_particle_list_would_repair",
    "broken_particle_list_repaired",
}
ROUTE_INPROGRESS_BUCKETS = {
    "running_locked",
    # A would-clear lock (dry-run) is still held, so the event is genuinely
    # in-progress.  An actually-cleared lock (--apply) is now free but was not
    # reclassified this pass; it goes to ROUTE_RECHECK_BUCKETS instead so the next
    # pass classifies the unlocked event on real state rather than waiting on it.
    "stale_lock_would_clear",
}
# States that an --apply pass changed but did not reclassify: route 'recheck'
# (skip + rescan next pass) rather than guessing.  Cheaper and safer than an
# in-loop reclassify, which would couple the route back to mid-pass count edits.
ROUTE_RECHECK_BUCKETS = {
    "stale_lock_cleared",
    # Dry-run only: an --apply pass will finalize these in place (re-archive +
    # afterburner.done), so they are not blocked -- skip and re-scan after apply.
    "archive_failed_would_finalize",
    "archive_surface_would_remove",
}

PARTICLE_FILES = ("particle_lists.oscar", "particle_lists.oscar.unfinished")

OSCAR_HEADER_LINE = "#!OSCAR2013 particle_lists t x y z mass p0 px py pz pdg ID charge"
OSCAR_UNITS_LINE = "# Units: fm fm fm fm GeV GeV GeV GeV GeV none none e"
SMASH_LINE_RE = re.compile(r"^# SMASH-\S+$")
FIRST_EVENT_OUT_RE = re.compile(r"^# event 0 out \d+$")
EVENT_MARKER_RE = re.compile(r"^# event\s+(\d+)\s+(out|end)\b")

# Bounded particle-file triage for repair_stale_events.py.  This must stay cheap:
# run_designs.py calls repair serially over designs before submission.  The full
# parser and final scientific validation remain in cache_one_design.py.
PARTICLE_HEAD_BYTES = 64 * 1024
PARTICLE_TAIL_BYTES = 200_000
PARTICLE_EXTENDED_TAIL_BYTES = 2_000_000

# Files that may legitimately remain in an event directory that never finished
# hydro.  Anything else makes a no-hydro.done directory "unexpected", hence
# ambiguous (skipped).  Extend at runtime with --allow-extra-file once a dry run
# shows which MUSIC byproducts your failed-hydro directories actually contain.
DEFAULT_BENIGN_FILES = {
    "trento_initial_condition_in_music_format.txt",
    "eccentricities_evo_eta_-0.5_0.5.dat",
    "momentum_anisotropy_eta_-0.5_0.5.dat",
}

# Event directories are zero-padded integers (e.g. 0000, 0123).  Using this
# pattern means non-event entries (logs, smash_output, repair logs, ...) are
# never classified or touched.
EVENT_DIR_RE = re.compile(r"^\d+$")


def is_event_dir(path):
    return path.is_dir() and EVENT_DIR_RE.match(path.name) is not None


def lock_age_hours(lock_path):
    try:
        mtime = lock_path.stat().st_mtime
    except FileNotFoundError:
        return None
    return (datetime.datetime.now().timestamp() - mtime) / 3600.0


def particle_file_state(event_dir):
    """Return (usable, zero_or_invalid) particle variants for an event dir."""
    usable = []
    zero_or_invalid = []
    for name in PARTICLE_FILES:
        path = Path(event_dir) / name
        if not path.exists():
            continue
        if not path.is_file():
            zero_or_invalid.append(name)
            continue
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            continue
        if size > 0:
            usable.append(name)
        else:
            zero_or_invalid.append(name)
    return sorted(usable), sorted(zero_or_invalid)


def _read_head_lines(path, max_bytes=PARTICLE_HEAD_BYTES):
    with path.open("rb") as f:
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace").splitlines()


def _read_tail_text(path, max_bytes):
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > max_bytes:
            f.seek(-max_bytes, os.SEEK_END)
        data = f.read(max_bytes)
    return data.decode("utf-8", errors="replace")


def _event_markers_from_text(text):
    markers = []
    for raw in text.splitlines():
        line = raw.strip()
        m = EVENT_MARKER_RE.match(line)
        if m:
            markers.append((int(m.group(1)), m.group(2), line))
    return markers


def quick_particle_structure_state(path):
    """Cheap structural triage for a particle list.

    This is deliberately not the full cache parser.  It reads a small head and
    bounded tail to catch obvious bad files before cache, while avoiding a
    serial full-file scan during run_designs.py submission.

    Returns (state, detail).  States:
      complete_structural          header OK; checked tail ends in an end marker
      salvageable_truncated_tail   header OK; checked tail ends in an open "out" block, and a
                                   complete subevent exists before it -- shown either by a prior
                                   end marker in the checked/extended tail, or (for a final
                                   subevent too large to fit even the extended window) inferred
                                   from the open block's event index being > 0
      unknown_particle_structure   header OK but bounded tail is inconclusive (e.g.
                                   "# event 0 out ... [EOF]" -- no evidence of any complete block)
      broken_particle_list         empty/unreadable/bad header/malformed start
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as e:
        return "broken_particle_list", {"reason": "stat_failed", "error": str(e)}

    if size <= 0:
        return "broken_particle_list", {"reason": "empty_file", "size": size}

    try:
        head_lines_all = _read_head_lines(path)
    except OSError as e:
        return "broken_particle_list", {"reason": "head_read_failed", "error": str(e)}

    # The expected header is effectively fixed; only the SMASH version string
    # and the event-0 multiplicity may vary.
    head_lines = [line.rstrip("\r") for line in head_lines_all]
    while head_lines and not head_lines[0].strip():
        head_lines.pop(0)

    if len(head_lines) < 4:
        return "broken_particle_list", {
            "reason": "header_too_short",
            "size": size,
            "head_preview": head_lines[:4],
        }

    if head_lines[0].strip() != OSCAR_HEADER_LINE:
        return "broken_particle_list", {
            "reason": "bad_oscar_header_line",
            "first_line": head_lines[0].strip(),
        }
    if head_lines[1].strip() != OSCAR_UNITS_LINE:
        return "broken_particle_list", {
            "reason": "bad_units_line",
            "second_line": head_lines[1].strip(),
        }
    if not SMASH_LINE_RE.match(head_lines[2].strip()):
        return "broken_particle_list", {
            "reason": "bad_smash_line",
            "third_line": head_lines[2].strip(),
        }
    if not FIRST_EVENT_OUT_RE.match(head_lines[3].strip()):
        return "broken_particle_list", {
            "reason": "bad_first_event_line",
            "fourth_line": head_lines[3].strip(),
        }

    # Tail check.  First try the cheap window; only expand for ambiguous tails
    # where no event marker is visible.
    try:
        tail = _read_tail_text(path, PARTICLE_TAIL_BYTES)
        markers = _event_markers_from_text(tail)
        used_tail_bytes = min(size, PARTICLE_TAIL_BYTES)
        if not markers and size > PARTICLE_TAIL_BYTES:
            tail = _read_tail_text(path, PARTICLE_EXTENDED_TAIL_BYTES)
            markers = _event_markers_from_text(tail)
            used_tail_bytes = min(size, PARTICLE_EXTENDED_TAIL_BYTES)
    except OSError as e:
        return "unknown_particle_structure", {"reason": "tail_read_failed", "error": str(e)}

    if not markers:
        return "unknown_particle_structure", {
            "reason": "no_event_marker_in_checked_tail",
            "checked_tail_bytes": used_tail_bytes,
            "size": size,
        }

    last_event_id, last_kind, last_line = markers[-1]
    if last_kind == "end":
        return "complete_structural", {
            "last_event_marker": last_line,
            "checked_tail_bytes": used_tail_bytes,
        }

    # Last marker is an open ("out") block: a truncated trailing subevent.  Decide whether a
    # complete subevent exists *before* it, using a two-tier approach so we classify on real
    # evidence where possible and only fall back to an index-based inference for the rare giant
    # final subevent.  Either way this is just the production quick check; cache_one_design.py
    # does the authoritative full parse and will reject the event if no complete block survives.

    # Tier 1: prior "end" already visible in the window we read -> confirmed complete block.
    has_prior_end = any(kind == "end" for _, kind, _ in markers[:-1])
    if has_prior_end:
        return "salvageable_truncated_tail", {
            "last_event_marker": last_line,
            "checked_tail_bytes": used_tail_bytes,
            "reason": "prior_end_in_checked_tail",
        }

    # Tier 1b: extend the window once (to the existing extended cap) and re-scan.  A legitimately
    # huge final subevent can push the prior "end" (closing the previous event) beyond the cheap
    # 200 KB window; the extended window very likely contains it, giving real evidence rather
    # than an inference.  Only worth doing if the file is actually larger than what we read.
    if used_tail_bytes < min(size, PARTICLE_EXTENDED_TAIL_BYTES):
        try:
            ext_tail = _read_tail_text(path, PARTICLE_EXTENDED_TAIL_BYTES)
            ext_markers = _event_markers_from_text(ext_tail)
            used_tail_bytes = min(size, PARTICLE_EXTENDED_TAIL_BYTES)
        except OSError as e:
            return "unknown_particle_structure", {
                "reason": "extended_tail_read_failed",
                "error": str(e),
                "last_event_marker": last_line,
            }
        if ext_markers:
            last_event_id, last_kind, last_line = ext_markers[-1]
            # If the extended window now closes the tail, it is structurally complete.
            if last_kind == "end":
                return "complete_structural", {
                    "last_event_marker": last_line,
                    "checked_tail_bytes": used_tail_bytes,
                }
            if any(kind == "end" for _, kind, _ in ext_markers[:-1]):
                return "salvageable_truncated_tail", {
                    "last_event_marker": last_line,
                    "checked_tail_bytes": used_tail_bytes,
                    "reason": "prior_end_in_extended_tail",
                }

    # Tier 2: even the extended window shows no prior "end".  Fall back to the event index of the
    # open trailing block: index > 0 means SMASH had already advanced past event 0, so at least
    # one earlier (presumably complete) subevent exists.  This is an inference, not direct
    # evidence -- reserved for the rare case of a single final subevent so large it fills the
    # whole extended window.  Index 0 (or unparseable) stays unknown: "# event 0 out ... [EOF]"
    # has no evidence of any complete subevent and must not be called salvageable.
    if last_kind == "out" and isinstance(last_event_id, int) and last_event_id > 0:
        return "salvageable_truncated_tail", {
            "last_event_marker": last_line,
            "checked_tail_bytes": used_tail_bytes,
            "reason": "tail_open_block_event_index_gt0_assumed_prior_complete",
        }

    return "unknown_particle_structure", {
        "reason": "tail_open_block_no_prior_checked_end",
        "last_event_marker": last_line,
        "checked_tail_bytes": used_tail_bytes,
    }


def load_cache_bad_events(run_dir):
    """Load cache_bad_events.json if present.  Returns (path, payload) or (path, None).
    Unreadable/malformed -> (path, None) with a warning (treated as absent; the events will be
    re-discovered by the next cache attempt if still bad)."""
    p = Path(run_dir) / CACHE_BAD_EVENTS
    if not p.is_file():
        return p, None
    try:
        return p, json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"[repair_stale_events] WARNING: unreadable {CACHE_BAD_EVENTS}: {e}", file=sys.stderr)
        return p, None


def bad_event_still_pending(run_dir, entry):
    """A cache-flagged bad event is 'still pending' (genuinely the rejected output, not a rerun)
    if its particle file is present and unchanged from the recorded size+mtime fingerprint.
    If the fingerprint is absent (best-effort write failed) we fall back to: any usable particle
    file present + afterburner.done present.  A rerun (newer mtime / different size) is NOT
    pending -- we must not clobber it."""
    ev_dir = Path(run_dir) / event_dir_name_for_id(run_dir, entry["event_id"])
    if not ev_dir.is_dir():
        return False  # gone / not yet produced -> not pending (will be handled as missing)
    name = entry.get("particle_file")
    size = entry.get("size")
    mtime = entry.get("mtime")
    if name is not None and size is not None and mtime is not None:
        f = ev_dir / name
        try:
            st = f.stat()
        except OSError:
            return False  # file gone -> already cleaned/rerun, not pending
        return int(st.st_size) == int(size) and abs(st.st_mtime - float(mtime)) < 1e-6
    # No fingerprint recorded: conservative live check.
    has_done = (ev_dir / AFTERBURNER_DONE).exists()
    has_particle = any((ev_dir / pf).is_file() and (ev_dir / pf).stat().st_size > 0
                       for pf in PARTICLE_FILES)
    return has_done and has_particle


def event_dir_name_for_id(run_dir, event_id):
    """Map a cache event id to its zero-padded directory name, matching the existing event dirs'
    width (cache and repair both zero-pad to the width of the largest index)."""
    # Infer pad width from existing event dirs; fall back to the id's own width.
    widths = {len(p.name) for p in Path(run_dir).iterdir()
              if p.is_dir() and p.name.isdigit()}
    pad = max(widths) if widths else len(str(event_id))
    return str(int(event_id)).zfill(pad)


def clean_afterburner_artifacts(event_dir, backup_root=None):
    """Back up and remove afterburner-side outputs, preserving hydro artifacts."""
    event_dir = Path(event_dir)
    if backup_root is not None:
        backup_dir = Path(backup_root) / event_dir.name
        backup_dir.mkdir(parents=True, exist_ok=True)
    else:
        backup_dir = None

    for name in list(PARTICLE_FILES) + [
        AFTERBURNER_DONE,
        AFTERBURNER_FAILED,
        ARCHIVE_FAILED,
        AFTERBURNER_RETRY_COUNT,
        AVERAGE_THERMO,
        RESULTS_DIR,
    ]:
        path = event_dir / name
        if not path.exists():
            continue
        if backup_dir is not None:
            dst = backup_dir / name
            if path.is_dir():
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(path, dst)
            else:
                shutil.copy2(path, dst)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def read_retry_count(event_dir):
    """Read the persisted afterburner attempt count (0 if absent).

    Mirrors run-events' read_afterburner_retry_count parser so repair can split
    failed_afterburner into still-retryable vs budget-exhausted without importing
    run-events.  Budget-exhausted events must route to 'blocked', not afterburner,
    or mode=auto would re-route them every pass into a no-op (run-events skips an
    event once attempts >= max_afterburner_retries).
    """
    path = Path(event_dir) / AFTERBURNER_RETRY_COUNT
    try:
        text = path.read_text()
    except FileNotFoundError:
        return 0
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("count"):
            try:
                return int(s.split("=", 1)[1].strip())
            except (IndexError, ValueError):
                return 0
    try:
        return int(text.strip())
    except ValueError:
        return 0


def classify_event(event_dir, benign_files, max_afterburner_retries=3):
    """Return (bucket, detail) for one event directory.  First match wins."""
    entries = list(event_dir.iterdir())
    names = {e.name for e in entries}

    has_lock = (event_dir / AFTERBURNER_LOCK).exists()
    has_afterburner_done = AFTERBURNER_DONE in names
    has_failed = AFTERBURNER_FAILED in names
    has_archive_failed = ARCHIVE_FAILED in names
    has_hydro_done = HYDRO_DONE in names
    has_no_surface = HYDRO_NO_SURFACE in names
    has_surface = (event_dir / SURFACE).is_file()
    nonempty_surface = has_surface and (event_dir / SURFACE).stat().st_size > 0
    has_results = (event_dir / RESULTS_DIR).exists()
    usable_particles, zero_particles = particle_file_state(event_dir)

    # A lock means the event may be active (or crashed mid-afterburner); never
    # repaired here, only reported.  --clear-stale-locks handles old locks.
    if has_lock:
        return "running_locked", {"lock_age_hours": lock_age_hours(event_dir / AFTERBURNER_LOCK)}

    # Terminal no-surface event: clean hydro that produced no particlization
    # surface.  Valid zero-output terminal state -> never repaired/deleted.  Any
    # particle output, afterburner.done, hydro.done, or nonempty surface present
    # alongside it is a marker contradiction (reported, not touched).
    if has_no_surface:
        if (has_afterburner_done or has_hydro_done or usable_particles
                or zero_particles or nonempty_surface):
            return "ambiguous_no_surface_conflict", {
                "afterburner_done": has_afterburner_done,
                "hydro_done": has_hydro_done,
                "usable_particles": usable_particles,
                "zero_particles": zero_particles,
                "nonempty_surface": nonempty_surface,
            }
        return "hydro_no_surface", {}

    # Zero-byte/nonregular particle files are not physical zero-particle events;
    # they are ambiguous interrupted/truncated writes.  Likewise, both particle
    # variants being usable at once is a manual-inspection state.
    if zero_particles:
        return "ambiguous_zero_byte_particle", {"particles": zero_particles}

    if len(usable_particles) > 1:
        return "ambiguous_particle_variants", {"particles": usable_particles}

    if has_afterburner_done:
        if has_archive_failed:
            return "ambiguous_done_with_archive_failed", {}
        if usable_particles:
            particle_name = usable_particles[0]
            particle_path = event_dir / particle_name
            structure_state, structure_detail = quick_particle_structure_state(particle_path)
            particle_detail = {
                "particles": usable_particles,
                "particle_structure": structure_state,
                **structure_detail,
            }
            if structure_state == "broken_particle_list":
                if has_hydro_done and nonempty_surface:
                    return "broken_particle_list", particle_detail
                return "broken_particle_no_local_surface", particle_detail
            if structure_state == "unknown_particle_structure":
                return "ambiguous_particle_structure_unknown", particle_detail
            if has_surface:
                return "done_with_surface_present", particle_detail
            return "complete", particle_detail
        return "done_no_particle_file", {}

    # archive.failed is a post-afterburner archival failure, not an afterburner
    # retry state.  Do not delete or rerun here; report it for explicit handling.
    if has_archive_failed:
        if usable_particles:
            return "archive_failed", {"particles": usable_particles}
        return "ambiguous_archive_failed_no_particle", {}

    if usable_particles:
        return "ambiguous_particle_no_done", {"particles": usable_particles}

    if has_hydro_done:
        # Hydro finished; afterburner neither done nor running.  run-events will
        # (re)attempt the afterburner on the next pass within its retry budget.
        if has_failed:
            attempts = read_retry_count(event_dir)
            if attempts >= max_afterburner_retries:
                # Budget exhausted: run-events will skip this event, so mode=auto
                # must NOT route it to afterburner (it would never advance).
                return "failed_afterburner_exhausted", {
                    "attempts": attempts,
                    "max_afterburner_retries": max_afterburner_retries,
                }
            return "failed_afterburner_retryable", {
                "attempts": attempts,
                "max_afterburner_retries": max_afterburner_retries,
            }
        return "afterburner_retryable", {}

    # No hydro.done from here on.
    if has_surface:
        return "ambiguous_surface_no_hydro_done", {}

    if has_results:
        return "ambiguous_unexpected_files", {"unexpected": [RESULTS_DIR]}

    # No progress markers/outputs and no lock: safe only if every remaining
    # entry is a known benign setup/byproduct file.
    unexpected = sorted(e.name for e in entries if e.name not in benign_files)
    if unexpected:
        return "ambiguous_unexpected_files", {"unexpected": unexpected}

    return "safe_stale_hydro", {}


def ready_marker_payload(run_dir, args, counts, particle_structure_seen, event_count):
    # Finalization and verified local cleanup both produce terminal particle events.
    ready_particle = (counts.get("complete", 0)
                      + counts.get("done_with_surface_present", 0)
                      + counts.get("archive_failed_finalized", 0)
                      + counts.get("archive_surface_removed", 0))
    ready_no_surface = counts.get("hydro_no_surface", 0)
    return {
        "schema": "ready_to_cache.v1",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "created_by": "repair_stale_events.py",
        "run_dir": str(run_dir),
        "system": args.system,
        "target_events": args.target_events,
        "event_dirs": event_count,
        "complete_particle_events": int(ready_particle),
        "hydro_no_surface": int(ready_no_surface),
        "terminal_ready_events": int(ready_particle + ready_no_surface),
        "particle_structure_counts": dict(particle_structure_seen),
        "counts": dict(counts),
    }


def marker_is_ready(counts, event_count, target_events):
    # Single source of truth: the same buckets route_from_counts treats as ready,
    # so a finalized archive_failed event counts as marker-ready in the same pass it
    # routes ready (they were previously hardcoded separately and drifted).
    ready_buckets = ROUTE_READY_BUCKETS
    nonready = {k: v for k, v in counts.items() if v and k not in ready_buckets}
    if nonready:
        return False, {"nonready_buckets": nonready}

    ready_total = sum(counts.get(k, 0) for k in ready_buckets)
    if target_events is not None:
        if ready_total < int(target_events):
            return False, {"terminal_ready_events": ready_total, "target_events": int(target_events)}
    elif ready_total < event_count:
        return False, {"terminal_ready_events": ready_total, "event_dirs": event_count}

    return True, {}

def route_from_counts(counts, event_count, target_events):
    """Derive a per-design route for run_designs.py --mode auto from repair buckets.

    Returns (route, tallies).  Route priority is productive-work-first so that a
    sequence of identical `--mode auto` passes converges:

      both        -> any event needs hydro (safe-stale, or a missing event dir).
                     Run hydro/both first; archived-surface afterburner tails are
                     intentionally deferred (mode=both cannot restore them).
      afterburner -> hydro is complete and retryable afterburner work remains.
                     run_designs submits this with restore_archives=True.
      blocked     -> the only remaining non-ready work is ambiguous or
                     budget-exhausted.  Never auto-run; flag for a human.
      recheck     -> an --apply action changed state (e.g. a cleared stale lock)
                     that was not reclassified this pass; skip and re-scan.
      in_progress -> only locks/transient states remain; skip and re-scan.
      ready       -> everything is terminal-ready; cache separately.

    Missing event directories (event_count < target) are folded into the hydro
    bucket, because classify_event only sees directories that exist on disk.
    """
    target = int(target_events) if target_events is not None else int(event_count)
    missing_dirs = max(0, target - int(event_count))

    def tally(bucket_set):
        return sum(v for k, v in counts.items() if k in bucket_set)

    ready = tally(ROUTE_READY_BUCKETS)
    hydro_incomplete = tally(ROUTE_HYDRO_INCOMPLETE_BUCKETS) + missing_dirs
    ab_remaining = tally(ROUTE_AFTERBURNER_BUCKETS)
    ab_exhausted = int(counts.get("failed_afterburner_exhausted", 0))
    recheck = tally(ROUTE_RECHECK_BUCKETS)
    in_progress = tally(ROUTE_INPROGRESS_BUCKETS)

    known = (
        ROUTE_READY_BUCKETS
        | ROUTE_HYDRO_INCOMPLETE_BUCKETS
        | ROUTE_AFTERBURNER_BUCKETS
        | ROUTE_RECHECK_BUCKETS
        | ROUTE_INPROGRESS_BUCKETS
        | {"failed_afterburner_exhausted"}
    )
    ambiguous = sum(v for k, v in counts.items() if v and k not in known)
    blocked = ambiguous + ab_exhausted

    tallies = {
        "event_dirs": int(event_count),
        "target_events": target,
        "missing_dirs": missing_dirs,
        "ready": ready,
        "hydro_incomplete": hydro_incomplete,
        "ab_remaining": ab_remaining,
        "ab_exhausted": ab_exhausted,
        "blocked": blocked,
        "recheck": recheck,
        "in_progress": in_progress,
    }

    # Priority: do productive work first; then re-scan states we just changed;
    # then flag the genuinely stuck; then wait on active work; else ready.
    if hydro_incomplete > 0:
        route = "both"
    elif ab_remaining > 0:
        route = "afterburner"
    elif recheck > 0:
        route = "recheck"
    elif blocked > 0:
        route = "blocked"
    elif in_progress > 0:
        route = "in_progress"
    else:
        route = "ready"

    return route, tallies


def _particle_ends_cleanly(p):
    """True if the OSCAR particle list ends on a clean '# event N end' marker."""
    try:
        with open(p, "rb") as fh:
            try:
                fh.seek(-4096, os.SEEK_END)
            except OSError:
                fh.seek(0)
            tail = fh.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return False
    for line in reversed(tail):
        if line.strip():
            return bool(_EVENT_END_RE.match(line.strip()))
    return False


def _parse_archive_target(marker_path):
    """Extract the intended surface.dat.zst path from an archive.failed marker."""
    try:
        text = Path(marker_path).read_text()
    except OSError:
        return None
    m = _MARKER_PATH_RE.search(text)
    return m.group(1) if m else None


def _derive_archive_target(event_dir, run_dir, archive_root):
    """The event's archive path, using the layout shared with run-events."""
    if len(run_dir.parts) < 4 or event_dir.parent.resolve() != run_dir.resolve():
        return None
    return archived_surface_path(archive_root, run_dir / event_dir.name)


def _resolve_archive_target(event_dir, run_dir, archive_root_resolved):
    """Derive a destination; explicit marker filenames must agree with it."""
    try:
        if event_dir.is_symlink():
            return None, "refused", "refusing a symlinked event directory"
        target = _derive_archive_target(event_dir, run_dir, archive_root_resolved)
        if target is None:
            return None, "refused", f"cannot derive archive target from {run_dir}"
        resolved = target.resolve()
        resolved.relative_to(archive_root_resolved)
        if target.name != SURFACE_ARCHIVE_NAME:
            return None, "refused", f"unexpected archive filename: {target}"
        # Missing marker is legitimate for later cleanup of completed events.
        # A read error is NOT evidence that there is no conflicting destination.
        try:
            marker = (event_dir / ARCHIVE_FAILED).read_text()
        except FileNotFoundError:
            marker = ""
        for match in _MARKER_PATH_RE.finditer(marker):
            if Path(match.group(1)).resolve() != resolved:
                return None, "refused", (f"marker target {match.group(1)} "
                                         f"disagrees with derived {target}")
        return target, None, None
    except ValueError:
        return None, "refused", "derived archive target escapes archive root"
    except (OSError, RuntimeError, UnicodeError) as exc:
        return None, "error", f"cannot resolve archive target: {exc}"


def _zstd_test(zstd_bin, path):
    try:
        return subprocess.run([zstd_bin, "-t", str(path)], capture_output=True).returncode == 0
    except OSError:
        return False


def _zstd_full_verify(zstd_bin, archive, original):
    """Compare decompressed bytes using bounded memory; always reap the child."""
    try:
        with Path(original).open("rb") as source, tempfile.TemporaryFile() as errors:
            with subprocess.Popen([zstd_bin, "-dc", str(archive)],
                                  stdout=subprocess.PIPE, stderr=errors) as proc:
                try:
                    while True:
                        chunk = proc.stdout.read(1024 * 1024)
                        if not chunk:
                            return not source.read(1) and proc.wait() == 0
                        if chunk != source.read(len(chunk)):
                            return False
                finally:
                    proc.stdout.close()
                    if proc.poll() is None:
                        proc.terminate()
                    proc.wait()
    except OSError:
        return False


def _atomic_write_text(path, text):
    """Publish a complete marker or summary, never a partially written file."""
    path = Path(path)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".repair-tmp.",
                                         delete=False) as fh:
            tmp = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _afterburner_done_text(template, prior_failure):
    if template is not None:
        # An explicitly requested template must not silently fall back to a stub.
        text = Path(template).read_text()
        if not text.strip():
            raise ValueError("afterburner.done template is empty")
        return text
    now = datetime.datetime.now().isoformat(timespec="seconds")
    return (
        "finalized_by = repair_stale_events.py (archive-finalize)\n"
        f"timestamp = {now}\n"
        f"hostname = {os.uname().nodename}\n"
        f"pid = {os.getpid()}\n"
        "note = pre-existing particle output passed bounded structure and footer "
        "checks; surface archive verified (full cache parsing still required)\n"
        f"prior_archive_failure = {prior_failure}\n"
    )


def _archive_preflight(event_dir, run_dir, archive_root_resolved, args):
    """Shared read-only eligibility checks for dry-run and apply.

    Eligibility does not predict future CFS write success. When an old finalizer
    already dropped the local surface, only the existing archive can be tested.
    Full byte comparison is impossible in that legacy state and is refused if
    explicitly requested.
    """
    try:
        if args.afterburner_done_template is not None:
            _afterburner_done_text(args.afterburner_done_template, "")
        if (event_dir / AFTERBURNER_LOCK).exists():
            return None, "refused", "afterburner lock present"
        if (event_dir / HYDRO_NO_SURFACE).exists():
            return None, "refused", "conflicting hydro.no_surface marker"
        usable, invalid = particle_file_state(event_dir)
        if invalid or len(usable) != 1:
            return None, "not_ready", "requires exactly one nonempty particle file"
        particle = event_dir / usable[0]
        state, detail = quick_particle_structure_state(particle)
        if state != "complete_structural" or not _particle_ends_cleanly(particle):
            return None, "not_ready", f"particle output is not complete: {state}: {detail}"
        surf = event_dir / SURFACE
        # A present but unusable surface must not be mistaken for a dropped one.
        if surf.exists() and (not surf.is_file() or surf.stat().st_size <= 0):
            return None, "not_ready", "local surface.dat is empty or not a regular file"
        local = surf.is_file()
        if not local and not (event_dir / AFTERBURNER_DONE).is_file():
            return None, "not_ready", "local surface missing and no prior done marker"
        target, status, detail = _resolve_archive_target(event_dir, run_dir, archive_root_resolved)
        if target is None:
            return None, status, detail
        if not local:
            if args.archive_full_verify:
                return None, "not_ready", "cannot compare archive: local surface already absent"
            if not target.is_file() or not _zstd_test(args.zstd_bin, target):
                return None, "not_ready", "local surface absent and existing archive fails verification"
        return (surf if local else None, target), None, None
    except (OSError, UnicodeError, ValueError) as exc:
        return None, "error", f"archive preflight failed: {exc}"


def _clean_archive_temps(target, event_dir):
    """Remove only known archiver/repair temporary names, between worker passes.

    The per-run repair flock excludes other repair processes. As with all repair
    mutations, callers must ensure event workers are not queued or running.
    """
    if target.parent.is_dir():
        pattern = re.compile(re.escape(target.name) + r"\.tmp\.\d+(?:\.\d+)?$")
        for path in target.parent.iterdir():
            if pattern.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                path.unlink()
    for path in event_dir.glob(AFTERBURNER_DONE + ".repair-tmp.*"):
        if path.is_file() and not path.is_symlink():
            path.unlink()


def finalize_archive_failed_event(event_dir, args, archive_root_resolved, run_dir):
    """Archive/finalize or finish a previously interrupted cleanup.

    The two-marker state is retryable. Never drop the local surface until the
    done marker is published AND archive.failed has been removed successfully.
    """
    plan, status, detail = _archive_preflight(event_dir, run_dir, archive_root_resolved, args)
    if plan is None:
        return status, detail
    surf, target = plan
    tmp = None
    try:
        # Validate template before any archive or marker mutation.
        prior_path = event_dir / ARCHIVE_FAILED
        prior = prior_path.read_text().strip().replace("\n", " ")[:300] if prior_path.exists() else ""
        done_text = _afterburner_done_text(args.afterburner_done_template, prior)
        _clean_archive_temps(target, event_dir)
        # Dropping the original always requires byte-for-byte correspondence.
        full_verify = surf is not None and (args.archive_full_verify or args.archive_remove_local)
        archive_ok = target.is_file() and (
            _zstd_full_verify(args.zstd_bin, target, surf)
            if full_verify else _zstd_test(args.zstd_bin, target))
        if not archive_ok:
            if surf is None:
                return "error", "existing archive no longer verifies; local surface absent"
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + f".tmp.{os.getpid()}")
            r = subprocess.run([args.zstd_bin, f"-{args.zstd_level}", "-q", "-f",
                                "-o", str(tmp), str(surf)], capture_output=True, text=True)
            if r.returncode != 0:
                return "error", f"zstd compress failed: {(r.stderr or r.stdout).strip()}"
            verified = (_zstd_full_verify(args.zstd_bin, tmp, surf)
                        if full_verify else _zstd_test(args.zstd_bin, tmp))
            if not verified:
                return "error", "archive failed verification (check CFS space/quota)"
            # Flush archive data before publishing or deleting the local original.
            with tmp.open("rb") as fh:
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            tmp = None

        done = event_dir / AFTERBURNER_DONE
        if prior_path.exists() or not done.is_file() or done.stat().st_size == 0:
            _atomic_write_text(done, done_text)
        prior_path.unlink(missing_ok=True)
        if args.archive_remove_local and surf is not None:
            surf.unlink()
        return "ok", ("archive already present" if archive_ok else "re-archived")
    except (OSError, UnicodeError, ValueError) as exc:
        return "error", f"archive finalization/cleanup failed (retryable): {exc}"
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError as exc:
                print(f"[repair_stale_events] WARNING: cannot remove {tmp}: {exc}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("run_dir", help="results folder containing per-event subdirectories")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="actually delete safe-stale directories (default: dry run, no changes)",
    )
    ap.add_argument(
        "--allow-extra-file",
        action="append",
        default=[],
        metavar="NAME",
        help="treat NAME as a benign setup/byproduct file when classifying (repeatable)",
    )
    ap.add_argument(
        "--clear-stale-locks",
        action="store_true",
        help="also remove afterburner.lock directories older than --lock-stale-hours",
    )
    ap.add_argument(
        "--lock-stale-hours",
        type=float,
        default=24.0,
        help="age threshold in hours for --clear-stale-locks (default: 24)",
    )
    ap.add_argument(
        "--log-root",
        default=None,
        help="directory for the JSON repair log (default: run_dir)",
    )
    ap.add_argument(
        "--target-events",
        type=int,
        default=None,
        help=(
            "expected number of events for ready_to_cache.json. If supplied, "
            "the marker is written only when terminal-ready events reach this target."
        ),
    )
    ap.add_argument(
        "--system",
        default=None,
        help="collision system label to record in ready_to_cache.json",
    )
    ap.add_argument(
        "--max-afterburner-retries",
        "--max_afterburner_retries",
        dest="max_afterburner_retries",
        type=int,
        default=3,
        help=(
            "afterburner retry budget, used only to split failed_afterburner into "
            "still-retryable vs budget-exhausted for the campaign route. Must match "
            "the value passed to run-events. Default: 3"
        ),
    )
    ap.add_argument(
        "--finalize-archive-failed",
        dest="finalize_archive_failed",
        action="store_true",
        help="Finalize archive_failed events in place: re-archive the local surface, "
             "verify, write afterburner.done, clear archive.failed -- WITHOUT re-running "
             "afterburner (the particle list already exists). Requires --archive-root and "
             "--apply. Default off: without it, archive_failed routes to blocked as before.",
    )
    ap.add_argument(
        "--archive-root",
        dest="archive_root",
        default=os.environ.get("HIC_SURFACE_ARCHIVE_ROOT"),
        help="CFS surface archive root; paths are relative to the configured archive base, including nested campaigns. "
             "Explicit marker filenames must agree. Required with --finalize-archive-failed.",
    )
    ap.add_argument("--zstd-bin", dest="zstd_bin", default="zstd",
                    help="zstd executable for archive finalization (default: zstd).")
    ap.add_argument("--archive-base", default=None,
                    help="Original source base for archive paths (default: HIC_SURFACE_ARCHIVE_BASE).")
    ap.add_argument("--zstd-level", dest="zstd_level", type=int, default=3,
                    help="zstd compression level for finalization (default 3; size only).")
    ap.add_argument("--archive-full-verify", dest="archive_full_verify", action="store_true",
                    help="Verify finalized archives by decompress-and-compare (stronger than zstd -t).")
    ap.add_argument("--archive-remove-local", dest="archive_remove_local", action="store_true",
                    help="Delete local surfaces after verified finalization, including retained surfaces "
                         "of completed events, with byte comparison before deletion. "
                         "Requires --finalize-archive-failed. Default: keep.")
    ap.add_argument("--afterburner-done-template", dest="afterburner_done_template", default=None,
                    help="Copy afterburner.done content verbatim from this file (a known-good marker) "
                         "instead of a provenance stub, if your cache step parses its content.")
    args = ap.parse_args()
    if args.archive_remove_local and not args.finalize_archive_failed:
        ap.error("--archive-remove-local requires --finalize-archive-failed")

    run_dir = Path(os.path.expandvars(os.path.expanduser(args.run_dir))).resolve()
    try:
        configure_archive_base(args.archive_base, run_dir)
    except ValueError as error:
        ap.error(str(error))
    if not run_dir.is_dir():
        print(f"ERROR: run_dir is not a directory: {run_dir}", file=sys.stderr)
        sys.exit(1)

    # A fully-reaped design carries reaped.done. Its raw particle lists are gone and its event
    # dirs may have been removed to reclaim inodes, so scanning would misclassify it as stale and
    # could route it for destructive "repair". Honor the marker: presence == complete, do nothing.
    if (run_dir / REAPED_MARKER).is_file():
        print(f"[repair_stale_events] {REAPED_MARKER} present in {run_dir}; design already reaped "
              f"-- skipping (no scan, no repair).")
        return

    lock_fd = os.open(run_dir, os.O_RDONLY)
    try:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"ERROR: another repair is operating on {run_dir}", file=sys.stderr)
            sys.exit(2)
        _repair_run(args, run_dir)
    finally:
        os.close(lock_fd)


def _repair_run(args, run_dir):
    benign = set(DEFAULT_BENIGN_FILES) | set(args.allow_extra_file)
    event_dirs = sorted(p for p in run_dir.iterdir() if is_event_dir(p))

    # Validate tools/template before scanning or mutating events. Dry-run uses
    # the same preflight, including archive tests for legacy missing-local states.
    archive_root_resolved = None
    if args.finalize_archive_failed:
        if not args.archive_root:
            print("ERROR: --finalize-archive-failed requires --archive-root "
                  "(or HIC_SURFACE_ARCHIVE_ROOT).", file=sys.stderr)
            sys.exit(1)
        ar = Path(os.path.expandvars(os.path.expanduser(args.archive_root)))
        if not ar.is_dir():
            print(f"ERROR: --archive-root does not exist: {ar}", file=sys.stderr)
            sys.exit(1)
        archive_root_resolved = ar.resolve()
        try:
            if subprocess.run([args.zstd_bin, "--version"], capture_output=True).returncode != 0:
                raise OSError(f"zstd not runnable as {args.zstd_bin!r}")
            if args.afterburner_done_template is not None:
                args.afterburner_done_template = str(Path(os.path.expandvars(
                    os.path.expanduser(args.afterburner_done_template))).resolve())
                _afterburner_done_text(args.afterburner_done_template, "")
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"ERROR: archive finalization setup failed: {exc}", file=sys.stderr)
            sys.exit(1)
        derived_design = archive_root_resolved / archive_relative_path(run_dir / "0").parent
        print(f"[repair_stale_events] archive destination: {derived_design}/<event>/{SURFACE_ARCHIVE_NAME}")
        if not derived_design.is_dir():
            print("[repair_stale_events] NOTE: derived design archive directory does not exist yet.",
                  file=sys.stderr)

    _, bad_before = load_cache_bad_events(run_dir)
    rejected_ids = set()
    if bad_before and isinstance(bad_before.get("bad_events"), list):
        rejected_ids = {int(e["event_id"]) for e in bad_before["bad_events"]
                        if bad_event_still_pending(run_dir, e)}

    counts = Counter()
    records = []
    unexpected_seen = Counter()
    particle_structure_seen = Counter()
    ready_marker_path = run_dir / READY_TO_CACHE
    repaired_any = False
    needs_recheck = False
    finalize_errors = 0

    for event_dir in event_dirs:
        bucket, detail = classify_event(event_dir, benign, args.max_afterburner_retries)

        if "particle_structure" in detail:
            particle_structure_seen[detail["particle_structure"]] += 1

        if bucket == "running_locked" and args.clear_stale_locks:
            age = detail.get("lock_age_hours")
            if age is not None and age > args.lock_stale_hours:
                if args.apply:
                    shutil.rmtree(event_dir / AFTERBURNER_LOCK, ignore_errors=True)
                    repaired_any = True
                    needs_recheck = True
                    bucket = "stale_lock_cleared"
                else:
                    bucket = "stale_lock_would_clear"

        if bucket == "ambiguous_unexpected_files":
            for name in detail.get("unexpected", []):
                unexpected_seen[name] += 1

        if bucket == "safe_stale_hydro":
            if args.apply:
                shutil.rmtree(event_dir)
                repaired_any = True
                needs_recheck = True
                bucket = "safe_stale_hydro_repaired"
            else:
                bucket = "safe_stale_hydro_would_repair"

        # A surface with no hydro.done is an orphaned/old-pipeline surface we have
        # chosen not to trust (a mid-evolution truncation can end on a clean line
        # and pass a cheap syntax check, so validating it cheaply is unsafe).  At
        # ~1-2% of events the conservative fix is to delete the whole event dir and
        # rerun hydro from scratch via mode=both, exactly like safe_stale_hydro.
        # No backup: the surface is suspect and is being regenerated by design.
        if bucket == "ambiguous_surface_no_hydro_done":
            if args.apply:
                shutil.rmtree(event_dir)
                repaired_any = True
                needs_recheck = True
                bucket = "ambiguous_surface_no_hydro_done_repaired"
            else:
                bucket = "ambiguous_surface_no_hydro_done_would_repair"

        if bucket == "broken_particle_list":
            if args.apply:
                backup_root = run_dir / "_repair_broken_particle_backup"
                clean_afterburner_artifacts(event_dir, backup_root=backup_root)
                repaired_any = True
                needs_recheck = True
                bucket = "broken_particle_list_repaired"
            else:
                bucket = "broken_particle_list_would_repair"

        # Both markers can remain after an interrupted commit. Completed events
        # with retained surfaces can also retry/request cleanup on a later pass.
        cleanup_only = bucket == "done_with_surface_present" and args.archive_remove_local
        if args.finalize_archive_failed and (
                bucket in {"archive_failed", "ambiguous_done_with_archive_failed"} or cleanup_only):
            if int(event_dir.name) in rejected_ids:
                status, fdetail = "not_ready", "particle output rejected by cache; needs rerun"
            elif args.apply:
                status, fdetail = finalize_archive_failed_event(
                    event_dir, args, archive_root_resolved, run_dir)
            else:
                plan, status, fdetail = _archive_preflight(
                    event_dir, run_dir, archive_root_resolved, args)
                if plan is not None:
                    status, fdetail = "ok", f"eligible; target={plan[1]} (writes not tested)"
            if status == "ok":
                if args.apply:
                    repaired_any = True
                    bucket = "archive_surface_removed" if cleanup_only else "archive_failed_finalized"
                else:
                    bucket = "archive_surface_would_remove" if cleanup_only else "archive_failed_would_finalize"
                detail = {**detail, "finalize": fdetail}
            elif status == "not_ready":
                bucket = "archive_failed_not_finalizable"
                detail = {**detail, "finalize": fdetail}
            else:
                finalize_errors += 1
                bucket = "archive_finalize_error"
                detail = {**detail, "finalize_error": fdetail}
                print(f"  [finalize] {event_dir.name}: {status}: {fdetail}", file=sys.stderr)

        counts[bucket] += 1
        records.append(
            {"event": str(event_dir.relative_to(run_dir)), "bucket": bucket, **detail}
        )

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[repair_stale_events] {mode}  run_dir={run_dir}  events={len(event_dirs)}")
    for bucket in sorted(counts):
        print(f"  {bucket}: {counts[bucket]}")
    if particle_structure_seen:
        print("  (particle structure quick-check counts:)")
        for name, c in sorted(particle_structure_seen.items()):
            print(f"    {name}: {c}")
    if unexpected_seen:
        print("  (distinct unexpected entries in ambiguous_unexpected_files:)")
        for name, c in unexpected_seen.most_common():
            print(f"    {name}: {c}")
        print(
            "  If any of these are benign MUSIC byproducts, re-run with "
            "--allow-extra-file NAME so those directories qualify as safe-stale."
        )

    # --- cache_bad_events.json: events the full cache parse rejected (mid-file gaps the cheap
    # head/tail check here cannot see).  Clean the still-pending ones for rerun (--apply) and
    # block readiness while any remain.  This is the loop-closing link: the expensive full read
    # the cache already paid for is what flags these; repair acts on it instead of re-scanning.
    bad_path, bad_payload = load_cache_bad_events(run_dir)
    cache_bad_pending = 0
    cache_bad_cleaned = 0
    if bad_payload and isinstance(bad_payload.get("bad_events"), list):
        pending_entries = [e for e in bad_payload["bad_events"]
                           if bad_event_still_pending(run_dir, e)]
        cache_bad_pending = len(pending_entries)
        n_listed = len(bad_payload["bad_events"])
        n_stale = n_listed - cache_bad_pending
        print(f"  (cache_bad_events.json: {n_listed} flagged, {cache_bad_pending} still pending, "
              f"{n_stale} already cleaned/rerun)")
        if args.apply:
            backup_root = run_dir / "_repair_cache_bad_backup"
            cleaned = 0
            for e in pending_entries:
                ev_dir = run_dir / event_dir_name_for_id(run_dir, e["event_id"])
                # Never clean an event that currently holds a lock (a worker may be writing).
                if (ev_dir / AFTERBURNER_LOCK).exists():
                    print(f"    SKIP event {e['event_id']}: lock present", file=sys.stderr)
                    continue
                clean_afterburner_artifacts(ev_dir, backup_root=backup_root)
                repaired_any = True
                needs_recheck = True
                cleaned += 1
            if cleaned:
                cache_bad_cleaned = cleaned
                print(f"  cleaned {cleaned} cache-flagged bad event(s) for rerun "
                      f"(backup: {backup_root})")
            # Re-evaluate pending after cleaning; if none remain, the marker is consumed.
            remaining = [e for e in bad_payload["bad_events"]
                         if bad_event_still_pending(run_dir, e)]
            cache_bad_pending = len(remaining)
            if cache_bad_pending == 0:
                try:
                    bad_path.unlink()
                    print(f"  removed consumed {CACHE_BAD_EVENTS} "
                          "(cleaned events now need rerun; they self-report as non-terminal)")
                except OSError:
                    pass

    ready, ready_detail = marker_is_ready(counts, len(event_dirs), args.target_events)
    # Cache-flagged bad events that are still pending block readiness regardless of the cheap
    # structural buckets: repair's head/tail check cannot see their mid-file gaps, so without this
    # the design could be (wrongly) marked ready again and re-fail at the cache.
    if cache_bad_pending > 0:
        ready = False
        ready_detail = dict(ready_detail or {})
        ready_detail["cache_bad_events_pending"] = cache_bad_pending
    if args.apply:
        if ready and not needs_recheck:
            payload = ready_marker_payload(
                run_dir=run_dir,
                args=args,
                counts=counts,
                particle_structure_seen=particle_structure_seen,
                event_count=len(event_dirs),
            )
            _atomic_write_text(ready_marker_path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
            print(f"[repair_stale_events] wrote ready marker: {ready_marker_path}")
        else:
            if ready_marker_path.exists():
                ready_marker_path.unlink()
                print(f"[repair_stale_events] removed stale ready marker: {ready_marker_path}")
            print(f"[repair_stale_events] not ready_to_cache: {ready_detail or {'needs_recheck': needs_recheck}}")
    else:
        print(f"[repair_stale_events] ready_to_cache if applied: {ready and not needs_recheck}")
        if not ready:
            print(f"[repair_stale_events] readiness detail: {ready_detail}")

    # Always-written per-design route summary for run_designs.py --mode auto.
    # Written in both DRY-RUN and APPLY. Cache cleanup forces recheck here,
    # because an --apply action mutated events whose post-action
    # state is NOT reflected in `counts` (those were tallied during the classify
    # loop, before this cleanup).  Re-deriving the route from a real next-pass scan
    # is safer than patching counts:
    #   - cache_bad_pending  > 0: flagged events still need cleaning/rerun.
    #   - cache_bad_cleaned  > 0: events were just cleaned and now need rerun
    #                             (clean_afterburner_artifacts preserves hydro), so
    #                             the stale 'complete' counts must not route 'ready'.
    route, route_tallies = route_from_counts(counts, len(event_dirs), args.target_events)
    if cache_bad_pending > 0 or cache_bad_cleaned > 0 or (needs_recheck and route == "ready"):
        route = "recheck"
    campaign_state_path = run_dir / CAMPAIGN_STATE
    campaign_payload = {
        "schema": "campaign_state.v1",
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "created_by": "repair_stale_events.py",
        "run_dir": str(run_dir),
        "system": args.system,
        "target_events": args.target_events,
        "phase": mode,
        "route": route,
        "ready_to_cache": bool(ready and not needs_recheck and cache_bad_pending == 0),
        "cache_bad_events_pending": int(cache_bad_pending),
        "has_blocked": bool(route_tallies.get("blocked", 0) > 0),
        "repaired_any": bool(repaired_any),
        "needs_recheck": bool(needs_recheck),
        "archive_finalize_errors": finalize_errors,
        "tallies": route_tallies,
        "counts": dict(counts),
    }
    _atomic_write_text(campaign_state_path, json.dumps(campaign_payload, indent=2, sort_keys=True) + "\n")
    print(f"[repair_stale_events] route={route}  ({campaign_state_path})")
    if args.finalize_archive_failed:
        fin = counts.get("archive_failed_finalized", 0)
        wf = counts.get("archive_failed_would_finalize", 0)
        nr = counts.get("archive_failed_not_finalizable", 0)
        if fin or wf or nr or finalize_errors:
            print(f"[repair_stale_events] archive-finalize: finalized={fin} "
                  f"would_finalize={wf} not_finalizable={nr} errors={finalize_errors}")

    log_root = (
        Path(os.path.expandvars(os.path.expanduser(args.log_root))).resolve()
        if args.log_root
        else run_dir
    )
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = log_root / f"repair_stale_events.{stamp}.{'apply' if args.apply else 'dryrun'}.json"
    _atomic_write_text(log_path,
        json.dumps(
            {
                "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
                "run_dir": str(run_dir),
                "mode": mode,
                "counts": dict(counts),
                "particle_structure_counts": dict(particle_structure_seen),
                "ready_to_cache": ready and not needs_recheck,
                "readiness_detail": ready_detail,
                "records": records,
            },
            indent=2,
        )
    )
    print(f"[repair_stale_events] wrote log: {log_path}")
    # Write diagnostics first; run_designs.py(check=True) must see operational
    # failures instead of silently treating a failed archive recovery as success.
    if finalize_errors:
        sys.exit(2)


if __name__ == "__main__":
    main()
