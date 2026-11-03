#!/usr/bin/env python3
"""
Single-system Bayesian inference: emulator + likelihood + MCMC.

The collision system (Au-Au 200, O-O 5.36, ...) is selected with `--system` (default AuAu200) or
`use_system(name)` from an importing script; everything system-specific -- matrix, data dir,
DATA_SPEC, which beam-energy norm is active, FIXED_PARAMS, feed-down -- lives in one SYSTEMS
entry. The matrix and feed-down files must carry a sibling `.meta.json` whose "system" matches
(hard error otherwise), so one system's model can never be scored against another's data. The
matrix meta's "analysis_profile" (missing = "baseline") must also equal the system's `profile`:
Analysis profiles may redefine a column under its old label (O-O: charged mean_pT, c2/c3_gap).
The description below is written for Au-Au; other systems follow the same matrix/data format.

Reads the analysis output `model_matrix_<SYSTEM>.npz` (already binned on STAR-style
centrality edges) and matches each model observable to the STAR/PHENIX data
bins, aggregating whole model bins where a data bin spans several (yields:
width-weighted mean; <pT>: yield-weighted; flow: multiplicity^2-weighted mean
in cumulant space). Charged flow is calibrated as the signed two-particle
cumulant c_n{2} (NaN-free), with the data mapped v_n{2} -> c = v^2. Calibrates
the 14 Au-Au-relevant design parameters against STAR AuAu-200 data and produces
posterior + posterior-predictive diagnostics.

Emulator: standalone PCA+GP replicating STAT's src.emulator. Model uncertainty
in the likelihood is the emulator predictive variance. (The Y_err-aware option
is surmise PCSK, simsd=Y_err; the emulator is behind a predict(X, return_std)
contract so it swaps cleanly.)
Sampler: dependency-free Goodman-Weare stretch-move ensemble (swap emcee/STAT in
at the same boundary).

Parameter policy: --parameter-mode {all (default), system, flat-eta} selects which parameters are
sampled; --shear-mode flat conditions eta_low = eta_high = 0, so eta_min is the constant eta/s
(temperature-dependent samples both slopes). Fixed parameters are conditioned in the full-design
emulator, never dropped from its training.

Multi-system: joint_inference.py (see JOINT.md) combines one emulator per system into a posterior
over shared physics parameters. It passes explicit system=/fixed_params= arguments to
prepare_inputs/input_config instead of calling use_system().

    python inference.py              # full inference (Au-Au)
    python inference.py --system OO5360 --emulator-only
    python inference.py --system OO5360 --shear-mode flat
    python inference.py --emulator-only
    python inference.py --selftest
"""

import os, sys, argparse
import hashlib
import json
from pathlib import Path
from collections import defaultdict
import numpy as np
from design_parameters import PARAMS_ALL

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import ticker
from scipy.interpolate import PchipInterpolator

from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.gaussian_process import GaussianProcessRegressor as GPR
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.model_selection import KFold
from sklearn.exceptions import ConvergenceWarning
import warnings
warnings.filterwarnings("ignore", category=ConvergenceWarning)  # GP bound-hits; benign

# ============================================================
# Paths (defaults; override with --matrix/--design/--data-dir/--out-dir)
# The design is SHARED by all systems (the same 500 points run through every system), so
# design_NNN = row NNN of DESIGN_DAT everywhere. The per-system paths (MATRIX_NPZ, DATA_DIR,
# OUT_DIR, ...) are set from SYSTEMS by use_system() further down.
# ============================================================
ROOT = Path(__file__).resolve().parent
DESIGN_DIR = str(ROOT / "model")   # staged matrices and feed-down inventories
# Keep the bundled table local so this directory also works as a standalone copy.
# Event generation retains a compatibility link to this same canonical file.
DESIGN_DAT  = str(ROOT / "model/design_params_combined500.dat")

# ============================================================
# Design parameters and generation share the same order and prior box in
# design_parameters.py. Its ranges match design_params_combined500.dat.
# ============================================================
# Each system uses exactly one beam-energy normalization; the others have no effect on it and are
# dropped from the emulator input (DROP_PARAMS is derived per system in use_system()).
NORM_PARAMS = ("norm5360", "norm5020", "norm2760", "norm200")
SHEAR_MODES = ("temperature-dependent", "flat")

# Au-Au `--parameter-mode system` slice: six parameters held FIXED, eight sampled. Adopted when the
# design had 242 points, too sparse for 14-D (the posterior wandered into emulator-extrapolation
# territory and railed at the walls). With the 500-point design the primary fit (--parameter-mode
# all) samples all 14; this slice remains an optional variant. Fixed parameters are CONDITIONED in
# the full 14-D emulator (it still trains on their design variation), NOT dropped from it -- so no
# signal becomes noise.
#   inert  (fixing ~free): k, eta_low, eta_high  -- fixed at prior-box midpoints
#   bulk shape (physics choice): zeta_width, zeta_T, zeta_skew fixed to a generic symmetric
#     (zeta/s)(T) peak near T_c (not a published MAP curve); zeta_max (the magnitude) stays FREE
#     as the single bulk parameter.
FIXED_PARAMS_AUAU = {
    "k":        1.5,    # TRENTo mult-fluctuation shape (prior mid [0.8,2.2]); ~inert (obs shift <=1.4 sigma)
    "eta_low": -0.5,     # (eta/s) low-T slope (prior mid [-2,1]); inert (<=0.3 sigma)
    "eta_high": 0.5,     # (eta/s) high-T slope (prior mid [-1,2]); ~inert (<=4.3 sigma)
    "zeta_T":     0.16,  # (zeta/s)(T) peak location [GeV], just above T_c~0.155 (prior [0.12,0.3])
    "zeta_width": 0.05,  # (zeta/s)(T) peak width [GeV] (prior [0.01,0.15])
    "zeta_skew":  0.0,   # (zeta/s)(T) peak asymmetry, symmetric (prior [-1,1])
}

# ============================================================
# Observable map: data file(s) -> model channel. The model matrix is already on
# STAR-style centrality edges, so each data bin is matched to the whole model bin(s)
# that tile it (aggregating only when a data bin spans several -- e.g. Lambda 20-40).
#   kind="yield"  : sum the ± data files; model = width-weighted mean of constituent bins
#   kind="meanpt" : single data file; model = (width*dN_dy)-weighted mean of <pT>
#   kind="flow"   : CUMULANT space. data v_n{2} -> c = v^2, sig_c = 2 v sqrt(sig_v^2+(floor v)^2);
#                   model = (width*dNch^2)-weighted mean of c_n_gap (multiplicity^2 pair weight).
#                   cent_max drops data bins with hi>cent_max (resolution cut; c2<=60, c3<=50).
#   sys_floor=f   : yield/meanpt add f*value in quadrature to the data error; flow folds a
#                   fractional f into sig_v before the delta map (stat-only flow errors are tiny).
# A data bin not exactly tiled by whole model bins (PHENIX proton 10-15/15-20, out-of-range)
#   is skipped with a printed note. c_n_gap is NaN-free, so no flow bins are dropped for NaN.
# ============================================================
# Feed-down: SMASH omits weak/e.m. decays (confirmed from its decaymodes file), so the model Lambda
# misses Sigma0->Lambda(gamma) and the model proton misses Sigma+->p. STAR's Lambda (nucl-ex/0606014)
# is feed-down CORRECTED (Xi removed ~15%) but by convention STILL contains Sigma0 (e.m., ctau~0,
# unremovable). apply_feeddown() multiplies the model yield by (1 + parent/reference) per (design,
# centrality) BEFORE assemble, using |eta|<2 inventory multiplicities from feeddown_yields.npz -- the
# ratio uses the same |eta|<2 window so acceptance cancels (the inventory is NOT a |y|<0.5 density).
# Map: model species -> (parent, reference) pidpm token. Model Sigma0/Lambda ~0.35 (flat in cent).
FEEDDOWN_CORR_AUAU = {
    "Lambda_tot": ("pidpm3212", "pidpm3122"),   # + Sigma0 -> Lambda   (essential)
    # "p_tot":    ("pidpm3222", "pidpm2212"),   # + Sigma+ -> proton   (insurance; enable when ready)
}
DATA_SPEC_AUAU = [
    dict(name="dN_dy_pion",   species="pi_tot", obs="dN_dy",  kind="yield",
         files=["dN_dy_pion_+.dat", "dN_dy_pion_-.dat"]),
    dict(name="dN_dy_kaon",   species="K_tot",  obs="dN_dy",  kind="yield",
         files=["dN_dy_kaon_+.dat", "dN_dy_kaon_-.dat"]),
    dict(name="dN_dy_proton", species="p_tot",  obs="dN_dy",  kind="yield",
         files=["../PHENIX/dN_dy_proton_+.dat", "../PHENIX/dN_dy_proton_-.dat"]),
    dict(name="dN_dy_Lambda", species="Lambda_tot", obs="dN_dy", kind="yield",
         files=["dN_dy_Lambda_tot.dat"]),
    dict(name="dN_dy_Xi",     species="Xi_tot",     obs="dN_dy", kind="yield",
         files=["dN_dy_Xi_tot.dat"]),
    dict(name="dN_dy_Omega",  species="Omega_tot",  obs="dN_dy", kind="yield",
         files=["dN_dy_Omega_tot.dat"]),
    dict(name="dNch_deta", species="charged",   obs="dNch_deta",              kind="yield",
         files=["dNch_deta.dat"]),
    dict(name="dET",       species="global_ET", obs="dET_total_visible_deta", kind="yield",
         files=["dET_deta.dat"]),
    dict(name="mean_pT_pion", species="pi_tot", obs="mean_pT", kind="meanpt",
         files=["mean_pT_pion.dat"]),
    dict(name="mean_pT_kaon", species="K_tot",  obs="mean_pT", kind="meanpt",
         files=["mean_pT_kaon.dat"]),
    dict(name="mean_pT_proton", species="p_tot", obs="mean_pT", kind="meanpt",
         files=["mean_pT_proton.dat"]),
    dict(name="c2_charged", species="charged", obs="c2_gap", kind="flow", sys_floor=0.05,
         cent_max=60.0, files=["v22.dat"]),
    dict(name="c3_charged", species="charged", obs="c3_gap", kind="flow", sys_floor=0.05,
         cent_max=50.0, files=["v32.dat"]),
]

# O-O 5.36 TeV. Likelihood-ready files are supplied via --data-dir (see data/OO5360.md;
# derived/ is selected explicitly below). Centrality errors are combined experimental errors
# (CMS is digitized); identified-hadron errors mix digitized totals and flagged assumptions.
# No experimental covariance is used.
# sys_floor=0.05 on every observable (decided 2026-09-22): a model-discrepancy + missing-covariance
# proxy, same value as Au-Au flow. It is added in quadrature even when data errors exceed 5%;
# it is not a covariance model or a measured experimental systematic.
# REQUIRES a matrix with analysis_profile OO_PROFILE (checked at load): that analysis profile
# REDEFINES two charged columns under their old labels, so this spec is only valid against it.
# Matching notes:
#  - dNch: CMS |eta|<0.5, pT>0 == model charged eta05 dNch_deta (unchanged by the profile).
#  - <pT>_ch: ATLAS measured FIDUCIAL <pT> (|eta|<0.5, 0.27<pT<5 GeV) vs the profile's charged
#    mean_pT: eta05, 0.25-5 GeV cached moments minus 0.4 of the [0.25,0.30) bin (flat dN/dpT),
#    i.e. ~0.27-5 GeV (residual <0.05% on ATLAS-tuned spectra; the raw 0.25 cut was ~2-2.7% low).
#  - flow: ALICE v_n{2}, |eta|<0.8, 0.2<pT<3, |Delta eta|>1.4 vs the profile's c2_gap/c3_gap =
#    cross_full: distinct oversamples of the same hydro event over the full |eta|<0.8, same pT cut
#    (a same-sample fallback for k=1 events is gated by FLOW_FALLBACK_MAX). The model has (almost)
#    no nonflow and, being boost-invariant, no decorrelation; the gapped data have some of both,
#    growing peripherally. c2: all ALICE bins, 0-60%. c3: cent_max=45: beyond 45% the model c3
#    is MC-noise dominated (noise/spread 0.59-0.85, k-fold RMSE/sd 0.82-1.04, 25-42% of designs
#    c3<0) -- a resolution cut, as Au-Au's c3<=50.
#  - Centrality: data bins are 5% (+1% in 0-5% for ATLAS/ALICE); the matrix is on the fine
#    0,1,..,5,10,..,80 grid, so every data bin is exactly one model bin (no aggregation).
#  - Identified yields and <pT>: ALICE strange hadrons vs multiplicity (below).
DATA_SPEC_OO5360 = [
    dict(name="dNch_deta", species="charged", obs="dNch_deta", kind="yield",
         sys_floor=0.05,
         files=["dN_deta_charged_CMS.dat"]),
    dict(name="mean_pT_charged", species="charged", obs="mean_pT", kind="meanpt",
         sys_floor=0.05,
         files=["mean_pT_charged_ATLAS.dat"]),
    dict(name="c2_charged", species="charged", obs="c2_gap", kind="flow",
         sys_floor=0.05,
         files=["v2_2_charged_ALICE.dat"]),
    dict(name="c3_charged", species="charged", obs="c3_gap", kind="flow",
         sys_floor=0.05, cent_max=45.0,
         files=["v3_2_charged_ALICE.dat"]),
    # ALICE preliminary strange hadrons vs <dNch/deta>: errors digitized where resolved;
    # K0S yields and seven Xi yield points still have assumed errors. Per-point evidence and
    # pixel-reading margins are in data/OO5360/uncertainties/. Lambda omitted (as
    # Au-Au; also no O-O Sigma0 feed-down file). This specification uses K0S; charged kaons
    # are a distinct species and would require their own experimental input and matching.
    dict(name="dN_dy_K0S_vs_mult", species="K0S", obs="dN_dy", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="assumed", files=["../derived/dN_dy_K0S_vs_mult.dat"]),
    dict(name="dN_dy_Xi_vs_mult", species="Xi_tot", obs="dN_dy", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="mixed", files=["../derived/dN_dy_Xi_tot_vs_mult.dat"]),
    dict(name="dN_dy_Omega_vs_mult", species="Omega_tot", obs="dN_dy", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="digitized", files=["../derived/dN_dy_Omega_tot_vs_mult.dat"]),
    # Identified <pT> (added 2026-10-12). The primary fit retains the historical K0S exclusion.
    # The old data ratio ~1.40 used extrapolated pT>0 charged <pT>, not the current fiducial
    # 0.27<pT<5 observable (central-bin ratio ~1.11). Recheck model reachability with the v3
    # matrix before drawing conclusions about that exclusion. Run with and without
    # (--drop-specs mean_pT_K0S_vs_mult,mean_pT_Xi_vs_mult,mean_pT_Omega_vs_mult).
    dict(name="mean_pT_K0S_vs_mult", species="K0S", obs="mean_pT", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="digitized", files=["../derived/mean_pT_K0S_vs_mult.dat"]),
    dict(name="mean_pT_Xi_vs_mult", species="Xi_tot", obs="mean_pT", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="digitized", files=["../derived/mean_pT_Xi_tot_vs_mult.dat"]),
    dict(name="mean_pT_Omega_vs_mult", species="Omega_tot", obs="mean_pT", kind="vs_mult",
         sys_floor=0.05, uncertainty_status="digitized", files=["../derived/mean_pT_Omega_tot_vs_mult.dat"]),
]
# PARKED, and INVALID with the OO_PROFILE matrix: ATLAS <pT> extrapolated to pT>0 (the 2026-10-08
# stopgap) matches only a "baseline"-profile matrix, whose charged mean_pT is pT>0 (the 186/240
# matrices). Never fit it alongside the fiducial table: they share the same measured spectra.
DATA_SPEC_OO5360_MEANPT_PT0 = [
    dict(name="mean_pT_charged_pT0", species="charged", obs="mean_pT", kind="meanpt",
         sys_floor=0.05,
         files=["../derived/mean_pT_charged_ATLAS_pT0.dat"]),
]
# Analysis profile required by the O-O measurement specification.
OO_PROFILE = "oo_cross_full_pt027_flat/v3"
# Largest allowed same-sample (k=1 fallback) share of the pair weight in any model bin feeding a fitted
# flow data bin. Fallback events carry same-sample nonflow that cross_full excludes. 0 = any fallback
# in a fitted flow bin is a hard error; raise it deliberately after looking at the numbers.
FLOW_FALLBACK_MAX = 0.0

