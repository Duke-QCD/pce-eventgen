#!/usr/bin/env python3
"""
cache_one_design.py

Build a validated, durably-copyable event-level cache for one (design, system)
directory of SMASH particle-list outputs, so that raw particle_lists.oscar* files
can later be safely deleted without losing dissertation-relevant observables.

The parse primitives (eta/y, particle membership, the signed-quantity conventions,
the eta-gap subevent split, the flow Q-vector sufficient statistics) were validated
in an earlier statistics pilot. The cache is tiered: low-dimensional
per-(event, oversample) quantities are retained, while selected linear histograms are
hydro-event aggregated to keep storage scalable.

The script does NOT delete raw particle lists. Deletion is a separate, dry-run-default
reaper gated on a durable, independently re-verified cache.verified marker on CFS.
This script only:
    parse (streaming, per shard) -> validate against raw -> atomic local write

CRITICAL correctness notes (confirmed against the statistics pilot, the PDG file, and
the ALICE dET/deta definition PRC 94 034903):
  * Signed baryon number: baryon_dict stores baryon-number MAGNITUDE (same
    for +pdg and -pdg). The experiment-style ET correction needs SIGNED B (+1 baryon,
    -1 antibaryon, 0 meson). We derive B = sign(pdg)*|baryon|, positive pdg == baryon.
    Reading |B| directly would give antibaryons the wrong-sign mass correction -- a bug
    identical in cache and in a naive raw reference, so it would survive careless
    validation. We compute signed B the same correct way in cache AND reference.
  * sinTheta = pT/|p|, computed only where |p|>0 (guard).
  * m_PDG is the PDG pole mass from the mass table, NOT sqrt(E^2-p^2) and NOT the OSCAR
    line mass, so broad/off-shell resonances use their species pole mass.
  * Global dET/deta is cached under TWO masks forming a clean partition of the visible
    final state:
        global_ET_total_visible  = hadrons + {pi0, eta, omega, gamma, e+-}
                                   (ALICE-style total-visible candidate)
        global_ET_hadronic       = hadronic-component mask (excludes pi0/eta/omega/gamma/e+-)
    EM-like component = total_visible - hadronic. The masks are EXPLICIT positive PDG
    predicates, asserted disjoint, so the subtraction is exact. Neutral kaons enter the
    global ET masks at FULL weight (the K0S 0.5 weight is a yield-observable convention
    only and must NOT reduce the energy they carry).
"""

import argparse
import hashlib
import datetime
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np


# ========================================================================================
# Schema version. Bump on ANY change to cached field names/semantics/binning.
# ========================================================================================
CACHE_SCHEMA_VERSION = "cache_one_design/v3"


# ========================================================================================
# Environment-driven cuts/conventions. Defaults match the frozen AuAu200 fingerprint.
# ========================================================================================
def env_float(name, default):
    return float(os.environ.get(name, default))


def env_path(name, default):
    value = os.environ.get(name, default)
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


CHARGED_ETA_MAX = env_float("HIC_ANALYSIS_CHARGED_ETA_MAX", 1.0)
IDENTIFIED_Y_MAX = env_float("HIC_ANALYSIS_IDENTIFIED_Y_MAX", 0.5)
IDENTIFIED_FLOW_Y_MAX = env_float("HIC_ANALYSIS_IDENTIFIED_FLOW_Y_MAX", 1.0)
FLOW_PT_MIN = env_float("HIC_ANALYSIS_FLOW_PT_MIN", 0.2)
FLOW_PT_MAX = env_float("HIC_ANALYSIS_FLOW_PT_MAX", 5.0)
SUBEVENT_ETA_GAP = env_float("HIC_ANALYSIS_SUBEVENT_ETA_GAP", 0.15)
SUBEVENT_ETA_GAP_HALF = 0.5 * SUBEVENT_ETA_GAP

# Per-PDG inventory acceptance (eta). Wide and FIXED: after raw deletion the inventory
# exists only at this acceptance, so it is chosen wider than any analysis cut.
INVENTORY_ETA_MAX = env_float("HIC_ANALYSIS_INVENTORY_ETA_MAX", 2.0)

# NOTE: centrality acceptance is NOT an env var here; the centrality windows are fixed
# (CENTRALITY_WINDOWS) and reconstructable from the charged-eta histogram. (Removing the
# old CENTRALITY_ETA_MAX env/fingerprint avoids false cache-incompatibility.)

# Particle tables: campaign.py exports the INI's [pipeline] pdg_mass/baryon_dict;
# standalone use falls back to this checkout's iS3D tables.
PDG_DIR = env_path("HIC_PDG_DIR", Path(__file__).resolve().parents[1] / "iS3D/PDG")
BARYON_DATA = env_path("HIC_BARYON_DICT", PDG_DIR / "baryon_dict.dat")
PDG_MASS_DATA = env_path("HIC_PDG_MASS", PDG_DIR / "pdg_smash_pce.dat")

NUCLEON_MASS = 0.938  # GeV, for the m_N experiment-ET variant (PHENIX literal wording)

# Authoritative known-species set (all PDGs in the loaded PDG mass table). Populated by
# main() and by the parallel pool initializer. Used by the global-ET hadronic mask so that
# an unknown visible PDG is classified 'unknown' (hard error), not silently hadronic.
KNOWN_PDGS = frozenset()


# ========================================================================================
# Species definitions.
# ========================================================================================
CORE_SPECIES = [
    "pi+", "pi-",
    "K+", "K-",
    "p", "pbar",
    "K0S",
    "Lambda", "antiLambda",
    "Xi-", "antiXi+",
    "Omega-", "antiOmega+",
    "phi",
]
# NOTE: 'charged' is intentionally NOT in CORE_SPECIES scalars. The charged integrated
# scalar (sc#charged) was dropped as redundant; charged_scalar_eta05 and _eta10 are
# derived downstream from the charged pT histograms (which carry all moments). 'charged'
# still appears for FLOW (it is the reference) -- see FLOW_SPECIES below.

# Flow species: charged is the reference, plus identified POIs. Integrated flow for all;
# pT-binned flow only for the cheap/high-stat subset.
FLOW_SPECIES = ["charged"] + [s for s in CORE_SPECIES]

# Scalar pT histograms. Strange species ARE included (pT-windowed strange yields are core).
# 'charged' is histogrammed in two eta windows (see CHARGED_PT_HIST_ETA_WINDOWS) and is the
# ONLY source of the charged integrated scalar (eta05/eta10) downstream.
SCALAR_PT_HIST_IDENTIFIED = [
    "pi+", "pi-",
    "K+", "K-",
    "p", "pbar",
    "K0S",
    "Lambda", "antiLambda",
    "Xi-", "antiXi+",
    "Omega-", "antiOmega+",
    "phi",
]

PT_BINNED_FLOW_SPECIES = [
    "charged",
    "pi+", "pi-",
    "K+", "K-",
    "p", "pbar",
]

IDENTIFIED_Y_HIST_SPECIES = [
    "pi+", "pi-",
    "K+", "K-",
    "p", "pbar",
    "K0S",
    "Lambda", "antiLambda",
    "Xi-", "antiXi+",
    "Omega-", "antiOmega+",
    "phi",
]

CHARGED_PT_HIST_ETA_WINDOWS = (0.5, 1.0)
REFERENCE_SPECIES = "charged"

# PDGs treated as the electromagnetic (non-hadronic) category for the global-ET partition.
# Matches ALICE category B: pi0, eta, gamma, e+-. (pi0/eta are mesons by quark content but
# are placed in the EM category because experimentally their energy is the EM decay energy;
# this is a deliberate membership choice, recorded in metadata.)
# PDGs assigned to the electromagnetic ET-bookkeeping category (ALICE category B): gamma,
# e+-, pi0, eta, omega. This is an experimental ET-COMPONENT assignment, NOT a claim that
# these are non-hadrons by quark content (pi0/eta/omega are mesons). They are placed here
# because their experimentally observed transverse energy is dominated by EM decay response.
# eta'(331) and other short-lived neutral resonances are deliberately NOT included by
# default; the first-run audit reports their counts so a later mask version can add them if
# a target convention requires it.
EM_CATEGORY_PDGS = frozenset({22, 11, -11, 111, 221, 223})
# Species explicitly audited (reported in the first-run PDG audit) because their ET-component
# treatment can be convention-dependent.
EM_AUDIT_CANDIDATE_PDGS = frozenset({331, 113, 213, -213, 223, 333})
# Particles excluded from the *visible* total entirely (should not appear in SMASH output;
# guarded by the first-run audit which flags any PDG in neither mask).
INVISIBLE_PDGS = frozenset({12, -12, 14, -14, 16, -16})  # neutrinos

# Explicit hadronic-visible exceptions that can appear in OSCAR output but are intentionally
# absent from the PDG pole-mass table used for ordinary species bookkeeping. 9000221 is the
# old sigma / f0(500)-like scalar-isoscalar resonance. It remains excluded from core
# species/yields and from the pole-mass table, but if SMASH emits it as a final particle its
# energy should count as hadronic-visible global ET, not EM-like and not unknown. Since it is
# a meson with B=0, no baryon ET mass-correction moment is affected by the missing pole mass.
EXPLICIT_HADRONIC_COMPONENT_PDGS = frozenset({9000221})


def is_charged_species(particle):
    return particle == "charged"


def particle_passes(pdg, charge, particle):
    if particle == "charged":
        return charge != 0
    if particle == "pi+":
        return pdg == 211
    if particle == "pi-":
        return pdg == -211
    if particle == "K+":
        return pdg == 321
    if particle == "K-":
        return pdg == -321
    if particle == "p":
        return pdg == 2212
    if particle == "pbar":
        return pdg == -2212
    if particle == "K0S":
        # SMASH propagates flavor eigenstates K0/K0bar (|pdg|==311); K0S is the statistical
        # 0.5*(K0+K0bar) proxy. Yield/ET get the 0.5 weight; flow uses the unweighted pool.
        return abs(pdg) == 311
    if particle == "Lambda":
        return pdg == 3122
    if particle == "antiLambda":
        return pdg == -3122
    if particle == "Xi-":
        return pdg == 3312
    if particle == "antiXi+":
        return pdg == -3312
    if particle == "Omega-":
        return pdg == 3334
    if particle == "antiOmega+":
        return pdg == -3334
    if particle == "phi":
        return pdg == 333
    raise ValueError(f"Unknown particle type: {particle}")


def species_yield_weight(particle):
    """Per-matched-particle weight for SPECIES yield/ET sums. K0S=0.5; else 1.0. Flow
    Q-vectors and the GLOBAL ET masks are NEVER weighted by this (see global ET code)."""
    return 0.5 if particle == "K0S" else 1.0


# ========================================================================================
# PDG tables: signed baryon number and pole masses.
# ========================================================================================
def load_baryon_magnitude_dict(path):
    """Baryon-number MAGNITUDE keyed by signed pdg (same value for +/-pdg), matching the
    pilot's baryon_dict.dat (`pdg_id baryon` per line). Sign is applied separately."""
    mag = {}
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pdg_id, baryon = int(parts[0]), int(parts[1])
            mag[pdg_id] = baryon
            mag[-pdg_id] = baryon
    return mag


def signed_baryon_number(pdg, baryon_mag_dict):
    """Signed B: +|B| baryons (positive pdg), -|B| antibaryons (negative pdg), 0 mesons.
    Convention confirmed: positive pdg == baryon."""
    mag = baryon_mag_dict.get(pdg, 0)
    if mag == 0:
        return 0
    return mag if pdg > 0 else -mag


def load_pole_mass_dict(path):
    """Parse the PCE particle list (pdg_smash_pce.dat). Non-comment lines:
        NAME  MASS[GeV]  WIDTH  PARITY  PDG [PDG ...]   # optional inline comment
    Name is a single whitespace token; >=1 pdg ids follow parity; all share the pole mass.
    Maps both +pdg and -pdg. Returns (masses, known_pdgs) where known_pdgs is the set of
    all pdg ids appearing in the table (the authoritative known-species universe)."""
    masses = {}
    known = set()
    with open(path, "r") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            try:
                mass = float(parts[1])
            except ValueError:
                continue
            for tok in parts[4:]:
                try:
                    pdg = int(tok)
                except ValueError:
                    continue
                masses[pdg] = mass
                masses[-pdg] = mass
                known.add(pdg)
                known.add(-pdg)
    return masses, known


# ---------------------------------------------------------------------------------------
# Global-ET mask membership as INDEPENDENT POSITIVE SETS (not complement-derived), so the
# partition audit is meaningful: a visible PDG that is in NEITHER set is an unknown species
# (hard error by default), and "in both" is impossible-by-disjointness and asserted.
#
# em_component: an experimental-ET-bookkeeping category (ALICE category B), NOT a claim that
#   these are "non-hadrons" in a quark-content sense. pi0/eta/omega are mesons; they are
#   placed here because their experimentally observed ET is dominated by EM decay response.
def em_category(pdg):
    return pdg in EM_CATEGORY_PDGS


