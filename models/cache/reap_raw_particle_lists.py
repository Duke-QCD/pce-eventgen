#!/usr/bin/env python3
"""
reap_raw_particle_lists.py

The reaper: deletes raw particle lists that a verified CFS cache already holds. DRY-RUN BY
DEFAULT. This is the only irreversible script in the pipeline; it is deliberately paranoid
about its own preconditions.

It works in two steps:
  1. For each event ID present in the verified CFS cache that passes the per-event gate below
     (afterburner.done present, no lock, exactly one nonempty particle file, mtime old enough),
     it deletes that event's particle list. The event directory and its markers stay, so a
     partially reaped design still counts its completed events. Events that fail the gate are
     left fully intact.
  2. Only if EVERY cached event ended in a clean state, it writes the design-level reaped.done
     marker (consumed by run_designs/repair to skip the design) and then removes all numeric
     event directories, reclaiming inodes. initial.hdf and other design-level files remain.

Deletion happens only after a full from-scratch CFS re-verification in the SAME invocation
(no trust carried across runs). It NEVER touches the local cache or the CFS cache.

Safety model (all must hold before any deletion):
  CFS cache gate (re-verified from scratch every run, including --apply):
    - cache.verified exists, parses, cache_valid==true, schema_version==v3
    - metadata.json present and hashes to the marker's recorded value
    - every shard listed exists; zstd -t passes; decompress-to-temp hash == marker
      uncompressed_sha256; decompressed size == marker uncompressed_size_bytes; np.load works
    - event_ids extracted from THAT verified temp .npz (not a re-read)
    - fingerprint_hash == the committed frozen expected fingerprint file
    - design_complete==true OR shortfall_accepted==true
    - ANY shard failure -> abort whole design, delete nothing
  Raw-design gate (per cached event):
    - raw event dir exists; afterburner.done present; no afterburner.lock / no legacy smash.lock
    - exactly one of particle_lists.oscar / .unfinished exists (both -> skip)
    - particle file mtime <= cache_started_unix - margin AND <= now - margin
      (missing cache_started_unix in metadata -> hard abort: rebuild cache with current script)
  User-action gate:
    - dry-run default; --apply + --confirm-design + --confirm-system (matched to the marker)
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

EXPECTED_SCHEMA = "cache_one_design/v3"
PARTICLE_FILES = ("particle_lists.oscar", "particle_lists.oscar.unfinished")

# Design-level "fully reaped" marker, written in the raw design dir after a clean reap. Its
# PRESENCE alone signals completion to run_designs/repair_stale_events so they skip the design
# without scanning per-event dirs (whose afterburner.done markers may be removed to reclaim
# inodes). A wrongly-present marker costs one manual `rm`; it is only written after a verified,
# error-free full reap, so that case should not arise.
REAPED_MARKER = "reaped.done"

# Classification buckets meaning an event needs no (further) reaping: just deleted, already
# absent from a prior reap, its dir already gone, or a clean no-surface event with nothing to
# reap. ANY other bucket means raw remains or the state is ambiguous -> not fully reaped, so the
# completion marker must NOT be written.
TERMINAL_OK_BUCKETS = frozenset({
    "would_delete", "already_absent_expected", "skipped_no_event_dir", "no_surface_nothing_to_reap"})


# ========================================================================================
# Hashing / canonical fingerprint (MUST match cache_one_design / publish byte-for-byte).
# ========================================================================================
def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def canonical_fingerprint_json(fingerprint):
    return json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))


def fingerprint_hash(fingerprint):
    return hashlib.sha256(canonical_fingerprint_json(fingerprint).encode("utf-8")).hexdigest()


# ========================================================================================
# zstd CLI wrappers (exit-code checked; no shell pipelines).
# ========================================================================================
def zstd_test(zst_path):
    r = subprocess.run(["zstd", "-q", "-t", zst_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"zstd -t failed (rc={r.returncode}) for {zst_path}: {r.stderr.strip()}")


def zstd_decompress_to_file(zst_path, out_path):
    """Decompress zst_path -> out_path, checking zstd's own exit code (no pipe)."""
    with open(out_path, "wb") as out:
        r = subprocess.run(["zstd", "-q", "-dc", zst_path], stdout=out, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f"zstd -dc failed (rc={r.returncode}) for {zst_path}: {r.stderr.decode().strip()}")