# <pT> is aggregated over model bins weighted by particle count ~ width * yield. The yield
# observable is dN_dy for identified species, dNch_deta for charged (the matrix has no charged dN_dy).
YIELD_OBS = {"charged": "dNch_deta"}   # species -> yield observable; default "dN_dy"

# ============================================================
# Systems. One entry per collision system; use_system() copies it into the module globals that
# the rest of the file (and the scripts that `import inference as I`) read at call time.
#   profile: the analysis_profile the matrix meta must carry (missing = "baseline"). The
#            fingerprint and feature labels cannot tell profiles apart, so this is the only guard
#            against fitting a spec to columns that were redefined under the same label.
#   norm   : the one active beam-energy normalization (the other NORM_PARAMS are dropped)
#   fixed  : FIXED_PARAMS for this system (a copy is installed, so scripts may overwrite
#            I.FIXED_PARAMS after use_system() without touching the registry)
#   feeddown_npz / feeddown_corr : Sigma feed-down inventory for THIS system (meta-checked)
# ============================================================
# Primary AuAu200 exclusions: Lambda/proton yields and proton mean pT (baryon
# annihilation and normalization tensions; see plot_excluded_baryons.py).
PRIMARY_DROP = ("dN_dy_Lambda", "dN_dy_proton", "mean_pT_proton")

SYSTEMS = {
    "AuAu200": dict(
        matrix=os.path.join(DESIGN_DIR, "model_matrix_AuAu200.npz"),
        profile="baseline",
        data_dir=str(ROOT / "data/AuAu200/STAR"),
        out_dir="plots_AuAu200", prefix="auau", norm="norm200",
        data_spec=DATA_SPEC_AUAU, fixed=FIXED_PARAMS_AUAU, primary_drop=PRIMARY_DROP,
        feeddown_npz=os.path.join(DESIGN_DIR, "feeddown_yields.npz"),
        feeddown_corr=FEEDDOWN_CORR_AUAU),
    "OO5360": dict(
        matrix=os.path.join(DESIGN_DIR, "model_matrix_OO5360.npz"),  # an OO_PROFILE matrix
        profile=OO_PROFILE,                     # ("baseline"-profile O-O matrices are rejected)
        data_dir=str(ROOT / "data/OO5360/centrality"),
        out_dir="plots_OO5360", prefix="oo", norm="norm5360",
        data_spec=DATA_SPEC_OO5360, primary_drop=("mean_pT_K0S_vs_mult",),
        fixed={},   # all 14 free (the current primary convention). The Au-Au 8-D fixings were
                    # justified by Au-Au identifiability (e.g. k inert) and do NOT carry over:
                    # multiplicity fluctuations (k) matter more in a small system.
        feeddown_npz=os.path.join(DESIGN_DIR, "feeddown_yields_OO5360.npz"),
        feeddown_corr=FEEDDOWN_CORR_AUAU),     # same SMASH Sigma0 omission; data Lambda includes Sigma0
}
DEFAULT_SYSTEM = "AuAu200"
_SYSTEM_KEYS = {"matrix", "profile", "data_dir", "out_dir", "prefix", "norm", "data_spec", "fixed",
                "primary_drop", "feeddown_npz", "feeddown_corr"}


def system_config(name):
    """Validate and return a system adapter without changing the active system."""
    if name not in SYSTEMS:
        raise KeyError(f"unknown system {name!r}; known: {sorted(SYSTEMS)}")
    cfg = SYSTEMS[name]
    if set(cfg) != _SYSTEM_KEYS:
        raise ValueError(f"SYSTEMS[{name!r}] keys: missing {sorted(_SYSTEM_KEYS - set(cfg))}, "
                         f"unknown {sorted(set(cfg) - _SYSTEM_KEYS)}")
    if cfg["norm"] not in NORM_PARAMS:
        raise ValueError(f"SYSTEMS[{name!r}] norm {cfg['norm']!r} not in {NORM_PARAMS}")
    names = [s["name"] for s in cfg["data_spec"]]
    if len(set(names)) != len(names):
        raise ValueError(f"SYSTEMS[{name!r}] data_spec has duplicate names")
    return cfg


def use_system(name):
    """Install SYSTEMS[name] as the active system (module globals). Call BEFORE overriding any
    global such as I.FIXED_PARAMS -- this resets them to the system's defaults."""
    global SYSTEM, MATRIX_NPZ, PROFILE, DATA_DIR, OUT_DIR, OUT_PREFIX, DATA_SPEC, DROP_PARAMS
    global FIXED_PARAMS, FEEDDOWN_NPZ, FEEDDOWN_CORR, SYSTEM_DROP
    cfg = system_config(name)
    SYSTEM = name
    MATRIX_NPZ, DATA_DIR, OUT_DIR, OUT_PREFIX = cfg["matrix"], cfg["data_dir"], cfg["out_dir"], cfg["prefix"]
    PROFILE = cfg["profile"]
    DATA_SPEC = list(cfg["data_spec"])
    DROP_PARAMS = set(NORM_PARAMS) - {cfg["norm"]}
    FIXED_PARAMS = dict(cfg["fixed"])
    SYSTEM_DROP = tuple(cfg["primary_drop"])
    FEEDDOWN_NPZ, FEEDDOWN_CORR = cfg["feeddown_npz"], dict(cfg["feeddown_corr"])


def check_system_meta(npz_path, system=None, profile=None):
    """Hard-error unless the sibling <stem>.meta.json exists and its "system" is the active system.
    Guards against scoring one system's model (or feed-down) against another's data: the matrices
    share design indices, so a mismatch would otherwise run silently. With `profile` (model matrices
    only, not feed-down inventories), its "analysis_profile" (missing = "baseline") must equal it.
    Returns the parsed meta."""
    import json
    system = SYSTEM if system is None else system
    meta = os.path.splitext(npz_path)[0] + ".meta.json"
    if not os.path.exists(meta):
        raise FileNotFoundError(f"{meta} missing: cannot verify {os.path.basename(npz_path)} "
                                f"belongs to system {system}")
    with open(meta) as f:
        m = json.load(f)
    if m.get("system") != system:
        raise ValueError(f"{os.path.basename(npz_path)} is for system {m.get('system')!r}, "
                         f"active system is {system!r}")
    if profile is not None and m.get("analysis_profile", "baseline") != profile:
        raise ValueError(f"{os.path.basename(npz_path)} has analysis_profile "
                         f"{m.get('analysis_profile', 'baseline')!r}; system {system} requires {profile!r} "
                         f"(the same labels mean different observables across profiles)")
    return m


use_system(DEFAULT_SYSTEM)

# Emulator / sampler knobs
PCA_VAR_THRESHOLD = 0.99
NPC_MAX           = 15
GP_ALPHA          = 1e-1
GP_NRESTARTS      = 2
EMU_VAR_CALIBRATE = True   # scale the emulator predictive std by the pooled k-fold closure z-std,
                           # recalibrating coverage to z_std~1 (the raw GP under-covers ~1.3x, which
                           # otherwise sharpens the likelihood and worsens railing + MCMC mixing)
NWALKERS          = 128
NBURN             = 5000
NSTEPS            = 40000   # zeta sector is weakly identified + correlated; needs the long run to mix
SAMPLER_A         = 1.5     # stretch-move scale (lower => smaller steps, higher acceptance)
SAMPLER_P_DE      = 0.7     # fraction of ensemble updates using a differential-evolution move
                            # (DE proposes along inter-walker difference vectors -> traverses
                            #  correlated/degenerate ridges the affine stretch mixes slowly; 0 = pure stretch)
SEED              = 12345
PLOT_THIN_N       = 40000  # corner-plot subsample (visual only; .npz keeps all samples)
CORNER_DPI        = 300    # corner density panels are rasterized; text/axes stay vector
MAX_CORNER_IN     = 22.0   # cap full-corner figure side (in) so the rasterized canvas stays modest
PLOT_NPOST        = 3000
PLOT_NPRIOR       = 5000
CORNER_FONT_SCALE = 2.6
# Convergence thresholds (rank-normalized split-Rhat; Vehtari et al. 2021). Two-tier verdict:
#   hard-fail (<<): chain not mixed -> do not trust that marginal.
#   soft (~):       marginal mixing -> medians/CIs usable, tighten with a longer run if you want sub-% Rhat.
RHAT_HARD   = 1.05   # hard-fail above this
RHAT_SOFT   = 1.01   # gold-standard "converged" bar; (RHAT_SOFT, RHAT_HARD] is the soft band
TAILESS_MIN = 400    # hard-fail below this many effective tail samples (unstable error bars)


# ============================================================
# Plot labels / parameter groups (mirrors analysis.py)
# ============================================================
OBS_LABELS = {
    "dN_dy_pion":   r"$dN_{\pi}/dy$",
    "dN_dy_kaon":   r"$dN_{K}/dy$",
    "dN_dy_proton": r"$dN_{p}/dy$",
    "dN_dy_Lambda": r"$dN_{\Lambda}/dy$",
    "dN_dy_Xi":     r"$dN_{\Xi}/dy$",
    "dN_dy_Omega":  r"$dN_{\Omega}/dy$",
    "mean_pT_pion":   r"$\langle p_T \rangle_{\pi}$ [GeV]",
    "mean_pT_kaon":   r"$\langle p_T \rangle_{K}$ [GeV]",
    "mean_pT_proton": r"$\langle p_T \rangle_{p}$ [GeV]",
    "mean_pT_charged": r"$\langle p_T \rangle_{\rm ch}$ [GeV]",
    "mean_pT_charged_pT0": r"$\langle p_T \rangle_{\rm ch}$ ($p_T>0$) [GeV]",
    "dN_dy_K0S_vs_mult": r"$dN_{K^0_S}/dy$", "dN_dy_Xi_vs_mult": r"$dN_{\Xi}/dy$",
    "dN_dy_Omega_vs_mult": r"$dN_{\Omega}/dy$",
    "proxy_dN_dy_pion_vs_mult": r"$dN_{\pi^++\pi^-}/dy$ (assumed proxy)",
    "proxy_dN_dy_kaon_vs_mult": r"$dN_{K^++K^-}/dy$ (assumed proxy)",
    "proxy_dN_dy_proton_vs_mult": r"$dN_{p+\bar p}/dy$ (assumed proxy)",
    "mean_pT_K0S_vs_mult": r"$\langle p_T \rangle_{K^0_S}$ [GeV]",
    "mean_pT_Xi_vs_mult": r"$\langle p_T \rangle_{\Xi}$ [GeV]",
    "mean_pT_Omega_vs_mult": r"$\langle p_T \rangle_{\Omega}$ [GeV]",
    "c2_charged": r"$c_2\{2\}$", "c3_charged": r"$c_3\{2\}$",
    "v2_charged": r"$v_2\{2\}$", "v3_charged": r"$v_3\{2\}$",
    "dNch_deta": r"$dN_{\rm ch}/d\eta$",
    "dET": r"$dE_T/d\eta$ [GeV]",
}
PARAM_LABELS = {
    "norm200": r"$N_{200}$", "norm5360": r"$N_{5360}$", "w": r"$w$", "k": r"$k$",
    "tau_l": r"$\tau_\ell$", "tau_s": r"$\tau_s$",
    "gamma_l_0": r"$\gamma_\ell^0$", "gamma_s_0": r"$\gamma_s^0$",
    "eta_min": r"$(\eta/s)_{\min}$", "eta_low": r"$(\eta/s)_{\rm low}$",
    "eta_high": r"$(\eta/s)_{\rm high}$", "zeta_max": r"$(\zeta/s)_{\max}$",
    "zeta_width": r"$(\zeta/s)_{w}$", "zeta_skew": r"$(\zeta/s)_{\rm skew}$",
    "zeta_T": r"$T_{\zeta}$",
}
PARAM_GROUPS = {   # filtered to active params at plot time; empty groups skipped
    "normalizations": ["norm5360", "norm5020", "norm2760", "norm200"],
    "geometry":       ["w", "k"],
    "equilibration":  ["tau_l", "tau_s", "gamma_l_0", "gamma_s_0"],
    "shear":          ["eta_min", "eta_low", "eta_high"],
    "bulk":           ["zeta_max", "zeta_width", "zeta_skew", "zeta_T"],
}