def is_hadronic_component(pdg, baryon_mag_dict, known_pdgs):
    """Hadronic-component membership: a KNOWN species (in the PDG table) that carries
    baryon number, OR a known meson (|pdg|>=100, in the table) -- excluding the EM set and
    excluding invisibles. Leptons/photons (|pdg|<100) are not hadronic. Crucially this is a
    POSITIVE test against the known-species table, so an unknown PDG returns False here AND
    False for em_category(), landing it in 'neither' -> flagged by the audit."""
    if pdg in EM_CATEGORY_PDGS or pdg in INVISIBLE_PDGS:
        return False
    if pdg in EXPLICIT_HADRONIC_COMPONENT_PDGS:
        return True
    if pdg not in known_pdgs:
        return False  # unknown species: not classified as hadronic (audit will catch it)
    if baryon_mag_dict.get(pdg, 0) != 0:
        return True   # baryon/antibaryon
    return abs(pdg) >= 100  # known meson


def classify_visible(pdg, baryon_mag_dict, known_pdgs):
    """Return one of: 'invisible', 'em', 'hadronic', 'unknown'.
    'unknown' = a visible PDG that is neither EM nor a known hadronic species; this is the
    hard-error condition for production caching (it would be silently dropped from total ET)."""
    if pdg in INVISIBLE_PDGS:
        return "invisible"
    if pdg in EM_CATEGORY_PDGS:
        return "em"
    if is_hadronic_component(pdg, baryon_mag_dict, known_pdgs):
        return "hadronic"
    return "unknown"


def visible_mask(pdg):
    """A particle is 'visible' (enters global_ET_total_visible) unless it is a neutrino.
    NOTE: an unknown visible PDG still enters total_visible (its energy is real); it is the
    audit, not this mask, that flags it -- so total ET is never silently undercounted."""
    return pdg not in INVISIBLE_PDGS


# ========================================================================================
# Binning (frozen with the AuAu200 fingerprint).
# ========================================================================================
def _build_scalar_pt_upper_edges():
    """Frozen scalar pT grid as UPPER edges (no leading 0.0; first bin is [0, 0.05)):
        0.00-1.00 : 0.05 -> uppers 0.05..1.00
        1.00-3.00 : 0.10
        3.00-5.00 : 0.25
        5.00-10.0 : 0.50
        10.0-20.0 : 1.00
    The implicit overflow bin is [20.0, inf). Bin index = searchsorted(uppers, pt, 'right'),
    giving 0 for [0,0.05) and len(uppers) for pT>=20."""
    uppers = []
    def span(lo, hi, step):
        n = int(round((hi - lo) / step))
        for i in range(1, n + 1):
            uppers.append(round(lo + i * step, 10))
    span(0.0, 1.0, 0.05)
    span(1.0, 3.0, 0.10)
    span(3.0, 5.0, 0.25)
    span(5.0, 10.0, 0.50)
    span(10.0, 20.0, 1.00)
    return np.asarray(uppers, dtype=np.float64)


SCALAR_PT_UPPER_EDGES = _build_scalar_pt_upper_edges()
N_SCALAR_PT_BINS = len(SCALAR_PT_UPPER_EDGES) + 1  # + overflow

# Flow pT INNER edges (interior boundaries). <0.10 underflow (index 0), >=5.0 overflow
# (last index). Bin index = searchsorted(FLOW_PT_INNER_EDGES, pt, 'right').
FLOW_PT_INNER_EDGES = np.asarray(
    [0.10, 0.15, 0.20, 0.30, 0.50, 0.80, 1.00, 1.50, 2.00, 2.50, 3.00, 4.00, 5.00],
    dtype=np.float64,
)
N_FLOW_PT_BINS = len(FLOW_PT_INNER_EDGES) + 1

# Signed charged-eta histogram (centrality + scalar eta-window flexibility) and global-ET
# eta histograms: inner edges -1.2 .. 1.2 step 0.05. Underflow (<-1.2) index 0, overflow
# (>=1.2) last index.
ETA_HIST_INNER_EDGES = np.round(np.arange(-1.2, 1.2 + 1e-9, 0.05), 10)
N_ETA_HIST_BINS = len(ETA_HIST_INNER_EDGES) + 1

# Signed identified-rapidity histogram: inner edges -1.0 .. 1.0 step 0.1.
Y_HIST_INNER_EDGES = np.round(np.arange(-1.0, 1.0 + 1e-9, 0.1), 10)
N_Y_HIST_BINS = len(Y_HIST_INNER_EDGES) + 1


def _assert_flow_edges_subset_of_scalar():
    scalar = set(np.round(SCALAR_PT_UPPER_EDGES, 6).tolist())
    for e in np.round(FLOW_PT_INNER_EDGES, 6).tolist():
        if e not in scalar:
            raise AssertionError(
                f"flow pT edge {e} not in scalar pT edge set; schema invariant violated")


_assert_flow_edges_subset_of_scalar()


def _edge_contains(edges, value, atol=1e-9):
    """True if value is exactly one of the histogram/cut edges, to tolerance."""
    return any(abs(float(e) - float(value)) <= atol for e in edges)


def _assert_cut_edges_aligned():
    """Schema-level invariant: env-overridable cuts must stay aligned to cached bins.
    Otherwise closure checks become approximate and future reconstruction from histograms
    can silently become convention-dependent."""
    for v, name in [(IDENTIFIED_Y_MAX, "IDENTIFIED_Y_MAX"),
                    (IDENTIFIED_FLOW_Y_MAX, "IDENTIFIED_FLOW_Y_MAX")]:
        if not _edge_contains(Y_HIST_INNER_EDGES, v) or not _edge_contains(Y_HIST_INNER_EDGES, -v):
            raise AssertionError(f"{name}={v} is not aligned to identified-y histogram edges")
    for v, name in [(CHARGED_ETA_MAX, "CHARGED_ETA_MAX")]:
        if not _edge_contains(ETA_HIST_INNER_EDGES, v) or not _edge_contains(ETA_HIST_INNER_EDGES, -v):
            raise AssertionError(f"{name}={v} is not aligned to charged/global eta histogram edges")
    for v, name in [(FLOW_PT_MIN, "FLOW_PT_MIN"), (FLOW_PT_MAX, "FLOW_PT_MAX")]:
        if not _edge_contains(FLOW_PT_INNER_EDGES, v):
            raise AssertionError(f"{name}={v} is not aligned to flow pT edges")
    for w in CHARGED_PT_HIST_ETA_WINDOWS:
        if not _edge_contains(ETA_HIST_INNER_EDGES, w) or not _edge_contains(ETA_HIST_INNER_EDGES, -w):
            raise AssertionError(f"charged pT histogram eta window {w} is not aligned to eta edges")


_assert_cut_edges_aligned()


CENTRALITY_WINDOWS = (
    ("cent_ch_eta05", 0.0, 0.5),
    ("cent_ch_eta10", 0.0, 1.0),
    ("cent_ch_sideband_eta05_10", 0.5, 1.0),
)
DEFAULT_CENTRALITY_FIELD = "cent_ch_eta10"

GLOBAL_ET_MASKS = ("total_visible", "hadronic")


# ========================================================================================
# Kinematics.
# ========================================================================================
def pseudorapidity_eta(px, py, pz):
    pabs = math.sqrt(px * px + py * py + pz * pz)
    denom = pabs - pz
    numer = pabs + pz
    if denom <= 0.0 or numer <= 0.0:
        return np.nan
    return 0.5 * math.log(numer / denom)


def rapidity_y(E, pz):
    denom = E - pz
    numer = E + pz
    if denom <= 0.0 or numer <= 0.0:
        return np.nan
    return 0.5 * math.log(numer / denom)


def _scalar_pt_bin(pt):
    if not math.isfinite(pt) or pt < 0.0:
        return -1
    return int(np.searchsorted(SCALAR_PT_UPPER_EDGES, pt, side="right"))


def _flow_pt_bin(pt):
    if not math.isfinite(pt):
        return -1
    return int(np.searchsorted(FLOW_PT_INNER_EDGES, pt, side="right"))


def _hist_bin(x, inner_edges):
    if not math.isfinite(x):
        return -1
    return int(np.searchsorted(inner_edges, x, side="right"))


def _eta_tag(w):
    return f"{int(round(w * 10)):02d}"


# ========================================================================================
# Per-(event, subevent) accumulators.
# ========================================================================================
# Per-species INTEGRATED scalar: trimmed to N, sum_pT, sum_pT2, sum_ET_total (NO correction
# moments -- species-level experiment-ET is diagnostic; the real dET/deta observable is the
# global masks, which DO carry the correction moments).
SCALAR_KEYS = ("N", "sum_pT", "sum_pT2", "sum_ET_total")

# Integrated flow per species (Q-vectors unweighted; full pool + hemispheres).
FLOW_KEYS = ("m_full", "q2_full", "q3_full", "q4_full",
             "m_fwd", "m_bwd",
             "q2_fwd", "q3_fwd", "q4_fwd",
             "q2_bwd", "q3_bwd", "q4_bwd")

# Scalar pT-hist fields. In v3 these histograms are hydro-event aggregated and trimmed
# to the quantities needed for pT-windowed yields/mean-pT/spectra. pT-windowed ET is
# intentionally not preserved here; global dET/deta is handled by the global ET eta masks.
PT_HIST_FIELDS = ("N", "sum_pT", "sum_pT2")

# pT-binned flow fields (fwd/bwd only; n=2,3,4).
PT_FLOW_FIELDS = ("m_fwd", "m_bwd",
                  "q2_fwd", "q3_fwd", "q4_fwd",
                  "q2_bwd", "q3_bwd", "q4_bwd")

# Identified y-hist fields (sum_ET_total only; experiment-ET vs y is diagnostic).
Y_HIST_FIELDS = ("N", "sum_pT", "sum_pT2", "sum_ET_total")

# Global-ET per-eta-bin fields (the minimal ET correction-moment set per mask).
GLOBAL_ET_FIELDS = ("sum_ET_total", "sum_B_sintheta", "sum_BmPDG_sintheta")


def _new_subevent_acc():
    acc = {
        "scalar": {p: {k: 0.0 for k in SCALAR_KEYS} for p in CORE_SPECIES},
        "flow": {p: {k: (0.0 if k.startswith("m") else 0j) for k in FLOW_KEYS}
                 for p in FLOW_SPECIES},
        "pt_hist": {},
        "pt_flow": {p: {k: np.zeros(N_FLOW_PT_BINS,
                                    dtype=(np.float64 if k.startswith("m") else np.complex128))
                        for k in PT_FLOW_FIELDS}
                    for p in PT_BINNED_FLOW_SPECIES},
        "charged_eta_hist": np.zeros(N_ETA_HIST_BINS, dtype=np.float64),
        "y_hist": {p: {k: np.zeros(N_Y_HIST_BINS, dtype=np.float64) for k in Y_HIST_FIELDS}
                   for p in IDENTIFIED_Y_HIST_SPECIES},
        "global_et": {m: {k: np.zeros(N_ETA_HIST_BINS, dtype=np.float64)
                          for k in GLOBAL_ET_FIELDS}
                      for m in GLOBAL_ET_MASKS},
        "inventory": {},  # pdg -> [N, sum_pT, sum_pT2, sum_ET_total, sum_B_sin, sum_BmPDG_sin]
        "cent": {label: 0.0 for (label, _, _) in CENTRALITY_WINDOWS},
        "audit_pdg": {},  # pdg -> count, over the visible set, for the first-run audit
    }
    # scalar pT-hist arrays
    for w in CHARGED_PT_HIST_ETA_WINDOWS:
        acc["pt_hist"][f"charged_eta{_eta_tag(w)}"] = {
            k: np.zeros(N_SCALAR_PT_BINS, dtype=np.float64) for k in PT_HIST_FIELDS}
    for p in SCALAR_PT_HIST_IDENTIFIED:
        acc["pt_hist"][p] = {k: np.zeros(N_SCALAR_PT_BINS, dtype=np.float64)
                             for k in PT_HIST_FIELDS}
    return acc


# ========================================================================================
# Event status (stricter than the pilot: requires afterburner.done before parsing).
# ========================================================================================
def _cache_particle_file_state(event_dir):
    """(usable, zero_or_invalid) particle variants for an event dir.

    Mirrors repair_stale_events.particle_file_state so the cache classifies
    particle output exactly as the repair/reaper scripts do: a usable output is a
    nonempty regular file; zero-byte or nonregular files are truncated/ambiguous.
    Deliberately duplicated (these are separate NERSC scripts) rather than shared.
    """
    usable, zero_or_invalid = [], []
    for name in ("particle_lists.oscar", "particle_lists.oscar.unfinished"):
        p = Path(event_dir) / name
        if not p.exists():
            continue
        if p.is_file() and p.stat().st_size > 0:
            usable.append((name, p))
        else:
            zero_or_invalid.append(name)
    return usable, zero_or_invalid


