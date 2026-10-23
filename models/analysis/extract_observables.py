#!/usr/bin/env python3
"""
extract_observables.py -- per-design observable sufficient-statistic extractor.

Reads ONE cache_one_design/v3 cache (published CFS form with `shard_*.npz.zst`, or a local
uncompressed cache with `shard_*.npz`) and writes a compact, self-describing per-design
artifact (.npz + .meta.json) of INTEGRATED-observable sufficient statistics for off-NERSC
Bayesian-inference aggregation.

It is READ-ONLY: it never writes into the cache, never decompresses in place, never touches raw
particle lists, and never calls repair/production scripts. It is safe to run as a NERSC array
(one task per design) alongside ongoing cache builds.

Design decisions (see schema proposal + review):
  * Stores per-hydro-event SUFFICIENT STATISTICS, not reduced 5% observables, so centrality
    consolidation downstream is "sum the stats then reduce" (exact), never "average ratios".
  * Stores RAW oversample-sums + `nsub_used`. The canonical reduction is EQUAL-HYDRO-EVENT
    weighting -- Sum_i(num_i/nsub_i) / Sum_i(den_i/nsub_i) -- performed in reduce_observables.py.
    Storing raw sums + nsub (not pre-divided) keeps the equal-vs-all-oversample comparison
    computable and the convention visible at reduction.
  * nsub==0 (hydro.no_surface, valid zero-output) events are KEPT: zero_event=True,
    cent_mult=0, all contributions 0, ranked most peripheral downstream. They are counted in
    n_events. They contribute no oversample rows, so iterating events via `offsets` yields an
    empty slice and a natural zero -- which is exactly why we iterate events, not packed rows.
  * Integrated observables only. Differential (pT-differential) flow and pT spectra live in the
    caches for posterity and are intentionally not surfaced here. Charged pT FLUCTUATIONS are
    out of scope (recorded in excluded_observables); they are not cleanly recoverable from the
    cache anyway (charged sum_pT/sum_pT2 are hydro-event-aggregated, not per-oversample).
  * Identified yields are stored as the FULL per-event rapidity histogram (not pre-integrated
    to a window), so the midrapidity window is a downstream choice, matching the cache's own
    0.1-bin y resolution.

Observable building blocks emitted (per hydro event; see reduce_observables.py for reconstruction):
  yields/<pT>      : identified -> full y-hist (yh#); charged -> pT-integrated pth#charged_eta{05,10}
  v_n{2}, n=2,3,4  : self two-subevent (pooled + gapped) for charged + all species;
                     differential POI-vs-charged-reference (gapped, disjoint hemispheres) for
                     identified species.
  dE_T/deta        : global-ET masks, midrapidity-integrated (optional/extra-to-spec).

NOTE: this script was written against the v3 cache source but has NOT been executed against a
real cache here. The one-design round-trip in reduce_observables.py --validate-against-cache is the
acceptance gate before launching the array.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time

import numpy as np

# ----------------------------------------------------------------------------------------
# Schema contract -- MUST match cache_one_design/v3. Asserted structurally against shard 0.
# ----------------------------------------------------------------------------------------
EXPECTED_CACHE_SCHEMA = "cache_one_design/v3"
# Stored schema and artifact names remain stable for existing campaigns.
OBSERVABLE_SCHEMA = "stage_a/v1"

CORE_SPECIES = ["pi+", "pi-", "K+", "K-", "p", "pbar", "K0S",
                "Lambda", "antiLambda", "Xi-", "antiXi+", "Omega-", "antiOmega+", "phi"]
FLOW_SPECIES = ["charged"] + CORE_SPECIES          # index 0 == charged reference
Y_HIST_SPECIES = list(CORE_SPECIES)                # IDENTIFIED_Y_HIST_SPECIES order
CHARGED_PTH_KEYS = ["charged_eta05", "charged_eta10"]   # pth# hk names; etawin axis order
CENTRALITY_LABELS = ["cent_ch_eta05", "cent_ch_eta10", "cent_ch_sideband_eta05_10"]
GLOBAL_ET_MASKS = ["total_visible", "hadronic"]
GLOBAL_ET_FIELDS = ["sum_ET_total", "sum_B_sintheta", "sum_BmPDG_sintheta"]
SCALAR_KEYS = ["N", "sum_pT", "sum_pT2", "sum_ET_total"]
PT_HIST_FIELDS = ["N", "sum_pT", "sum_pT2"]
Y_HIST_FIELDS = ["N", "sum_pT", "sum_pT2"]          # sum_ET_total dropped (diagnostic, not needed)
HARMONICS = [2, 3, 4]                               # cache stores q2/q3/q4 only (no q1 -> no v1)

# Bin edges -- must match v3. Assertion (below) checks the array widths agree with the shard.
Y_HIST_INNER_EDGES = np.round(np.arange(-1.0, 1.0 + 1e-9, 0.1), 10)
ETA_HIST_INNER_EDGES = np.round(np.arange(-1.2, 1.2 + 1e-9, 0.05), 10)
N_Y_HIST_BINS = len(Y_HIST_INNER_EDGES) + 1
N_ETA_HIST_BINS = len(ETA_HIST_INNER_EDGES) + 1

# Quantities excluded from this artifact, recorded for provenance.
EXCLUDED_OBSERVABLES = [
    "charged_pT_fluctuations (out of scope; not cleanly recoverable from v3 cache)",
    "pT_differential_flow (kept in caches for posterity)",
    "pT_spectra (kept in caches for posterity)",
    "v1_directed_flow (cache stores q2/q3/q4 only)",
    "higher_cumulants v_n{4}+/6-particle (requires harmonics/correlations not cached)",
]


# ----------------------------------------------------------------------------------------
# IO helpers (handle both local .npz and published .npz.zst; metadata .json or .json.zst)
# ----------------------------------------------------------------------------------------
def _run(cmd):
    return subprocess.run(cmd, capture_output=True)


def _zstd_decompress_to_tempfile(zst_path, zstd="zstd"):
    fd, tmp = tempfile.mkstemp(suffix=".npz")
    os.close(fd)
    r = _run([zstd, "-q", "-f", "-d", zst_path, "-o", tmp])
    if r.returncode != 0:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise RuntimeError(f"zstd -d failed for {zst_path}: {r.stderr.decode(errors='replace').strip()}")
    return tmp


def _read_text_maybe_zst(path_base, zstd="zstd"):
    if os.path.exists(path_base):
        with open(path_base) as f:
            return f.read()
    if os.path.exists(path_base + ".zst"):
        r = _run([zstd, "-q", "-dc", path_base + ".zst"])
        if r.returncode != 0:
            raise RuntimeError(f"zstd -dc failed for {path_base}.zst: {r.stderr.decode(errors='replace').strip()}")
        return r.stdout.decode()
    raise FileNotFoundError(path_base + " (and .zst)")


def _resolve_shard_path(cache_dir, base_name):
    """base_name like 'shard_0000.npz'; published form is base_name + '.zst'."""
    p = os.path.join(cache_dir, base_name)
    if os.path.exists(p):
        return p, False
    if os.path.exists(p + ".zst"):
        return p + ".zst", True
    raise FileNotFoundError(f"missing shard {base_name} (and .zst) in {cache_dir}")


def _load_shard(shard_path, is_zst, zstd="zstd"):
    if not is_zst:
        return np.load(shard_path), None
    tmp = _zstd_decompress_to_tempfile(shard_path, zstd=zstd)
    return np.load(tmp), tmp


# ----------------------------------------------------------------------------------------
# Schema gate
# ----------------------------------------------------------------------------------------
def _assert_schema(z0, marker, fingerprint):
    """Gate on cache.verified schema if present, plus a structural check on shard 0 keys."""
    if marker is not None:
        got = marker.get("schema_version")
        if got != EXPECTED_CACHE_SCHEMA:
            raise SystemExit(f"cache.verified schema_version {got!r} != {EXPECTED_CACHE_SCHEMA!r}")
    elif isinstance(fingerprint, dict):
        got = fingerprint.get("schema_version") or fingerprint.get("cache_schema_version")
        if got is not None and got != EXPECTED_CACHE_SCHEMA:
            raise SystemExit(f"fingerprint schema_version {got!r} != {EXPECTED_CACHE_SCHEMA!r}")
    keys = set(z0.files)
    required = ["event_ids", "nsubs", "offsets",
                "sc#0#N", "fl#0#m_full", "fl#0#q2_full", "fl#0#q2_fwd", "fl#0#q2_bwd",
                "yh#0#N", f"pth#{CHARGED_PTH_KEYS[1]}#N", f"cent#{CENTRALITY_LABELS[1]}",
                f"get#{GLOBAL_ET_MASKS[0]}#{GLOBAL_ET_FIELDS[0]}"]
    missing = [k for k in required if k not in keys]
    if missing:
        raise SystemExit(f"shard is not v3-shaped; missing keys: {missing[:8]}")
    # bin-width sanity
    if z0["yh#0#N"].shape[1] != N_Y_HIST_BINS:
        raise SystemExit(f"y-hist width {z0['yh#0#N'].shape[1]} != expected {N_Y_HIST_BINS}")
    if z0[f"get#{GLOBAL_ET_MASKS[0]}#{GLOBAL_ET_FIELDS[0]}"].shape[1] != N_ETA_HIST_BINS:
        raise SystemExit("eta-hist width mismatch vs expected v3 edges")


def _central_eta_bin_mask(eta_window):
    """Boolean mask over the N_ETA_HIST_BINS eta-hist bins selecting |eta| < eta_window.

    Bin index convention is searchsorted(ETA_HIST_INNER_EDGES, eta, 'right'): bin b (1..len)
    covers [edge_{b-1}, edge_b); bin 0 is underflow, bin len is overflow. A bin is "inside"
    the window iff its FULL [lo, hi) interval lies within [-eta_window, +eta_window).
    """
    edges = ETA_HIST_INNER_EDGES
    mask = np.zeros(N_ETA_HIST_BINS, dtype=bool)
    for b in range(1, len(edges) + 1):          # interior bins only (skip under/overflow)
        lo = edges[b - 1]
        hi = edges[b] if b < len(edges) else np.inf
        if lo >= -eta_window - 1e-9 and hi <= eta_window + 1e-9:
            mask[b] = True
    return mask


# ----------------------------------------------------------------------------------------
# Core extraction
# ----------------------------------------------------------------------------------------
def _canonical_fingerprint_hash(fingerprint):
    """sha256 of the canonical fingerprint JSON. MUST match the canonical_fingerprint_json used
    by cache_one_design / publish_cache_to_cfs (sort_keys=True, tight separators)."""
    canon = json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def load_expected_fingerprint(path):
    """CLI analysis requires a selected scope, independent of array compatibility."""
    if not path:
        raise ValueError("expected fingerprint is required: run through models/campaign.py "
                         "--config CFG or pass --expected-fingerprint PATH")
    with open(os.path.expandvars(os.path.expanduser(path))) as f:
        expected = json.load(f)
    if not isinstance(expected, dict) or expected.get("schema_version") != EXPECTED_CACHE_SCHEMA:
        raise ValueError("expected fingerprint is not a v3 cache scope")
    return expected


def assert_expected_scope(fingerprint, expected, context):
    if not isinstance(fingerprint, dict):
        raise ValueError(f"{context}: missing cache fingerprint")
    if _canonical_fingerprint_hash(fingerprint) != _canonical_fingerprint_hash(expected):
        keys = sorted(k for k in fingerprint.keys() | expected.keys()
                      if fingerprint.get(k) != expected.get(k))
        raise ValueError(f"{context}: cache does not match selected expected fingerprint; "
                         f"differing keys: {', '.join(keys)}")


def validate_artifact_scope(prefix, expected):
    with open(prefix + ".meta.json") as f:
        meta = json.load(f)
    fp = meta.get("cache_fingerprint")
    assert_expected_scope(fp, expected, prefix)
    if meta.get("cache_fingerprint_hash") != _canonical_fingerprint_hash(fp):
        raise ValueError(f"{prefix}: artifact fingerprint/hash mismatch")
    return meta


def validate_cache_scope(cache_dir, expected, zstd="zstd"):
    meta = json.loads(_read_text_maybe_zst(os.path.join(cache_dir, "metadata.json"), zstd=zstd))
    fp = meta.get("fingerprint")
    assert_expected_scope(fp, expected, cache_dir)
    if meta.get("fingerprint_hash") not in (None, _canonical_fingerprint_hash(fp)):
        raise ValueError(f"{cache_dir}: metadata fingerprint/hash mismatch")
    return meta


def _assert_shard_consistency(z, base):
    """Cheap internal-consistency checks; clearer failures than downstream shape errors.
    Published caches satisfy these by construction; this mainly guards local/pre-publish runs."""
    ev = z["event_ids"]
    offs = z["offsets"]
    nsubs = z["nsubs"]
    ne = len(ev)
    if len(offs) != ne + 1:
        raise SystemExit(f"{base}: offsets length {len(offs)} != n_events+1 ({ne + 1})")
    if int(offs[0]) != 0 or np.any(np.diff(offs) < 0):
        raise SystemExit(f"{base}: offsets not monotonic from 0")
    if len(nsubs) != ne or not np.array_equal(np.diff(offs), nsubs):
        raise SystemExit(f"{base}: offsets do not match nsubs")
    total_sub = int(offs[-1])
    if z[f"cent#{CENTRALITY_LABELS[0]}"].shape[0] != total_sub:
        raise SystemExit(f"{base}: per-oversample array length != offsets[-1] ({total_sub})")
    if z["yh#0#N"].shape[0] != ne:
        raise SystemExit(f"{base}: per-event array length != n_events ({ne})")


def _assert_fingerprint_matches_constants(fp):
    """Verify the cache fingerprint's semantics match the constants this code reconstructs
    against. Keys/formats confirmed from cache_one_design.build_fingerprint(). Catches a
    same-shape, different-semantics cache (different species order, cuts, or binning), which a
    structural shape check cannot."""
    def _eq(key, expected):
        if fp.get(key) != expected:
            raise SystemExit(f"cache fingerprint {key!r} mismatch: {fp.get(key)!r} != {expected!r}")

    def _close(key, expected, atol=1e-9):
        got = fp.get(key)
        if got is None or len(got) != len(expected) or not np.allclose(np.asarray(got, float), expected, atol=atol):
            raise SystemExit(f"cache fingerprint {key!r} edges mismatch vs expected v3")

    def _scalar(key, expected, atol=1e-6):
        got = fp.get(key)
        if got is None or abs(float(got) - expected) > atol:
            raise SystemExit(f"cache fingerprint {key!r} = {got!r} != {expected}")

    _eq("schema_version", EXPECTED_CACHE_SCHEMA)
    _eq("core_species", CORE_SPECIES)
    _eq("flow_species", FLOW_SPECIES)
    _eq("identified_y_hist_species", Y_HIST_SPECIES)
    _eq("charged_pt_hist_eta_windows", [0.5, 1.0])     # -> charged_eta05 / charged_eta10
    _eq("global_et_masks", GLOBAL_ET_MASKS)
    _eq("centrality_windows", [["cent_ch_eta05", 0.0, 0.5],
                                ["cent_ch_eta10", 0.0, 1.0],
                                ["cent_ch_sideband_eta05_10", 0.5, 1.0]])
    # binning the reconstruction depends on (window masks for yields and central-ET)
    _close("y_hist_inner_edges", Y_HIST_INNER_EDGES)
    _close("eta_hist_inner_edges", ETA_HIST_INNER_EDGES)
    # Flow arrays already encode their cuts; extraction never re-cuts them.
    # Validate the domain and carry the actual fingerprint into the artifact.
    values = {}
    for key in ("subevent_eta_gap", "flow_pt_min", "flow_pt_max",
                "charged_eta_max", "identified_y_max", "identified_flow_y_max"):
        try:
            values[key] = float(fp[key])
        except (KeyError, TypeError, ValueError):
            raise SystemExit(f"missing or invalid cache cut {key!r}")
        if not np.isfinite(values[key]):
            raise SystemExit(f"nonfinite cache cut {key!r}")
    if not (0 <= values["flow_pt_min"] < values["flow_pt_max"]):
        raise SystemExit("invalid cached flow pT interval")
    if not (0 < values["charged_eta_max"] <= ETA_HIST_INNER_EDGES[-1]
            and 0 <= values["subevent_eta_gap"] < 2 * values["charged_eta_max"]):
        raise SystemExit("invalid cached charged acceptance / subevent gap")
    for key in ("identified_y_max", "identified_flow_y_max"):
        if not (0 < values[key] <= Y_HIST_INNER_EDGES[-1]):
            raise SystemExit(f"invalid cached rapidity acceptance {key!r}")
    # storage-level assumptions the whole reconstruction rests on
    hsl = fp.get("histogram_storage_levels", {})
    if hsl.get("flow_q_vectors") != "per_oversample":
        raise SystemExit("fingerprint: flow_q_vectors not per_oversample")
    for k in ("scalar_pt_histograms", "identified_y_histograms"):
        if hsl.get(k) != "hydro_event_aggregated_over_oversamples":
            raise SystemExit(f"fingerprint: {k} not hydro_event_aggregated_over_oversamples")


def extract_design(cache_dir, et_eta_window=0.5, require_verified=True, zstd="zstd",
                   expected_fingerprint=None):
    meta = json.loads(_read_text_maybe_zst(os.path.join(cache_dir, "metadata.json"), zstd=zstd))
    fingerprint = meta.get("fingerprint")
    if expected_fingerprint is not None:
        assert_expected_scope(fingerprint, expected_fingerprint, cache_dir)
    fingerprint_hash = meta.get("fingerprint_hash")
    if isinstance(fingerprint, dict):
        computed_hash = _canonical_fingerprint_hash(fingerprint)
        if fingerprint_hash is not None and fingerprint_hash != computed_hash:
            raise SystemExit("metadata fingerprint_hash disagrees with fingerprint contents")
        fingerprint_hash = computed_hash

    # Semantic gate: the cache fingerprint must agree with the constants this code reconstructs
    # against. Shape checks alone cannot catch a same-shape, different-semantics cache.
    if isinstance(fingerprint, dict):
        _assert_fingerprint_matches_constants(fingerprint)
    elif require_verified:
        raise SystemExit("metadata.json has no fingerprint; cannot verify cache semantics")

    marker = None
    marker_path = os.path.join(cache_dir, "cache.verified")
    if os.path.exists(marker_path):
        with open(marker_path) as f:
            marker = json.load(f)
        # Strict checks against the marker keys actually written by publish_cache_to_cfs
        # (confirmed: cache_valid, schema_version, fingerprint_hash, expected_fingerprint_match,
        # n_events). NOTE: design_complete is intentionally NOT gated -- accepted-shortfall
        # designs legitimately carry design_complete=False and are valid for analysis.
        if marker.get("cache_valid") is not True:
            raise SystemExit("cache.verified cache_valid is not True")
        if marker.get("schema_version") != EXPECTED_CACHE_SCHEMA:
            raise SystemExit(f"cache.verified schema_version {marker.get('schema_version')!r} != {EXPECTED_CACHE_SCHEMA!r}")
        if fingerprint_hash is not None and marker.get("fingerprint_hash") != fingerprint_hash:
            raise SystemExit("cache.verified fingerprint_hash disagrees with metadata.json")
        if marker.get("expected_fingerprint_match") is False:
            raise SystemExit("cache.verified reports expected_fingerprint_match=False")
        if "n_events" in marker and int(marker["n_events"]) != int(meta["n_events"]):
            raise SystemExit("cache.verified n_events disagrees with metadata.json")
    elif require_verified:
        raise SystemExit(
            f"no cache.verified in {cache_dir}; refuse to extract an unverified cache. "
            "Pass --allow-unverified for a local pre-publish cache.")

    shard_names = meta.get("shards")
    if not shard_names:
        raise SystemExit("metadata.json has no 'shards' list")
    n_events_meta = int(meta["n_events"])

    n_sc, n_fl, n_y = len(CORE_SPECIES), len(FLOW_SPECIES), len(Y_HIST_SPECIES)
    n_h, n_cent = len(HARMONICS), len(CENTRALITY_LABELS)
    n_et_m, n_et_f = len(GLOBAL_ET_MASKS), len(GLOBAL_ET_FIELDS)
    eta_mask = _central_eta_bin_mask(et_eta_window)

    # Output buffers (filled per event across shards, in metadata 'shards' order).
    out = {
        "event_id": np.zeros(n_events_meta, dtype=np.int64),
        "nsub_used": np.zeros(n_events_meta, dtype=np.int64),
        "zero_event": np.zeros(n_events_meta, dtype=bool),
        "cent_mult": np.zeros((n_events_meta, n_cent), dtype=np.float64),
        # yields / <pT>
        "scalar_sums": np.zeros((n_events_meta, n_sc, len(SCALAR_KEYS)), dtype=np.float64),
        "yh_N": np.zeros((n_events_meta, n_y, N_Y_HIST_BINS), dtype=np.float64),
        "yh_sum_pT": np.zeros((n_events_meta, n_y, N_Y_HIST_BINS), dtype=np.float64),
        "yh_sum_pT2": np.zeros((n_events_meta, n_y, N_Y_HIST_BINS), dtype=np.float64),
        "charged_N": np.zeros((n_events_meta, len(CHARGED_PTH_KEYS)), dtype=np.float64),
        "charged_sum_pT": np.zeros((n_events_meta, len(CHARGED_PTH_KEYS)), dtype=np.float64),
        "charged_sum_pT2": np.zeros((n_events_meta, len(CHARGED_PTH_KEYS)), dtype=np.float64),
        # flow self-cumulants (per FLOW_SPECIES): pooled + gapped numerator/denominator
        "flow_pooled_num": np.zeros((n_events_meta, n_fl, n_h), dtype=np.float64),
        "flow_pooled_den": np.zeros((n_events_meta, n_fl, n_h), dtype=np.float64),
        "flow_gap_num": np.zeros((n_events_meta, n_fl, n_h), dtype=np.float64),
        "flow_gap_den": np.zeros((n_events_meta, n_fl, n_h), dtype=np.float64),
        # differential identified-vs-charged-reference flow (per CORE_SPECIES)
        "flow_diff_num": np.zeros((n_events_meta, n_sc, n_h), dtype=np.float64),
        "flow_diff_den": np.zeros((n_events_meta, n_sc, n_h), dtype=np.float64),
        # global ET, midrapidity-integrated (oversample-summed)
        "et_central": np.zeros((n_events_meta, n_et_m, n_et_f), dtype=np.float64),
    }

    row = 0
    for shard_idx, base in enumerate(shard_names):
        shard_path, is_zst = _resolve_shard_path(cache_dir, base)
        z, tmp = _load_shard(shard_path, is_zst, zstd=zstd)
        try:
            if shard_idx == 0:
                _assert_schema(z, marker, fingerprint)
            _assert_shard_consistency(z, base)

            ev = z["event_ids"]
            offs = z["offsets"]
            ne = len(ev)

            # Preload arrays once per shard.
            cent = {lab: z[f"cent#{lab}"] for lab in CENTRALITY_LABELS}
            sc = {(si, fld): z[f"sc#{si}#{fld}"]
                  for si in range(n_sc) for fld in SCALAR_KEYS}
            fl = {(si, key): z[f"fl#{si}#{key}"]
                  for si in range(n_fl) for key in
                  (["m_full", "m_fwd", "m_bwd"]
                   + [f"q{n}_full" for n in HARMONICS]
                   + [f"q{n}_fwd" for n in HARMONICS]
                   + [f"q{n}_bwd" for n in HARMONICS])}
            yh = {(si, fld): z[f"yh#{si}#{fld}"]
                  for si in range(n_y) for fld in Y_HIST_FIELDS}     # each (ne, N_Y_HIST_BINS)
            pth = {(hk, fld): z[f"pth#{hk}#{fld}"]
                   for hk in CHARGED_PTH_KEYS for fld in PT_HIST_FIELDS}  # (ne, N_SCALAR_PT_BINS)
            get = {(m, fld): z[f"get#{m}#{fld}"]
                   for m in GLOBAL_ET_MASKS for fld in GLOBAL_ET_FIELDS}  # (total_sub, N_ETA_HIST_BINS)

            if row + ne > n_events_meta:
                raise SystemExit(
                    f"event-count overflow: shard {base} would exceed metadata n_events={n_events_meta}")

            for i in range(ne):
                r = row + i
                a, b = int(offs[i]), int(offs[i + 1])
                k = b - a                       # == nsubs[i]
                out["event_id"][r] = int(ev[i])
                out["nsub_used"][r] = k
                out["zero_event"][r] = (k == 0)

                # centrality (mean over oversamples; 0 for zero-events)
                if k > 0:
                    for ci, lab in enumerate(CENTRALITY_LABELS):
                        seg = cent[lab][a:b]
                        out["cent_mult"][r, ci] = float(np.nanmean(seg)) if seg.size else 0.0
                # else leave 0 -> ranks most peripheral

                # identified yields: full per-event y-hist (cache already summed over oversamples)
                for si in range(n_y):
                    out["yh_N"][r, si, :] = yh[(si, "N")][i]
                    out["yh_sum_pT"][r, si, :] = yh[(si, "sum_pT")][i]
                    out["yh_sum_pT2"][r, si, :] = yh[(si, "sum_pT2")][i]

                # charged yields/<pT>: pT-integrate the charged pT-hist (cache already os-summed)
                for wi, hk in enumerate(CHARGED_PTH_KEYS):
                    out["charged_N"][r, wi] = float(pth[(hk, "N")][i].sum())
                    out["charged_sum_pT"][r, wi] = float(pth[(hk, "sum_pT")][i].sum())
                    out["charged_sum_pT2"][r, wi] = float(pth[(hk, "sum_pT2")][i].sum())

                if k == 0:
                    continue   # all per-oversample contributions are zero for zero-events

                # scalar moments (CORE_SPECIES): oversample-sum (cross-check for yields/<pT>)
                for si in range(n_sc):
                    for fi, fld in enumerate(SCALAR_KEYS):
                        out["scalar_sums"][r, si, fi] = float(sc[(si, fld)][a:b].sum())

                # flow self-cumulants (FLOW_SPECIES): pooled + gapped num/den, oversample-summed
                for si in range(n_fl):
                    mfull = fl[(si, "m_full")][a:b].astype(np.float64)
                    mfwd = fl[(si, "m_fwd")][a:b].astype(np.float64)
                    mbwd = fl[(si, "m_bwd")][a:b].astype(np.float64)
                    for hi, n in enumerate(HARMONICS):
                        qfull = fl[(si, f"q{n}_full")][a:b]
                        qfwd = fl[(si, f"q{n}_fwd")][a:b]
                        qbwd = fl[(si, f"q{n}_bwd")][a:b]
                        # pooled: numer = |Q_n|^2 - M ; denom = M(M-1)
                        out["flow_pooled_num"][r, si, hi] = float(
                            ((qfull * np.conj(qfull)).real - mfull).sum())
                        out["flow_pooled_den"][r, si, hi] = float((mfull * (mfull - 1.0)).sum())
                        # gapped: numer = Re(Q_f Q_b*) ; denom = m_f m_b
                        out["flow_gap_num"][r, si, hi] = float((qfwd * np.conj(qbwd)).real.sum())
                        out["flow_gap_den"][r, si, hi] = float((mfwd * mbwd).sum())

                # differential identified-vs-charged-reference (disjoint hemispheres; no overlap sub.)
                cmfwd = fl[(0, "m_fwd")][a:b].astype(np.float64)
                cmbwd = fl[(0, "m_bwd")][a:b].astype(np.float64)
                cqfwd = {n: fl[(0, f"q{n}_fwd")][a:b] for n in HARMONICS}
                cqbwd = {n: fl[(0, f"q{n}_bwd")][a:b] for n in HARMONICS}
                for pj in range(n_sc):                 # identified POI; flow index = pj + 1
                    fsi = pj + 1
                    pmfwd = fl[(fsi, "m_fwd")][a:b].astype(np.float64)
                    pmbwd = fl[(fsi, "m_bwd")][a:b].astype(np.float64)
                    for hi, n in enumerate(HARMONICS):
                        pqfwd = fl[(fsi, f"q{n}_fwd")][a:b]
                        pqbwd = fl[(fsi, f"q{n}_bwd")][a:b]
                        num = (pqfwd * np.conj(cqbwd[n])).real + (pqbwd * np.conj(cqfwd[n])).real
                        den = pmfwd * cmbwd + pmbwd * cmfwd
                        out["flow_diff_num"][r, pj, hi] = float(num.sum())
                        out["flow_diff_den"][r, pj, hi] = float(den.sum())

                # global ET, midrapidity-integrated, oversample-summed
                for mi, m in enumerate(GLOBAL_ET_MASKS):
                    for fi, fld in enumerate(GLOBAL_ET_FIELDS):
                        seg = get[(m, fld)][a:b]               # (k, N_ETA_HIST_BINS)
                        out["et_central"][r, mi, fi] = float(seg[:, eta_mask].sum())

            row += ne
        finally:
            try:
                z.close()
            except Exception:
                pass
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    if row != n_events_meta:
        raise SystemExit(f"event-count reconciliation failed: read {row}, metadata says {n_events_meta}")

    # self-validation
    if not np.all(np.isfinite(out["cent_mult"])):
        raise SystemExit("non-finite cent_mult produced")
    for key in ("flow_pooled_num", "flow_pooled_den", "flow_gap_num", "flow_gap_den",
                "flow_diff_num", "flow_diff_den"):
        if not np.all(np.isfinite(out[key])):
            raise SystemExit(f"non-finite {key} produced")

    artifact_meta = _build_artifact_meta(cache_dir, meta, marker, et_eta_window, out)
    return out, artifact_meta


def _build_artifact_meta(cache_dir, cache_meta, marker, et_eta_window, out):
    nsub = out["nsub_used"]
    fp = cache_meta.get("fingerprint") or {}
    return {
        "system": (marker or {}).get("system") or os.path.basename(os.path.normpath(cache_dir)),
        "stage_a_schema_version": OBSERVABLE_SCHEMA,
        "cache_schema_version": EXPECTED_CACHE_SCHEMA,
        "cache_fingerprint_hash": (cache_meta.get("fingerprint_hash")
                                   or (_canonical_fingerprint_hash(cache_meta["fingerprint"])
                                       if isinstance(cache_meta.get("fingerprint"), dict) else None)),
        "cache_verified_present": marker is not None,
        "source_cache_dir": os.path.abspath(cache_dir),
        "n_events": int(out["event_id"].size),
        "n_zero_events": int(np.count_nonzero(out["zero_event"])),
        "n_dropped_bad_events_in_cache": int(cache_meta.get("n_dropped_bad_events", 0)),
        "nsub_min": int(nsub.min()) if nsub.size else 0,
        "nsub_median": float(np.median(nsub)) if nsub.size else 0.0,
        "nsub_max": int(nsub.max()) if nsub.size else 0,
        # axis label orders (downstream maps columns to species/harmonics by these)
        "species_scalar": CORE_SPECIES,
        "species_flow": FLOW_SPECIES,
        "species_yhist": Y_HIST_SPECIES,
        "species_diff": CORE_SPECIES,
        "charged_pth_windows": CHARGED_PTH_KEYS,
        "harmonics": HARMONICS,
        "centrality_estimators": CENTRALITY_LABELS,
        "global_et_masks": GLOBAL_ET_MASKS,
        "global_et_fields": GLOBAL_ET_FIELDS,
        # binning + acceptances (downstream window selection)
        "y_hist_inner_edges": Y_HIST_INNER_EDGES.tolist(),
        "eta_hist_inner_edges": ETA_HIST_INNER_EDGES.tolist(),
        "bin_convention": "bin = searchsorted(inner_edges, x, side='right'); bin 0 underflow, bin len overflow",
        # Authoritative cache settings, verbatim (not re-keyed here, to avoid guessing the
        # fingerprint's internal key names): the cache fingerprint dict itself.
        "cache_fingerprint": cache_meta.get("fingerprint"),
        # Values the v3 reconstruction code ASSUMES (e.g. to build window masks). These are
        # duplicated only for convenience and are explicitly labeled as assumptions; the
        # authoritative values live in cache_fingerprint above. The schema gate + structural
        # width checks guard against extracting a cache whose binning differs from v3.
        "reconstruction_assumes": {
            "flow_eta_gap": fp.get("subevent_eta_gap"),
            "flow_pt_window": [fp.get("flow_pt_min"), fp.get("flow_pt_max")],
            "charged_eta_acceptance": fp.get("charged_eta_max"),
            "identified_flow_y_acceptance": fp.get("identified_flow_y_max"),
        },
        "et_central_eta_window": et_eta_window,
        # conventions / policy
        "normalization_convention": "equal_hydro_event (reduce as Sum_i(num_i/nsub_i)/Sum_i(den_i/nsub_i))",
        "stored_values": "RAW oversample-sums; divide by nsub_used downstream for equal-event weighting",
        "zero_event_policy": "kept; cent_mult=0, all contributions 0, ranked most peripheral, counted in n_events",
        "charged_flow_estimator": "self two-subevent (gapped)",
        "identified_flow_estimator": "differential POI-vs-charged-reference (gapped); self-cumulant retained as diagnostic",
        "excluded_observables": EXCLUDED_OBSERVABLES,
        "systematic_limitations": [
            "Flow uses fixed opposite eta subevents, not every pair passing a pairwise separation cut; acceptance and gap cannot be changed after raw deletion.",
            "Centrality uses oversample-averaged midrapidity model multiplicity, not experimental forward-detector activity or per-oversample event selection.",
        ],
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "extractor": os.path.basename(__file__),
    }


def write_artifact(out, artifact_meta, out_prefix):
    os.makedirs(os.path.dirname(os.path.abspath(out_prefix)), exist_ok=True)
    npz_tmp = out_prefix + ".npz.tmp"
    np.savez(npz_tmp, **out)
    if not os.path.exists(npz_tmp) and os.path.exists(npz_tmp + ".npz"):
        npz_tmp = npz_tmp + ".npz"
    os.replace(npz_tmp, out_prefix + ".npz")
    meta_tmp = out_prefix + ".meta.json.tmp"
    with open(meta_tmp, "w") as f:
        json.dump(artifact_meta, f, indent=2, sort_keys=True)
    os.replace(meta_tmp, out_prefix + ".meta.json")


# ----------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="per-design observable sufficient-stat extractor (read-only).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--cache-dir", help="Full path to the cache dir (<...>/<design>/<system>).")
    g.add_argument("--cache-root", help="Root; combined with --design and --system.")
    ap.add_argument("--design", help="e.g. design_067 (with --cache-root).")
    ap.add_argument("--system", default=os.environ.get("HIC_CACHE_SYSTEM", "AuAu200"),
                    help="e.g. AuAu200 or OO5360 (with --cache-root).")
    ap.add_argument("--out-dir", required=True, help="Output directory for the artifact.")
    ap.add_argument("--out-prefix", default=None,
                    help="Override output basename (default <design>_<system>_stageA).")
    ap.add_argument("--et-eta-window", type=float, default=0.5,
                    help="Half-width for midrapidity global-ET integration (default 0.5).")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="Permit a cache without cache.verified (local pre-publish only).")
    ap.add_argument("--zstd", default="zstd", help="zstd binary (default 'zstd').")
    ap.add_argument("--expected-fingerprint", default=os.environ.get("HIC_EXPECTED_FINGERPRINT_V3"),
                    help="Selected scope; defaults to HIC_EXPECTED_FINGERPRINT_V3.")
    args = ap.parse_args()
    expected = load_expected_fingerprint(args.expected_fingerprint)

    if args.cache_dir:
        cache_dir = args.cache_dir
        design = os.path.basename(os.path.dirname(os.path.normpath(cache_dir)))
        system = os.path.basename(os.path.normpath(cache_dir))
    else:
        if not args.design:
            ap.error("--cache-root requires --design")
        cache_dir = os.path.join(args.cache_root, args.design, args.system)
        design, system = args.design, args.system

    prefix = args.out_prefix or f"{design}_{system}_stageA"
    out_prefix = os.path.join(args.out_dir, prefix)

    t0 = time.time()
    out, meta = extract_design(cache_dir, et_eta_window=args.et_eta_window,
                               require_verified=not args.allow_unverified, zstd=args.zstd,
                               expected_fingerprint=expected)
    write_artifact(out, meta, out_prefix)
    print(f"[analysis] {design}/{system}: {meta['n_events']} events "
          f"({meta['n_zero_events']} zero), nsub[min/med/max]="
          f"{meta['nsub_min']}/{meta['nsub_median']}/{meta['nsub_max']} "
          f"-> {out_prefix}.npz ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