# ============================================================
# Loading: matrix, design, X
# ============================================================
def load_matrix(path, *, system=None, profile=None):
    check_system_meta(path, system=system, profile=PROFILE if profile is None else profile)
    with np.load(path, allow_pickle=False) as z:
        Y, errors = np.asarray(z["Y"], float), np.asarray(z["Y_err"], float)
        labels, names = z["feature_labels"].astype(str), z["design_name"].astype(str)
        indices = z["design_index"]
    if Y.ndim != 2 or not Y.size or errors.shape != Y.shape:
        raise ValueError("Matrix requires nonempty 2-D Y and matching Y_err.")
    if labels.shape != (Y.shape[1],) or names.shape != (len(Y),) or indices.shape != (len(Y),):
        raise ValueError("Matrix labels, names and design indices must match Y dimensions.")
    if not np.issubdtype(indices.dtype, np.integer):
        raise ValueError("design_index must contain integers (zero-based full-design row IDs).")
    if len(set(labels)) != len(labels) or len(set(indices)) != len(indices):
        raise ValueError("Duplicate feature labels or design indices are ambiguous.")
    if np.isinf(Y).any() or np.isinf(errors).any() or np.any(errors < 0):
        raise ValueError("Matrix contains infinite values or negative standard deviations.")
    return Y, errors, labels.tolist(), indices.astype(int), names.tolist()


def _parse_header(path):
    with open(path) as f:
        for line in f:
            it = line.split()
            if len(it) >= 2 and it[0] == "#" and it[1] == "Parameter":
                return it[2:]
            if not line.startswith("#"):
                return None
    return None


def load_design(path, *, drop_params=None):
    arr = np.loadtxt(path)
    if arr.ndim == 1:
        arr = arr[None, :]
    expected = [p[0] for p in PARAMS_ALL]
    hdr = _parse_header(path)
    if hdr is not None and hdr != expected:
        raise ValueError(f"design header mismatch:\n file:{hdr}\n want:{expected}")
    if arr.shape[1] != len(expected):
        raise ValueError(f"design has {arr.shape[1]} cols, want {len(expected)}")
    if not len(arr) or not np.isfinite(arr).all():
        raise ValueError("Design must contain finite parameter values.")
    drop_params = DROP_PARAMS if drop_params is None else drop_params
    active = [i for i, (nm, _, _) in enumerate(PARAMS_ALL) if nm not in drop_params]
    names = [PARAMS_ALL[i][0] for i in active]
    ranges = np.array([[PARAMS_ALL[i][1], PARAMS_ALL[i][2]] for i in active], float)
    return arr, active, names, ranges


def build_X(design_arr, design_index, active):
    if design_index.min() < 0 or design_index.max() >= design_arr.shape[0]:
        raise ValueError("design_index out of bounds (0-based design_NNN = row NNN)")
    return np.ascontiguousarray(design_arr[design_index][:, active], float)


def resolve_free(pnames, ranges, *, fixed_params=None):
    """Split the active params into free (sampled) and fixed (held at FIXED_PARAMS). Returns
    free_pos (column indices into the active/emulator vector), free_names, free_ranges, and a
    'template' active-length vector holding the fixed values (free slots overwritten at eval)."""
    fixed = FIXED_PARAMS if fixed_params is None else fixed_params
    unknown = set(fixed) - set(pnames)
    if unknown:
        raise ValueError(f"FIXED_PARAMS not among active params: {unknown}")
    for nm, v in fixed.items():
        i = pnames.index(nm)
        if not (ranges[i, 0] <= v <= ranges[i, 1]):
            raise ValueError(f"fixed {nm}={v} outside prior [{ranges[i,0]},{ranges[i,1]}]")
    free_pos = [i for i, nm in enumerate(pnames) if nm not in fixed]
    free_names = [pnames[i] for i in free_pos]
    free_ranges = np.asarray(ranges, float)[free_pos]
    template = np.array([fixed.get(nm, 0.0) for nm in pnames], float)
    return free_pos, free_names, free_ranges, template


def expand_free(theta_free, free_pos, template):
    """(n, n_free) free-param samples -> (n, n_active) full vectors with fixed slots filled from
    `template`. This conditions the full-dimensional emulator on the fixed slice."""
    tf = np.atleast_2d(theta_free)
    full = np.tile(template, (tf.shape[0], 1))
    full[:, free_pos] = tf
    return full


def shear_fixed_params(mode):
    """Fixed shear coordinates for single- or multi-system parameter policies.

    Both modes keep eta_min sampled. Flat viscosity conditions the full emulator
    on zero slopes; no design columns are removed or retrained as noise.
    """
    if mode not in SHEAR_MODES:
        raise ValueError(f"Unknown shear mode {mode!r}; choose from {SHEAR_MODES}")
    return {"eta_low": 0.0, "eta_high": 0.0} if mode == "flat" else {}


def parameter_fixed_params(parameter_mode="all", shear_mode=None, *, system=None):
    """Resolve a parameter slice and shear choice without changing active settings.

    flat-eta is the legacy spelling of all + flat. The system slice may fix
    additional parameters; an explicit flat choice overrides its shear slopes.
    """
    if parameter_mode not in ("all", "system", "flat-eta"):
        raise ValueError(f"Unknown parameter mode {parameter_mode!r}")
    if parameter_mode == "flat-eta" and shear_mode not in (None, "flat"):
        raise ValueError("--parameter-mode flat-eta conflicts with --shear-mode temperature-dependent")
    shear_mode = shear_mode or ("flat" if parameter_mode == "flat-eta" else "temperature-dependent")
    cfg = SYSTEMS[SYSTEM if system is None else system]
    fixed = dict(cfg["fixed"]) if parameter_mode == "system" else {}
    fixed.update(shear_fixed_params(shear_mode))
    return fixed


def configure_parameters(parameter_mode="all", shear_mode=None):
    """Apply the selected parameter policy after use_system()."""
    global FIXED_PARAMS
    FIXED_PARAMS = parameter_fixed_params(parameter_mode, shear_mode)


def selected_shear_mode(fixed_params=None):
    """Derive the physical shear choice from fixed coordinates, including saved fits."""
    fixed = FIXED_PARAMS if fixed_params is None else fixed_params
    return "flat" if all(fixed.get(p) == 0.0 for p in ("eta_low", "eta_high")) else "temperature-dependent"


def param_labels(fixed_params=None, base=None):
    """Plot labels for a parameter slice: in flat-shear fits eta_min is the constant eta/s."""
    labels = dict(PARAM_LABELS if base is None else base)
    if selected_shear_mode(fixed_params) == "flat":
        labels["eta_min"] = r"$\eta/s$"
    return labels


def apply_feeddown(Y, labels, didx, path, corr=None, *, system=None):
    """Add SMASH-omitted weak/e.m. feed-down to model dN_dy columns, in place on a copy of Y.
    For each species in FEEDDOWN_CORR, multiply its dN_dy columns by (1 + parent/reference) per
    (design, centrality), using |eta|<2 inventory multiplicities from `path`. Parent and reference
    share the |eta|<2 window so the acceptance cancels in the ratio. Joins on design_index (the
    feed-down file may hold more designs than the matrix; align on the index, not row order)."""
    corr = FEEDDOWN_CORR if corr is None else corr
    if not corr:
        return Y
    check_system_meta(path, system=system)
    z = read_npz(path)
    fL = [str(x) for x in z["feature_labels"]]
    fY = np.asarray(z["Y"], float)
    findices = z["design_index"]
    if (findices.ndim != 1 or fY.ndim != 2 or fY.shape != (len(findices), len(fL))
            or not np.issubdtype(findices.dtype, np.integer)
            or len(set(findices)) != len(findices) or len(set(fL)) != len(fL)):
        raise ValueError("Feed-down inventory has invalid dimensions or ambiguous labels/design indices.")
    frow = {int(d): i for i, d in enumerate(findices)}
    fcol = {(l.split("__")[0], l.split("__")[1]): j for j, l in enumerate(fL)}
    miss = [int(d) for d in didx if int(d) not in frow]
    if miss:
        raise KeyError(f"{len(miss)} matrix designs absent from {os.path.basename(path)}: {miss[:8]}")
    rows = np.array([frow[int(d)] for d in didx])
    Y = Y.copy(); ncol = 0
    for j, lab in enumerate(labels):
        sp, ce, ob = lab.split("__")
        if ob != "dN_dy" or sp not in corr:
            continue
        ptok, rtok = corr[sp]
        if (ptok, ce) not in fcol or (rtok, ce) not in fcol:
            raise KeyError(f"feed-down file lacks {(ptok, ce)} or {(rtok, ce)} (centrality format mismatch?)")
        den = fY[rows, fcol[(rtok, ce)]]
        ratio = np.where(den > 0, fY[rows, fcol[(ptok, ce)]] / np.where(den > 0, den, 1.0), 0.0)
        Y[:, j] = Y[:, j] * (1.0 + ratio); ncol += 1
    print(f"  feed-down: scaled {ncol} model dN_dy columns [{', '.join(corr)}] by (1+parent/ref) "
          f"from {os.path.basename(path)}")
    return Y


# ============================================================
# Matching model observables (already on STAR-style edges) to data bins
# ============================================================
def build_lookup(Y, Yerr, labels):
    """(species,obs) -> parallel arrays lo[K], hi[K], vals[n,K], errs[n,K], sorted by low edge."""
    tmp = defaultdict(list)
    for j, lab in enumerate(labels):
        sp, ce, ob = lab.split("__")
        lo, hi = (float(x) for x in ce.split("_"))
        tmp[(sp, ob)].append((lo, hi, j))
    val, err, elo, ehi = {}, {}, {}, {}
    for key, lst in tmp.items():
        lst.sort()
        cols = [j for _, _, j in lst]
        elo[key] = np.array([lo for lo, _, _ in lst])
        ehi[key] = np.array([hi for _, hi, _ in lst])
        val[key] = Y[:, cols]
        err[key] = Yerr[:, cols]
    return val, err, elo, ehi


def constituents(edges_lo, edges_hi, lo, hi, tol=1e-6):
    """Indices of the model bins whose union exactly tiles the data bin [lo, hi).
    Returns None when [lo, hi) is not an exact union of whole model bins (a data bin
    finer than the model, or partly/entirely outside the model's centrality range)."""
    idx = [i for i in range(len(edges_lo))
           if edges_lo[i] >= lo - tol and edges_hi[i] <= hi + tol]
    if not idx:
        return None
    covered = float(np.sum(edges_hi[idx] - edges_lo[idx]))
    spans = (abs(min(edges_lo[i] for i in idx) - lo) <= tol and
             abs(max(edges_hi[i] for i in idx) - hi) <= tol)
    if abs(covered - (hi - lo)) > tol or not spans:
        return None
    return idx


def read_cent_file(path):
    raw = np.loadtxt(path, ndmin=2)
    if not len(raw) or raw.shape[1] < 5:
        raise ValueError(f"{path}: expected centrality bounds, midpoint, value and error")
    if not np.isfinite(raw[:, :min(6, raw.shape[1])]).all():
        raise ValueError(f"{path}: non-finite measurement or centrality")
    if (np.any(raw[:, 0] < 0) or np.any(raw[:, 1] > 100)
            or np.any(raw[:, 0] >= raw[:, 1])
            or np.any(raw[1:, 0] < raw[:-1, 1])
            or not np.allclose(raw[:, 2], (raw[:, 0] + raw[:, 1]) / 2, rtol=0, atol=1e-6)):
        raise ValueError(f"{path}: invalid, overlapping or unordered centrality bins/midpoints")
    if np.any(raw[:, 4:min(6, raw.shape[1])] < 0):
        raise ValueError(f"{path}: negative measurement uncertainty")
    lo, hi, y = raw[:, 0], raw[:, 1], raw[:, 3]
    err = np.sqrt(raw[:, 4]**2 + raw[:, 5]**2) if raw.shape[1] >= 6 else raw[:, 4]
    return lo, hi, y, err


def _resolve(path):
    if os.path.exists(path):
        return path
    # tolerate '+'<->'__' filename sanitisation
    alt = path.replace("_+.", "__.") if "_+." in path else path.replace("__.", "_+.")
    if os.path.exists(alt):
        return alt
    raise FileNotFoundError(path)


def read_data_summed(files, data_dir):
    los = his = ysum = e2 = None
    for fn in files:
        lo, hi, y, e = read_cent_file(_resolve(os.path.join(data_dir, fn)))
        if los is None:
            los, his, ysum, e2 = lo, hi, y.copy(), e**2
        else:
            if not (np.allclose(lo, los) and np.allclose(hi, his)):
                raise ValueError(f"centrality bins differ across {files}")
            ysum += y
            e2 += e**2
    return los, his, ysum, np.sqrt(e2)