def event_status(base_dir, ev_name):
    event_dir = Path(base_dir) / ev_name
    done = (event_dir / "afterburner.done").is_file()
    hydro_done = (event_dir / "hydro.done").is_file()
    no_surface = (event_dir / "hydro.no_surface").is_file()
    surface = event_dir / "surface.dat"
    nonempty_surface = surface.is_file() and surface.stat().st_size > 0

    usable, zero_or_invalid = _cache_particle_file_state(event_dir)

    # A terminal no-surface event must carry no particle artifact (usable OR
    # zero-byte/nonregular), no afterburner.done, no hydro.done, and no nonempty
    # surface; any of those co-present is a marker contradiction.  Checked BEFORE
    # the generic zero-byte branch so a no-surface contradiction is reported as
    # such (and trips the publish hard-fail) rather than as a plain zero-byte case.
    if no_surface and (done or hydro_done or usable or zero_or_invalid or nonempty_surface):
        return "ambiguous_no_surface_conflict", None

    # Truncated/ambiguous particle output is never cached as a clean event.
    if zero_or_invalid:
        return "ambiguous_zero_byte_particle", None
    if len(usable) > 1:
        return "ambiguous_particle_variants", None

    if usable:
        particle_path = usable[0][1]
        if done:
            return "particle_file", particle_path
        return "incomplete_no_done", None
    if done:
        return "completed_zero", None
    if no_surface:
        return "no_surface", None
    if (event_dir / "afterburner.failed").is_file():
        return "failed", None
    if (event_dir / "afterburner.lock").exists() or (event_dir / "smash.lock").is_file():
        return "running", None
    return "missing_or_not_started", None



# ========================================================================================
# OSCAR row structural validation.
# ========================================================================================
MALFORMED_OSCAR_KEYS = ("wrong_field_count", "bad_pdg_token", "bad_numeric_field")


def _empty_malformed_counts():
    return {k: 0 for k in MALFORMED_OSCAR_KEYS}


def _merge_malformed_counts(dst, src):
    if not src:
        return
    for k in MALFORMED_OSCAR_KEYS:
        dst[k] = dst.get(k, 0) + int(src.get(k, 0))


def _total_malformed_counts(counts):
    return sum(int(counts.get(k, 0)) for k in MALFORMED_OSCAR_KEYS)


# Fixed thesis-pipeline policy for structurally truncated OSCAR files:
# keep complete event blocks, drop one incomplete trailing block, and reject files whose
# beginning is malformed or that contain zero complete blocks.  This is intentionally a
# parser invariant, not a user-facing flag.
OSCAR_HEADER_LINE0 = "#!OSCAR2013 particle_lists t x y z mass p0 px py pz pdg ID charge"
OSCAR_HEADER_LINE1 = "# Units: fm fm fm fm GeV GeV GeV GeV GeV none none e"
OSCAR_SMASH_RE = re.compile(r"^# SMASH-\S+$")
OSCAR_EVENT0_OUT_RE = re.compile(r"^# event 0 out \d+$")

OSCAR_STRUCTURE_KEYS = (
    "files_with_dropped_final_block",
    "dropped_final_subevents",
    "rejected_malformed_beginning",
    "rejected_zero_complete_blocks",
    # Prefix-salvage: a mid-file *grammar* break (out-while-open, end-without-open, end-id
    # mismatch, row-outside-block) is treated as effective EOF -- complete blocks before it are
    # kept, everything from the break onward is discarded as untrustworthy.  This generalizes the
    # trailing-truncation drop to mid-file corruption.
    "files_with_discarded_corrupt_tail",
    # Non-fatal diagnostic only: blocks whose raw non-comment row count != the declared "out M".
    # This is NOT a structural gate (M==raw-rows is an unverified SMASH invariant; gating on it
    # could silently discard good data, which is unrecoverable after reaping).  Recorded so a
    # real mismatch is visible without ever truncating on it.
    "rowcount_mismatch_blocks",
)

# When the full parse rejects events (malformed beginning / zero complete blocks / format
# confusion -- e.g. a mid-file gap that the cheap head/tail repair check cannot see), the abort
# writes the rejected event ids here, in the design (system) dir, so the expensive full read is
# not wasted: repair_stale_events.py reads this, blocks ready_to_cache, and cleans exactly those
# events for rerun.  Per-event size+mtime let repair detect a rerun and avoid clobbering it.
CACHE_BAD_EVENTS_FILE = "cache_bad_events.json"


def _empty_oscar_structure_counts():
    return {k: 0 for k in OSCAR_STRUCTURE_KEYS}


def _merge_oscar_structure_counts(dst, src):
    if not src:
        return
    for k in OSCAR_STRUCTURE_KEYS:
        dst[k] = dst.get(k, 0) + int(src.get(k, 0))


def _zero_oscar_structure():
    return _empty_oscar_structure_counts()


def _rejecting_oscar_structure(reason):
    out = _zero_oscar_structure()
    if reason == "malformed_beginning":
        out["rejected_malformed_beginning"] = 1
    else:
        out["rejected_zero_complete_blocks"] = 1
    return out


def _accepted_oscar_structure(dropped_final_subevent, discarded_corrupt_tail=False,
                              rowcount_mismatch_blocks=0):
    out = _zero_oscar_structure()
    if dropped_final_subevent:
        out["files_with_dropped_final_block"] = 1
        out["dropped_final_subevents"] = 1
    if discarded_corrupt_tail:
        out["files_with_discarded_corrupt_tail"] = 1
    if rowcount_mismatch_blocks:
        out["rowcount_mismatch_blocks"] = int(rowcount_mismatch_blocks)
    return out


def _event_marker_kind(line):
    """Return 'out', 'end', or None for an OSCAR event marker line."""
    if not line.startswith("# event "):
        return None
    parts = line.split()
    if len(parts) >= 4 and parts[3] == "out":
        return "out"
    if len(parts) >= 4 and parts[3] == "end":
        return "end"
    return None


def _event_marker_id_and_count(line):
    """For a '# event N out M' or '# event N end ...' line, return (N, M_or_None).
    N is the event id; M is the declared particle count for an 'out' marker (None for 'end' or
    if unparseable).  Used by the prefix-salvage parser for the end-id grammar check and the
    non-fatal declared-vs-raw row-count diagnostic."""
    parts = line.split()
    # parts: ['#', 'event', N, ('out'|'end'), M_or_status, ...]
    if len(parts) < 4:
        return None, None
    try:
        ev_id = int(parts[2])
    except (ValueError, IndexError):
        return None, None
    declared = None
    if parts[3] == "out" and len(parts) >= 5:
        try:
            declared = int(parts[4])
        except ValueError:
            declared = None
    return ev_id, declared


def _parse_integer_pdg_token(tok):
    """Return integer nonzero PDG if tok is integer-valued; otherwise None.

    This rejects malformed/concatenated OSCAR rows where a coordinate or momentum
    fragment lands in the PDG column (e.g. '-1991.14', '45.758', or '0').
    Integer-valued float spellings such as '211.0' are accepted for tolerance,
    but genuine unknown integer PDGs still pass through to the existing PDG audit.
    """
    try:
        val = float(tok)
    except ValueError:
        return None
    if not math.isfinite(val) or not val.is_integer():
        return None
    pdg = int(val)
    if pdg == 0:
        return None
    return pdg


def _parse_oscar_particle_row(line, malformed_counts):
    """Parse one non-comment OSCAR particle row.

    Returns (E, px, py, pz, pdg, charge) or None if the row is structurally
    malformed. The key distinction is that malformed rows are skipped/counted,
    while well-formed rows with unknown integer PDGs are still allowed to reach
    the normal unknown-PDG hard error.
    """
    parts = line.split()
    if len(parts) != 12:
        malformed_counts["wrong_field_count"] += 1
        return None

    pdg = _parse_integer_pdg_token(parts[9])
    if pdg is None:
        malformed_counts["bad_pdg_token"] += 1
        return None

    try:
        E = float(parts[5]); px = float(parts[6]); py = float(parts[7])
        pz = float(parts[8]); charge = int(float(parts[11]))
    except ValueError:
        malformed_counts["bad_numeric_field"] += 1
        return None

    return E, px, py, pz, pdg, charge