# ========================================================================================
# CFS cache deep verification. Returns (cached_event_ids:set[int], marker:dict).
# Aborts (raises) on ANY shard failure -> caller deletes nothing.
# ========================================================================================
def deep_verify_cfs_cache(cfs_cache, expected_fingerprint_path, tmp_dir):
    marker_path = os.path.join(cfs_cache, "cache.verified")
    if not os.path.isfile(marker_path):
        raise RuntimeError(f"No cache.verified in CFS cache: {cfs_cache}")
    with open(marker_path) as f:
        marker = json.load(f)

    if not marker.get("cache_valid", False):
        raise RuntimeError("cache.verified says cache_valid is not true.")
    if marker.get("schema_version") != EXPECTED_SCHEMA:
        raise RuntimeError(f"Schema {marker.get('schema_version')!r} != {EXPECTED_SCHEMA!r}.")

    # completeness gate
    design_complete = marker.get("design_complete")
    shortfall_accepted = marker.get("shortfall_accepted", False)
    if not (design_complete is True or shortfall_accepted is True):
        raise RuntimeError(
            f"Design not complete and shortfall not accepted "
            f"(design_complete={design_complete}, shortfall_accepted={shortfall_accepted}). "
            f"Refusing deletion.")

    # fingerprint vs committed frozen expected file (independent of the marker's own copy)
    if not expected_fingerprint_path or not os.path.isfile(expected_fingerprint_path):
        raise RuntimeError(
            f"--expected-fingerprint must point to the committed frozen expected fingerprint "
            f"JSON. Not found: {expected_fingerprint_path}")
    with open(expected_fingerprint_path) as f:
        expected_fp = json.load(f)
    expected_hash = fingerprint_hash(expected_fp)
    marker_fp_hash = marker.get("fingerprint_hash")
    # recompute from the marker's embedded fingerprint too, and require all three agree
    recomputed = fingerprint_hash(marker.get("fingerprint", {}))
    if not (expected_hash == marker_fp_hash == recomputed):
        raise RuntimeError(
            "Fingerprint mismatch. Refusing deletion.\n"
            f"  committed expected hash    = {expected_hash}\n"
            f"  marker fingerprint_hash    = {marker_fp_hash}\n"
            f"  recomputed-from-marker     = {recomputed}")
        

    # metadata.json present and matches the marker's recorded hash
    files = marker.get("files", {})
    meta_rec = files.get("metadata.json")
    cfs_meta = os.path.join(cfs_cache, "metadata.json")
    if not os.path.isfile(cfs_meta):
        raise RuntimeError("metadata.json missing in CFS cache.")
    if not meta_rec or meta_rec.get("compressed") is not False or "sha256" not in meta_rec:
        raise RuntimeError("cache.verified lacks a valid metadata.json hash record.")
    
    if sha256_file(cfs_meta) != meta_rec["sha256"]:
        raise RuntimeError("metadata.json hash != marker record.")
            
    with open(cfs_meta) as f:
        metadata = json.load(f)
        
    metadata_fp_hash = metadata.get("fingerprint_hash") or fingerprint_hash(metadata.get("fingerprint", {}))
    if metadata_fp_hash != marker_fp_hash:
        raise RuntimeError(
            "metadata.json fingerprint does not match cache.verified fingerprint.\n"
            f"  metadata fingerprint_hash = {metadata_fp_hash}\n"
            f"  marker fingerprint_hash   = {marker_fp_hash}"
            )

    # shard set: every .zst in the marker (the files dict minus metadata.json)
    shard_keys = [k for k in files if k.endswith(".npz.zst")]
    if not shard_keys:
        raise RuntimeError("Marker lists no shards.")

    os.makedirs(tmp_dir, exist_ok=True)
    cached_event_ids = set()
    n_shards = 0
    for zname in sorted(shard_keys):
        rec = files[zname]
        cfs_zst = os.path.join(cfs_cache, zname)
        if not os.path.isfile(cfs_zst):
            raise RuntimeError(f"Shard listed in marker is missing on CFS: {zname}")
        zstd_test(cfs_zst)  # exit-code checked
        fd, tmp_npz = tempfile.mkstemp(suffix=".npz", dir=tmp_dir)
        os.close(fd)
        try:
            zstd_decompress_to_file(cfs_zst, tmp_npz)
            dh = sha256_file(tmp_npz)
            if dh != rec.get("uncompressed_sha256"):
                raise RuntimeError(
                    f"RECOVERABILITY FAILURE: shard {zname} decompressed hash {dh} != marker "
                    f"uncompressed_sha256 {rec.get('uncompressed_sha256')}. Aborting whole design.")
            dn = os.path.getsize(tmp_npz)
            if dn != rec.get("uncompressed_size_bytes"):
                raise RuntimeError(
                    f"shard {zname} decompressed size {dn} != marker {rec.get('uncompressed_size_bytes')}.")
            with np.load(tmp_npz) as z:
                if "event_ids" not in z.files:
                    raise RuntimeError(f"shard {zname} has no event_ids array.")
                ids = z["event_ids"]
                for e in ids.tolist():
                    eid = int(e)
                    if eid in cached_event_ids:
                        raise RuntimeError(f"Duplicate event_id {eid} found in CFS cache shards; refusing deletion.")
                    cached_event_ids.add(eid)
        finally:
            if os.path.exists(tmp_npz):
                os.remove(tmp_npz)
        n_shards += 1

    print(f"CFS deep-verify passed: {n_shards} shards, {len(cached_event_ids)} cached event IDs, "
          f"fingerprint matches frozen expected scope.")
    return cached_event_ids, marker, metadata