def _assemble_vs_mult(spec, val, err, elo, ehi, floor, data_dir, cols, errcols, ydata, sdata, lab):
    """kind='vs_mult': data vs <dNch/deta> (no centrality mapping). Per design, interpolate the model
    (species, obs) column -- yield or <pT> -- LINEARLY in that design's own charged dNch_deta across its
    centrality bins, at each data x; the emulator then trains on these interpolated values. Data
    points outside the
    multiplicity range covered by EVERY design are skipped (no extrapolation, no design dropping).
    Data file: `dNch_deta val err` (one file)."""
    if len(spec["files"]) != 1:
        raise ValueError(f"{spec['name']}: kind='vs_mult' takes exactly one file")
    raw = np.loadtxt(_resolve(os.path.join(data_dir, spec["files"][0])), ndmin=2)
    if raw.shape[1] != 3:
        raise ValueError(f"{spec['name']}: expected columns `dNch_deta val err`, got {raw.shape[1]}")
    x, d_y, d_e = raw[:, 0], raw[:, 1], raw[:, 2]
    if (not len(raw) or not np.isfinite(raw).all() or np.any(x <= 0)
            or np.any(np.diff(x) <= 0) or np.any(d_e < 0)):
        raise ValueError(f"{spec['name']}: need finite data, positive strictly increasing "
                         "multiplicity and nonnegative uncertainties")
    key, mkey = (spec["species"], spec["obs"]), ("charged", "dNch_deta")
    for k in (key, mkey):
        if k not in val:
            raise KeyError(f"model lacks {k}")
    if not (np.array_equal(elo[key], elo[mkey]) and np.array_equal(ehi[key], ehi[mkey])):
        raise ValueError(f"{spec['name']}: {key} and {mkey} are on different centrality bins")
    M, V, E = val[mkey], val[key], err[key]
    x_lo, x_hi = M.min(1).max(), M.max(1).min()          # multiplicity range common to all designs
    order = np.argsort(M, axis=1)
    n_before = len(cols)
    for b in range(len(x)):
        if not (x_lo <= x[b] <= x_hi):
            print(f"    [skip] {spec['name']} dNch={x[b]:.4g}: outside the multiplicity range "
                  f"[{x_lo:.4g}, {x_hi:.4g}] covered by every design")
            continue
        m = np.array([np.interp(x[b], M[i, o], V[i, o]) for i, o in enumerate(order)])
        me = np.array([np.interp(x[b], M[i, o], E[i, o]) for i, o in enumerate(order)])
        dval, de = d_y[b], float(np.sqrt(d_e[b]**2 + (floor * d_y[b])**2))
        if not (np.isfinite(dval) and np.isfinite(de) and de > 0):
            raise ValueError(f"{spec['name']} dNch={x[b]:.4g}: data value {dval!r} with error {de!r}")
        cols.append(m); errcols.append(me); ydata.append(dval); sdata.append(de)
        lab.append(f"{spec['name']}__{x[b]:.4g}_{x[b]:.4g}")     # lo=hi=x -> plots place it at x
    if len(cols) == n_before:
        raise ValueError(f"{spec['name']}: no data point inside the model multiplicity range")


def check_flow_fallback(matrix_path, slabels, didx, max_frac=None, *, system=None, profile=None,
                        data_spec=None):
    """OO_PROFILE matrices only: hard-error if the k=1 same-sample flow fallback carries more than
    `max_frac` (default FLOW_FALLBACK_MAX) of the pair weight in any model bin inside a FITTED flow
    data bin, for any design. Reads the per-design, per-bin charged_flow_sample_usage written by the
    matrix aggregation step."""
    max_frac = FLOW_FALLBACK_MAX if max_frac is None else max_frac
    profile = PROFILE if profile is None else profile
    specs = DATA_SPEC if data_spec is None else data_spec
    if profile != OO_PROFILE:
        return
    flow_names = {s["name"] for s in specs if s["kind"] == "flow"}
    fitted = [tuple(float(x) for x in l.split("__")[1].split("_")) for l in slabels
              if l.split("__")[0] in flow_names]
    if not fitted:
        return
    meta = check_system_meta(matrix_path, system=system, profile=profile)
    usage = meta.get("charged_flow_sample_usage")
    if usage is None:
        raise KeyError(f"{os.path.basename(matrix_path)} meta lacks charged_flow_sample_usage "
                       f"(required by {OO_PROFILE}); cannot check the k=1 flow fallback")
    got = sorted(int(d["design_index"]) for d in usage)
    if got != sorted(int(d) for d in didx):
        raise ValueError("charged_flow_sample_usage designs do not match the matrix design_index")
    worst, where, nev = 0.0, None, 0
    for d in usage:
        for b in d["bins"]:
            if not any(lo - 1e-6 <= b["cent_low"] and b["cent_high"] <= hi + 1e-6 for lo, hi in fitted):
                continue
            f = b["same_full_pair_weight_fraction"]
            if f is None:       # no pairs at all: observable analysis should have failed this design
                raise ValueError(f"{d['design_name']} {b['cent_low']:g}-{b['cent_high']:g}%: "
                                 f"no flow pairs in a fitted flow bin")
            nev += int(b["same_full_fallback_events"])
            if float(f) > worst:
                worst, where = float(f), f"{d['design_name']} {b['cent_low']:g}-{b['cent_high']:g}%"
    print(f"  k=1 flow fallback in fitted flow bins: {nev} events, max pair-weight share "
          f"{worst:.3g}{' (' + where + ')' if where else ''}; limit FLOW_FALLBACK_MAX={max_frac:g}")
    if worst > max_frac:
        raise ValueError(f"k=1 same-sample flow fallback share {worst:.3g} at {where} exceeds "
                         f"FLOW_FALLBACK_MAX={max_frac:g} (same-sample nonflow in a fitted flow bin)")


def xlabel_for(name):
    """x-axis label for an observable-group name (multiplicity-space specs end in _vs_mult)."""
    return r"$\langle dN_{\rm ch}/d\eta\rangle$" if name.endswith("_vs_mult") else r"% centrality"


def assemble(Y, Yerr, labels, data_dir, drop_specs=(), *, data_spec=None):
    """Match model observables (already on STAR-style centrality edges) to the STAR/PHENIX
    data bins and build the model design matrix + matched data vector. A data bin spanning
    several model bins is aggregated (yields: width-weighted mean; <pT>: (width*dN_dy)-weighted;
    flow: (width*dNch^2)-weighted mean in cumulant space). Charged flow is calibrated as the
    cumulant c_n{2}: data v_n{2} maps to c = v^2, sig_c = 2 v sqrt(sig_v^2 + (floor v)^2).

    Returns (Ystar, Ystar_e, y_data, sig_data, labels, valid_designs); valid masks designs that
    are NaN-free across all kept observables (c_n{2} is NaN-free, so this now drops ~nothing)."""
    val, err, elo, ehi = build_lookup(Y, Yerr, labels)
    specs = DATA_SPEC if data_spec is None else data_spec
    drop = set(drop_specs)
    unknown = drop - {s["name"] for s in specs}
    if unknown:
        raise ValueError(f"drop_specs not in DATA_SPEC: {sorted(unknown)}")
    assumed = [s["name"] for s in specs
               if s["name"] not in drop and s.get("uncertainty_status") in ("assumed", "mixed")]
    if assumed:
        warnings.warn("Preliminary O-O inputs use assumed uncertainties for some or all points "
                      "in these groups: " + ", ".join(assumed) + ". Other identified errors are "
                      "digitized totals with a reading margin. See data/OO5360.md and the supplied dataset notes.",
                      UserWarning, stacklevel=2)
    cols, errcols, ydata, sdata, lab = [], [], [], [], []
    for spec in specs:
        if spec["name"] in drop:
            continue
        sp, ob, kind = spec["species"], spec["obs"], spec["kind"]
        floor = float(spec.get("sys_floor", 0.0))
        cent_max = float(spec.get("cent_max", np.inf))
        if kind == "vs_mult":
            _assemble_vs_mult(spec, val, err, elo, ehi, floor, data_dir, cols, errcols, ydata, sdata, lab)
            continue
        d_lo, d_hi, d_y, d_e = read_data_summed(spec["files"], data_dir)
        if (sp, ob) not in elo:
            raise KeyError(f"model lacks {(sp, ob)}")
        yob = YIELD_OBS.get(sp, "dN_dy")
        if kind == "meanpt" and (sp, yob) not in elo:
            raise KeyError(f"kind='meanpt' needs {(sp, yob)} for the particle-count weight")
        if kind == "flow" and ("charged", "dNch_deta") not in elo:
            raise KeyError("kind='flow' needs (charged, dNch_deta) for the multiplicity weight")
        n_before = len(cols)
        for b in range(len(d_lo)):
            if d_hi[b] > cent_max + 1e-6:                          # resolution cut (flow)
                continue
            idx = constituents(elo[(sp, ob)], ehi[(sp, ob)], d_lo[b], d_hi[b])
            if idx is None:                                        # data bin not tiled by model bins
                print(f"    [skip] {spec['name']} {d_lo[b]:g}-{d_hi[b]:g}%: "
                      f"not an exact union of model bins (finer than / outside the model grid)")
                continue
            width = ehi[(sp, ob)][idx] - elo[(sp, ob)][idx]        # model-bin widths ~ event count
            if kind == "yield":
                w = width
                m = (val[(sp, ob)][:, idx] * w).sum(1) / w.sum()
                me = np.sqrt(((w * err[(sp, ob)][:, idx])**2).sum(1)) / w.sum()
                dval, de = d_y[b], float(np.sqrt(d_e[b]**2 + (floor * d_y[b])**2))
            elif kind == "meanpt":
                w = width * val[(sp, yob)][:, idx]                 # particles ~ events * yield
                W = w.sum(1, keepdims=True)
                m = (val[(sp, ob)][:, idx] * w).sum(1) / W[:, 0]
                me = np.sqrt(((w / W)**2 * err[(sp, ob)][:, idx]**2).sum(1))
                dval, de = d_y[b], float(np.sqrt(d_e[b]**2 + (floor * d_y[b])**2))
            elif kind == "flow":                                   # cumulant space: model c_n_gap, data v->c
                w = width * val[("charged", "dNch_deta")][:, idx]**2   # pair weight ~ events * M^2
                W = w.sum(1, keepdims=True)
                m = (val[(sp, ob)][:, idx] * w).sum(1) / W[:, 0]
                me = np.sqrt(((w / W)**2 * err[(sp, ob)][:, idx]**2).sum(1))
                v, sv = d_y[b], d_e[b]
                dval = v * v                                       # c = v^2 (exact central-value map)
                de = float(2.0 * v * np.sqrt(sv**2 + (floor * v)**2))  # delta method + v-space floor
            else:
                raise ValueError(f"unknown kind {kind!r} for {spec['name']}")
            if not (np.isfinite(dval) and np.isfinite(de) and de > 0):   # zero error -> infinite weight
                raise ValueError(f"{spec['name']} {d_lo[b]:g}-{d_hi[b]:g}%: data value {dval!r} with "
                                 f"error {de!r} (after sys_floor={floor:g}); a zero error usually means "
                                 f"digitized without errors -- supply them or set a sys_floor")
            cols.append(m); errcols.append(me)
            ydata.append(dval); sdata.append(de)
            lab.append(f"{spec['name']}__{d_lo[b]:g}_{d_hi[b]:g}")
        if len(cols) == n_before:                                  # every bin skipped -> fail, don't vanish
            raise ValueError(f"{spec['name']}: no data bin matches the model centrality grid "
                             f"(drop it via drop_specs, or re-bin the matrix)")
    if not cols:
        raise ValueError("No observables matched: all specs were excluded or unavailable.")
    Ystar = np.column_stack(cols)
    valid = ~np.isnan(Ystar).any(axis=1)   # designs NaN-free across all kept observables
    return (Ystar, np.column_stack(errcols), np.array(ydata), np.array(sdata), lab, valid)


# ============================================================
# PCA + GP emulator (STAT-equivalent)
# ============================================================
class PCAGPEmulator:
    def __init__(self, X, Y, npc=None, var_threshold=PCA_VAR_THRESHOLD,
                 npc_max=NPC_MAX, alpha=GP_ALPHA, nrestarts=GP_NRESTARTS, seed=SEED,
                 var_inflation=1.0):
        self.X = np.asarray(X, float); Y = np.asarray(Y, float)
        self.nobs = Y.shape[1]
        self.var_inflation = np.asarray(var_inflation, float)   # closure-calibrated predictive-std
                                                                  # scale: scalar (global) or (nobs,)
                                                                  # (per-observable); broadcasts either way
        self.scaler = StandardScaler(copy=True)
        self.pca = PCA(whiten=True, svd_solver="full")
        Z_all = self.pca.fit_transform(self.scaler.fit_transform(Y))
        if npc is None:
            npc = int(np.searchsorted(np.cumsum(self.pca.explained_variance_ratio_), var_threshold) + 1)
        self.npc = int(max(1, min(npc, npc_max, Z_all.shape[1])))
        Z = Z_all[:, :self.npc]
        ptp = self.X.max(0) - self.X.min(0); ptp = np.where(ptp > 0, ptp, 1.0)
        # Matern nu=1.5 (not RBF): closure-tested 2026-09 -- RBF's infinite smoothness under-reports
        # predictive uncertainty (pooled z-std 1.118, i.e. mildly overconfident) across every
        # observable; nu=1.5 is the best-calibrated point (z-std 1.040) with flat RMSE/spread vs
        # RBF. nu=0.5 overshoots into underconfidence AND degrades RMSE/spread on every channel
        # (overfits per-design MC noise) -- 1.5 is not an arbitrary choice, it's the tested optimum.
        kernel = (ConstantKernel(1.0, (1e-3, 1e3))
                  * Matern(length_scale=ptp, length_scale_bounds=np.outer(ptp, (0.4, 1e2)), nu=1.5)
                  + WhiteKernel(0.1, (1e-2, 1e2)))
        self.gps = [GPR(kernel=kernel, alpha=alpha, n_restarts_optimizer=nrestarts,
                        normalize_y=False, random_state=seed).fit(self.X, z) for z in Z.T]
        trans = (self.pca.components_ * np.sqrt(self.pca.explained_variance_[:, None]) * self.scaler.scale_)
        self._mean = self.scaler.mean_; self._trans = trans
        A = trans[:self.npc]
        self._var_trans = np.einsum("ki,kj->kij", A, A).reshape(self.npc, self.nobs**2)
        B = trans[self.npc:]
        self._cov_trunc = B.T @ B
        self._cov_trunc.flat[::self.nobs + 1] += 1e-4 * self.scaler.var_

    def predict(self, Xs, return_std=False):
        Xs = np.atleast_2d(np.asarray(Xs, float))
        outs = [gp.predict(Xs, return_std=return_std) for gp in self.gps]
        if return_std:
            mpc = np.column_stack([o[0] for o in outs]); spc = np.column_stack([o[1] for o in outs])
        else:
            mpc = np.column_stack(outs)
        Y = mpc @ self._trans[:self.npc] + self._mean
        if not return_std:
            return Y
        var = (spc**2) @ self._var_trans
        var = var.reshape(Xs.shape[0], self.nobs, self.nobs) + self._cov_trunc
        return Y, self.var_inflation * np.sqrt(np.clip(np.einsum("nii->ni", var), 0.0, None))