# ========================================================================================
# Single-pass OSCAR parse for one event.
# ========================================================================================
def summarize_oscar_file(file_path, event_id, baryon_mag, pole_mass):
    subevents = []
    in_event = False
    acc = None
    malformed_rows = _empty_malformed_counts()
    header_lines = []
    header_complete = False
    dropped_final_subevent = False
    # Prefix-salvage bookkeeping: track the current open block's id and declared count so we can
    # enforce grammar (end-id must match open-id) and record (non-fatally) declared-vs-raw counts.
    cur_event_id = None
    cur_declared_M = None
    cur_raw_rows = 0
    discarded_corrupt_tail = False
    rowcount_mismatch_blocks = 0

    def reject(reason):
        return {
            "event_id": int(event_id),
            "nsub": 0,
            "subevents": [],
            "malformed_rows": malformed_rows,
            "oscar_structure": _rejecting_oscar_structure(reason),
            "parse_issue": reason,
        }

    with open(file_path, "r") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue

            # Strict beginning policy for this campaign.  The first four nonblank lines must be
            # the OSCAR header, Units line, SMASH version, and '# event 0 out N'.  The SMASH
            # version and N are the only allowed variable fields.  This rejects the observed
            # no-header files that begin with raw particle rows.
            if not header_complete:
                header_lines.append(line)
                n = len(header_lines)
                if n == 1:
                    if line != OSCAR_HEADER_LINE0:
                        return reject("malformed_beginning")
                    continue
                if n == 2:
                    if line != OSCAR_HEADER_LINE1:
                        return reject("malformed_beginning")
                    continue
                if n == 3:
                    if not OSCAR_SMASH_RE.match(line):
                        return reject("malformed_beginning")
                    continue
                if n == 4:
                    if not OSCAR_EVENT0_OUT_RE.match(line):
                        return reject("malformed_beginning")
                    header_complete = True
                    in_event = True
                    acc = _new_subevent_acc()
                    cur_event_id, cur_declared_M = _event_marker_id_and_count(line)
                    cur_raw_rows = 0
                    continue
                return reject("malformed_beginning")

            if line.startswith("#"):
                marker = _event_marker_kind(line)
                if marker == "out":
                    # A new 'out' while a block is still open is a mid-file grammar break.  Under
                    # prefix-salvage we treat the first such break as effective EOF: keep the
                    # complete blocks already collected, discard this open block and everything
                    # after.  We do NOT reject the whole file (that would throw away valid prefix
                    # data) and we do NOT resync forward (fragile).  Only a file with zero complete
                    # blocks before the break is rejected (handled after the loop).
                    if in_event:
                        discarded_corrupt_tail = True
                        break
                    in_event = True
                    acc = _new_subevent_acc()
                    cur_event_id, cur_declared_M = _event_marker_id_and_count(line)
                    cur_raw_rows = 0
                elif marker == "end":
                    if not in_event or acc is None:
                        # 'end' with no open block: grammar break -> effective EOF.
                        discarded_corrupt_tail = True
                        break
                    end_id, _ = _event_marker_id_and_count(line)
                    if cur_event_id is not None and end_id is not None and end_id != cur_event_id:
                        # 'end N' closing a different event than the open 'out': grammar break.
                        discarded_corrupt_tail = True
                        break
                    # Non-fatal diagnostic: declared 'out M' vs raw non-comment rows seen.  Never
                    # a structural gate -- just counted so a real mismatch is visible.
                    if cur_declared_M is not None and cur_raw_rows != cur_declared_M:
                        rowcount_mismatch_blocks += 1
                    subevents.append(acc)
                    in_event = False
                    acc = None
                    cur_event_id = None
                    cur_declared_M = None
                    cur_raw_rows = 0
                continue
            if not in_event:
                # Particle row outside any open block: grammar break -> effective EOF, keep prefix.
                discarded_corrupt_tail = True
                break

            cur_raw_rows += 1
            parsed = _parse_oscar_particle_row(line, malformed_rows)
            if parsed is None:
                continue
            E, px, py, pz, pdg, charge = parsed

            eta = pseudorapidity_eta(px, py, pz)
            y = rapidity_y(E, pz)
            pt = math.sqrt(px * px + py * py)
            pabs = math.sqrt(px * px + py * py + pz * pz)
            if pabs > 0.0:
                sin_theta = pt / pabs
                et_total = E * sin_theta
            else:
                sin_theta = 0.0
                et_total = 0.0
            phi = math.atan2(py, px)

            B = signed_baryon_number(pdg, baryon_mag)
            m_pole = pole_mass.get(pdg, 0.0)
            b_sin = B * sin_theta
            bm_sin = B * m_pole * sin_theta

            # ---- first-run audit: count every visible pdg ----
            if visible_mask(pdg):
                acc["audit_pdg"][pdg] = acc["audit_pdg"].get(pdg, 0) + 1

            # ---- per-PDG inventory (wide eta, every particle incl. neutrals) ----
            if math.isfinite(eta) and abs(eta) < INVENTORY_ETA_MAX:
                inv = acc["inventory"].get(pdg)
                if inv is None:
                    inv = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                    acc["inventory"][pdg] = inv
                inv[0] += 1.0; inv[1] += pt; inv[2] += pt * pt
                inv[3] += et_total; inv[4] += b_sin; inv[5] += bm_sin

            # ---- global ET masks (FULL weight for every particle, incl. K0/K0bar) ----
            eta_bin = _hist_bin(eta, ETA_HIST_INNER_EDGES)
            if eta_bin >= 0:
                if visible_mask(pdg):
                    gt = acc["global_et"]["total_visible"]
                    gt["sum_ET_total"][eta_bin] += et_total
                    gt["sum_B_sintheta"][eta_bin] += b_sin
                    gt["sum_BmPDG_sintheta"][eta_bin] += bm_sin
                    if is_hadronic_component(pdg, baryon_mag, KNOWN_PDGS):
                        gh = acc["global_et"]["hadronic"]
                        gh["sum_ET_total"][eta_bin] += et_total
                        gh["sum_B_sintheta"][eta_bin] += b_sin
                        gh["sum_BmPDG_sintheta"][eta_bin] += bm_sin

            # ---- centrality windows ----
            if charge != 0 and math.isfinite(eta):
                ae = abs(eta)
                for (label, lo, hi) in CENTRALITY_WINDOWS:
                    if lo <= ae < hi:
                        acc["cent"][label] += 1.0

            # ---- signed charged-eta histogram ----
            if charge != 0 and eta_bin >= 0:
                acc["charged_eta_hist"][eta_bin] += 1.0

            # ---- per-species accumulation ----
            charged_eta_ok = math.isfinite(eta) and abs(eta) < CHARGED_ETA_MAX
            ident_y_ok = math.isfinite(y) and abs(y) < IDENTIFIED_Y_MAX
            ident_flow_y_ok = math.isfinite(y) and abs(y) < IDENTIFIED_FLOW_Y_MAX
            flow_pt_ok = (FLOW_PT_MIN <= pt < FLOW_PT_MAX)
            sb = _scalar_pt_bin(pt)

            c2 = c3 = c4 = None  # lazily built when first needed

            for p in FLOW_SPECIES:
                if not particle_passes(pdg, charge, p):
                    continue
                w = species_yield_weight(p)

                if is_charged_species(p):
                    yield_ok = charged_eta_ok
                    flow_acc_ok = charged_eta_ok and flow_pt_ok
                else:
                    yield_ok = ident_y_ok
                    flow_acc_ok = ident_flow_y_ok and flow_pt_ok

                # integrated scalar moments (charged scalar is NOT stored; charged is flow-only)
                if (p in CORE_SPECIES) and yield_ok:
                    sc = acc["scalar"][p]
                    sc["N"] += w
                    sc["sum_pT"] += w * pt
                    sc["sum_pT2"] += w * pt * pt
                    sc["sum_ET_total"] += w * et_total

                # flow
                if flow_acc_ok:
                    if c2 is None:
                        c2 = complex(math.cos(2 * phi), math.sin(2 * phi))
                        c3 = complex(math.cos(3 * phi), math.sin(3 * phi))
                        c4 = complex(math.cos(4 * phi), math.sin(4 * phi))
                    fl = acc["flow"][p]
                    fl["m_full"] += 1.0
                    fl["q2_full"] += c2; fl["q3_full"] += c3; fl["q4_full"] += c4
                    fwd = eta > SUBEVENT_ETA_GAP_HALF
                    bwd = eta < -SUBEVENT_ETA_GAP_HALF
                    if fwd:
                        fl["m_fwd"] += 1.0
                        fl["q2_fwd"] += c2; fl["q3_fwd"] += c3; fl["q4_fwd"] += c4
                    elif bwd:
                        fl["m_bwd"] += 1.0
                        fl["q2_bwd"] += c2; fl["q3_bwd"] += c3; fl["q4_bwd"] += c4
                    if p in PT_BINNED_FLOW_SPECIES:
                        fb = _flow_pt_bin(pt)
                        if fb >= 0:
                            pf = acc["pt_flow"][p]
                            if fwd:
                                pf["m_fwd"][fb] += 1.0
                                pf["q2_fwd"][fb] += c2; pf["q3_fwd"][fb] += c3; pf["q4_fwd"][fb] += c4
                            elif bwd:
                                pf["m_bwd"][fb] += 1.0
                                pf["q2_bwd"][fb] += c2; pf["q3_bwd"][fb] += c3; pf["q4_bwd"][fb] += c4

                # scalar pT histograms (identified species, |y| acceptance)
                if (p in SCALAR_PT_HIST_IDENTIFIED) and ident_y_ok and sb >= 0:
                    h = acc["pt_hist"][p]
                    h["N"][sb] += w
                    h["sum_pT"][sb] += w * pt
                    h["sum_pT2"][sb] += w * pt * pt

                # signed identified-y histogram
                if (p in IDENTIFIED_Y_HIST_SPECIES):
                    yb = _hist_bin(y, Y_HIST_INNER_EDGES)
                    if yb >= 0:
                        hy = acc["y_hist"][p]
                        hy["N"][yb] += w
                        hy["sum_pT"][yb] += w * pt
                        hy["sum_pT2"][yb] += w * pt * pt
                        hy["sum_ET_total"][yb] += w * et_total

            # charged scalar pT histograms in the two eta windows (separate from flow loop,
            # since 'charged' is not an identified species and uses eta windows)
            if sb >= 0 and charge != 0:
                if c2 is None and flow_pt_ok:
                    pass  # not needed for scalar hist
                for ww in CHARGED_PT_HIST_ETA_WINDOWS:
                    if math.isfinite(eta) and abs(eta) < ww:
                        h = acc["pt_hist"][f"charged_eta{_eta_tag(ww)}"]
                        h["N"][sb] += 1.0
                        h["sum_pT"][sb] += pt
                        h["sum_pT2"][sb] += pt * pt

    if in_event and acc is not None and not discarded_corrupt_tail:
        # Expected walltime/interruption case: all previous complete blocks are usable,
        # but the final open block is incomplete and is deliberately dropped.  (If we broke out
        # on a grammar break, the open block is part of the discarded corrupt tail, not a clean
        # trailing truncation -- so this only applies when we reached real EOF mid-block.)
        dropped_final_subevent = True

    if not subevents:
        # Zero complete blocks salvaged: nothing usable.  This is the only path that flags the
        # event for rerun (cache_bad_events) -- either a malformed beginning (already returned
        # above) or a corruption before the first complete block.  Either way rerun is the
        # remedy, reported as zero_complete_blocks.
        return {
            "event_id": int(event_id),
            "nsub": 0,
            "subevents": [],
            "malformed_rows": malformed_rows,
            "oscar_structure": _rejecting_oscar_structure("zero_complete_blocks"),
            "parse_issue": "zero_complete_blocks",
        }

    return {
        "event_id": int(event_id),
        "nsub": len(subevents),
        "subevents": subevents,
        "malformed_rows": malformed_rows,
        "oscar_structure": _accepted_oscar_structure(
            dropped_final_subevent,
            discarded_corrupt_tail=discarded_corrupt_tail,
            rowcount_mismatch_blocks=rowcount_mismatch_blocks,
        ),
        "parse_issue": None,
    }

def zero_event_record(event_id):
    """A completed event with no particle list -> nsub=0 record, so event-counting and
    centrality denominators stay correct when --allow-completed-zero is set."""
    return {"event_id": int(event_id), "nsub": 0, "subevents": []}


# ========================================================================================
# Shard packing. One .npz per chunk of events. Dense per-(event,subevent) arrays + offsets;
# inventory as a sorted COO triple. float64 moments, complex128 Q-vectors.
# ========================================================================================
def _pack_records_to_npz(records, shard_path):
    if not records:
        return 0
    nsubs = np.asarray([r["nsub"] for r in records], dtype=np.int64)
    offsets = np.concatenate([[0], np.cumsum(nsubs)]).astype(np.int64)
    total_sub = int(offsets[-1])
    arrays = {
        "event_ids": np.asarray([r["event_id"] for r in records], dtype=np.int64),
        "nsubs": nsubs,
        "offsets": offsets,
    }

    def iter_sub():
        for r in records:
            for se in r["subevents"]:
                yield se

    # centrality
    for (label, _, _) in CENTRALITY_WINDOWS:
        col = np.fromiter((se["cent"][label] for se in iter_sub()),
                          dtype=np.float64, count=total_sub) if total_sub else np.zeros(0)
        arrays[f"cent#{label}"] = col

    # scalar moments
    for si, p in enumerate(CORE_SPECIES):
        for k in SCALAR_KEYS:
            col = np.fromiter((se["scalar"][p][k] for se in iter_sub()),
                              dtype=np.float64, count=total_sub) if total_sub else np.zeros(0)
            arrays[f"sc#{si}#{k}"] = col

    # integrated flow
    for si, p in enumerate(FLOW_SPECIES):
        for k in FLOW_KEYS:
            dt = np.complex128 if k.startswith("q") else np.float64
            col = np.array([se["flow"][p][k] for se in iter_sub()], dtype=dt) \
                if total_sub else np.zeros(0, dtype=dt)
            arrays[f"fl#{si}#{k}"] = col

    # scalar pT histograms (v3): hydro-event aggregated over oversamples. These are
    # linear sums, so the hydro-event central values for pT-windowed yields/mean-pT are
    # unchanged, while oversample-level re-cut/resampling flexibility is intentionally not
    # preserved for arbitrary pT windows. Shape: (n_events_in_shard, n_pt_bins).
    sample = None
    for r in records:
        if r["subevents"]:
            sample = r["subevents"][0]["pt_hist"]
            break
    sample = sample or {}
    for hk in sorted(sample.keys()):
        for fk in PT_HIST_FIELDS:
            mat = np.zeros((len(records), N_SCALAR_PT_BINS), dtype=np.float64)
            for ri, r in enumerate(records):
                for se in r["subevents"]:
                    mat[ri, :] += se["pt_hist"][hk][fk]
            arrays[f"pth#{hk}#{fk}"] = mat

    # pT-binned flow
    for si, p in enumerate(PT_BINNED_FLOW_SPECIES):
        for fk in PT_FLOW_FIELDS:
            dt = np.complex128 if fk.startswith("q") else np.float64
            mat = np.zeros((total_sub, N_FLOW_PT_BINS), dtype=dt)
            for i, se in enumerate(iter_sub()):
                mat[i, :] = se["pt_flow"][p][fk]
            arrays[f"ptf#{si}#{fk}"] = mat

    # charged-eta histogram
    mat = np.zeros((total_sub, N_ETA_HIST_BINS), dtype=np.float64)
    for i, se in enumerate(iter_sub()):
        mat[i, :] = se["charged_eta_hist"]
    arrays["chetahist"] = mat

    # identified-y histograms (v3): hydro-event aggregated over oversamples. Shape:
    # (n_events_in_shard, n_y_bins).
    for si, p in enumerate(IDENTIFIED_Y_HIST_SPECIES):
        for fk in Y_HIST_FIELDS:
            mat = np.zeros((len(records), N_Y_HIST_BINS), dtype=np.float64)
            for ri, r in enumerate(records):
                for se in r["subevents"]:
                    mat[ri, :] += se["y_hist"][p][fk]
            arrays[f"yh#{si}#{fk}"] = mat

    # global ET masks
    for m in GLOBAL_ET_MASKS:
        for fk in GLOBAL_ET_FIELDS:
            mat = np.zeros((total_sub, N_ETA_HIST_BINS), dtype=np.float64)
            for i, se in enumerate(iter_sub()):
                mat[i, :] = se["global_et"][m][fk]
            arrays[f"get#{m}#{fk}"] = mat

    # per-PDG inventory (sorted COO)
    inv_sub, inv_pdg, inv_vals = [], [], []
    for i, se in enumerate(iter_sub()):
        for pdg, vals in se["inventory"].items():
            inv_sub.append(i); inv_pdg.append(int(pdg)); inv_vals.append(vals)
    inv_sub = np.asarray(inv_sub, dtype=np.int64)
    inv_pdg = np.asarray(inv_pdg, dtype=np.int64)
    inv_vals = np.asarray(inv_vals, dtype=np.float64) if len(inv_vals) \
        else np.zeros((0, 6), dtype=np.float64)
    if inv_sub.size:
        order = np.lexsort((inv_pdg, inv_sub))
        inv_sub, inv_pdg, inv_vals = inv_sub[order], inv_pdg[order], inv_vals[order]
    arrays["inv_sub"] = inv_sub
    arrays["inv_pdg"] = inv_pdg
    arrays["inv_vals"] = inv_vals

    # v3 write-time aggregation spot check. The in-memory closures validate each subevent;
    # this check validates the new hydro-event aggregation path used when packing pT and y
    # histograms into the .npz shard. It guards against future regressions where the
    # event-row += per-subevent sums could drift from the parsed records.
    check_indices = []
    for ri, r in enumerate(records):
        if r["subevents"]:
            check_indices.append(ri)
            break
    for ri in range(len(records) - 1, -1, -1):
        if records[ri]["subevents"] and ri not in check_indices:
            check_indices.append(ri)
            break
    if check_indices:
        # Charged eta10 pT histogram: primary charged scalar source in v3.
        hk = "charged_eta10"
        for fk in PT_HIST_FIELDS:
            arr_key = f"pth#{hk}#{fk}"
            if arr_key in arrays:
                for ri in check_indices:
                    expected = np.zeros(N_SCALAR_PT_BINS, dtype=np.float64)
                    for se in records[ri]["subevents"]:
                        expected += se["pt_hist"][hk][fk]
                    if not np.allclose(arrays[arr_key][ri, :], expected, rtol=1e-12, atol=1e-12):
                        raise RuntimeError(
                            f"v3 aggregation check failed for {arr_key}, event row {ri}")

        # One identified pT histogram and one y histogram exercise the species-indexed
        # aggregation path. pi+ is high-statistics and should exist in every normal run.
        if "pi+" in SCALAR_PT_HIST_IDENTIFIED:
            hk = "pi+"
            for fk in PT_HIST_FIELDS:
                arr_key = f"pth#{hk}#{fk}"
                if arr_key in arrays:
                    for ri in check_indices:
                        expected = np.zeros(N_SCALAR_PT_BINS, dtype=np.float64)
                        for se in records[ri]["subevents"]:
                            expected += se["pt_hist"][hk][fk]
                        if not np.allclose(arrays[arr_key][ri, :], expected, rtol=1e-12, atol=1e-12):
                            raise RuntimeError(
                                f"v3 aggregation check failed for {arr_key}, event row {ri}")
        if "pi+" in IDENTIFIED_Y_HIST_SPECIES:
            si = IDENTIFIED_Y_HIST_SPECIES.index("pi+")
            for fk in Y_HIST_FIELDS:
                arr_key = f"yh#{si}#{fk}"
                if arr_key in arrays:
                    for ri in check_indices:
                        expected = np.zeros(N_Y_HIST_BINS, dtype=np.float64)
                        for se in records[ri]["subevents"]:
                            expected += se["y_hist"]["pi+"][fk]
                        if not np.allclose(arrays[arr_key][ri, :], expected, rtol=1e-12, atol=1e-12):
                            raise RuntimeError(
                                f"v3 aggregation check failed for {arr_key}, event row {ri}")

    tmp = shard_path + ".tmp"
    np.savez(tmp, **arrays)
    if not os.path.exists(tmp) and os.path.exists(tmp + ".npz"):
        tmp = tmp + ".npz"
    os.replace(tmp, shard_path)
    return len(records)