# ========================================================================================
# Per-event classification.
# ========================================================================================
def classify_event(raw_event_dir, cache_started_unix, margin, prior_deleted, now):
    """Return (bucket, particle_file_or_None, info). Does NOT delete."""
    d = Path(raw_event_dir)
    if not d.is_dir():
        return "skipped_no_event_dir", None, {}

    # Particle-file state, matching repair_stale_events.py semantics: a usable
    # output is exactly one nonempty regular file.  Zero-byte or nonregular
    # particle files are truncated/ambiguous and are never deletable.
    usable = []
    zero_or_invalid = []
    for fn in PARTICLE_FILES:
        p = d / fn
        if not p.exists():
            continue
        if p.is_file() and p.stat().st_size > 0:
            usable.append(fn)
        else:
            zero_or_invalid.append(fn)

    done = (d / "afterburner.done").is_file()
    # The active lock written by run-events is afterburner.lock, a DIRECTORY
    # (created via mkdir), so test existence rather than is_file; smash.lock is
    # also checked for any legacy/other producer.  Either present -> never delete.
    lock = (d / "afterburner.lock").exists() or (d / "smash.lock").exists()

    # Terminal no-surface event: clean hydro produced no particlization surface,
    # so there is no raw particle list to reap.  Any particle artifact (usable OR
    # zero-byte/nonregular), afterburner.done, or a nonempty surface alongside
    # hydro.no_surface is a marker contradiction.  Never delete in either case.
    if (d / "hydro.no_surface").is_file():
        hydro_done = (d / "hydro.done").is_file()
        surface = d / "surface.dat"
        nonempty_surface = surface.is_file() and surface.stat().st_size > 0
        if done or hydro_done or usable or zero_or_invalid or nonempty_surface:
            return "skipped_no_surface_conflict", None, {
                "afterburner_done": done, "hydro_done": hydro_done,
                "usable_particles": usable,
                "zero_or_invalid_particles": zero_or_invalid,
                "nonempty_surface": nonempty_surface}
        return "no_surface_nothing_to_reap", None, {}

    # A zero-byte/nonregular particle file is truncated/ambiguous output: never
    # delete; surface for inspection (matches repair's ambiguous_zero_byte_particle).
    if zero_or_invalid:
        return "skipped_zero_byte_particle", None, {
            "zero_or_invalid_particles": zero_or_invalid, "usable_particles": usable}

    if len(usable) == 0:
        # absent: distinguish we-deleted-it from unexpectedly-gone
        if str(d) in prior_deleted:
            return "already_absent_expected", None, {}
        return "absent_unexpected", None, {}
    if len(usable) == 2:
        return "skipped_both_particle_files_exist", None, {}
    if not done:
        return "skipped_no_afterburner_done", None, {}
    if lock:
        return "skipped_lock_present", None, {}

    pf = d / usable[0]
    mt = pf.stat().st_mtime
    # mtime gate: must predate cache start (so the cached parse covers current bytes) AND not
    # be very recent (an actively-written file the lock check might have missed).
    if mt > (cache_started_unix - margin) or mt > (now - margin):
        return "skipped_mtime_newer_than_cache_start", None, {
            "mtime": mt, "cache_started_unix": cache_started_unix}
    return "would_delete", str(pf), {"mtime": mt, "size": pf.stat().st_size}