# ============================================================
# Closure
# ============================================================
def kfold_splits(n, n_splits=8, seed=SEED):
    """(train, held-out) row positions of the closure folds; shared so held-out order is defined once."""
    return list(KFold(n_splits=n_splits, shuffle=True, random_state=seed).split(np.arange(n)))


def kfold_closure(X, Y, n_splits=8, seed=SEED):
    t, p, s = [], [], []
    for tr, te in kfold_splits(len(X), n_splits, seed):
        emu = PCAGPEmulator(X[tr], Y[tr])
        m, sd = emu.predict(X[te], return_std=True)
        t.append(Y[te]); p.append(m); s.append(sd)
    return np.vstack(t), np.vstack(p), np.vstack(s)


def closure_report(t, p, s, labels, prefix, make_plots=True):
    r = p - t; z = r / np.where(s > 0, s, np.nan)
    name_of = np.array([l.split("__")[0] for l in labels])
    print("    %-16s %9s %8s %8s %8s" % ("observable", "RMSE", "RMSE/sd", "z_mean", "z_std"))
    for nm in sorted(set(name_of)):
        c = np.where(name_of == nm)[0]
        rr = r[:, c].ravel(); zz = z[:, c].ravel(); zz = zz[np.isfinite(zz)]
        sp = t[:, c].std()
        print("    %-16s %9.4g %8.3f %8.3f %8.3f"
              % (nm, np.sqrt(np.mean(rr**2)), np.sqrt(np.mean(rr**2))/sp if sp else np.nan,
                 np.mean(zz), np.std(zz)))
    if not make_plots:
        return
    fig, ax = plt.subplots(1, 2, figsize=(9, 4))
    ax[0].scatter(t.ravel(), p.ravel(), s=4, alpha=0.3)
    lo, hi = t.min(), t.max(); ax[0].plot([lo, hi], [lo, hi], "k--", lw=1)
    ax[0].set_xlabel("true"); ax[0].set_ylabel("emulated"); ax[0].set_title("closure")
    zz = z.ravel(); zz = zz[np.isfinite(zz)]
    ax[1].hist(zz, bins=40, density=True, alpha=0.6)
    xs = np.linspace(-4, 4, 200); ax[1].plot(xs, np.exp(-xs**2/2)/np.sqrt(2*np.pi), "k-")
    ax[1].set_title(f"z mean={zz.mean():.2f} std={zz.std():.2f}")
    fig.tight_layout(); fig.savefig(f"{prefix}_closure.pdf"); plt.close(fig)


def emulator_settings():
    """Serializable PCA/GP constructor settings for saved-likelihood reconstruction."""
    return dict(var_threshold=PCA_VAR_THRESHOLD, npc_max=NPC_MAX, alpha=GP_ALPHA,
                nrestarts=GP_NRESTARTS, seed=SEED)


def fit_emulator(inputs, prefix, *, n_splits=8, make_plots=True):
    """Shared closure, uncertainty calibration and full-grid training for each system."""
    X, Ystar, slabels, pref = inputs.X, inputs.Y, inputs.labels, str(prefix)
    print("== closure (k-fold, real design) ==")
    t, p, s = kfold_closure(X, Ystar, n_splits=n_splits, seed=SEED)
    rows = np.concatenate([te for _, te in kfold_splits(len(X), n_splits, SEED)])   # stacked row order
    closure_report(t, p, s, slabels, pref, make_plots=make_plots)
    lam = np.ones(t.shape[1])
    if EMU_VAR_CALIBRATE:
        zc = (p - t) / np.where(s > 0, s, np.nan)
        lam = np.nanstd(zc, axis=0)
        bad = ~np.isfinite(lam)
        if bad.any():
            print(f"  WARNING: {int(bad.sum())} observable(s) had a non-finite closure z-std "
                  f"(all-NaN column?) -- falling back to no inflation (1.0) for those: "
                  f"{[slabels[j] for j in np.where(bad)[0]]}")
        lam = np.where(bad, 1.0, lam)
        lam = np.maximum(lam, 1.0)   # floor: only correct documented OVER-confidence, never deflate
                                      # a column's raw GP uncertainty below what it reported
        print(f"  emulator variance recalibration: per-observable closure z-std, "
              f"range [{lam.min():.3f}, {lam.max():.3f}], median {np.median(lam):.3f} "
              f"-> scaling each observable's predictive std independently (honest per-channel coverage)")

    print("== train emulator (full design) ==")
    emu = PCAGPEmulator(X, Ystar, var_inflation=lam)
    print(f"  npc={emu.npc} explains "
          f"{np.cumsum(emu.pca.explained_variance_ratio_)[emu.npc-1]:.4f}"
          f"  (var_inflation range [{emu.var_inflation.min():.3f}, {emu.var_inflation.max():.3f}])")
    if make_plots:
        pc_spectrum(emu, f"{pref}_PC_spectrum.pdf")

    return emu, dict(truth=t, predicted=p, std=s, var_inflation=lam, rows=rows)


# ============================================================
# Likelihood + Goodman-Weare ensemble sampler
# ============================================================
def gaussian_measurement_loglike(sig_data, cov_data=None):
    """Prepare Gaussian likelihood, retaining optional correlated proxy/anchor block.

    Emulator uncertainties keep the existing diagonal convention. Only channels
    with off-diagonal input covariance require a Cholesky factorization.
    """
    sig_data = np.asarray(sig_data, float)
    if not np.all(np.isfinite(sig_data) & (sig_data > 0)):
        raise ValueError("Need finite positive measurement errors")
    var_exp = sig_data**2
    correlated = np.array([], dtype=int)
    block = None
    if cov_data is not None:
        covariance = np.asarray(cov_data, float)
        if (covariance.shape != (len(sig_data), len(sig_data)) or not np.isfinite(covariance).all()
                or not np.allclose(covariance, covariance.T, rtol=1e-10, atol=1e-12)
                or not np.allclose(covariance.diagonal(), var_exp, rtol=1e-8, atol=1e-12)):
            raise ValueError("Measurement covariance must be symmetric with diagonal sig_data**2")
        if np.linalg.eigvalsh(covariance).min() < -1e-10 * max(var_exp.max(), 1.):
            raise ValueError("Measurement covariance is not positive semidefinite")
        off = covariance - np.diag(covariance.diagonal())
        correlated = np.flatnonzero(np.any(off != 0, axis=1))
        block = covariance[np.ix_(correlated, correlated)]
    independent = np.setdiff1d(np.arange(len(sig_data)), correlated)

    def evaluate(residual, std):
        variance = std**2 + var_exp
        result = -.5*np.sum(residual[:, independent]**2/variance[:, independent]
                            + np.log(2*np.pi*variance[:, independent]), axis=1)
        if len(correlated):
            cov = np.broadcast_to(block, (len(residual), *block.shape)).copy()
            ii = np.arange(len(correlated))
            cov[:, ii, ii] += std[:, correlated]**2
            chol = np.linalg.cholesky(cov)
            whitened = np.linalg.solve(chol, residual[:, correlated, None])[:, :, 0]
            result -= .5*(np.sum(whitened**2, axis=1)
                          + 2*np.log(chol[:, ii, ii]).sum(axis=1)
                          + len(correlated)*np.log(2*np.pi))
        return result
    return evaluate


def make_logprob(emu, y_data, sig_data, ranges, *, cov_data=None):
    lo, hi = ranges[:, 0], ranges[:, 1]
    gaussian = gaussian_measurement_loglike(sig_data, cov_data)

    def logprob_batch(Theta):
        Theta = np.atleast_2d(Theta)
        out = np.full(Theta.shape[0], -np.inf)
        inside = np.all((Theta > lo) & (Theta < hi), axis=1)
        if inside.any():
            mean, std = emu.predict(Theta[inside], return_std=True)
            res = mean - y_data
            out[inside] = gaussian(res, std)
        return out
    return logprob_batch


def ensemble_sample(logprob_batch, p0, nsteps, a=2.0, p_de=0.0, rng=None):
    """Red-black ensemble sampler mixing two affine-/translation-symmetric moves:

      stretch (Goodman-Weare): cand = po[j] + z*(pa - po[j]), z ~ g(z), acc gains (nd-1)log z.
      differential-evolution (ter Braak / emcee DEMove): cand = pa + gamma*(po[r1]-po[r2]) + eps,
        a *symmetric* proposal (plain Metropolis accept) whose step direction is the difference of
        two complementary-half walkers -> it aligns with correlated/degenerate ridges that the
        stretch move traverses slowly. gamma0 = 2.38/sqrt(2 nd); gamma=1 on ~10% of DE proposals
        for occasional long jumps. eps is a tiny isotropic jitter (keeps the move irreducible).

    Each (step, half) picks one move by Bernoulli(p_de). Proposing the active half only from the
    frozen complementary half preserves detailed balance for both moves. p_de=0 -> pure stretch."""
    rng = np.random.default_rng(rng)
    nw, nd = p0.shape
    half = nw // 2
    pos = p0.copy()
    lp = logprob_batch(pos)
    chain = np.empty((nsteps, nw, nd))
    de_gamma0 = 2.38 / np.sqrt(2.0 * nd)
    for t in range(nsteps):
        for s in (0, 1):
            a_sl = slice(0, half) if s == 0 else slice(half, nw)
            o_sl = slice(half, nw) if s == 0 else slice(0, half)
            pa, po = pos[a_sl], pos[o_sl]
            na, no = pa.shape[0], po.shape[0]
            if p_de > 0.0 and rng.random() < p_de:   # differential-evolution move
                r1 = rng.integers(no, size=na)
                r2 = rng.integers(no, size=na)
                same = r1 == r2                       # require two distinct donors
                while same.any():
                    r2[same] = rng.integers(no, size=int(same.sum())); same = r1 == r2
                gamma = np.full(na, de_gamma0)
                gamma[rng.random(na) < 0.1] = 1.0
                cand = pa + gamma[:, None] * (po[r1] - po[r2]) + 1e-5 * rng.standard_normal((na, nd))
                lc = logprob_batch(cand)
                logacc = lc - lp[a_sl]                # symmetric proposal
            else:                                     # stretch move
                zz = ((a - 1.0) * rng.random(na) + 1.0)**2 / a
                j = rng.integers(no, size=na)
                cand = po[j] + zz[:, None] * (pa - po[j])
                lc = logprob_batch(cand)
                logacc = (nd - 1) * np.log(zz) + lc - lp[a_sl]
            acc = np.log(rng.random(na)) < logacc
            pa[acc] = cand[acc]
            lpa = lp[a_sl].copy(); lpa[acc] = lc[acc]
            pos[a_sl] = pa; lp[a_sl] = lpa
        chain[t] = pos
    return chain

def make_logprob_u(emu, y_data, sig_data, ranges, free_pos, template, *, cov_data=None):
    """Log-posterior in unbounded logit space over the FREE params: theta = lo + (hi-lo)*sigmoid(u).
    Fixed params are inserted (via free_pos/template) before the emulator call, so the full-D
    emulator is CONDITIONED on the fixed slice rather than retrained on dropped columns. `ranges`
    are the free-param ranges. The uniform prior's walls map to +-inf (no out-of-bounds proposal);
    the sum-log term is the Jacobian of the logit map under a uniform prior."""
    lo, hi = ranges[:, 0], ranges[:, 1]; w = hi - lo
    gaussian = gaussian_measurement_loglike(sig_data, cov_data)
    def logprob_u(U):
        U = np.atleast_2d(U)
        p = 1.0 / (1.0 + np.exp(-U)); theta_free = lo + w * p
        mean, std = emu.predict(expand_free(theta_free, free_pos, template), return_std=True)
        ll = gaussian(mean - y_data, std)
        jac = np.sum(np.log(np.clip(p * (1.0 - p), 1e-300, None)), axis=1)
        return ll + jac
    return logprob_u


def theta_from_u(U, ranges):
    lo, hi = ranges[:, 0], ranges[:, 1]
    return lo + (hi - lo) * (1.0 / (1.0 + np.exp(-U)))

def _split_chains(x):
    """x: (n, m) per-walker chains -> (n//2, 2m) split chains (Vehtari split-Rhat)."""
    n = x.shape[0] // 2
    return np.concatenate([x[:n], x[n:2*n]], axis=1)


def _rhat(x):
    """Potential scale reduction on (n, m) split chains (m>=2)."""
    n, m = x.shape
    cm = x.mean(0)
    B = n * cm.var(ddof=1)
    W = x.var(0, ddof=1).mean()
    if W <= 0:
        return 1.0
    return float(np.sqrt(((n - 1) / n * W + B / n) / W))


def _ess(x):
    """Effective sample size on (n, m) split chains via the Vehtari combined autocorrelation
    (within-chain autocov + between-chain variance) truncated by Geyer's initial monotone rule."""
    n, m = x.shape
    if n < 8:
        return float(n * m)
    xc = x - x.mean(0, keepdims=True)
    nf = 1
    while nf < 2 * n:
        nf *= 2
    f = np.fft.rfft(xc, n=nf, axis=0)
    acov = np.fft.irfft(f * np.conj(f), n=nf, axis=0)[:n] / n   # (n, m) biased autocov per chain
    W = acov[0].mean()
    if W <= 0:
        return float(n * m)
    B = n * x.mean(0).var(ddof=1) if m > 1 else 0.0
    var_plus = (n - 1) / n * W + B / n
    rho = 1.0 - (W - acov.mean(1)) / var_plus
    rho[0] = 1.0
    tau, t = 1.0, 1
    while t + 1 < n:                                            # Geyer initial monotone sequence
        p = rho[t] + rho[t + 1]
        if p <= 0:
            break
        tau += 2.0 * p; t += 2
    return float(n * m / max(tau, 1e-3))