# ========================================================================================
# Independent validation reference (one event), recomputed by a separate code path.
# ========================================================================================
def _reference_event(file_path, baryon_mag, pole_mass):
    """Independent physics reference for validation, using the same block-boundary
    policy as summarize_oscar_file().

    The reference path intentionally remains much simpler in what it accumulates, but it
    must agree with the production parser on which OSCAR subevents are trusted.  In
    particular, prefix-salvage treats the first mid-file grammar break as effective EOF:
    keep complete blocks before it, discard the open/broken block and everything after it,
    and never try to resynchronize later in the file.
    """
    out = []
    in_event = False
    cur = None
    cur_event_id = None
    malformed_rows = _empty_malformed_counts()

    def fresh():
        return {
            "n_charged_eta10": 0,      # fixed scalar histogram acceptance |eta|<1
            "n_piplus_y05": 0,         # pi+, |y|<IDENTIFIED_Y_MAX (integer)
            "p_N_y05": 0.0, "p_sum_pT": 0.0, "p_sum_pT2": 0.0,
            "p_et_total": 0.0, "p_b_sin": 0.0, "p_bm_sin": 0.0,
            "n_k0s_yield": 0.0,        # K0S yield (0.5-weighted, |y|<IDENTIFIED_Y_MAX)
            "m_k0s_flow": 0,           # K0S flow multiplicity (unweighted, flow cuts)
            "ch_et_total": 0.0, "ch_bm_sin": 0.0,
            "q2re": 0.0, "q2im": 0.0, "m_ch_flow": 0,           # charged ungapped
            "q2re_f": 0.0, "q2im_f": 0.0, "m_ch_fwd": 0,        # charged fwd
            "q2re_b": 0.0, "q2im_b": 0.0, "m_ch_bwd": 0,        # charged bwd
            "get_tv_total": 0.0, "get_had_total": 0.0,          # global ET totals (all eta bins)
        }

    with open(file_path, "r") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                marker = _event_marker_kind(line)
                if marker == "out":
                    if in_event:
                        # Mid-file grammar break: do not keep the open block and do not
                        # resynchronize to later markers.  This mirrors prefix-salvage.
                        break
                    ev_id, _ = _event_marker_id_and_count(line)
                    in_event = True
                    cur_event_id = ev_id
                    cur = fresh()
                elif marker == "end":
                    if not in_event or cur is None:
                        break
                    end_id, _ = _event_marker_id_and_count(line)
                    if cur_event_id is not None and end_id is not None and end_id != cur_event_id:
                        break
                    out.append(cur)
                    in_event = False
                    cur = None
                    cur_event_id = None
                # Non-event comments/header lines are ignored by the reference path.
                continue
            if not in_event or cur is None:
                # Particle row outside a block is a grammar break under prefix-salvage.
                break
            parsed = _parse_oscar_particle_row(line, malformed_rows)
            if parsed is None:
                continue
            E, px, py, pz, pdg, charge = parsed
            eta = pseudorapidity_eta(px, py, pz)
            y = rapidity_y(E, pz)
            pt = math.sqrt(px * px + py * py)
            pabs = math.sqrt(px * px + py * py + pz * pz)
            sin_t = pt / pabs if pabs > 0 else 0.0
            et = E * sin_t
            B = signed_baryon_number(pdg, baryon_mag)
            mp = pole_mass.get(pdg, 0.0)
            phi = math.atan2(py, px)
            flow_pt = (FLOW_PT_MIN <= pt < FLOW_PT_MAX)

            # The charged scalar histogram is fixed at |eta|<1 independently of
            # the environment-selected charged FLOW acceptance.
            if charge != 0 and math.isfinite(eta) and abs(eta) < 1.0:
                cur["n_charged_eta10"] += 1
            if charge != 0 and math.isfinite(eta) and abs(eta) < CHARGED_ETA_MAX:
                cur["ch_et_total"] += et
                cur["ch_bm_sin"] += B * mp * sin_t
                if flow_pt:
                    cur["q2re"] += math.cos(2 * phi); cur["q2im"] += math.sin(2 * phi)
                    cur["m_ch_flow"] += 1
                    if eta > SUBEVENT_ETA_GAP_HALF:
                        cur["q2re_f"] += math.cos(2 * phi); cur["q2im_f"] += math.sin(2 * phi)
                        cur["m_ch_fwd"] += 1
                    elif eta < -SUBEVENT_ETA_GAP_HALF:
                        cur["q2re_b"] += math.cos(2 * phi); cur["q2im_b"] += math.sin(2 * phi)
                        cur["m_ch_bwd"] += 1
            if pdg == 211 and math.isfinite(y) and abs(y) < IDENTIFIED_Y_MAX:
                cur["n_piplus_y05"] += 1
            if pdg == 2212 and math.isfinite(y) and abs(y) < IDENTIFIED_Y_MAX:
                cur["p_N_y05"] += 1.0
                cur["p_sum_pT"] += pt
                cur["p_sum_pT2"] += pt * pt
                cur["p_et_total"] += et
                cur["p_b_sin"] += B * sin_t
                cur["p_bm_sin"] += B * mp * sin_t
            if abs(pdg) == 311:
                if math.isfinite(y) and abs(y) < IDENTIFIED_Y_MAX:
                    cur["n_k0s_yield"] += 0.5
                if flow_pt and math.isfinite(y) and abs(y) < IDENTIFIED_FLOW_Y_MAX:
                    cur["m_k0s_flow"] += 1
            # global ET totals, summed over the histogram eta range only (to match cache)
            if math.isfinite(eta) and (ETA_HIST_INNER_EDGES[0] <= eta < ETA_HIST_INNER_EDGES[-1]):
                if visible_mask(pdg):
                    cur["get_tv_total"] += et
                    if is_hadronic_component(pdg, baryon_mag, KNOWN_PDGS):
                        cur["get_had_total"] += et
    return out

def _close(a, b, rtol=1e-9, atol=1e-9):
    return abs(a - b) <= atol + rtol * max(1.0, abs(b))


def validate_event_against_cache(parsed, file_path, baryon_mag, pole_mass):
    ref = _reference_event(file_path, baryon_mag, pole_mass)
    msgs = []
    ok = True
    if len(ref) != parsed["nsub"]:
        return False, [f"subevent count: ref={len(ref)} cache={parsed['nsub']}"]
    for j, (se, rf) in enumerate(zip(parsed["subevents"], ref)):
        # charged integer count via charged_eta10 pT-hist sum (charged has no sc# scalar)
        n_ch = float(np.sum(se["pt_hist"]["charged_eta10"]["N"]))
        if abs(n_ch - rf["n_charged_eta10"]) > 1e-9:
            ok = False; msgs.append(f"sub {j}: charged N(eta10) cache={n_ch} ref={rf['n_charged_eta10']}")
        # pi+ integer count
        n_pi = se["scalar"]["pi+"]["N"]
        if abs(n_pi - rf["n_piplus_y05"]) > 1e-9:
            ok = False; msgs.append(f"sub {j}: pi+ N cache={n_pi} ref={rf['n_piplus_y05']}")
        # proton scalar and pT-hist correction-moment check (signed-B baryon ET reconstruction)
        for fk, ref_key in (("N", "p_N_y05"), ("sum_pT", "p_sum_pT"),
                            ("sum_pT2", "p_sum_pT2"), ("sum_ET_total", "p_et_total")):
            val = se["scalar"]["p"][fk]
            if not _close(val, rf[ref_key]):
                ok = False; msgs.append(f"sub {j}: p {fk} cache={val} ref={rf[ref_key]}")
        # K0S yield (0.5-weighted) and flow multiplicity (unweighted) -- the factor-of-2 check
        n_k0s = se["scalar"]["K0S"]["N"]
        if abs(n_k0s - rf["n_k0s_yield"]) > 1e-9:
            ok = False; msgs.append(f"sub {j}: K0S yield cache={n_k0s} ref={rf['n_k0s_yield']}")
        m_k0s = se["flow"]["K0S"]["m_full"]
        if abs(m_k0s - rf["m_k0s_flow"]) > 1e-9:
            ok = False; msgs.append(f"sub {j}: K0S flow M cache={m_k0s} ref={rf['m_k0s_flow']}")
        # charged ungapped c2 (the -M subtraction)
        m = se["flow"]["charged"]["m_full"]; q2 = se["flow"]["charged"]["q2_full"]
        if m > 1 and rf["m_ch_flow"] > 1:
            c2c = ((q2 * q2.conjugate()).real - m) / (m * (m - 1.0))
            mr = rf["m_ch_flow"]
            c2r = ((rf["q2re"]**2 + rf["q2im"]**2) - mr) / (mr * (mr - 1.0))
            if abs(c2c - c2r) > 1e-9 + 1e-9 * max(1.0, abs(c2r)):
                ok = False; msgs.append(f"sub {j}: charged c2_full cache={c2c} ref={c2r}")
        # charged gapped c2 (NO -M subtraction)
        mf = se["flow"]["charged"]["m_fwd"]; mb = se["flow"]["charged"]["m_bwd"]
        qf = se["flow"]["charged"]["q2_fwd"]; qb = se["flow"]["charged"]["q2_bwd"]
        if mf > 0 and mb > 0 and rf["m_ch_fwd"] > 0 and rf["m_ch_bwd"] > 0:
            cg_c = (qf * qb.conjugate()).real / (mf * mb)
            qfr = complex(rf["q2re_f"], rf["q2im_f"]); qbr = complex(rf["q2re_b"], rf["q2im_b"])
            cg_r = (qfr * qbr.conjugate()).real / (rf["m_ch_fwd"] * rf["m_ch_bwd"])
            if abs(cg_c - cg_r) > 1e-9 + 1e-9 * max(1.0, abs(cg_r)):
                ok = False; msgs.append(f"sub {j}: charged c2_gap cache={cg_c} ref={cg_r}")
        # global ET totals (summed over INTERIOR eta bins == reference's [-1.2,1.2) range)
        tv = float(np.sum(se["global_et"]["total_visible"]["sum_ET_total"][1:-1]))
        hd = float(np.sum(se["global_et"]["hadronic"]["sum_ET_total"][1:-1]))
        if not _close(tv, rf["get_tv_total"]):
            ok = False; msgs.append(f"sub {j}: global ET total_visible cache={tv} ref={rf['get_tv_total']}")
        if not _close(hd, rf["get_had_total"]):
            ok = False; msgs.append(f"sub {j}: global ET hadronic cache={hd} ref={rf['get_had_total']}")
    return ok, msgs


def _bin_window_mask(inner_edges, n_bins, lo_cut, hi_cut):
    """Return mask for bins fully contained in [lo_cut, hi_cut). Assumes cut edges align."""
    mask = np.zeros(n_bins, dtype=bool)
    for bi in range(1, n_bins - 1):
        lo = inner_edges[bi - 1]
        hi = inner_edges[bi]
        if lo >= lo_cut - 1e-12 and hi <= hi_cut + 1e-12:
            mask[bi] = True
    return mask


