#!/usr/bin/env python3
"""
publish_cache_to_cfs.py

Publish a validated LOCAL v3 event cache to CFS as a compressed, independently
re-verified archive, and write the cache.verified marker that gates raw deletion.

This is the SOLE writer of cache.verified. cache_one_design.py ends at a validated local
cache; this script does:

    local validated cache  ->  zstd-compressed, decompress-verified CFS cache  ->  cache.verified

It NEVER deletes raw particle lists and NEVER deletes the local cache.

Verification model (recoverability, not just transfer integrity):
  For each shard X.npz:
    1. uncompressed_sha256 = sha256(local X.npz)            # anchored to the VALIDATED original
    2. compress X.npz -> X.npz.zst.tmp  (zstd CLI, exit-code checked)
    3. local `zstd -t` on the .tmp                          # integrity of the compressed frame
    4. rename .tmp -> X.npz.zst  (only after local -t passes)
    5. copy X.npz.zst to CFS .publishing dir, fsync
    6. compressed_sha256 = sha256(CFS X.npz.zst)            # cheap CFS-transfer integrity
    7. CFS `zstd -t` on the CFS copy (exit-code checked)
    8. stream `zstd -dc` the CFS copy into sha256 (zstd exit-code checked, NOT pipe-swallowed)
       and require it equals uncompressed_sha256            # THE recoverability gate
  Only after every shard + metadata.json passes all of the above is cache.verified written,
  then the .publishing dir is atomically renamed to the final CFS path.

The marker stores BOTH hashes per file (compressed for cheap re-checks; uncompressed for the
deletion-gate deep verify), the full fingerprint + its canonical hash, the frozen expected-
fingerprint comparison result, code/table provenance hashes, the zstd version/level, and the
cache-validity-vs-design-completeness distinction.

CLI checks scope at publish time too (per agreement): a fingerprint mismatch against the
frozen expected file is a hard error unless --allow-fingerprint-mismatch.
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

import numpy as np

EXPECTED_SCHEMA = "cache_one_design/v3"


# ========================================================================================
# Hashing / canonical JSON (mirrors cache_one_design.canonical_fingerprint_json exactly).
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


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def canonical_fingerprint_json(fingerprint):
    """MUST match cache_one_design.canonical_fingerprint_json byte-for-byte."""
    return json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))


def fingerprint_hash(fingerprint):
    return hashlib.sha256(canonical_fingerprint_json(fingerprint).encode("utf-8")).hexdigest()


# ========================================================================================
# zstd CLI wrappers (exit-code checked; no shell pipelines that swallow zstd errors).
# ========================================================================================
def zstd_version():
    try:
        out = subprocess.run(["zstd", "--version"], capture_output=True, text=True, check=True)
        return out.stdout.strip() or out.stderr.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        raise SystemExit(f"zstd CLI not available: {e}. Install/load zstd before publishing.")


def zstd_compress(src_npz, dst_zst_tmp, level, threads):
    """Compress src -> dst_zst_tmp. zstd's own exit code is checked (no pipe)."""
    cmd = ["zstd", "-q", f"-{level}", f"-T{threads}", "-f", "-o", dst_zst_tmp, src_npz]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"zstd compress failed (rc={r.returncode}) for {src_npz}: {r.stderr.strip()}")