def _rank_normal(x):
    """Rank-normalize a full sample array to N(0,1) scores (for rank-Rhat / bulk-ESS)."""
    from scipy.stats import rankdata, norm
    r = rankdata(x).reshape(x.shape)
    return norm.ppf((r - 0.5) / x.size)


def mcmc_diagnostics(chain_post, pnames):
    """chain_post: (nsteps, nwalkers, ndim) post-burn samples. Prints a convergence verdict.

    Reports the modern Vehtari et al. (2021) diagnostics, robust to non-Gaussian / flat marginals
    (which the old median-drift test false-alarmed on): rank-normalized split-Rhat, bulk-ESS
    (rank-normalized), and tail-ESS (min over the 5%/95% quantile indicators). Two-tier verdict:
    hard-fail (<<) on rank-Rhat>RHAT_HARD or tail-ESS<TAILESS_MIN; soft-flag (~) in the
    (RHAT_SOFT, RHAT_HARD] band. tau/N is kept as a mixing-speed footnote."""
    NS, NW, ND = chain_post.shape
    moved = np.any(np.diff(chain_post, axis=0) != 0, axis=2)
    print("\n== MCMC diagnostics ==")
    print(f"  post-burn: {NS} steps x {NW} walkers = {NS*NW} samples")
    print(f"  acceptance: {moved.mean():.3f}   (want >~0.2)")
    print(f"  {'param':12s} {'rank-Rhat':>9s} {'bulkESS':>8s} {'tailESS':>8s} "
          f"{'N/tau':>7s} {'median':>9s} {'verdict':>8s}")
    bad = False; marg = False
    rows = []
    for i, p in enumerate(pnames):
        x = chain_post[:, :, i]                       # (NS, NW)
        xs = _split_chains(x)
        rr = _rhat(_rank_normal(xs))                  # rank-normalized split-Rhat
        be = _ess(_rank_normal(xs))                   # bulk-ESS
        q05, q95 = np.percentile(x, [5, 95])          # tail-ESS: min over the two quantile indicators
        te = min(_ess(_split_chains((x <= q05).astype(float))),
                 _ess(_split_chains((x >= q95).astype(float))))
        bulk_ess_raw = _ess(xs)                        # for an N/tau mixing-speed read
        ntau = bulk_ess_raw / NW
        hard = not np.isfinite(rr) or not np.isfinite(te) or (rr > RHAT_HARD) or (te < TAILESS_MIN)
        soft = (not hard) and (rr > RHAT_SOFT)
        bad = bad or hard; marg = marg or soft
        tag = "  <<" if hard else ("  ~" if soft else "")
        print(f"  {p:12s} {rr:9.3f} {be:8.0f} {te:8.0f} {ntau:7.1f} "
              f"{np.median(x):9.3f}{tag}")
        rows.append(dict(parameter=p, rank_rhat=float(rr) if np.isfinite(rr) else None,
                         bulk_ess=float(be) if np.isfinite(be) else None,
                         tail_ess=float(te) if np.isfinite(te) else None, hard_fail=bool(hard)))
    if bad:
        v = (f"NOT converged (rows <<: rank-Rhat>{RHAT_HARD} or tail-ESS<{TAILESS_MIN} "
             f"-> resample those directions, or they are non-identified by the data)")
    elif marg:
        v = (f"converged for inference (medians/CIs usable); rows ~ are marginal "
             f"({RHAT_SOFT}<rank-Rhat<={RHAT_HARD}) -> run longer for sub-% Rhat")
    else:
        v = f"converged (rank-Rhat<={RHAT_SOFT} and tail-ESS>={TAILESS_MIN} throughout)"
    print("  VERDICT:", v)
    return dict(converged=not bool(bad), marginal=bool(marg), verdict=v, parameters=rows)


# ============================================================
# Plots (set + style mirror analysis.py)
# ============================================================
def _obs_groups(labels):
    g = defaultdict(list)
    for j, l in enumerate(labels):
        nm, ce = l.split("__")
        g[nm].append((0.5 * sum(float(x) for x in ce.split("_")), j))
    return {nm: sorted(v) for nm, v in g.items()}


def _obs_grid(names, max_cols=2):
    nc = min(max_cols, len(names)); nr = int(np.ceil(len(names) / nc))
    fig, axes = plt.subplots(nr, nc, figsize=(7 * nc, 4.5 * nr), squeeze=False)
    return fig, axes.ravel()


def plot_observable_bands_grid(pred, labels, y_data, sig_data, path, alpha_band=0.01):
    """Each row of `pred` (shape (nsamp, N)) drawn as a thin line vs data."""
    groups = _obs_groups(labels); names = list(groups)
    fig, axes = _obs_grid(names)
    for ax, nm in zip(axes, names):
        xs = np.array([c for c, _ in groups[nm]]); cols = [j for _, j in groups[nm]]
        for row in pred[:, cols]:
            ax.plot(xs, row, "b-", alpha=alpha_band)
        ax.errorbar(xs, y_data[cols], yerr=sig_data[cols], fmt="ro", label="Measurements")
        ax.set_title(nm); ax.set_xlabel(xlabel_for(nm))
        ax.set_ylabel(OBS_LABELS.get(nm, nm)); ax.legend(loc="best")
    for ax in axes[len(names):]:
        ax.axis("off")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def trace_plot(chain, pnames, path, nburn, labels=None):
    labels = param_labels() if labels is None else labels
    nsteps, nw, nd = chain.shape
    thin = max(1, nsteps // 400); alpha = min(1.0, 20.0 / nw)
    xs = np.arange(0, nsteps, thin)
    fig, axes = plt.subplots(nd, 1, figsize=(12, 1.8 * nd), squeeze=False)
    axes = axes.ravel()
    for i, ax in enumerate(axes):
        ax.plot(xs, chain[::thin, :, i], lw=0.4, alpha=alpha)
        ax.axvline(nburn, color="k", ls="--", lw=0.7)
        ax.set_ylabel(labels.get(pnames[i], pnames[i]))
    axes[-1].set_xlabel("Step")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


def pc_spectrum(emu, path):
    evr = emu.pca.explained_variance_ratio_; cum = np.cumsum(evr)
    fig, ax = plt.subplots()
    ax.plot(np.arange(1, evr.size + 1), cum, marker="o", ms=3)
    ax.axhline(PCA_VAR_THRESHOLD, ls="--"); ax.axvline(emu.npc, ls="--")
    ax.set_xlabel("PC index"); ax.set_ylabel("Cumulative explained variance")
    ax.set_ylim(0, 1.01); ax.set_title(f"npc={emu.npc}, cum var={cum[emu.npc-1]:.4f}")
    fig.tight_layout(); fig.savefig(path); plt.close(fig)


# ---- STAT-style corner (ported from analysis.py) ----
def format_ci(samples_1d, fmt=".2f"):
    q16, q50, q84 = np.percentile(samples_1d, [16, 50, 84])
    return rf"{q50:{fmt}}$^{{+{q84-q50:{fmt}}}}_{{-{q50-q16:{fmt}}}}$"


def set_tight(fig, pad=.05, h_pad=.1, w_pad=.1, rect=(0., 0., 1., 1.)):
    fig.tight_layout(pad=pad, h_pad=h_pad, w_pad=w_pad, rect=rect)


def get_param_subset(samples, labels, ranges, selected_labels):
    labels = list(labels)
    idx = [labels.index(name) for name in selected_labels]
    return (np.asarray(samples)[:, idx], [labels[i] for i in idx],
            np.asarray(ranges, dtype=float)[idx])


_FONT_S, _FONT_N, _FONT_L, _OFFBLACK = 6, 8, 10, "#262626"
POSTERIOR_RC = {
    "mathtext.fontset": "custom", "mathtext.default": "it",
    "font.size": _FONT_N, "legend.fontsize": _FONT_N, "axes.labelsize": _FONT_N,
    "axes.titlesize": _FONT_L, "xtick.labelsize": _FONT_S, "ytick.labelsize": _FONT_S,
    "font.weight": 400, "axes.labelweight": 400, "axes.titleweight": 400,
    "lines.linewidth": .5, "lines.markersize": 3, "lines.markeredgewidth": 0,
    "patch.linewidth": .5, "axes.linewidth": .4,
    "xtick.major.width": .4, "ytick.major.width": .4,
    "xtick.minor.width": .4, "ytick.minor.width": .4,
    "xtick.major.size": 1.2, "ytick.major.size": 1.2,
    "xtick.minor.size": .8, "ytick.minor.size": .8,
    "xtick.major.pad": 1.5, "ytick.major.pad": 1.5,
    "axes.formatter.limits": (-5, 5), "axes.spines.top": False,
    "axes.spines.right": False, "axes.labelpad": 3,
    "text.color": _OFFBLACK, "axes.edgecolor": _OFFBLACK, "axes.labelcolor": _OFFBLACK,
    "xtick.color": _OFFBLACK, "ytick.color": _OFFBLACK,
    "legend.numpoints": 1, "legend.scatterpoints": 1, "legend.frameon": False,
    "image.cmap": "Blues", "image.interpolation": "none", "pdf.fonttype": 42,
    "figure.facecolor": "white", "axes.facecolor": "white",
    "savefig.facecolor": "white", "axes.grid": False,
}


def posterior_stat_style_localrc(samples, labels, ranges, outpath, scale=1, padr=.99,
                                 padt=.98, cmap_name=None, fullheight=270/72.27,
                                 rc_update=None, hist_bins=30, hist2d_bins=35, ci_fmt=".2f",
                                 dpi=192):
    samples = np.asarray(samples, dtype=float)
    ranges = np.asarray(ranges, dtype=float)
    ndim = samples.shape[1]
    if samples.shape[1] != len(labels):
        raise ValueError("samples.shape[1] must match len(labels)")
    if ranges.shape != (ndim, 2):
        raise ValueError(f"ranges must be ({ndim}, 2), got {ranges.shape}")
    cmap_name = cmap_name or rc_update.get("image.cmap", "Blues") if rc_update else "Blues"
    rc_update = rc_update or {}

    with plt.rc_context(rc=rc_update):
        cmap = plt.get_cmap(cmap_name).copy(); cmap.set_bad("white")
        line_color = cmap(0.8); fill_color = cmap(0.5, alpha=0.1)
        fig, axes = plt.subplots(nrows=ndim, ncols=ndim, sharex="col", sharey="row",
                                 figsize=(2*scale*fullheight*ndim/3, 2*scale*fullheight*ndim/3),
                                 squeeze=False)
        for i, ax in enumerate(np.diag(axes)):
            lim = (ranges[i, 0], ranges[i, 1]); s = samples[:, i]
            counts, edges = np.histogram(s, bins=hist_bins, range=lim)
            x = 0.5 * (edges[1:] + edges[:-1])
            if counts.max() > 0 and len(x) >= 2:
                y = 0.85 * (lim[1] - lim[0]) * (counts / counts.max()) + lim[0]
                interp = PchipInterpolator(x, y)
                xf = np.linspace(x[0], x[-1], 10 * x.size); yf = interp(xf)
                ax.plot(xf, yf, lw=0.5, color=line_color)
                ax.fill_between(xf, lim[0], yf, color=fill_color, zorder=-10)
            ax.set_xlim(lim); ax.set_ylim(lim)
            ax.annotate(format_ci(s, fmt=ci_fmt), (0.62, 0.92), xycoords="axes fraction",
                        ha="center", va="bottom")
        for ny, nx in zip(*np.tril_indices_from(axes, k=-1)):
            *_, qm = axes[ny, nx].hist2d(samples[:, nx], samples[:, ny], bins=hist2d_bins,
                                         range=(ranges[nx], ranges[ny]), cmap=cmap, cmin=1)
            qm.set_rasterized(True)
            axes[nx, ny].set_axis_off()
        for i in range(ndim):
            for j in range(ndim):
                axes[i, j].spines["top"].set_visible(False)
                axes[i, j].spines["right"].set_visible(False)
        for label, axb in zip(labels, axes[-1]):
            axb.set_xlabel(label.replace(r"\ [", "$\n$["))
        for label, axl in zip(labels, axes[:, 0]):
            axl.set_ylabel(label.replace(r"\ [", "$\n$["))
        for i in range(ndim):
            for j in range(ndim):
                ax = axes[i, j]
                if not ax.axison:
                    continue
                ax.xaxis.set_major_locator(ticker.LinearLocator(3))
                ax.yaxis.set_major_locator(ticker.LinearLocator(3))
        for axb in axes[-1]:
            xt = axb.get_xticklabels()
            if len(xt) >= 2:
                xt[0].set_horizontalalignment("left"); xt[-1].set_horizontalalignment("right")
        for axl in axes[:, 0]:
            yt = axl.get_yticklabels()
            if len(yt) >= 2:
                yt[0].set_verticalalignment("bottom"); yt[-1].set_verticalalignment("top")
        set_tight(fig, rect=[0., 0., padr, padt])
        fig.savefig(outpath, dpi=dpi); plt.close(fig)


def corner_full_scale(nd):
    """Figure scale of the full nd-param corner: <1 beyond ~8 params, capping the side at MAX_CORNER_IN
    (the rasterized canvas; uncapped 14-param at CORNER_DPI exhausts memory). Fonts do not shrink with it."""
    return min(1.0, MAX_CORNER_IN * 3.0 / (2.0 * (270 / 72.27) * nd))


def make_corner_plots(samp, pnames, ranges, prefix, full_rc=None, rc=None, param_labels=None):
    """Full/group corners with optional local styling; never mutate the active fit's labels/RC."""
    rc = POSTERIOR_RC if rc is None else rc
    param_labels = PARAM_LABELS if param_labels is None else param_labels
    lab = [param_labels.get(p, p) for p in pnames]
    nd = len(pnames)
    posterior_stat_style_localrc(samp, labels=lab, ranges=ranges,
                                 outpath=f"{prefix}_posterior_full.pdf",
                                 rc_update=rc if full_rc is None else full_rc,
                                 dpi=CORNER_DPI, scale=corner_full_scale(nd))
    for gname, sel in PARAM_GROUPS.items():
        sel = [p for p in sel if p in pnames]
        if len(sel) < 2:                      # 1-param group -> no 2D panel; skip
            continue
        ss, sl, sr = get_param_subset(samp, pnames, ranges, sel)
        posterior_stat_style_localrc(ss, labels=[param_labels.get(p, p) for p in sl],
                                     ranges=sr, outpath=f"{prefix}_posterior_{gname}.pdf",
                                     rc_update=rc, dpi=CORNER_DPI)


# ============================================================
# Main
# ============================================================


def split_specs(value):
    """Parse comma-separated observable names, allowing surrounding whitespace."""
    return tuple(part.strip() for part in value.split(",") if part.strip())


def read_npz(path):
    """Load array-only results and close the archive immediately."""
    with np.load(path, allow_pickle=False) as archive:
        return {name: archive[name] for name in archive.files}


def enable_oo_yield_proxies():
    """Explicit opt-in; the primary system registry is never modified."""
    if SYSTEM != "OO5360":
        raise ValueError("--oo-yield-proxies is valid only for OO5360")
    from build_oo_yield_proxies import GROUPS
    if any(spec.get("assumed_proxy") for spec in DATA_SPEC):
        return
    for name, species in zip(GROUPS[1:], ("pi_tot", "K_tot", "p_tot")):
        DATA_SPEC.append(dict(name=name, species=species, obs="dN_dy", kind="vs_mult",
                              sys_floor=.05, uncertainty_status="proxy", assumed_proxy=True,
                              files=[f"../optional/{name}.dat"]))


def proxy_measurement_covariance(labels, measured, sigmas, data_dir, *, data_spec=None):
    """Join the selected submatrix of the reproducible proxy/anchor covariance.

    Marginal DAT errors already enter sigmas. Add only off-diagonal terms so the
    normal per-observable model discrepancy floor remains independent.
    """
    specs = DATA_SPEC if data_spec is None else data_spec
    selected = {spec["name"]: spec for spec in specs
                if any(label.startswith(spec["name"] + "__") for label in labels)}
    if not any(spec.get("assumed_proxy") for spec in selected.values()):
        return None
    from build_oo_yield_proxies import build
    root = Path(data_dir).resolve().parent
    metadata = json.loads((root / "optional/yield_proxy_metadata.json").read_text())
    widths = metadata["assumed_fractional_widths"]
    _, _, archive = build(root, check=True, transfer_kaon=widths["kaon_ratio_transfer"],
                          transfer_proton=widths["proton_ratio_transfer"],
                          isospin=widths["charged_neutral_kaon_isospin"])
    index = {str(label): i for i, label in enumerate(archive["labels"])}
    target, source = [], []
    for i, label in enumerate(labels):
        spec = selected[label.split("__")[0]]
        if spec.get("assumed_proxy") and label not in index:
            raise ValueError(f"Proxy covariance lacks selected point {label}")
        if label not in index:
            continue
        j = index[label]
        expected_error = np.sqrt(archive["covariance"][j,j]
                                 + (spec.get("sys_floor", 0)*archive["values"][j])**2)
        if (not np.isclose(measured[i], archive["values"][j], rtol=1e-8, atol=1e-10)
                or not np.isclose(sigmas[i], expected_error, rtol=1e-8, atol=1e-10)):
            raise ValueError(f"Proxy/anchor value or marginal error differs for {label}")
        target.append(i); source.append(j)
    covariance = np.diag(sigmas**2)
    subset = archive["covariance"][np.ix_(source, source)].copy()
    np.fill_diagonal(subset, 0.)
    covariance[np.ix_(target, target)] += subset
    warnings.warn("OPTIONAL ASSUMED light-hadron yield proxies selected: correlated with K0S "
                  "anchors and each other; projected error bars are not experimental uncertainties. "
                  "See optional/yield_proxy_metadata.json for all assumed widths.", UserWarning, stacklevel=2)
    return covariance


def input_settings(system=None, *, data_spec=None, fixed_params=None):
    """Resolve explicit settings for independent system contexts; legacy callers use globals."""
    from types import SimpleNamespace
    if system is None:
        values = dict(system=SYSTEM, matrix=MATRIX_NPZ, profile=PROFILE, data_dir=DATA_DIR,
                      norm=SYSTEMS[SYSTEM]["norm"], data_spec=DATA_SPEC, fixed=FIXED_PARAMS,
                      feeddown_npz=FEEDDOWN_NPZ, feeddown_corr=FEEDDOWN_CORR)
    else:
        values = dict(system_config(system), system=system)
    if data_spec is not None:
        values["data_spec"] = data_spec
    if fixed_params is not None:
        values["fixed"] = fixed_params
    return SimpleNamespace(**values)


def prepare_inputs(matrix_path=None, design_path=None, data_dir=None, drop_specs=(), feeddown=None,
                   *, system=None, data_spec=None, fixed_params=None):
    """One input contract shared by preflight, fitting and diagnostic scripts.

    The assembly's existing NaN-row exclusion is preserved. Infinite assembled
    values, empty grids and invalid measurements fail before emulator training.
    An explicit system uses its registry settings without changing the active
    globals; fixed_params supplies a study's common parameter policy.
    """
    from types import SimpleNamespace
    settings = input_settings(system, data_spec=data_spec, fixed_params=fixed_params)
    matrix_path = settings.matrix if matrix_path is None else matrix_path
    design_path = DESIGN_DAT if design_path is None else design_path
    data_dir = settings.data_dir if data_dir is None else data_dir
    feeddown = settings.feeddown_npz if feeddown is None else feeddown
    Y, errors, labels, indices, names = load_matrix(matrix_path, system=settings.system, profile=settings.profile)
    design, active, pnames, ranges = load_design(design_path, drop_params=set(NORM_PARAMS) - {settings.norm})
    X = build_X(design, indices, active)
    if feeddown:
        Y = apply_feeddown(Y, labels, indices, feeddown, corr=settings.feeddown_corr, system=settings.system)
    grid, grid_errors, measured, sigmas, selected, valid = assemble(
        Y, errors, labels, data_dir, drop_specs=drop_specs, data_spec=settings.data_spec)
    check_flow_fallback(matrix_path, selected, indices, system=settings.system, profile=settings.profile,
                        data_spec=settings.data_spec)
    if not valid.any() or not grid.shape[1] or not np.isfinite(grid[valid]).all():
        raise ValueError("No usable finite assembled model grid.")
    if not np.isfinite(measured).all() or not np.all(np.isfinite(sigmas) & (sigmas > 0)):
        raise ValueError("Measurements must be finite with positive finite errors.")
    resolve_free(pnames, ranges, fixed_params=settings.fixed)
    covariance = proxy_measurement_covariance(selected, measured, sigmas, data_dir, data_spec=settings.data_spec)
    return SimpleNamespace(X=X[valid], Y=grid[valid], errors=grid_errors[valid],
                           measured=measured, sigmas=sigmas, labels=selected,
                           covariance=covariance,
                           pnames=pnames, ranges=ranges, valid=valid,
                           indices=indices[valid], names=np.asarray(names)[valid],
                           raw_shape=Y.shape)


def file_identity(path):
    path = Path(path).expanduser().resolve()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"path": str(path), "sha256": digest.hexdigest()}