def validate_closures(records):
    """Cache-internal closure checks (per subevent):
      * identified pT-hist bins sum to integrated scalar moments for fields with integrated
        scalar counterparts;
      * identified y-hist bins inside |y|<IDENTIFIED_Y_MAX sum to integrated scalar moments;
      * charged-eta-hist bins reconstruct the named centrality windows;
      * global hadronic ET is a subset of total_visible ET per eta bin.
    pT-hist ET/correction moments are intentionally absent in v3; global ET masks carry
    the ET correction moments for dET/deta.
    """
    msgs = []
    ok = True
    y_mask = _bin_window_mask(Y_HIST_INNER_EDGES, N_Y_HIST_BINS,
                              -IDENTIFIED_Y_MAX, IDENTIFIED_Y_MAX)
    eta_masks = {}
    for (label, lo, hi) in CENTRALITY_WINDOWS:
        pos = _bin_window_mask(ETA_HIST_INNER_EDGES, N_ETA_HIST_BINS, lo, hi)
        neg = _bin_window_mask(ETA_HIST_INNER_EDGES, N_ETA_HIST_BINS, -hi, -lo)
        eta_masks[label] = pos | neg
    for r in records:
        for se in r["subevents"]:
            # pT-hist closure for identified species: v3 pT hists only carry N/sum_pT/sum_pT2.
            for p in SCALAR_PT_HIST_IDENTIFIED:
                hist = se["pt_hist"][p]
                for fk in PT_HIST_FIELDS:
                    hsum = float(np.sum(hist[fk]))
                    sval = se["scalar"][p][fk]
                    if not _close(hsum, sval):
                        ok = False
                        msgs.append(f"ev {r['event_id']}: pT-hist closure {p}.{fk} hist={hsum} sc={sval}")
                        break
            # y-hist closure: sum over |y|<IDENTIFIED_Y_MAX bins == integrated scalar fields.
            for p in IDENTIFIED_Y_HIST_SPECIES:
                if p not in CORE_SPECIES:
                    continue
                yh = se["y_hist"][p]
                for fk in Y_HIST_FIELDS:
                    ysum = float(np.sum(yh[fk][y_mask]))
                    sval = se["scalar"][p][fk]
                    if not _close(ysum, sval):
                        ok = False
                        msgs.append(f"ev {r['event_id']}: y-hist closure {p}.{fk} yhist={ysum} sc={sval}")
                        break
            # centrality closure: charged-eta-hist named windows.
            chh = se["charged_eta_hist"]
            for (label, _, _) in CENTRALITY_WINDOWS:
                tot = float(np.sum(chh[eta_masks[label]]))
                if not _close(tot, se["cent"][label]):
                    ok = False
                    msgs.append(f"ev {r['event_id']}: centrality closure {label} hist={tot} cent={se['cent'][label]}")
            # partition: hadronic ET <= total_visible ET per eta bin for every ET moment.
            for fk in GLOBAL_ET_FIELDS:
                tv = se["global_et"]["total_visible"][fk]
                hd = se["global_et"]["hadronic"][fk]
                # For signed correction moments, hadronic can exceed total in individual bins
                # by sign cancellation, so only enforce subset inequality for positive ET_total.
                if fk == "sum_ET_total" and np.any(hd > tv + 1e-6):
                    ok = False
                    msgs.append(f"ev {r['event_id']}: hadronic ET exceeds total_visible in some eta bin")
    return ok, msgs

def audit_partition(records, baryon_mag, pole_mass):
    """First-run audit. Returns (counts_by_pdg, unknown_pdgs, missing_mass_baryons).
      * unknown_pdgs: visible PDGs that are neither EM nor a known hadronic species. These
        would be silently dropped from the hadronic mask (and only counted in total_visible),
        so they are a HARD ERROR for production caching.
      * missing_mass_baryons: PDGs with baryon number != 0 but no pole mass in the table,
        which would silently lose their experiment-ET correction. Also a hard error."""
    counts = {}
    for r in records:
        for se in r["subevents"]:
            for pdg, c in se["audit_pdg"].items():
                counts[pdg] = counts.get(pdg, 0.0) + c
    unknown, missing_mass = [], []
    for pdg in counts:
        kind = classify_visible(pdg, baryon_mag, KNOWN_PDGS)
        if kind == "unknown":
            unknown.append(pdg)
        if baryon_mag.get(pdg, 0) != 0 and pdg not in pole_mass:
            missing_mass.append(pdg)
    return counts, unknown, missing_mass


# ========================================================================================
# Fingerprint, checksum helpers, local metadata.
# ========================================================================================
def build_fingerprint():
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "charged_eta_max": round(CHARGED_ETA_MAX, 6),
        "identified_y_max": round(IDENTIFIED_Y_MAX, 6),
        "identified_flow_y_max": round(IDENTIFIED_FLOW_Y_MAX, 6),
        "flow_pt_min": round(FLOW_PT_MIN, 6),
        "flow_pt_max": round(FLOW_PT_MAX, 6),
        "subevent_eta_gap": round(SUBEVENT_ETA_GAP, 6),
        "inventory_eta_max": round(INVENTORY_ETA_MAX, 6),
        "nucleon_mass": round(NUCLEON_MASS, 6),
        "core_species": list(CORE_SPECIES),
        "flow_species": list(FLOW_SPECIES),
        "scalar_pt_hist_identified": list(SCALAR_PT_HIST_IDENTIFIED),
        "pt_binned_flow_species": list(PT_BINNED_FLOW_SPECIES),
        "identified_y_hist_species": list(IDENTIFIED_Y_HIST_SPECIES),
        "charged_pt_hist_eta_windows": list(CHARGED_PT_HIST_ETA_WINDOWS),
        "scalar_pt_upper_edges": [float(x) for x in SCALAR_PT_UPPER_EDGES],
        "flow_pt_inner_edges": [float(x) for x in FLOW_PT_INNER_EDGES],
        "eta_hist_inner_edges": [float(x) for x in ETA_HIST_INNER_EDGES],
        "y_hist_inner_edges": [float(x) for x in Y_HIST_INNER_EDGES],
        "centrality_windows": [[lbl, lo, hi] for (lbl, lo, hi) in CENTRALITY_WINDOWS],
        "default_centrality_field": DEFAULT_CENTRALITY_FIELD,
        "global_et_masks": list(GLOBAL_ET_MASKS),
        "et_em_component_pdg_set": sorted(EM_CATEGORY_PDGS),
        "et_em_audit_candidate_pdgs": sorted(EM_AUDIT_CANDIDATE_PDGS),
        "invisible_pdgs": sorted(INVISIBLE_PDGS),
        "explicit_hadronic_component_pdgs": sorted(EXPLICIT_HADRONIC_COMPONENT_PDGS),
        "histogram_storage_levels": {
            "scalar_pt_histograms": "hydro_event_aggregated_over_oversamples",
            "identified_y_histograms": "hydro_event_aggregated_over_oversamples",
            "flow_q_vectors": "per_oversample",
            "global_et_eta_histograms": "per_oversample",
        },
        "charged_scalar_aliases": {
            "charged_scalar_eta05": "sum over bins of pth#charged_eta05#<field> (hydro-event aggregated in v3)",
            "charged_scalar_eta10": "sum over bins of pth#charged_eta10#<field> (hydro-event aggregated in v3)",
        },
        "et_conventions": {
            "total": "sum E*sin(theta)",
            "exp_pdg_mass": "sum_ET_total - sum_BmPDG_sintheta",
            "exp_mN": "sum_ET_total - m_N * sum_B_sintheta",
            "baryon_sign_convention": "B = sign(pdg)*|baryon|, positive pdg == baryon",
            "mass_source": "PDG pole mass table (not OSCAR line mass)",
        },
        "global_et_definition": {
            "total_visible": "all final particles except neutrinos; includes the EM-component "
                             "set {gamma, e+-, pi0, eta, omega} and all hadronic species",
            "hadronic": "hadronic-component ET mask: known hadronic species, EXCLUDING the EM-"
                        "component set. This is an experimental ET-component bookkeeping split "
                        "(ALICE category A+C+D vs B), NOT a quark-content statement -- pi0/eta/"
                        "omega are mesons but assigned to the EM component.",
            "em_like_reconstruction": "total_visible - hadronic",
            "neutral_kaon_weight": "FULL weight in global ET (K0S 0.5 weight is yield-only)",
            "oscar_line_mass": "IGNORED for ET; pole mass from the PDG table is used instead, "
                               "so off-shell resonances use their species pole mass.",
            "unknown_pdg_policy": "a visible PDG that is neither EM nor a known hadronic species "
                                  "aborts caching by default (would be dropped from hadronic ET).",
            "explicit_hadronic_exceptions": "9000221 is counted as hadronic-visible global ET "
                                            "if present, but is not promoted to a core analysis "
                                            "species or restored to the pole-mass table.",
        },
        "k0s_convention": "abs(pdg)==311; species yield/ET weight 0.5; flow unweighted pooled; "
                          "M_K0S_flow ~= 2*N_K0S_yield; global ET uses FULL weight",
    }


def canonical_fingerprint_json(fingerprint):
    """Deterministic JSON serialization of the fingerprint, used identically by
    --dump-fingerprint, the publish marker's fingerprint_hash, and the reaper, so the hash
    comparison cannot false-fail on formatting/whitespace/key-order differences. Floats are
    left to Python's repr but the fingerprint is built from rounded/np.round'd constants, so
    they serialize stably. The serialization is compact and key-sorted."""
    return json.dumps(fingerprint, sort_keys=True, separators=(",", ":"))


def fingerprint_hash(fingerprint):
    return hashlib.sha256(canonical_fingerprint_json(fingerprint).encode("utf-8")).hexdigest()


def sha256_of_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def write_json_atomic(path, obj):
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ========================================================================================
# Parallel parse: pool initializer holds the big dicts in module globals (not pickled per task).
# ========================================================================================
_BARYON_MAG = None
_POLE_MASS = None


def _pool_init(baryon_mag, pole_mass, known_pdgs):
    global _BARYON_MAG, _POLE_MASS, KNOWN_PDGS
    _BARYON_MAG = baryon_mag
    _POLE_MASS = pole_mass
    KNOWN_PDGS = known_pdgs


def _parse_one_worker(args):
    i, directory, max_events = args
    ev = event_name(i, max_events)
    status, path = event_status(directory, ev)
    if status == "particle_file":
        rec = summarize_oscar_file(path, i, _BARYON_MAG, _POLE_MASS)
        if rec["nsub"] > 0:
            return status, rec
        return status, ("zero_after_parse", i, rec.get("malformed_rows", {}),
                        rec.get("oscar_structure", {}), rec.get("parse_issue"))
    if status in ("completed_zero", "no_surface"):
        return status, (status, i)
    return status, None


def event_name(i, max_events):
    return str(i).zfill(len(str(max_events - 1)))


# ========================================================================================
# Safety checks for the output path.
# ========================================================================================
def _protected_paths():
    return {os.path.abspath(p) for p in
            ["/", os.path.expanduser("~"),
             os.environ.get("PSCRATCH", "/__nope_pscratch__"),
             os.environ.get("CFS", "/__nope_cfs__")]}


def _assert_safe_output(out_dir, input_design):
    out_abs = os.path.abspath(out_dir)
    in_abs = os.path.abspath(input_design)
    if out_abs in _protected_paths():
        raise SystemExit(f"Refusing: --output-cache is a protected path: {out_abs}")
    if out_abs == in_abs or out_abs.startswith(in_abs + os.sep):
        raise SystemExit(f"Refusing: --output-cache is inside --input-design: {out_abs}")
    if in_abs.startswith(out_abs + os.sep):
        raise SystemExit(f"Refusing: --input-design is inside --output-cache: {out_abs}")



# ========================================================================================
# Size audit.
# ========================================================================================
def category_size_audit(cache_dir):
    """Sum .npz member nbytes by category prefix, across shards. Returns dict category->bytes."""
    cats = {"scalar": ["sc#"], "integrated_flow": ["fl#"], "pt_hist": ["pth#"],
            "pt_binned_flow": ["ptf#"], "charged_eta_hist": ["chetahist"],
            "y_hist": ["yh#"], "global_et": ["get#"], "inventory": ["inv_"],
            "centrality": ["cent#"], "index": ["event_ids", "nsubs", "offsets"]}
    totals = {c: 0 for c in cats}
    other = 0
    for fn in os.listdir(cache_dir):
        if not fn.endswith(".npz"):
            continue
        z = np.load(os.path.join(cache_dir, fn))
        for k in z.files:
            nb = z[k].nbytes
            placed = False
            for c, prefixes in cats.items():
                if any(k.startswith(pp) for pp in prefixes):
                    totals[c] += nb; placed = True; break
            if not placed:
                other += nb
    totals["other"] = other
    return totals