def build_raw_event_map(raw_design):
    if not os.path.isdir(raw_design):
        raise SystemExit(f"--raw-design is not a directory: {raw_design}")
    out = {}
    for name in os.listdir(raw_design):
        p = os.path.join(raw_design, name)
        if not os.path.isdir(p) or not name.isdigit():
            continue
        eid = int(name)
        if eid in out:
            raise SystemExit(
                f"Ambiguous raw event dirs for event_id {eid}: {out[eid]} and {p}"
            )
        out[eid] = p
    return out

# ========================================================================================
# Main.
# ========================================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Reaper: delete raw particle lists for events in a verified CFS cache. "
                    "DRY-RUN by default. Irreversible with --apply.")
    ap.add_argument("--cfs-cache", required=True, help="Verified CFS cache directory.")
    ap.add_argument("--raw-design", required=True,
                    help="Raw design/system directory in PSCRATCH (contains numeric event dirs).")
    ap.add_argument("--expected-fingerprint", required=True,
                    help="Committed frozen expected_cache_fingerprint_v3.json.")
    ap.add_argument("--tmp-dir", default=None,
                    help="Scratch dir for shard decompression. Default: $PSCRATCH/tmp.")
    ap.add_argument("--mtime-margin-seconds", type=int, default=60)
    ap.add_argument("--apply", action="store_true", help="Actually delete (default: dry run).")
    ap.add_argument("--bucket-json", default=None, metavar="PATH",
                    help="Also write the bucket counts and key context as machine-readable JSON "
                         "to PATH (on both dry-run and apply). For the orchestration wrapper to "
                         "consume instead of scraping stdout. Writing this file changes no gate.")
    ap.add_argument("--confirm-design", default=None)
    ap.add_argument("--confirm-system", default=None)
    args = ap.parse_args()

    # tmp-dir default: $PSCRATCH/tmp; fail loudly if PSCRATCH unset (never silently use /tmp)
    if args.tmp_dir:
        tmp_dir = args.tmp_dir
    else:
        pscratch = os.environ.get("PSCRATCH")
        if not pscratch:
            raise SystemExit("PSCRATCH is not set and --tmp-dir not given; refusing to default "
                             "to /tmp. Set PSCRATCH or pass --tmp-dir.")
        tmp_dir = os.path.join(pscratch, "tmp")

    cfs_cache = os.path.abspath(args.cfs_cache)
    raw_design = os.path.abspath(args.raw_design)

    # ---- CFS deep verification (from scratch, every run including --apply) ----
    cached_event_ids, marker, metadata = deep_verify_cfs_cache(
        cfs_cache, args.expected_fingerprint, tmp_dir
    )
    
    meta_raw = metadata.get("system_dir")
    if not meta_raw:
        raise SystemExit("CFS metadata.json has no system_dir; refusing to reap.")
    
    if os.path.realpath(raw_design) != os.path.realpath(meta_raw):
        raise SystemExit(
            "Refusing: --raw-design does not match CFS metadata system_dir.\n"
            f"  --raw-design:      {os.path.realpath(raw_design)}\n"
            f"  metadata system_dir:{os.path.realpath(meta_raw)}"
        )

    # cache_started_unix is REQUIRED (mtime gate is load-bearing). Absence -> hard abort.
    cache_started_unix = marker.get("fingerprint", {}).get("cache_started_unix")
    if cache_started_unix is None:
        # fingerprint may not carry it; read from metadata.json (the authoritative producer record)
        with open(os.path.join(cfs_cache, "metadata.json")) as f:
            md = json.load(f)
        cache_started_unix = md.get("cache_started_unix")
    if cache_started_unix is None:
        raise SystemExit(
            "cache_started_unix is absent from the cache metadata. The mtime gate cannot be "
            "applied safely. Rebuild this cache with the current cache_one_design.py (which "
            "records cache_started_unix) before reaping. Refusing to delete.")

    # ---- confirm-design / confirm-system must MATCH the marker (not just be present) ----
    marker_design = marker.get("design_id")
    marker_system = marker.get("system")
    if args.apply:
        if args.confirm_design != marker_design:
            raise SystemExit(f"--confirm-design {args.confirm_design!r} != marker design_id "
                             f"{marker_design!r}. Refusing.")
        if args.confirm_system != marker_system:
            raise SystemExit(f"--confirm-system {args.confirm_system!r} != marker system "
                             f"{marker_system!r}. Refusing.")

    # ---- load prior deletion records (resumability / absent classification) ----
    log_dir = os.path.join(cfs_cache, "reaper_logs")
    prior_deleted = set()
    if os.path.isdir(log_dir):
        for fn in os.listdir(log_dir):
            if fn.endswith(".jsonl"):
                with open(os.path.join(log_dir, fn)) as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if (
                            e.get("status") == "deleted"
                            and e.get("raw_event_dir")
                            and e.get("fingerprint_hash") == marker.get("fingerprint_hash")
                        ):
                            prior_deleted.add(e["raw_event_dir"])

    # ---- classify every cached event ----
    now = time.time()
    buckets = {k: 0 for k in (
        "would_delete", "already_absent_expected", "absent_unexpected", "skipped_no_event_dir",
        "skipped_no_afterburner_done", "skipped_lock_present", "skipped_both_particle_files_exist",
        "skipped_mtime_newer_than_cache_start", "no_surface_nothing_to_reap",
        "skipped_no_surface_conflict", "skipped_zero_byte_particle", "skipped_other")}
    plan = []  # (event_id, raw_dir, bucket, particle_file, info)
    raw_event_map = build_raw_event_map(raw_design)
    for eid in sorted(cached_event_ids):
        raw_dir = raw_event_map.get(eid)
        if raw_dir is None:
            buckets["skipped_no_event_dir"] += 1
            plan.append((eid, None, "skipped_no_event_dir", None, {}))
            continue
        bucket, pf, info = classify_event(raw_dir, cache_started_unix,
                                          args.mtime_margin_seconds, prior_deleted, now)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        plan.append((eid, raw_dir, bucket, pf, info))

    # ---- bucket reconciliation: every cached event in exactly one bucket ----
    total_classified = sum(buckets.values())
    if total_classified != len(cached_event_ids):
        raise SystemExit(f"CLASSIFICATION BUG: buckets sum to {total_classified} but there are "
                         f"{len(cached_event_ids)} cached event IDs. Aborting (deleted nothing).")

    # ---- dry-run aggregate summary ----
    print("\n==== CFS cache ====")
    print(f"  design_id={marker_design} system={marker_system}")
    print(f"  shards={len([k for k in marker.get('files',{}) if k.endswith('.npz.zst')])} "
          f"compressed={marker.get('total_compressed_shard_bytes','?')} "
          f"decompressed={marker.get('total_decompressed_shard_bytes','?')}")
    print(f"  design_complete={marker.get('design_complete')} "
          f"shortfall_accepted={marker.get('shortfall_accepted')}")
    print("==== Deletion plan ====")
    would = buckets["would_delete"]
    would_bytes = sum(info.get("size", 0) for (_, _, b, _, info) in plan if b == "would_delete")
    print(f"  cached event IDs: {len(cached_event_ids)}")
    for k in sorted(buckets):
        if buckets[k]:
            print(f"    {k:42s} {buckets[k]}")
    print(f"  would free ~{would_bytes/1e9:.2f} GB across {would} events")
    if buckets["absent_unexpected"]:
        print(f"  WARNING: {buckets['absent_unexpected']} cached events have NO raw particle "
              f"file and no prior deletion record. Investigate before --apply.", file=sys.stderr)

    # machine-readable bucket summary (both dry-run and apply); changes no gate
    if args.bucket_json:
        bucket_doc = {
            "design_id": marker_design,
            "system": marker_system,
            "cfs_cache": os.path.abspath(args.cfs_cache),
            "raw_design": os.path.abspath(args.raw_design),
            "fingerprint_hash": marker.get("fingerprint_hash"),
            "cached_event_ids": len(cached_event_ids),
            "buckets": dict(buckets),
            "would_delete_bytes": int(would_bytes),
            "applied": bool(args.apply),
            "design_complete": marker.get("design_complete"),
            "shortfall_accepted": marker.get("shortfall_accepted"),
            "timestamp_unix": time.time(),
        }
        tmp = args.bucket_json + ".tmp"
        with open(tmp, "w") as bf:
            json.dump(bucket_doc, bf, indent=2, sort_keys=True)
        os.replace(tmp, args.bucket_json)

    print("\nNOTE: This script verifies the CFS cache and checks per-event locks/mtimes, but it "
          "CANNOT know about future or planned reruns. Use --apply only if no active or planned "
          "job should rewrite these cached particle lists.")

    if not args.apply:
        print("\nDRY RUN (default). No files deleted. Re-run with "
              "--apply --confirm-design <id> --confirm-system <sys> to delete.")
        return

    # ---- apply: delete, logging each action to an append-only JSONL ----
    os.makedirs(log_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    jsonl = os.path.join(log_dir, f"reap_{stamp}.jsonl")
    marker_fp_hash = marker.get("fingerprint_hash")
    expected_fp_hash = marker.get("expected_fingerprint_hash")
    n_deleted = 0
    deleted_bytes = 0
    n_apply_failures = 0  # would_delete events that did NOT cleanly delete (lock/vanished/error)
    with open(jsonl, "a") as logf:
        for (eid, raw_dir, bucket, pf, info) in plan:
            entry = {
                "event_id": eid, "raw_event_dir": raw_dir, "status": None,
                "reason": None, "deleted_files": [], "timestamp": time.time(),
                "cache_verified_path": os.path.join(cfs_cache, "cache.verified"),
                "fingerprint_hash": marker_fp_hash, "expected_fingerprint_hash": expected_fp_hash,
            }
            if bucket != "would_delete":
                entry["status"] = "skipped" if bucket.startswith("skipped") else bucket
                entry["reason"] = bucket
                logf.write(json.dumps(entry) + "\n")
                continue
            # re-check the lock IMMEDIATELY before deletion (minimize TOCTOU window). This guards
            # the whole-directory removal exactly as it guarded the file unlink before.
            if (Path(raw_dir) / "afterburner.lock").exists() or (Path(raw_dir) / "smash.lock").exists():
                entry["status"] = "skipped"; entry["reason"] = "lock_appeared_before_delete"
                n_apply_failures += 1
                logf.write(json.dumps(entry) + "\n")
                continue
            # This event passed the full gate (verified cache, afterburner.done, no lock, old
            # mtime). Delete ONLY the particle list here -- not the whole directory. Keeping
            # afterburner.done means a PARTIAL reap (e.g. a lock appears on a later event, so the
            # design is not fully reaped and no marker is written) still counts correctly via
            # count_terminal_events, so it can't trigger a spurious resubmission. Whole-directory
            # removal happens below, only on a fully clean reap.
            try:
                sz = os.path.getsize(pf)
                mt = os.path.getmtime(pf)
                os.remove(pf)
                entry["status"] = "deleted"
                entry["deleted_files"] = [pf]
                entry["file_sizes"] = [sz]
                entry["file_mtimes"] = [mt]
                n_deleted += 1
                deleted_bytes += sz
            except FileNotFoundError:
                entry["status"] = "absent_unexpected"
                entry["reason"] = "vanished_between_plan_and_delete"
                n_apply_failures += 1
            except OSError as e:
                entry["status"] = "error"; entry["reason"] = str(e)
                n_apply_failures += 1
            logf.write(json.dumps(entry) + "\n")
            logf.flush()

    # ---- design-level completion marker ----
    # Write reaped.done ONLY if every cached event ended in a terminal-OK bucket (deleted /
    # already-absent / no-dir / no-surface) AND no apply-time failure occurred. Any other bucket
    # (un-reaped raw, lock, ambiguous, suspicious) means the design is NOT fully reaped, so we
    # leave no marker -- preserving the "marker present == fully reaped" invariant the readers
    # (run_designs, repair_stale_events) rely on to skip the per-event scan.
    bad_buckets = {k: v for k, v in buckets.items() if v and k not in TERMINAL_OK_BUCKETS}
    fully_reaped = (not bad_buckets) and n_apply_failures == 0
    if fully_reaped:
        # Every cached event reaped cleanly. Now reclaim inodes by removing ALL numeric event
        # dirs (the reaped ones, now particle-less, plus any uncached/stray/failed-shortfall
        # dirs -- all expendable on a verified-complete reap). Non-numeric files (initial.hdf,
        # design-level files) are left untouched.
        #
        # Order: write the marker FIRST, then remove dirs. If the process dies mid-sweep, the
        # design is still correctly marked complete (so run_designs/repair skip it), and a re-run
        # of the reaper simply finishes removing whatever dirs remain. Removing dirs before
        # writing the marker would leave a window where dirs are gone but the design looks
        # incomplete to count_terminal_events -- the exact resubmission hole we are avoiding.
        mk = Path(raw_design) / REAPED_MARKER
        marker_ok = False
        try:
            tmp = mk.with_name(mk.name + ".tmp")
            tmp.write_text(
                f"reaped by reap_raw_particle_lists.py at "
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}; "
                f"design={marker_design} system={marker_system} n_deleted={n_deleted}\n")
            os.replace(tmp, mk)
            marker_ok = True
        except OSError as e:
            print(f"WARNING: reap succeeded but could not write {REAPED_MARKER} at {mk}: {e}",
                  file=sys.stderr)

        n_dirs_removed = 0
        if marker_ok:  # only sweep once the design is safely marked
            try:
                for d in Path(raw_design).iterdir():
                    if d.is_dir() and d.name.isdigit():
                        # last-instant lock guard, same as the per-event path
                        if (d / "afterburner.lock").exists() or (d / "smash.lock").exists():
                            continue
                        try:
                            shutil.rmtree(d)
                            n_dirs_removed += 1
                        except OSError as e:
                            print(f"WARNING: could not remove event dir {d}: {e}", file=sys.stderr)
            except OSError as e:
                print(f"WARNING: could not scan event dirs in {raw_design}: {e}", file=sys.stderr)
            print(f"Wrote {REAPED_MARKER} and removed {n_dirs_removed} numeric event dir(s): {mk}")
    else:
        print(f"NOT writing {REAPED_MARKER} and NOT removing event dirs: design not fully reaped "
              f"(apply_failures={n_apply_failures}, non-terminal buckets={bad_buckets or 'none'}). "
              f"Reaped events' particle lists were deleted but their directories (with "
              f"afterburner.done) were kept, so the design stays correctly countable.")

    summary = {
        "design_id": marker_design, "system": marker_system,
        "cfs_cache": cfs_cache, "raw_design": raw_design,
        "n_deleted": n_deleted, "deleted_bytes": deleted_bytes,
        "buckets": buckets, "jsonl_log": jsonl,
        "fingerprint_hash": marker_fp_hash,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with open(os.path.join(log_dir, f"reap_{stamp}.summary.json"), "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    print(f"\nApplied: deleted {n_deleted} particle list(s) (~{deleted_bytes/1e9:.2f} GB). "
          f"Log: {jsonl}")
    if fully_reaped:
        print("Every cached event was reaped, so the event directories were removed as well.")
    else:
        print("Event directories (with their markers) were kept because the design is not fully "
              "reaped; events that did not pass the gate were left fully intact.")
    print("The local cache and CFS cache were NOT touched.")


if __name__ == "__main__":
    main()