def physical_input_files(config):
    """Identities required for plot replay; source-code hashes remain provenance.

    Proxy generation is checked separately against its saved tables, evidence
    and complete covariance archive by build(check=True) during assembly.
    Rendering-only edits to that generator must not invalidate identical data.
    Fit/cache reuse still compares the complete configuration, including code.
    """
    return [(entry["role"], entry["sha256"]) for entry in config["files"]
            if entry["role"] not in ("implementation", "proxy_implementation")]


def input_config(matrix_path=None, design_path=None, data_dir=None, drop_specs=(), feeddown=None,
                 *, system=None, data_spec=None, fixed_params=None):
    """Content-based provenance; changing an input or its metadata invalidates a cache."""
    settings = input_settings(system, data_spec=data_spec, fixed_params=fixed_params)
    matrix = Path(settings.matrix if matrix_path is None else matrix_path).expanduser().resolve()
    design = Path(DESIGN_DAT if design_path is None else design_path).expanduser().resolve()
    data = Path(settings.data_dir if data_dir is None else data_dir).expanduser().resolve()
    feeddown = settings.feeddown_npz if feeddown is None else feeddown
    files = {"matrix": matrix, "matrix_metadata": matrix.with_suffix(".meta.json"),
             "design": design, "implementation": Path(__file__).resolve()}
    for spec in settings.data_spec:
        if spec["name"] not in drop_specs:
            for filename in spec["files"]:
                files["data/" + filename] = Path(_resolve(str(data / filename))).resolve()
    proxy_enabled = any(spec.get("assumed_proxy") and spec["name"] not in drop_specs for spec in settings.data_spec)
    if proxy_enabled:
        for relative in ("optional/projection_ratios.json", "optional/yield_proxy_metadata.json",
                         "optional/yield_proxy_covariance.npz", "sources/ALI-SIMUL-480342.pdf",
                         "sources/ALI-SIMUL-480342.png", "derived/dN_dy_K0S_vs_mult.dat",
                         "uncertainties/dN_dy_K0S_vs_mult.json"):
            files["proxy/"+relative] = data.parent / relative
        files["proxy_implementation"] = ROOT / "build_oo_yield_proxies.py"
    if feeddown:
        inventory = Path(feeddown).expanduser().resolve()
        files.update(feeddown=inventory, feeddown_metadata=inventory.with_suffix(".meta.json"))
    import scipy
    import sklearn
    return dict(schema=1, system=settings.system, profile=settings.profile, matrix=str(matrix), design=str(design),
                data_dir=str(data), feeddown=str(Path(feeddown).expanduser().resolve()) if feeddown else "",
                drop_specs=sorted(set(drop_specs)), fixed_params=dict(settings.fixed),
                shear_mode=selected_shear_mode(settings.fixed),
                oo_yield_proxies_enabled=proxy_enabled,
                libraries=dict(numpy=np.__version__, scipy=scipy.__version__, sklearn=sklearn.__version__),
                files=[dict(role=role, **file_identity(path)) for role, path in sorted(files.items())])


def config_json(config):
    return json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False)


def require_cache_config(result, config):
    if "config_json" not in result or str(result["config_json"]) != config_json(config):
        raise ValueError("Cached result has missing or different input/settings provenance. "
                         "Use a new output directory or explicitly recompute (--refit where available).")


def result_scope(result):
    """Comparable configuration for a collection of per-row recovery results."""
    if "config_json" not in result:
        return None  # All-legacy collections remain readable, but cannot verify their provenance.
    config = json.loads(str(result["config_json"]))
    return config_json({key: value for key, value in config.items() if key not in ("row", "design_index")})


def restore_posterior(path):
    """Restore the fixed slice from a saved posterior, including 14-D and flat-eta runs."""
    global FIXED_PARAMS
    result = read_npz(path)
    if "fixed_names" not in result or "fixed_values" not in result:
        raise ValueError("Posterior has no fixed-parameter metadata; cannot reconstruct its parameter slice.")
    names = result["fixed_names"].astype(str).tolist()
    values = result["fixed_values"]
    if len(names) != len(values) or len(set(names)) != len(names):
        raise ValueError("Invalid fixed-parameter metadata in posterior.")
    FIXED_PARAMS = dict(zip(names, values.tolist()))
    active = [p for p in PARAMS_ALL if p[0] not in DROP_PARAMS]
    _, free, ranges, _ = resolve_free([p[0] for p in active], np.array([p[1:] for p in active]))
    if result["param_names"].astype(str).tolist() != free or not np.array_equal(result["ranges"], ranges):
        raise ValueError("Posterior parameter names/ranges do not match the selected system and fixed slice.")
    samples = result["samples"]
    if samples.ndim != 2 or samples.shape[1] != len(free) or not samples.size or not np.isfinite(samples).all():
        raise ValueError("Posterior samples must be nonempty and finite, with one column per free parameter.")
    return result


def posterior_inputs(run_dir, system=DEFAULT_SYSTEM, matrix=None, design=None, data_dir=None,
                     drop_specs=None):
    """Replay a fit's inputs and fixed slice for predictive plots; verify input contents.

    For legacy posteriors without provenance, supply the matching input paths.
    """
    global MATRIX_NPZ, DESIGN_DAT, DATA_DIR
    use_system(system)
    post = restore_posterior(Path(run_dir) / f"{OUT_PREFIX}_posterior.npz")
    cfg = json.loads(str(post["config_json"])) if "config_json" in post else None
    if cfg is not None:
        if cfg["system"] != system or cfg["fixed_params"] != FIXED_PARAMS:
            raise ValueError("Posterior configuration does not match its system/fixed parameters.")
        if cfg.get("oo_yield_proxies_enabled", False):
            enable_oo_yield_proxies()
        MATRIX_NPZ = matrix or cfg["matrix"]
        DESIGN_DAT = design or cfg["design"]
        DATA_DIR = data_dir or cfg["data_dir"]
        drop = tuple(cfg["drop_specs"]) if drop_specs is None else drop_specs
        current = input_config(drop_specs=drop, feeddown=cfg["feeddown"])
        # Plot code can evolve; physical inputs and the selected observable set must agree.
        if physical_input_files(current) != physical_input_files(cfg) or sorted(drop) != cfg["drop_specs"]:
            raise ValueError("Plot inputs differ from the saved fit. Use matching files and exclusions.")
        feeddown = cfg["feeddown"]
    else:
        print("WARNING: legacy posterior has no input provenance; verify matrix/data/exclusions.")
        MATRIX_NPZ = matrix or MATRIX_NPZ
        DESIGN_DAT = design or DESIGN_DAT
        DATA_DIR = data_dir or DATA_DIR
        drop = (PRIMARY_DROP if system == "AuAu200" else ()) if drop_specs is None else drop_specs
        feeddown = ""
    return post, tuple(drop), feeddown