def zstd_test(zst_path):
    """`zstd -t` integrity check; exit code checked."""
    r = subprocess.run(["zstd", "-q", "-t", zst_path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"zstd -t failed (rc={r.returncode}) for {zst_path}: {r.stderr.strip()}")


def zstd_decompress_sha256(zst_path, chunk=1 << 20):
    """Stream `zstd -dc zst_path` into a sha256 hasher, WITHOUT a shell pipe, checking zstd's
    own exit code (a pipe would return sha256sum's status and could mask a zstd failure).
    Returns the sha256 of the decompressed stream. Never materializes the full output."""
    proc = subprocess.Popen(["zstd", "-q", "-dc", zst_path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    h = hashlib.sha256()
    nbytes = 0
    try:
        while True:
            b = proc.stdout.read(chunk)
            if not b:
                break
            h.update(b)
            nbytes += len(b)
    finally:
        proc.stdout.close()
        rc = proc.wait()
        err = proc.stderr.read().decode("utf-8", "replace")
        proc.stderr.close()
    if rc != 0:
        raise RuntimeError(f"zstd -dc failed (rc={rc}) for {zst_path}: {err.strip()}")
    return h.hexdigest(), nbytes


# ========================================================================================
# Read-back smoke test: prove a published .npz.zst actually loads as a cache. (Temporary
# proof here; later factor into cache_io.py. Publishing and reading are different concerns.)
# ========================================================================================
def read_shard_smoke_test(cfs_zst_path):
    """Decompress to a temp file and np.load it; confirm it has the expected index arrays.
    Returns the list of array keys. Uses a temp FILE (robust for large shards), not BytesIO."""
    fd, tmp = tempfile.mkstemp(suffix=".npz")
    os.close(fd)
    try:
        with open(tmp, "wb") as out:
            r = subprocess.run(
                ["zstd", "-q", "-dc", cfs_zst_path],
                stdout=out,
                stderr=subprocess.PIPE,
            )
        if r.returncode != 0:
            raise RuntimeError(f"smoke-test decompress failed for {cfs_zst_path}: {r.stderr.decode().strip()}")
        with np.load(tmp) as z:
            keys = list(z.files)
        for required in ("event_ids", "nsubs", "offsets"):
            if required not in keys:
                raise RuntimeError(f"smoke test: shard {cfs_zst_path} missing index array '{required}'")
        return keys
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ========================================================================================
# Path safety.
# ========================================================================================
def assert_safe_cfs_path(cfs_dir, local_cache):
    cfs_abs = os.path.abspath(cfs_dir)
    local_abs = os.path.abspath(local_cache)
    protected = {os.path.abspath(p) for p in
                 ["/", os.path.expanduser("~"),
                  os.environ.get("PSCRATCH", "/__nope_pscratch__"),
                  os.environ.get("CFS", "/__nope_cfs__")]}
    if cfs_abs in protected:
        raise SystemExit(f"Refusing: --cfs-cache is a protected path: {cfs_abs}")
    if cfs_abs == local_abs or cfs_abs.startswith(local_abs + os.sep) \
            or local_abs.startswith(cfs_abs + os.sep):
        raise SystemExit(f"Refusing: --cfs-cache and --local-cache overlap: {cfs_abs} vs {local_abs}")

def assert_existing_final_safe_to_overwrite(cfs_final, overwrite_non_cache=False):
    """Refuse to overwrite arbitrary existing directories. A normal replacement target
    should be an existing published cache with cache.verified. Anything else is probably
    a path typo or stale partial output and should require an extra explicit flag."""
    if not os.path.exists(cfs_final):
        return

    if not os.path.isdir(cfs_final):
        raise SystemExit(
            f"Refusing to overwrite existing non-directory CFS path: {cfs_final}"
        )

    marker = os.path.join(cfs_final, "cache.verified")
    incomplete = os.path.join(cfs_final, "cache.incomplete")

    if os.path.exists(incomplete):
        raise SystemExit(
            f"Refusing to overwrite path containing cache.incomplete: {cfs_final}\n"
            "This looks like a stale/partial publish. Inspect and remove it manually, "
            "or use --overwrite-non-cache only if you are certain."
        )

    if os.path.isfile(marker):
        try:
            with open(marker) as f:
                old_marker = json.load(f)
        except Exception as e:
            raise SystemExit(
                f"Existing cache.verified is not valid JSON at {marker}: {e}\n"
                "Refusing automatic overwrite. Inspect/remove manually."
            )
        if old_marker.get("schema_version") != EXPECTED_SCHEMA:
            print(
                f"WARNING: overwriting existing cache with schema "
                f"{old_marker.get('schema_version')!r}, expected {EXPECTED_SCHEMA!r}.",
                file=sys.stderr,
            )
        return

    if overwrite_non_cache:
        print(
            f"WARNING: --overwrite-non-cache set; replacing existing directory without "
            f"cache.verified: {cfs_final}",
            file=sys.stderr,
        )
        return

    raise SystemExit(
        f"Refusing to overwrite existing directory without cache.verified:\n"
        f"  {cfs_final}\n"
        f"This does not look like a published cache. If this is intentional, pass both "
        f"--overwrite and --overwrite-non-cache, or remove the directory manually."
    )

def assert_same_filesystem(publishing_dir, final_dir):
    """The atomic os.replace(publishing -> final) requires both on the same filesystem.
    Pre-check by comparing st_dev of their common parent; os.replace's own cross-device
    error is the hard backstop if this is somehow bypassed."""
    parent = os.path.dirname(os.path.abspath(final_dir))
    if not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)
    pub_parent = os.path.dirname(os.path.abspath(publishing_dir))
    if os.path.abspath(parent) != os.path.abspath(pub_parent):
        raise SystemExit(f"Internal error: .publishing parent {pub_parent} != final parent {parent}")
    # st_dev of the shared parent is sufficient (publishing is created inside it).
    _ = os.stat(parent).st_dev  # raises if parent missing; same-dir guarantees same dev


# ========================================================================================
# Provenance.
# ========================================================================================
def git_commit_or_none(path):
    try:
        r = subprocess.run(["git", "-C", os.path.dirname(os.path.abspath(path)) or ".",
                            "rev-parse", "HEAD"], capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except FileNotFoundError:
        pass
    return None


def provenance_hashes(this_script, cache_script, pdg_mass, baryon_dict):
    prov = {}
    for label, path in [("publish_script", this_script), ("cache_script", cache_script),
                        ("pdg_mass_table", pdg_mass), ("baryon_dict", baryon_dict)]:
        if path and os.path.isfile(path):
            prov[f"{label}_sha256"] = sha256_file(path)
            prov[f"{label}_path"] = os.path.abspath(path)
        else:
            prov[f"{label}_sha256"] = None
            prov[f"{label}_path"] = os.path.abspath(path) if path else None
    gc = git_commit_or_none(this_script)
    if gc:
        prov["git_commit"] = gc
    return prov


# ========================================================================================
# Main publish.
# ========================================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Publish a validated local v3 cache to CFS as a compressed, decompress-"
                    "verified archive and write the cache.verified deletion-gate marker. "
                    "Sole writer of cache.verified. Never deletes raw or the local cache.")
    ap.add_argument("--local-cache", required=True, help="Local validated v3 cache directory.")
    ap.add_argument("--cfs-cache", required=True, help="Final CFS destination directory.")
    ap.add_argument("--system", required=True)
    ap.add_argument("--design-id", required=True)
    ap.add_argument("--compression-level", type=int, default=3)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--overwrite", action="store_true",
                    help="Required to replace an existing final CFS cache directory.")
    ap.add_argument("--expected-fingerprint", default=None, metavar="PATH",
                    help="Frozen canonical expected-v3-fingerprint JSON (from "
                         "cache_one_design.py --dump-fingerprint). If given, a mismatch is a "
                         "hard error unless --allow-fingerprint-mismatch.")
    ap.add_argument("--allow-fingerprint-mismatch", action="store_true",
                    help="Downgrade fingerprint mismatch to a warning (testing only).")
    ap.add_argument("--allow-missing-expected-fingerprint", action="store_true",
                    help="Permit publishing without --expected-fingerprint (diagnostic only). "
                         "By default --expected-fingerprint is required so the cache scope is "
                         "compared to the frozen expected v3 fingerprint before publishing.")
    ap.add_argument("--allow-no-surface-conflicts", action="store_true",
                    help="Permit publishing a cache whose status_counts report "
                         "ambiguous_no_surface_conflict events (diagnostic only). By default "
                         "such contradictory event states block a deletion-grade publish.")
    ap.add_argument("--target-events", type=int, default=None,
                    help="Production event target for this design. If cached_events < target, "
                         "design_complete=false and publishing records a shortfall.")
    ap.add_argument("--accept-shortfall", action="store_true",
                    help="Explicitly accept a design shortfall (sets shortfall_accepted=true). "
                         "A deliberate human decision; the reaper refuses deletion of an "
                         "incomplete design unless this was set.")
    ap.add_argument("--shortfall-reason", default=None)
    ap.add_argument("--cache-script", default=None,
                    help="Path to cache_one_design.py for provenance hashing.")
    ap.add_argument("--pdg-mass", default=os.environ.get("HIC_PDG_MASS"),
                    help="PDG mass table path for provenance hashing.")
    ap.add_argument("--baryon-dict", default=os.environ.get("HIC_BARYON_DICT"),
                    help="Baryon dict path for provenance hashing.")
    ap.add_argument("--overwrite-non-cache", action="store_true",
                    help="DANGEROUS: allow --overwrite to replace an existing CFS directory "
                         "that does not contain cache.verified. Normally this is refused to "
                         "protect against typoing --cfs-cache as a broader project directory.")
    args = ap.parse_args()

    local = args.local_cache
    meta_path = os.path.join(local, "metadata.json")
    if not os.path.isfile(meta_path):
        raise SystemExit(f"No metadata.json in local cache: {local}. Not a valid cache directory.")
    with open(meta_path) as f:
        metadata = json.load(f)
    fingerprint = metadata.get("fingerprint", {})
    schema = fingerprint.get("schema_version")
    if schema != EXPECTED_SCHEMA:
        raise SystemExit(f"Local cache schema is {schema!r}, expected {EXPECTED_SCHEMA!r}. Refusing.")

    shard_files = list(metadata.get("shards", []))
    if not shard_files:
        raise SystemExit("metadata.json lists no shards. Nothing to publish.")
    for fn in shard_files:
        if not os.path.isfile(os.path.join(local, fn)):
            raise SystemExit(f"Shard listed in metadata is missing on disk: {fn}")
            
    validation = metadata.get("validation", {})
    if not validation.get("raw_reference_enabled") or validation.get("raw_reference_events", 0) <= 0:
        raise SystemExit("Local cache metadata does not prove raw-reference validation passed.")

    zver = zstd_version()
    print(f"Using {zver}; compression level {args.compression_level}, threads {args.threads}.")

    # Fingerprint scope check (publish-time).
    fp_hash = fingerprint_hash(fingerprint)
    expected_hash = None
    expected_match = None
    if not args.expected_fingerprint and not args.allow_missing_expected_fingerprint:
        raise SystemExit(
            "ABORT: --expected-fingerprint is required (the frozen canonical "
            "expected_cache_fingerprint_v3.json) so the cache scope is compared to the "
            "frozen expected scope before publishing. Pass --allow-missing-expected-fingerprint "
            "for a diagnostic publish without this check.")
    if args.expected_fingerprint:
        with open(args.expected_fingerprint) as f:
            expected_fp = json.load(f)
        expected_hash = fingerprint_hash(expected_fp)
        expected_match = (expected_hash == fp_hash)
        if not expected_match:
            msg = (f"Fingerprint MISMATCH vs frozen expected scope:\n"
                   f"  cache fingerprint_hash    = {fp_hash}\n"
                   f"  expected fingerprint_hash = {expected_hash}\n"
                   f"  ({args.expected_fingerprint})")
            if args.allow_fingerprint_mismatch:
                print("WARNING (overridden):", msg, file=sys.stderr)
            else:
                raise SystemExit("ABORT: " + msg +
                                 "\nPass --allow-fingerprint-mismatch to override (testing only).")
        else:
            print("Fingerprint matches frozen expected v3 scope.")

    # Completeness vs validity.
    cached_events = int(metadata.get("n_events", 0))

    # Terminal-event breakdown, derived from the cache's status_counts. A design
    # is complete on the basis of TERMINAL events, not particle events: a
    # hydro.no_surface event is a successfully simulated zero-output event that is
    # cached as an nsub=0 record, so it already counts toward n_events /
    # cached_events below.
    status_counts = metadata.get("status_counts", {})
    n_particle_events = int(status_counts.get("particle_file", 0))
    n_no_surface_events = int(status_counts.get("no_surface", 0))
    n_completed_zero_events = int(status_counts.get("completed_zero", 0))
    n_terminal_events = n_particle_events + n_no_surface_events + n_completed_zero_events
    n_no_surface_conflicts = int(status_counts.get("ambiguous_no_surface_conflict", 0))
    if n_no_surface_conflicts:
        msg = (f"cache reports {n_no_surface_conflicts} ambiguous_no_surface_conflict event(s) "
               f"(hydro.no_surface coexisting with hydro.done / afterburner.done / particle "
               f"output / nonempty surface). The event-state taxonomy is internally contradictory.")
        if args.allow_no_surface_conflicts:
            print("WARNING (overridden):", msg,
                  "Publishing anyway (--allow-no-surface-conflicts; diagnostic only).",
                  file=sys.stderr)
        else:
            raise SystemExit(
                "ABORT: " + msg + " Refusing to publish a deletion-grade cache. Investigate, "
                "or pass --allow-no-surface-conflicts for a diagnostic publish.")
    # Other ambiguous/truncated particle states do not block publish: the affected
    # events are simply not cached, so the reaper will not be told to delete their raw.
    for amb in ("ambiguous_particle_variants", "ambiguous_zero_byte_particle"):
        c = int(status_counts.get(amb, 0))
        if c:
            print(f"WARNING: cache reports {c} {amb} event(s); these were NOT cached "
                  f"(their raw is not eligible for deletion). Investigate if unexpected.",
                  file=sys.stderr)
    if n_no_surface_events or n_completed_zero_events:
        print(f"Terminal-event breakdown: particle={n_particle_events} "
              f"no_surface={n_no_surface_events} completed_zero={n_completed_zero_events} "
              f"-> terminal={n_terminal_events} (cached records n_events={cached_events}).")

    if args.target_events is not None:
        design_complete = cached_events >= args.target_events
    else:
        design_complete = None

    # Events the cache builder DROPPED as non-salvageable (cache_one_design --max-dropped-events).
    # These are terminal events (afterburner.done) whose OSCAR was corrupt; they were excluded
    # from the cache, lowering cached_events below target. A shortfall that is FULLY explained by
    # such drops -- i.e. the design still reached target terminal events once the drops are added
    # back -- is auto-accepted here, so the reaper will reap. A genuine under-run (cached_events
    # short for any other reason, or short even after adding drops back) is NOT auto-accepted and
    # still requires explicit --accept-shortfall or completion.
    n_dropped_bad_events = int(metadata.get("n_dropped_bad_events", 0))
    drop_explained_shortfall = (
        design_complete is False
        and n_dropped_bad_events > 0
        and (cached_events + n_dropped_bad_events) >= args.target_events
    )
    shortfall_accepted = bool(args.accept_shortfall) or drop_explained_shortfall
    shortfall_reason = args.shortfall_reason
    if drop_explained_shortfall and not args.accept_shortfall:
        shortfall_reason = (shortfall_reason + "; " if shortfall_reason else "") + (
            f"auto-accepted: {n_dropped_bad_events} non-salvageable event(s) dropped at cache "
            f"time; cached {cached_events} + dropped {n_dropped_bad_events} >= target "
            f"{args.target_events}")
        print(f"NOTE: shortfall auto-accepted -- cached {cached_events} is below target "
              f"{args.target_events} solely because {n_dropped_bad_events} non-salvageable "
              f"event(s) were dropped; design reached target terminal events. Reaping allowed.",
              file=sys.stderr)
    if design_complete is False and not shortfall_accepted:
        print(f"NOTE: design appears incomplete (cached {cached_events} < target "
              f"{args.target_events}) and --accept-shortfall NOT set. Publishing the cache is "
              f"allowed, but the reaper will refuse raw deletion until the shortfall is "
              f"accepted or the design is completed.", file=sys.stderr)

    # ---- set up .publishing sibling on the SAME CFS filesystem as the final path ----
    cfs_final = os.path.abspath(args.cfs_cache)
    assert_safe_cfs_path(cfs_final, local)
    if os.path.exists(cfs_final):
        if not args.overwrite:
            raise SystemExit(f"Final CFS cache exists: {cfs_final}. Pass --overwrite to replace.")
        assert_existing_final_safe_to_overwrite(
            cfs_final,
            overwrite_non_cache=args.overwrite_non_cache,
        )
    cfs_parent = os.path.dirname(cfs_final)
    os.makedirs(cfs_parent, exist_ok=True)
    cfs_pub = os.path.join(cfs_parent, "." + os.path.basename(cfs_final) + ".publishing")
    assert_same_filesystem(cfs_pub, cfs_final)
    if os.path.exists(cfs_pub):
        shutil.rmtree(cfs_pub)
    os.makedirs(cfs_pub, exist_ok=True)

    # in-progress sentinel so a stale verified marker can never be trusted mid-publish
    with open(os.path.join(cfs_pub, "cache.incomplete"), "w") as f:
        json.dump({"started_unix": time.time()}, f)

    t0 = time.time()
    files_record = {}
    total_compressed = 0
    total_uncompressed = 0

    print(f"Publishing {len(shard_files)} shards + metadata.json to {cfs_final}")
    all_event_ids = set()
    duplicate_event_ids = []
    for fn in shard_files:
        local_npz = os.path.join(local, fn)
        uncompressed_sha = sha256_file(local_npz)            # anchored to validated original
        uncompressed_size = os.path.getsize(local_npz)

        zst_name = fn + ".zst"
        local_zst_tmp = os.path.join(local, zst_name + ".tmp")
        zstd_compress(local_npz, local_zst_tmp, args.compression_level, args.threads)
        zstd_test(local_zst_tmp)                              # local frame integrity
        local_zst = os.path.join(local, zst_name)
        os.replace(local_zst_tmp, local_zst)                  # only after -t passes

        # copy compressed file to CFS .publishing, fsync
        cfs_zst = os.path.join(cfs_pub, zst_name)
        with open(local_zst, "rb") as src, open(cfs_zst + ".tmp", "wb") as dst:
            while True:
                b = src.read(1 << 20)
                if not b:
                    break
                dst.write(b)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(cfs_zst + ".tmp", cfs_zst)

        compressed_sha = sha256_file(cfs_zst)                 # cheap CFS-transfer integrity
        compressed_size = os.path.getsize(cfs_zst)
        zstd_test(cfs_zst)                                    # CFS frame integrity (exit-checked)
        decomp_sha, decomp_n = zstd_decompress_sha256(cfs_zst)  # THE recoverability gate
        if decomp_sha != uncompressed_sha:
            raise RuntimeError(
                f"RECOVERABILITY GATE FAILED for {fn}: decompressed CFS copy hash {decomp_sha} "
                f"!= validated local .npz hash {uncompressed_sha}. CFS archive does NOT recover "
                f"the validated cache; aborting (no marker written).")
        if decomp_n != uncompressed_size:
            raise RuntimeError(f"Decompressed size {decomp_n} != original {uncompressed_size} for {fn}")

        # Independent packed-event-id reconciliation: read the event_ids actually
        # packed into this (now recoverability-verified) shard and accumulate them.
        # Checked against metadata n_events after the loop; catches a shard that
        # silently dropped or duplicated events. Read from the local .npz, which the
        # recoverability gate just proved is byte-identical to the CFS copy.
        with np.load(local_npz) as _z:
            shard_ids = [int(x) for x in _z["event_ids"]]
        for eid in shard_ids:
            if eid in all_event_ids:
                duplicate_event_ids.append(eid)
            all_event_ids.add(eid)

        files_record[zst_name] = {
            "compressed_sha256": compressed_sha,
            "compressed_size_bytes": compressed_size,
            "uncompressed_sha256": uncompressed_sha,
            "uncompressed_size_bytes": uncompressed_size,
            "uncompressed_name": fn,
        }
        total_compressed += compressed_size
        total_uncompressed += uncompressed_size
        # clean up the local .zst (the local cache stays as .npz; .zst is a publish artifact)
        os.remove(local_zst)
        print(f"  {fn}: {uncompressed_size/1e6:.1f} MB -> {compressed_size/1e6:.1f} MB "
              f"({uncompressed_size/max(1,compressed_size):.1f}x), recoverability verified")

    # ---- packed-event-id reconciliation (hard gate; no override) ----
    # The shards' actual event_ids must be unique and number exactly metadata
    # n_events. There is no benign reason for these to disagree; a mismatch means
    # the cache is internally inconsistent and must not gate raw deletion.
    if duplicate_event_ids:
        dups = sorted(set(duplicate_event_ids))
        raise RuntimeError(
            f"Duplicate event_ids across shards: {dups[:20]}"
            f"{f' (+{len(dups) - 20} more)' if len(dups) > 20 else ''}. "
            f"Cache is internally inconsistent; refusing to publish (no marker written).")
    if len(all_event_ids) != cached_events:
        raise RuntimeError(
            f"Packed-event-id reconciliation FAILED: shards contain {len(all_event_ids)} unique "
            f"event_ids but metadata n_events = {cached_events}. Cache is internally inconsistent; "
            f"refusing to publish (no marker written).")
    print(f"Packed-event-id reconciliation passed: {len(all_event_ids)} unique event_ids "
          f"== n_events ({cached_events}).")

    # metadata.json: copy verbatim and hash (uncompressed; it is small)
    cfs_meta = os.path.join(cfs_pub, "metadata.json")
    shutil.copyfile(meta_path, cfs_meta)
    meta_sha = sha256_file(cfs_meta)
    if meta_sha != sha256_file(meta_path):
        raise RuntimeError("metadata.json copy hash mismatch")
    files_record["metadata.json"] = {
        "sha256": meta_sha,
        "size_bytes": os.path.getsize(cfs_meta),
        "compressed": False,
    }

    # ---- read-back smoke test on one published shard ----
    first_zst = os.path.join(cfs_pub, shard_files[0] + ".zst")
    keys = read_shard_smoke_test(first_zst)
    print(f"Read-back smoke test passed on {shard_files[0]}.zst ({len(keys)} arrays load).")

    # ---- write cache.verified (only now, after every file passed) ----
    prov = provenance_hashes(os.path.abspath(__file__), args.cache_script,
                             args.pdg_mass, args.baryon_dict)
    metadata_bytes = os.path.getsize(cfs_meta)
    marker = {
        "cache_valid": True,
        "schema_version": EXPECTED_SCHEMA,
        "system": args.system,
        "design_id": args.design_id,
        "local_cache_path": os.path.abspath(local),
        "cfs_cache_path": cfs_final,
        "compression": {
            "codec": "zstd",
            "format": "zstd-frame",
            "level": args.compression_level,
            "threads": args.threads,
            "zstd_version": zver,
            "verification": "compressed-hash + zstd-t + decompress-and-hash-vs-validated-original",
        },
        "files": files_record,
        "total_compressed_shard_bytes": total_compressed,
        "total_decompressed_shard_bytes": total_uncompressed,
        "metadata_json_bytes": metadata_bytes,
        "total_cfs_payload_bytes_excluding_marker": total_compressed + metadata_bytes,
        "total_recoverable_cache_content_bytes_excluding_marker": total_uncompressed + metadata_bytes,
        "size_accounting_note": (
            "Shard totals count only shard payloads. Metadata.json is stored uncompressed "
            "and verified by ordinary sha256, not zstd decompression. "
            "total_cfs_payload_bytes_excluding_marker = compressed shards + metadata.json. "
            "total_recoverable_cache_content_bytes_excluding_marker = decompressed shard "
            "contents + metadata.json. cache.verified is excluded because the marker "
            "contains these values and would otherwise be self-referential."),
        "n_events": cached_events,
        "n_subevents_total": int(metadata.get("n_subevents_total", 0)),
        "status_counts": metadata.get("status_counts", {}),
        "n_particle_events": n_particle_events,
        "n_no_surface_events": n_no_surface_events,
        "n_completed_zero_events": n_completed_zero_events,
        "n_terminal_events": n_terminal_events,
        "n_no_surface_conflicts": n_no_surface_conflicts,
        "n_zero_records": int(metadata.get("n_zero_records", 0)),
        "n_nonzero_records": int(metadata.get("n_nonzero_records", 0)),
        "unique_event_ids_verified": len(all_event_ids),
        "nsubs_zero_reader_note": (
            "This cache may contain records with nsubs==0 representing terminal "
            "zero-output events (hydro.no_surface; also completed_zero if that was "
            "enabled). They preserve event_ids and centrality/event-count denominators. "
            "A downstream reader that iterates packed subevent rows will SILENTLY DROP "
            "these from centrality / percentile construction unless it explicitly treats "
            "nsubs==0 as a real Nch=0 event (and gives zero WEIGHT, not zero VALUE, to "
            "flow cumulants and yield ratios)."),
        "terminal_event_accounting_note": (
            "n_events is the number of cached event records and is the basis for "
            "design_complete. It already includes terminal zero-output events "
            "(hydro.no_surface, plus completed_zero if --allow-completed-zero was used), "
            "so a design can be complete with n_particle_events < target_events. "
            "n_terminal_events = n_particle_events + n_no_surface_events + "
            "n_completed_zero_events, all from the cache status_counts. n_particle_events "
            "counts events with afterburner.done and a nonempty particle file (a few may "
            "have been cached as zero records if they parsed to zero subevents under "
            "--allow-parse-zero), so n_terminal_events need not equal n_events exactly."),
        "target_events": args.target_events,
        "cached_events": cached_events,
        "n_dropped_bad_events": n_dropped_bad_events,
        "design_complete": design_complete,
        "shortfall_accepted": shortfall_accepted,
        "shortfall_reason": shortfall_reason,
        "fingerprint": fingerprint,
        "fingerprint_hash": fp_hash,
        "expected_fingerprint_path": (os.path.abspath(args.expected_fingerprint)
                                      if args.expected_fingerprint else None),
        "expected_fingerprint_hash": expected_hash,
        "expected_fingerprint_match": expected_match,
        "provenance": prov,
        "verified_unix": time.time(),
        "verified_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "cache_started_unix": metadata.get("cache_started_unix"),
        "cache_created_unix": metadata.get("created_unix"),
        "note": "Each file was copied to CFS, integrity-tested, and DECOMPRESSED-AND-HASHED "
                "against the validated local original. The reaper MUST independently re-run "
                "zstd -t + decompress-and-hash for all shards (compare uncompressed_sha256) "
                "and re-check fingerprint_hash against the frozen expected scope before "
                "deleting raw. cache_valid != design_complete; refuse deletion of an "
                "incomplete design unless shortfall_accepted.",
                
    }
    marker_tmp = os.path.join(cfs_pub, "cache.verified.tmp")
    with open(marker_tmp, "w") as f:
        json.dump(marker, f, indent=2, sort_keys=True)
    os.replace(marker_tmp, os.path.join(cfs_pub, "cache.verified"))
    os.remove(os.path.join(cfs_pub, "cache.incomplete"))

    # ---- atomic swap .publishing -> final ----
    if os.path.exists(cfs_final):
        shutil.rmtree(cfs_final)
    os.replace(cfs_pub, cfs_final)

    ratio = total_uncompressed / max(1, total_compressed)
    print(f"Published in {time.time()-t0:.1f}s. "
          f"{total_uncompressed/1e9:.2f} GB -> {total_compressed/1e9:.2f} GB ({ratio:.1f}x).")
    print(f"cache.verified written to {cfs_final}.")
    if design_complete is False:
        if shortfall_accepted:
            print(f"NOTE: design_complete=False but shortfall_accepted=True "
                  f"(reason: {shortfall_reason!r}). The reaper may permit raw deletion "
                  f"on the strength of this accepted shortfall.")
        else:
            print(f"REMINDER: design_complete=False and shortfall_accepted=False. "
                  f"The reaper will REFUSE raw deletion until the shortfall is accepted "
                  f"(re-publish with --accept-shortfall) or the design is completed.")
    print("Raw particle lists and local cache NOT deleted.")


if __name__ == "__main__":
    main()