# ========================================================================================
# Driver.
# ========================================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Build a validated event-level cache for one design/system directory. "
                    "Never deletes raw particle lists.")
    ap.add_argument("--input-design", default=None)
    ap.add_argument("--output-cache", default=None)
    ap.add_argument("--dump-fingerprint", default=None, metavar="PATH",
                    help="Write the canonical cache fingerprint JSON to PATH and exit (does not "
                         "parse or cache anything). Use this once to generate the frozen "
                         "expected_cache_fingerprint_v3.json that publish_cache_to_cfs.py and "
                         "the reaper compare against. The output is byte-stable (sorted keys, "
                         "compact separators) so its sha256 is a stable scope identifier.")
    ap.add_argument("--num-events", type=int, default=10000)
    ap.add_argument("--max-events", type=int, default=10000)
    ap.add_argument("--start-index", type=int, default=0)
    ap.add_argument("--events-per-shard", type=int, default=50)
    ap.add_argument("--nprocs", type=int, default=1)
    ap.add_argument("--validate-events", type=int, default=8)
    ap.add_argument("--no-validate", action="store_true")
    ap.add_argument("--overwrite", action="store_true",
                    help="Required to replace an existing --output-cache directory.")
    ap.add_argument("--allow-completed-zero", action="store_true",
                    help="Treat events with afterburner.done but no particle list as valid "
                         "zero-particle events (nsub=0). Without this, encountering one is a "
                         "hard error.")
    ap.add_argument("--allow-parse-zero", action="store_true",
                    help="Treat a particle file that parses to zero subevents as a zero event. "
                         "Without this, parse_zero is a hard error because it usually indicates "
                         "malformed/truncated OSCAR output or an unexpected format.")
    ap.add_argument("--max-dropped-events", type=int,
                    default=int(os.environ.get("HIC_MAX_DROPPED_EVENTS", "0")), metavar="N",
                    help="If between 1 and N non-salvageable events (malformed beginning / zero "
                         "complete blocks) are found, DROP them (exclude from the cache) and "
                         "proceed, recording the count in metadata as n_dropped_bad_events instead "
                         "of aborting. More than N still aborts (a systematic problem). Default 0 "
                         "(abort on any bad event), overridable via the HIC_MAX_DROPPED_EVENTS "
                         "environment variable. A dropped-event design is published with a "
                         "drop-explained shortfall (see publish_cache_to_cfs.py); the bad events "
                         "are NOT written to cache_bad_events.json when dropped, so repair will not "
                         "try to rerun them.")
    ap.add_argument("--allow-unknown-pdg", action="store_true",
                    help="Downgrade unknown-visible-PDG and missing-mass-baryon hard errors to "
                         "warnings (DIAGNOSTIC ONLY; not safe for a cache that gates deletion).")
    ap.add_argument("--allow-malformed-rows", type=int,
                    default=int(os.environ.get("HIC_ALLOW_MALFORMED_ROWS", "0")), metavar="N",
                    help="Allow up to N structurally malformed non-comment OSCAR rows to be "
                         "skipped (a skipped row is a dropped PARTICLE, not a dropped event; the "
                         "event still caches, slightly under-populated). Default 0 is fail-closed, "
                         "overridable via the HIC_ALLOW_MALFORMED_ROWS environment variable. "
                         "Malformed rows include wrong field count, non-integer/zero PDG token, or "
                         "nonnumeric kinematic fields. Well-formed unknown integer PDGs still "
                         "trigger the normal unknown-PDG audit.")
    args = ap.parse_args()

    # --dump-fingerprint: write the canonical fingerprint and exit. No parsing/caching.
    if args.dump_fingerprint:
        fp = build_fingerprint()
        canon = canonical_fingerprint_json(fp)
        d = os.path.dirname(args.dump_fingerprint) or "."
        os.makedirs(d, exist_ok=True)
        with open(args.dump_fingerprint, "w") as f:
            f.write(canon)
        print(f"Wrote canonical fingerprint to {args.dump_fingerprint}")
        print(f"fingerprint_hash (sha256 of canonical JSON): {fingerprint_hash(fp)}")
        print("Freeze/commit this file as the canonical expected v3 scope; "
              "publish_cache_to_cfs.py and the reaper compare against it.")
        return

    if not args.input_design or not args.output_cache:
        raise SystemExit("--input-design and --output-cache are required (unless --dump-fingerprint).")

    _assert_safe_output(args.output_cache, args.input_design)
    if os.path.exists(args.output_cache) and not args.overwrite:
        raise SystemExit(f"--output-cache exists: {args.output_cache}. Pass --overwrite to replace.")

    t0 = time.time()
    global KNOWN_PDGS
    baryon_mag = load_baryon_magnitude_dict(BARYON_DATA)
    pole_mass, KNOWN_PDGS = load_pole_mass_dict(PDG_MASS_DATA)
    # Load-bearing sanity checks: signed B must restore the antibaryon sign from the
    # magnitude-only baryon table before any cache can be trusted.
    assert signed_baryon_number(2212, baryon_mag) == 1
    assert signed_baryon_number(-2212, baryon_mag) == -1
    assert signed_baryon_number(3122, baryon_mag) == 1
    assert signed_baryon_number(-3122, baryon_mag) == -1
    assert signed_baryon_number(211, baryon_mag) == 0
    print(f"Loaded baryon dict ({len(baryon_mag)}), pole-mass table ({len(pole_mass)}), "
          f"known-species set ({len(KNOWN_PDGS)} signed PDGs).")

    indices = list(range(args.start_index, args.start_index + args.num_events))
    out_dir = args.output_cache
    tmp_dir = out_dir.rstrip("/") + ".building"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)
    os.makedirs(tmp_dir, exist_ok=True)

    status_counts = {}
    shard_files = []
    n_events = 0
    n_subevents_total = 0
    n_zero_records = 0
    n_nonzero_records = 0
    # Low-nsub / salvage audit (prefix-salvage may keep fewer subevents per event).  These are
    # visibility only -- no hard threshold -- so reduced statistics from salvaged events are
    # apparent at a glance after a design caches.
    audit_nsub = {"le_1": 0, "le_3": 0, "le_5": 0, "discarded_corrupt_tail": 0,
                  "dropped_final_block": 0}
    malformed_counts = _empty_malformed_counts()
    malformed_by_event = {}
    oscar_structure_counts = _empty_oscar_structure_counts()
    bad_oscar_events = []
    dropped_bad_events = []  # populated only when bad events are DROPPED (<= --max-dropped-events)
    audit_counts = {}
    audit_unknown = set()
    audit_missing_mass = set()
    validated = 0
    val_budget = 0 if args.no_validate else args.validate_events
    if args.allow_malformed_rows < 0:
        raise SystemExit("--allow-malformed-rows must be nonnegative.")
    if args.max_dropped_events < 0:
        raise SystemExit("--max-dropped-events must be nonnegative.")

    def record_malformed(event_id, counts):
        total = _total_malformed_counts(counts)
        if total <= 0:
            return
        _merge_malformed_counts(malformed_counts, counts)
        key = str(int(event_id))
        if key not in malformed_by_event:
            malformed_by_event[key] = _empty_malformed_counts()
        _merge_malformed_counts(malformed_by_event[key], counts)

    def record_oscar_structure(event_id, counts):
        _merge_oscar_structure_counts(oscar_structure_counts, counts)

    # Streaming: parse one shard-chunk of events, validate a few, write the shard, discard.
    def handle_records(records):
        nonlocal n_events, n_subevents_total, validated, n_zero_records, n_nonzero_records
        for r in records:
            n_events += 1
            n_subevents_total += r["nsub"]
            record_malformed(r["event_id"], r.get("malformed_rows", {}))
            structure = r.get("oscar_structure", {})
            record_oscar_structure(r["event_id"], structure)
            if r["nsub"] == 0:
                n_zero_records += 1
            else:
                n_nonzero_records += 1
                ns = r["nsub"]
                if ns <= 1:
                    audit_nsub["le_1"] += 1
                if ns <= 3:
                    audit_nsub["le_3"] += 1
                if ns <= 5:
                    audit_nsub["le_5"] += 1
            if structure.get("files_with_discarded_corrupt_tail"):
                audit_nsub["discarded_corrupt_tail"] += 1
            if structure.get("files_with_dropped_final_block"):
                audit_nsub["dropped_final_block"] += 1
        # partition audit (cheap, accumulate)
        ac, unk, miss = audit_partition(records, baryon_mag, pole_mass)
        for k, v in ac.items():
            audit_counts[k] = audit_counts.get(k, 0.0) + v
        audit_unknown.update(unk)
        audit_missing_mass.update(miss)
        # validation on a spread of events within this chunk, up to budget
        if val_budget and records:
            order = sorted(range(len(records)), key=lambda j: records[j]["nsub"])
            pick = {order[0], order[-1]}  # fewest and most subevents (cap-saturation proxy)
            for j in order[::max(1, len(records) // max(1, val_budget))]:
                if len(pick) >= val_budget:
                    break
                pick.add(j)
            for j in sorted(pick):
                if validated >= val_budget:
                    break
                rec = records[j]
                if rec["nsub"] == 0:
                    continue
                ev = event_name(rec["event_id"], args.max_events)
                _, path = event_status(args.input_design, ev)
                if path is None:
                    continue
                ok, msgs = validate_event_against_cache(rec, path, baryon_mag, pole_mass)
                if not ok:
                    for m in msgs[:10]:
                        print("  VALIDATION FAILURE:", m, file=sys.stderr)
                    raise SystemExit(f"Validation failed on event {rec['event_id']}; aborting.")
                validated += 1
        ok, msgs = validate_closures(records)
        if not ok:
            for m in msgs[:10]:
                print("  CLOSURE FAILURE:", m, file=sys.stderr)
            raise SystemExit("Closure validation failed; aborting.")

    def parse_chunk(chunk_indices):
        recs = []
        if args.nprocs and args.nprocs > 1:
            with mp.Pool(processes=args.nprocs, initializer=_pool_init,
                         initargs=(baryon_mag, pole_mass, KNOWN_PDGS)) as pool:
                results = pool.map(_parse_one_worker,
                                   [(i, args.input_design, args.max_events) for i in chunk_indices])
            for status, payload in results:
                status_counts[status] = status_counts.get(status, 0) + 1
                if status == "particle_file" and isinstance(payload, dict):
                    recs.append(payload)
                elif isinstance(payload, tuple) and payload[0] == "zero_after_parse":
                    status_counts["parse_zero"] = status_counts.get("parse_zero", 0) + 1
                    if len(payload) > 2:
                        record_malformed(payload[1], payload[2])
                    structure = payload[3] if len(payload) > 3 else {}
                    issue = payload[4] if len(payload) > 4 else None
                    _handle_parse_zero(payload[1], recs, structure, issue)
                elif isinstance(payload, tuple) and payload[0] == "completed_zero":
                    _handle_completed_zero(payload[1], recs)
                elif isinstance(payload, tuple) and payload[0] == "no_surface":
                    _handle_no_surface(payload[1], recs)
        else:
            for i in chunk_indices:
                ev = event_name(i, args.max_events)
                status, path = event_status(args.input_design, ev)
                status_counts[status] = status_counts.get(status, 0) + 1
                if status == "particle_file":
                    rec = summarize_oscar_file(path, i, baryon_mag, pole_mass)
                    if rec["nsub"] > 0:
                        recs.append(rec)
                    else:
                        status_counts["parse_zero"] = status_counts.get("parse_zero", 0) + 1
                        record_malformed(i, rec.get("malformed_rows", {}))
                        _handle_parse_zero(i, recs, rec.get("oscar_structure", {}), rec.get("parse_issue"))
                elif status == "completed_zero":
                    _handle_completed_zero(i, recs)
                elif status == "no_surface":
                    _handle_no_surface(i, recs)
        recs.sort(key=lambda r: r["event_id"])
        return recs

    def _handle_completed_zero(i, recs):
        if args.allow_completed_zero:
            recs.append(zero_event_record(i))
        else:
            raise SystemExit(
                f"Event {i} has afterburner.done but no particle list (completed_zero). "
                "Pass --allow-completed-zero to treat as a zero-particle event, or investigate.")

    def _handle_no_surface(i, recs):
        # Legitimate terminal zero-output event: hydro completed and produced no
        # particlization surface (hydro.no_surface).  Cached as an nsub=0 record
        # so event-counting and centrality denominators stay correct.  No flag
        # gate, unlike completed_zero: hydro.no_surface is written only by the
        # vetted inline hydro path, so it is trusted as a valid terminal class.
        recs.append(zero_event_record(i))

    def _handle_parse_zero(i, recs, structure=None, issue=None):
        structure = structure or {}
        if args.allow_parse_zero:
            rec = zero_event_record(i)
            rec["oscar_structure"] = structure
            rec["parse_issue"] = issue
            recs.append(rec)
        else:
            # No record will be passed to handle_records, so record the rejected structure here.
            record_oscar_structure(i, structure)
            bad_oscar_events.append({
                "event_id": int(i),
                "reason": issue or "zero_complete_blocks",
                "structure": {k: int(structure.get(k, 0)) for k in OSCAR_STRUCTURE_KEYS
                              if int(structure.get(k, 0))},
            })

    eps = args.events_per_shard
    shard_idx = 0
    for s in range(0, len(indices), eps):
        chunk = indices[s:s + eps]
        recs = parse_chunk(chunk)
        if not recs:
            continue
        handle_records(recs)
        shard_name = f"shard_{shard_idx:04d}.npz"
        n = _pack_records_to_npz(recs, os.path.join(tmp_dir, shard_name))
        if n > 0:
            shard_files.append(shard_name)
            shard_idx += 1
        print(f"  shard {shard_idx} written ({n} events); running totals: "
              f"{n_events} events, {n_subevents_total} subevents", flush=True)

    if bad_oscar_events and not args.allow_parse_zero:
        bad_oscar_events.sort(key=lambda x: x["event_id"])
        drop = len(bad_oscar_events) <= args.max_dropped_events

        # Per-event payload, used either for the metadata drop-record OR the abort file. Records
        # each event's current particle file (name/size/mtime) for downstream auditing.
        bad_payload_events = []
        for item in bad_oscar_events:
            ev = event_name(item["event_id"], args.max_events)
            edir = Path(args.input_design) / ev
            entry = {
                "event_id": int(item["event_id"]),
                "reason": item.get("reason"),
                "structure": item.get("structure", {}),
            }
            try:
                usable, _ = _cache_particle_file_state(edir)
                if usable:
                    name, p = usable[0]
                    st = p.stat()
                    entry["particle_file"] = name
                    entry["size"] = int(st.st_size)
                    entry["mtime"] = float(st.st_mtime)
            except OSError:
                pass  # best-effort fingerprint; absence just means repair re-checks live state
            bad_payload_events.append(entry)

        if drop:
            # Within threshold: DROP these events and proceed. They were never added to recs, so
            # the shards already contain only good events; n_events reflects the reduced count.
            # Record the drop in metadata (auditable) and do NOT write cache_bad_events.json --
            # writing it would make repair_stale_events.py try to rerun events we deliberately
            # dropped, fighting this decision. publish_cache_to_cfs.py reads n_dropped_bad_events
            # and accepts the resulting shortfall iff it is fully explained by the drops.
            dropped_bad_events.extend(bad_payload_events)
            shown = [e["event_id"] for e in bad_payload_events[:50]]
            print(f"DROPPING {len(bad_oscar_events)} non-salvageable event(s) "
                  f"(<= --max-dropped-events={args.max_dropped_events}); caching {n_events} good "
                  f"events. Dropped event ids: {shown}"
                  f"{' ...' if len(bad_payload_events) > 50 else ''}", flush=True)
            # fall through: tmp_dir kept; metadata + cache written below.
        else:
            # Over threshold: a systematic problem. Persist cache_bad_events.json so repair can
            # clean exactly these events for rerun and block readiness, then abort.
            shutil.rmtree(tmp_dir)
            bad_marker = {
                "schema": "cache_bad_events.v1",
                "system": os.path.basename(os.path.normpath(args.input_design)),
                "target_events": args.num_events,
                "created_by": "cache_one_design.py",
                "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "bad_events": bad_payload_events,
            }
            try:
                bad_path = os.path.join(args.input_design, CACHE_BAD_EVENTS_FILE)
                tmp_bad = bad_path + ".tmp"
                with open(tmp_bad, "w") as bf:
                    json.dump(bad_marker, bf, indent=2, sort_keys=True)
                    bf.write("\n")
                os.replace(tmp_bad, bad_path)
                print(f"  wrote {bad_path} ({len(bad_payload_events)} events) for repair to clean.",
                      file=sys.stderr)
            except OSError as e:
                print(f"  WARNING: could not write {CACHE_BAD_EVENTS_FILE}: {e}", file=sys.stderr)

            print("ABORT: particle files with afterburner.done but zero usable complete OSCAR "
                  "subevents or malformed beginnings were found:", file=sys.stderr)
            for item in bad_oscar_events[:50]:
                print(f"  event {item['event_id']}: reason={item['reason']} "
                      f"structure={item['structure']}", file=sys.stderr)
            if len(bad_oscar_events) > 50:
                print(f"  ... {len(bad_oscar_events) - 50} more", file=sys.stderr)
            raise SystemExit(
                f"ABORT: {len(bad_oscar_events)} non-salvageable OSCAR particle file(s) exceeds "
                f"--max-dropped-events={args.max_dropped_events}. Repair/rerun those events, raise "
                f"--max-dropped-events (or HIC_MAX_DROPPED_EVENTS) to drop them, or use "
                f"--allow-parse-zero only for diagnostics.")

    if n_events == 0:
        shutil.rmtree(tmp_dir)
        raise SystemExit("No valid events parsed; nothing cached.")

    if not args.no_validate:
        print(f"Validation passed (raw-reference on {validated} events + closures).")

    malformed_total = _total_malformed_counts(malformed_counts)
    malformed_top_events = sorted(
        (
            {"event_id": int(ev), "total": _total_malformed_counts(c),
             "by_reason": {k: int(c.get(k, 0)) for k in MALFORMED_OSCAR_KEYS if int(c.get(k, 0))}}
            for ev, c in malformed_by_event.items()
        ),
        key=lambda x: (-x["total"], x["event_id"]),
    )[:20]
    if malformed_total:
        summary = {k: int(malformed_counts.get(k, 0)) for k in MALFORMED_OSCAR_KEYS}
        print(f"Malformed OSCAR rows skipped: total={malformed_total}, by_reason={summary}",
              file=sys.stderr)
        if malformed_top_events:
            print("Top malformed-row events:", file=sys.stderr)
            for item in malformed_top_events[:10]:
                print(f"  event {item['event_id']}: total={item['total']} "
                      f"by_reason={item['by_reason']}", file=sys.stderr)
    if malformed_total > args.allow_malformed_rows:
        shutil.rmtree(tmp_dir)
        raise SystemExit(
            f"ABORT: skipped {malformed_total} malformed OSCAR rows, exceeding "
            f"--allow-malformed-rows={args.allow_malformed_rows}. These rows were not parsed "
            "as particles. If the count is acceptable, rerun with an explicit larger "
            "--allow-malformed-rows value; otherwise rerun/repair the affected events.")
    elif malformed_total:
        print(f"WARNING: accepting cache with {malformed_total} skipped malformed OSCAR rows "
              f"because --allow-malformed-rows={args.allow_malformed_rows}.", file=sys.stderr)

    # Hard-error gates (before any cache is published). An unknown visible PDG would be
    # silently dropped from the hadronic mask; a baryon with no pole mass would silently lose
    # its experiment-ET correction. Both make the cached ET wrong-and-unrecoverable, so they
    # abort by default. --allow-unknown-pdg downgrades to a warning (diagnostic use only).
    if audit_missing_mass:
        names = sorted(int(p) for p in audit_missing_mass)
        msg = (f"Baryon PDGs present with NO pole mass in the table (experiment-ET correction "
               f"would be silently zero): {names}. Add them to the PDG mass table or fix the "
               f"baryon dict.")
        if args.allow_unknown_pdg:
            print("WARNING (overridden):", msg, file=sys.stderr)
        else:
            shutil.rmtree(tmp_dir)
            raise SystemExit("ABORT: " + msg + " (pass --allow-unknown-pdg to override for diagnostics).")
    if audit_unknown:
        names = sorted(int(p) for p in audit_unknown)
        msg = (f"Visible PDGs in OSCAR that are NEITHER in the EM set NOR a known hadronic "
               f"species (would be excluded from the hadronic global-ET mask): {names}. "
               f"Classify them (extend EM set or PDG table) before caching.")
        if args.allow_unknown_pdg:
            print("WARNING (overridden):", msg, file=sys.stderr)
        else:
            shutil.rmtree(tmp_dir)
            raise SystemExit("ABORT: " + msg + " (pass --allow-unknown-pdg to override for diagnostics).")

    fingerprint = build_fingerprint()
    # PDG audit summary (sorted by count desc) for the EM/neutral-kaon checks
    audit_sorted = sorted(audit_counts.items(), key=lambda kv: -kv[1])
    pdg_audit = {str(int(p)): int(c) for p, c in audit_sorted}
    metadata = {
        "fingerprint": fingerprint,
        "fingerprint_hash": fingerprint_hash(fingerprint),
        "system_dir": os.path.abspath(args.input_design),
        "n_events": n_events,
        "n_dropped_bad_events": len(dropped_bad_events),
        "dropped_bad_events": dropped_bad_events,
        "n_subevents_total": n_subevents_total,
        "n_zero_records": n_zero_records,
        "n_nonzero_records": n_nonzero_records,
        "malformed_oscar_rows": {
            "total": int(malformed_total),
            "by_reason": {k: int(malformed_counts.get(k, 0)) for k in MALFORMED_OSCAR_KEYS},
            "events_with_malformed": len(malformed_by_event),
            "max_per_event": max((_total_malformed_counts(c) for c in malformed_by_event.values()),
                                  default=0),
            "top_events": malformed_top_events,
            "allowed_threshold": int(args.allow_malformed_rows),
            "policy": "skip malformed non-comment rows with wrong field count, non-integer/zero PDG token, or nonnumeric kinematic fields; well-formed unknown integer PDGs still hard-fail unless --allow-unknown-pdg",
        },
        "oscar_block_policy": {
            "counts": {k: int(oscar_structure_counts.get(k, 0)) for k in OSCAR_STRUCTURE_KEYS},
            "policy": (
                "require exact OSCAR header except SMASH version and event-0 particle count; "
                "cache complete '# event N out'...'# event N end' blocks (end id must match the "
                "open id); on the first mid-file grammar break (out-while-open, end-without-open, "
                "end-id mismatch, row-outside-block) stop reading and discard that block plus the "
                "rest as an untrustworthy corrupt tail (no forward resync); drop one incomplete "
                "trailing block at clean EOF; require at least one complete kept subevent; "
                "normalization uses the kept nsub; declared-vs-raw row count is recorded "
                "(rowcount_mismatch_blocks) but NEVER used as a structural gate; malformed "
                "beginnings and zero-complete-block files hard-fail unless --allow-parse-zero"
            ),
            "minimum_complete_subevents": 1,
            "salvage_audit": {
                "events_nsub_le_1": int(audit_nsub["le_1"]),
                "events_nsub_le_3": int(audit_nsub["le_3"]),
                "events_nsub_le_5": int(audit_nsub["le_5"]),
                "events_with_discarded_corrupt_tail": int(audit_nsub["discarded_corrupt_tail"]),
                "events_with_dropped_final_block": int(audit_nsub["dropped_final_block"]),
            },
        },
        "events_per_shard": eps,
        "shards": shard_files,
        "status_counts": status_counts,
        "pdg_audit_counts": pdg_audit,
        "unknown_visible_pdgs": sorted(int(p) for p in audit_unknown),
        "missing_mass_baryon_pdgs": sorted(int(p) for p in audit_missing_mass),
        "created_unix": time.time(),
        "created_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        "cache_started_unix": float(t0),
        "validation": {
            "raw_reference_enabled": not args.no_validate,
            "raw_reference_events": validated,
            "closures_passed": True
        }
    }
    write_json_atomic(os.path.join(tmp_dir, "metadata.json"), metadata)

    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.replace(tmp_dir, out_dir)
    print(f"Local cache written: {out_dir} ({n_events} events, {n_subevents_total} subevents, "
          f"{len(shard_files)} shards).")

    # The cache is now durably in place, so any cache_bad_events.json from a PRIOR failed attempt
    # is stale: this run either had no bad events, or dropped them (recorded in metadata as
    # n_dropped_bad_events) and succeeded. Remove it so repair_stale_events.py does not later
    # consume it and rerun events we intentionally dropped. Placed AFTER os.replace so an abort
    # (which happens before this point) still leaves the file for repair.
    stale_bad_path = os.path.join(args.input_design, CACHE_BAD_EVENTS_FILE)
    try:
        if os.path.exists(stale_bad_path):
            os.remove(stale_bad_path)
            print(f"Removed stale {CACHE_BAD_EVENTS_FILE} after successful cache: {stale_bad_path}")
    except OSError as e:
        print(f"WARNING: could not remove stale {CACHE_BAD_EVENTS_FILE}: {e}", file=sys.stderr)

    # Salvage / low-nsub audit -- visibility into reduced statistics from prefix-salvage.
    if (audit_nsub["discarded_corrupt_tail"] or audit_nsub["dropped_final_block"]
            or audit_nsub["le_5"]):
        print("Salvage audit (reduced-statistics visibility, no hard threshold):")
        print(f"  events with discarded corrupt tail : {audit_nsub['discarded_corrupt_tail']}")
        print(f"  events with dropped final block    : {audit_nsub['dropped_final_block']}")
        print(f"  events nsub<=1 / <=3 / <=5         : "
              f"{audit_nsub['le_1']} / {audit_nsub['le_3']} / {audit_nsub['le_5']}")
        rmm = oscar_structure_counts.get("rowcount_mismatch_blocks", 0)
        if rmm:
            print(f"  blocks with declared!=raw rowcount : {rmm} (diagnostic only, not gated)")

    # size audit
    sizes = category_size_audit(out_dir)
    total = sum(sizes.values())
    print("Cache size by category:")
    for c, b in sorted(sizes.items(), key=lambda kv: -kv[1]):
        print(f"  {c:20s} {b/1e6:9.2f} MB  ({100*b/max(1,total):5.1f}%)")
    print(f"  {'TOTAL':20s} {total/1e6:9.2f} MB")

    # report a few key audit PDGs explicitly
    print("Key PDG audit counts (per cached design):")
    for pdg, name in [(22, "gamma"), (11, "e-"), (-11, "e+"), (111, "pi0"), (221, "eta"),
                      (223, "omega"), (331, "eta'"), (113, "rho0"), (213, "rho+"),
                      (333, "phi"), (2112, "n"), (-2112, "nbar"), (311, "K0"), (-311, "K0bar"),
                      (310, "K0S"), (130, "K0L"), (9000221, "sigma/f0(500)")]:
        cnt = int(audit_counts.get(pdg, 0))
        tag = ""
        if pdg in EM_CATEGORY_PDGS:
            tag = "  [EM mask]"
        elif pdg in EM_AUDIT_CANDIDATE_PDGS:
            tag = "  [EM-candidate: in hadronic mask unless added to EM set]"
        print(f"  {name:6s} ({pdg:5d}): {cnt}{tag}")

    # Publishing to CFS is intentionally NOT done here. The cache script's responsibility
    # ends at producing a validated LOCAL cache. Compression, CFS copy, decompress-and-hash
    # verification, and the cache.verified deletion-gate marker are handled by the separate
    # publish_cache_to_cfs.py, which is the SOLE writer of cache.verified (one marker writer,
    # no drift). Run that next on this local cache.
    print(f"Done in {time.time()-t0:.1f}s. Local cache is validated but NOT yet published. "
          f"Run publish_cache_to_cfs.py to compress+verify+publish to CFS. "
          f"Raw particle lists NOT deleted (separate reaper, gated on cache.verified).")


if __name__ == "__main__":
    main()