def add_input_arguments(parser, *, system=True, feeddown=True, drops=True, proxies=False):
    if system:
        parser.add_argument("--system", default=DEFAULT_SYSTEM, choices=sorted(SYSTEMS))
    parser.add_argument("--matrix", help="Matrix NPZ with same-stem .meta.json (default: selected system).")
    parser.add_argument("--design", default=DESIGN_DAT, help="Full design table; indices are zero-based rows.")
    parser.add_argument("--data-dir", help="Measurement directory (default: selected system).")
    if proxies:
        parser.add_argument("--oo-yield-proxies", action="store_true",
                            help="Opt into ASSUMED pi/K/p proxies anchored to O-O K0S, using their joint "
                                 "covariance. Disabled by default; OO5360 only.")
    if drops:
        parser.add_argument("--drop-specs", default=None,
                            help="Comma-separated observable spec names to exclude (default: the system's "
                                 "primary exclusions; pass '' to keep every observable).")
    if feeddown:
        parser.add_argument("--feeddown", nargs="?", const="system", default=None, metavar="PATH",
                            help="Apply Sigma feed-down from an inventory NPZ (no PATH: the system's default "
                                 "inventory). Off by default.")


def drop_selection(args):
    """Observable exclusions from --drop-specs; None selects the system's primary set."""
    return SYSTEM_DROP if args.drop_specs is None else split_specs(args.drop_specs)


def feeddown_selection(args):
    """prepare_inputs feed-down argument: "" disables, None selects the system inventory."""
    return "" if args.feeddown is None else (None if args.feeddown == "system" else args.feeddown)


def configure_inputs(args, *, system=None):
    use_system(system or getattr(args, "system", DEFAULT_SYSTEM))
    global MATRIX_NPZ, DESIGN_DAT, DATA_DIR
    MATRIX_NPZ = args.matrix or MATRIX_NPZ
    DESIGN_DAT = args.design
    DATA_DIR = args.data_dir or DATA_DIR
    if getattr(args, "oo_yield_proxies", False):
        enable_oo_yield_proxies()


def add_parameter_arguments(parser, *, mode="all"):
    """Shared parameter policy for fitting, preflight and suite launchers."""
    parser.add_argument("--parameter-mode", choices=("all", "system", "flat-eta"), default=mode,
                        help="all: every active parameter sampled before applying the shear choice; "
                             "system: the system's fixed slice (AuAu200 fixes six parameters); "
                             "flat-eta: legacy shorthand for all + --shear-mode flat")
    parser.add_argument("--shear-mode", choices=SHEAR_MODES, default=None,
                        help="temperature-dependent: use the parameter mode's shear slopes (default); "
                             "flat: fix eta_low = eta_high = 0 and sample eta_min as constant eta/s. "
                             "The flat-eta parameter mode defaults to flat.")


def validate_run_sizes(nsteps, nburn, nwalkers, nsplits, ndesign=None):
    if nsteps < 4 or nburn < 0 or nwalkers < 4 or nwalkers % 2:
        raise ValueError("Require nsteps >= 4, nburn >= 0, and an even nwalkers >= 4.")
    if nsplits < 2 or (ndesign is not None and nsplits > ndesign):
        raise ValueError("Require 2 <= nsplits <= number of usable designs.")


def add_plot_arguments(parser):
    """Shared default plotting controls for fit launchers and the O-O suite."""
    parser.add_argument("--no-plots", action="store_true", help="Skip all figures; save settings and posterior.")
    parser.add_argument("--plot-npost", type=int, default=PLOT_NPOST, help="Posterior draws for observable figures (capped at saved samples).")
    parser.add_argument("--plot-nprior", type=int, default=PLOT_NPRIOR, help="Prior draws for observable figures.")
    parser.add_argument("--corner-font-scale", type=float, default=CORNER_FONT_SCALE,
                    help="Font multiplier for corner plots.")


def main(matrix_path=None, design_path=None, data_dir=None,
         out_dir=None, emulator_only=False, nsteps=NSTEPS, drop_specs=(), feeddown=None,
         make_plots=True, n_splits=8, nburn=NBURN, nwalkers=NWALKERS, seed=SEED,
         plot_npost=PLOT_NPOST, plot_nprior=PLOT_NPRIOR, corner_font_scale=CORNER_FONT_SCALE):
    """Run on the ACTIVE system (use_system). None -> that system's default; feeddown="" disables."""
    matrix_path = MATRIX_NPZ if matrix_path is None else matrix_path
    design_path = DESIGN_DAT if design_path is None else design_path
    data_dir = DATA_DIR if data_dir is None else data_dir
    out_dir = OUT_DIR if out_dir is None else out_dir
    feeddown = FEEDDOWN_NPZ if feeddown is None else feeddown
    validate_run_sizes(nsteps, nburn, nwalkers, n_splits)
    if (any(not isinstance(value, (int, np.integer)) or value < 1 for value in (plot_npost, plot_nprior))
            or not np.isfinite(corner_font_scale) or corner_font_scale <= 0):
        raise ValueError("Plot draw counts must be positive integers; corner font scale must be positive and finite.")
    pref = os.path.join(out_dir, OUT_PREFIX)
    print(f"  system : {SYSTEM} (profile {PROFILE})\n  matrix : {matrix_path}\n  design : {design_path}\n"
          f"  data   : {data_dir}\n  out    : {out_dir}\n  shear  : {selected_shear_mode()}")

    inputs = prepare_inputs(matrix_path, design_path, data_dir, drop_specs, feeddown)
    X, Ystar, pnames, ranges = inputs.X, inputs.Y, inputs.pnames, inputs.ranges
    y_data, sig_data, slabels = inputs.measured, inputs.sigmas, inputs.labels
    print(f"  excluded by assembly: {int((~inputs.valid).sum())} designs")
    print(f"  {Ystar.shape[1]} observables; {Ystar.shape[0]} designs; data vector {y_data.shape}")
    validate_run_sizes(nsteps, nburn, nwalkers, n_splits, len(X))
    config = input_config(matrix_path, design_path, data_dir, drop_specs, feeddown)
    config.update(nsteps=nsteps, nburn=nburn, nwalkers=nwalkers, seed=seed, nsplits=n_splits,
                  emulator_settings=emulator_settings(),
                  emulator_only=emulator_only, usable_design_indices=inputs.indices.tolist(),
                  plotting=dict(enabled=make_plots, npost=plot_npost,
                                nprior=plot_nprior, corner_font_scale=corner_font_scale, seed=0))
    os.makedirs(out_dir, exist_ok=True)
    Path(f"{pref}_run.json").write_text(json.dumps(config, indent=2) + "\n")

    emu, _ = fit_emulator(inputs, pref, n_splits=n_splits, make_plots=make_plots)
    config["emulator_variance_inflation"] = emu.var_inflation.tolist()
    Path(f"{pref}_run.json").write_text(json.dumps(config, indent=2) + "\n")

    if emulator_only:
        print(f"  emulator validation complete; run settings: {pref}_run.json"); return

    print("== sample posterior ==")
    rng = np.random.default_rng(seed)
    free_pos, free_names, free_ranges, template = resolve_free(pnames, ranges)
    nfix = len(pnames) - len(free_names)
    if nfix:
        fixed_str = ", ".join(f"{nm}={FIXED_PARAMS[nm]:g}" for nm in pnames if nm in FIXED_PARAMS)
        print(f"  {len(free_names)} free params sampled; {nfix} fixed (emulator conditioned): {fixed_str}")
    lo, hi = free_ranges[:, 0], free_ranges[:, 1]
    # init walkers in the central half, in unbounded logit space
    th0 = rng.uniform(lo + 0.25*(hi-lo), hi - 0.25*(hi-lo), size=(nwalkers, len(free_names)))
    u0 = np.log((th0 - lo) / (hi - th0))
    chain_u = ensemble_sample(make_logprob_u(emu, y_data, sig_data, free_ranges, free_pos, template,
                                            cov_data=getattr(inputs, "covariance", None)),
                              u0, nburn + nsteps, a=SAMPLER_A, p_de=SAMPLER_P_DE, rng=rng)
    chain = theta_from_u(chain_u, free_ranges)   # back to theta for diagnostics/plots/everything below
    del chain_u                                  # same size as chain; not needed past this point
    samp = chain[nburn:].reshape(-1, len(free_names))
    print(f"  drew {samp.shape[0]} post-burn samples")
    diagnostics = mcmc_diagnostics(chain[nburn:], free_names)
    Path(f"{pref}_diagnostics.json").write_text(json.dumps(diagnostics, indent=2, allow_nan=False) + "\n")

    print("\n  posterior (median +68%):")
    q = np.percentile(samp, [16, 50, 84], axis=0)
    for i, nm in enumerate(free_names):
        width = (free_ranges[i, 1] - free_ranges[i, 0])
        constrained = (q[2, i] - q[0, i]) / width
        flag = "  <- ~prior" if constrained > 0.6 else ""
        print(f"    {nm:11s} {q[1,i]:8.3f}  +{q[2,i]-q[1,i]:.3f}/-{q[1,i]-q[0,i]:.3f}{flag}")

    # save samples first, so the posterior is on disk before the memory-heavy plots
    fixed_names = np.array([nm for nm in pnames if nm in FIXED_PARAMS])
    fixed_values = np.array([FIXED_PARAMS[nm] for nm in pnames if nm in FIXED_PARAMS], float)
    np.savez(f"{pref}_posterior.npz", samples=samp, param_names=np.array(free_names),
             ranges=free_ranges, fixed_names=fixed_names, fixed_values=fixed_values,
             config_json=np.array(config_json(config)))
    if not make_plots:
        print(f"  wrote {pref}_posterior.npz (make_plots=False -> skipping plots)"); return

    print("== plots ==")
    import gc
    # design ensemble (actual model outputs) vs data — the gating view
    plot_observable_bands_grid(Ystar, slabels, y_data, sig_data,
                               f"{pref}_PredictedDesign.pdf", alpha_band=0.05)
    from make_flow_plots import plot_from_fit
    plot_from_fit(emu, samp, inputs, pref, engine=sys.modules[__name__],
                  npost=plot_npost, nprior=plot_nprior)
    # trace + corner (full + parameter groups), STAT style
    trace_plot(chain, free_names, f"{pref}_MCMCSamples.pdf", nburn)
    del chain; gc.collect()
    from regen_corners import plot_from_samples
    plot_from_samples(samp, free_names, free_ranges, pref, font_scale=corner_font_scale,
                      engine=sys.modules[__name__])
    print(f"\n  wrote to {out_dir}/:")
    print("    closure, PC_spectrum, PredictedDesign, MCMCSamples, posterior_full + posterior_<group>,")
    print("    PosteriorSummary/PriorSummary_{yields,ptflow}")


def selftest():
    print("== SELFTEST ==")
    rng = np.random.default_rng(0)
    n, p, m = 87, 6, 60
    Xs = rng.uniform(0, 1, (n, p)); W = rng.normal(size=(p, 8))
    feats = np.column_stack([np.sin(Xs @ W[:, :4]), (Xs @ W[:, 4:])**2])
    M = rng.normal(size=(8, m)); Yt = feats @ M + 5.0
    Ys = Yt + rng.normal(scale=0.01*np.abs(Yt))
    t, pr, sd = kfold_closure(Xs, Ys)
    z = ((pr - t)/sd).ravel()
    ok = (np.sqrt(np.mean((pr-t)**2))/t.std() < 0.2) and (0.5 < z.std() < 2.0)
    print(f"  RMSE/spread={np.sqrt(np.mean((pr-t)**2))/t.std():.3f}  z std={z.std():.3f} ->",
          "PASS" if ok else "CHECK")
    return ok


def cli(argv=None, *, mode="all", out_dir=None, nsteps=NSTEPS, auau_only=False, require_flat=False):
    """Common fitting CLI, also used by the flat-viscosity launcher.

    Defaults are each system's primary analysis: all active parameters sampled,
    the system's primary observable exclusions, and no feed-down correction.
    require_flat rejects any parameter selection that does not fix both shear slopes at zero.
    """
    ap = argparse.ArgumentParser(description="Standalone emulator and Bayesian inference",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    add_input_arguments(ap, system=not auau_only, proxies=not auau_only)
    add_parameter_arguments(ap, mode=mode)
    ap.add_argument("--out-dir", default=out_dir, help="Output directory (default: selected system).")
    ap.add_argument("--emulator-only", action="store_true", help="Validate/train without sampling.")
    ap.add_argument("--selftest", action="store_true", help="Run synthetic closure without external inputs.")
    ap.add_argument("--nsteps", type=int, default=nsteps, help="Post-burn MCMC steps per walker.")
    ap.add_argument("--nburn", type=int, default=NBURN, help="Burn-in steps per walker.")
    ap.add_argument("--nwalkers", type=int, default=NWALKERS, help="Even number of ensemble walkers.")
    ap.add_argument("--seed", type=int, default=SEED, help="MCMC seed (closure keeps its production seed).")
    add_plot_arguments(ap)
    ap.add_argument("--nsplits", type=int, default=8, help="Closure folds; reductions are development shortcuts.")
    a = ap.parse_args(argv)
    if a.selftest:
        return 0 if selftest() else 1
    try:
        configure_inputs(a, system="AuAu200" if auau_only else None)
        configure_parameters(a.parameter_mode, a.shear_mode)
        if require_flat and selected_shear_mode() != "flat":
            raise ValueError("this launcher requires flat shear (eta_low = eta_high = 0); "
                             "use inference.py for temperature-dependent shear")
        main(matrix_path=MATRIX_NPZ, design_path=DESIGN_DAT, data_dir=DATA_DIR,
             out_dir=a.out_dir, emulator_only=a.emulator_only, nsteps=a.nsteps,
             nburn=a.nburn, nwalkers=a.nwalkers, seed=a.seed, drop_specs=drop_selection(a),
             feeddown=feeddown_selection(a), make_plots=not a.no_plots, n_splits=a.nsplits,
             plot_npost=a.plot_npost, plot_nprior=a.plot_nprior,
             corner_font_scale=a.corner_font_scale)
    except (OSError, ValueError, KeyError, IndexError) as error:
        ap.exit(1, f"Inference failed: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(cli())
