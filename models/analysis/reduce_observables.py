#!/usr/bin/env python3
"""
reduce_observables.py -- consume a per-design artifact (from extract_observables.py) and reduce
it to integrated observables per centrality class, with a hydro-event bootstrap for the
statistical uncertainty. Also provides a round-trip validator against the source cache.

This is the canonical reduction. Centrality classes are defined on the MODEL's own
charged-multiplicity distribution (rank events, cut percentiles). The default weighting is
EQUAL-HYDRO-EVENT:

    observable_ratio = Sum_i ( num_i / nsub_i ) / Sum_i ( den_i / nsub_i )

not all-oversample (Sum_i num_i / Sum_i den_i), which would overweight events that happened to
receive more oversamples. nsub==0 (zero-output) events contribute 0 and rank most peripheral.

Reconstruction (per centrality class C; w_i = 1/nsub_i for equal-event, 1 for all-oversample,
0 for nsub_i==0):
  identified dN/dy (window) = mean_{i in C}( Sum_win yh_N_i / nsub_i ) / (2*y_window)
  charged dN_ch/deta        = mean_{i in C}( charged_N_i[win] / nsub_i ) / (2*eta_window)
  <pT>                      = Sum_i w_i * sum_pT_i / Sum_i w_i * N_i
  c_n (self, pooled/gap)    = Sum_i w_i * num_i / Sum_i w_i * den_i ;  v_n = sqrt(c_n) where >0
  charged v_n{2}            = sqrt(c_n_gap[charged])
  identified v_n{2} (diff)  = d_n / sqrt(c_n_gap[charged]) ,
                              d_n = Sum_i w_i * diff_num_i / Sum_i w_i * diff_den_i

Usage:
  reduce:   python reduce_observables.py reduce  --artifact PREFIX [--estimator cent_ch_eta05]
                  [--bin-edges 0,5,10,...,70] [--y-window 0.5] [--charged-window eta05]
                  [--n-boot 200] [--weighting equal_event|all_oversample] [--json OUT.json]
  validate: python reduce_observables.py validate --artifact PREFIX --cache-dir DIR
                  (recomputes a subset from the cache independently; asserts agreement;
                   prints equal-event vs all-oversample deltas)
"""

import argparse
import json
import os

import numpy as np

import extract_observables as sx   # constants + cache IO helpers


def _json_default(o):
    """Serialize numpy scalars/arrays; raise loudly on anything genuinely non-serializable
    (do NOT silently coerce to null, which would hide bugs)."""
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


# ----------------------------------------------------------------------------------------
# Artifact loading
# ----------------------------------------------------------------------------------------
def load_artifact(prefix):
    z = np.load(prefix + ".npz")
    try:
        out = {k: z[k] for k in z.files}
    finally:
        z.close()
    with open(prefix + ".meta.json") as f:
        meta = json.load(f)
    return out, meta


def window_bin_mask(inner_edges, window):
    """Interior histogram bins whose [lo, hi) lies fully within [-window, window).

    Matches the extractor's eta-mask logic and the v3 searchsorted('right') bin convention.
    """
    edges = np.asarray(inner_edges)
    n_bins = len(edges) + 1
    mask = np.zeros(n_bins, dtype=bool)
    for b in range(1, len(edges) + 1):
        lo = edges[b - 1]
        hi = edges[b] if b < len(edges) else np.inf
        if lo >= -window - 1e-9 and hi <= window + 1e-9:
            mask[b] = True
    return mask


# ----------------------------------------------------------------------------------------
# Centrality
# ----------------------------------------------------------------------------------------
def centrality_classes(cent_col, edges_pct):
    """Return [(lo_pct, hi_pct, idx_array), ...] by ranking events on cent_col (high mult =
    central = low percentile). Zero-multiplicity events (incl zero-events) rank most peripheral.
    """
    order = np.argsort(-cent_col, kind="stable")     # descending multiplicity
    n = len(order)
    out = []
    for lo, hi in zip(edges_pct[:-1], edges_pct[1:]):
        i0 = int(round(n * lo / 100.0))
        i1 = int(round(n * hi / 100.0))
        out.append((lo, hi, order[i0:i1]))
    return out


# ----------------------------------------------------------------------------------------
# Reduction
# ----------------------------------------------------------------------------------------
def _weights(nsub, weighting):
    nz = nsub > 0
    if weighting == "equal_event":
        w = np.zeros_like(nsub, dtype=float)
        w[nz] = 1.0 / nsub[nz]
        return w
    if weighting == "all_oversample":
        return np.where(nz, 1.0, 0.0)
    raise ValueError(weighting)


def _ratio(num, den):
    return float(num / den) if den > 0 else np.nan


def _event_mean(per_ev, n_ev):
    """Mean over all events in the class (zero-events included as 0); NaN for an empty class."""
    return float(per_ev.mean()) if n_ev > 0 else float("nan")


def _strict_root(c):
    return float(np.sqrt(c)) if (np.isfinite(c) and c > 0) else np.nan


# --- diagonal statistical errors (cheap, single-pass; the bootstrap is only for covariance) ---
# A delete-one jackknife over hydro events for
# ratio / v_n observables (the equivalent of se_pair), which for a plain mean coincides with the
# standard error of the mean (se_1d 'sem'). All are O(events): leave-one-out sums are total minus
# one element, so the whole thing is vectorized, no resampling loop.
def _jack_ratio_se(num_i, den_i, root=False, strict=False):
    """Delete-one jackknife SE of R = sum(num_i)/sum(den_i), or of sqrt(R) when root=True.
    num_i/den_i are the per-event weighted contributions (the same ones summed for the point)."""
    num_i = np.asarray(num_i, float); den_i = np.asarray(den_i, float)
    n = num_i.size
    if n < 2:
        return np.nan
    A = num_i.sum(); B = den_i.sum()
    Bj = B - den_i
    with np.errstate(divide="ignore", invalid="ignore"):
        Rj = np.where(Bj > 0, (A - num_i) / Bj, np.nan)
        tj = np.sqrt(np.where(Rj > 0, Rj, np.nan)) if root else Rj
    if strict and not np.all(np.isfinite(tj)):
        return np.nan
    tj = tj[np.isfinite(tj)]
    m = tj.size
    if m < 2:
        return np.nan
    return float(np.sqrt((m - 1.0) / m * np.sum((tj - tj.mean()) ** 2)))


def _jack_diff_se(pnum_i, pden_i, cnum_i, cden_i):
    """Delete-one jackknife SE of the differential v_n = (sum Pnum/sum Pden)/sqrt(sum Cnum/sum Cden),
    leaving out one event from POI and charged-reference sums together (they share the event set)."""
    pnum_i = np.asarray(pnum_i, float); pden_i = np.asarray(pden_i, float)
    cnum_i = np.asarray(cnum_i, float); cden_i = np.asarray(cden_i, float)
    n = pnum_i.size
    if n < 2:
        return np.nan
    Pa, Pb, Ca, Cb = pnum_i.sum(), pden_i.sum(), cnum_i.sum(), cden_i.sum()
    Pbj = Pb - pden_i; Cbj = Cb - cden_i
    with np.errstate(divide="ignore", invalid="ignore"):
        Pj = np.where(Pbj > 0, (Pa - pnum_i) / Pbj, np.nan)
        Cj = np.where(Cbj > 0, (Ca - cnum_i) / Cbj, np.nan)
        tj = np.where(Cj > 0, Pj / np.sqrt(Cj), np.nan)
    tj = tj[np.isfinite(tj)]
    m = tj.size
    if m < 2:
        return np.nan
    return float(np.sqrt((m - 1.0) / m * np.sum((tj - tj.mean()) ** 2)))


def _mean_se(per_ev, n_ev, scale=1.0):
    """SE of an equal-event mean density (zero-events included): std/sqrt(N), times scale."""
    if n_ev < 2:
        return np.nan
    return float(np.nanstd(per_ev, ddof=1) / np.sqrt(n_ev) * scale)


# Charge-combined experimental channels. Formed by SUMMING sufficient statistics of the
# constituents, never by averaging reduced values. K0S and phi have no partner and stay as-is.
COMBOS = {
    "pi_tot": ("pi+", "pi-"),
    "K_tot": ("K+", "K-"),
    "p_tot": ("p", "pbar"),
    "Lambda_tot": ("Lambda", "antiLambda"),
    "Xi_tot": ("Xi-", "antiXi+"),
    "Omega_tot": ("Omega-", "antiOmega+"),
}


def _species_record(winN, winPt, diff_num_h, diff_den_h, self_num_h, self_den_h,
                    *, w, nsub, n_ev, weighting, dy, c_ref, cref_num_h, cref_den_h, harmonics):
    """Build one species' observable dict (each entry a (value, sigma) pair) from its
    (already window-summed) sufficient stats.

    winN/winPt: per-event window-integrated counts / sum_pT. diff_*_h, self_*_h: lists over
    harmonics of per-event numerators/denominators. self_*_h may be None (combined channels:
    the self two-subevent cumulant is NOT formable from per-species self stats because the
    cross terms are not stored; the differential estimator IS formable, being linear in the
    POI Q-vector). Differential flow uses the charged reference cumulant c_ref; cref_num_h/
    cref_den_h are its per-event arrays, needed for the jackknife of the differential v_n."""
    if weighting == "equal_event":
        per_ev = np.zeros(n_ev)
        nz = nsub > 0
        per_ev[nz] = winN[nz] / nsub[nz]
        dN_dy = _event_mean(per_ev, n_ev) / dy
        se_y = _mean_se(per_ev, n_ev, scale=1.0 / dy)
    else:
        dN_dy = _ratio(float(winN.sum()), float(nsub.sum())) / dy
        se_y = _jack_ratio_se(winN, nsub) / dy
    pt_num, pt_den = w * winPt, w * winN
    d = {"dN_dy": (dN_dy, se_y),
         "mean_pT": (_ratio(float(pt_num.sum()), float(pt_den.sum())),
                     _jack_ratio_se(pt_num, pt_den))}
    for hi, n in enumerate(harmonics):
        if self_num_h is not None:
            sn_i, sd_i = w * self_num_h[hi], w * self_den_h[hi]
            d[f"v{n}_2_self_diag"] = (_strict_root(_ratio(float(sn_i.sum()), float(sd_i.sum()))),
                                      _jack_ratio_se(sn_i, sd_i, root=True))
        pn_i, pd_i = w * diff_num_h[hi], w * diff_den_h[hi]
        dn = _ratio(float(pn_i.sum()), float(pd_i.sum()))
        sr = _strict_root(c_ref[n])
        v = float(dn / sr) if (np.isfinite(dn) and np.isfinite(sr) and sr > 0) else np.nan
        se_v = _jack_diff_se(pn_i, pd_i, w * cref_num_h[hi], w * cref_den_h[hi])
        d[f"v{n}_2"] = (v, se_v)
    return d


def reduce_class(out, meta, idx, *, weighting="equal_event", y_window=0.5, charged_window="eta05"):
    """Reduce one centrality class (event indices `idx`) to an observable dict."""
    species_fl = meta["species_flow"]
    species_y = meta["species_yhist"]
    species_sc = meta["species_scalar"]
    harmonics = meta["harmonics"]
    ch_idx = species_fl.index("charged")
    oo_adjusted = sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE

    nsub = out["nsub_used"][idx].astype(float)
    w = _weights(nsub, weighting)
    n_ev = len(idx)

    y_mask = window_bin_mask(meta["y_hist_inner_edges"], y_window)
    dy = 2.0 * y_window
    cw_i = meta["charged_pth_windows"].index("charged_" + charged_window)
    eta_w = {"eta05": 0.5, "eta10": 1.0}[charged_window]
    deta = 2.0 * eta_w

    res = {"_n_events": n_ev, "_weighting": weighting}

    # charged self-cumulant (reference for differential) per harmonic, plus its per-event arrays
    # (kept for the differential v_n jackknife).
    c_ref = {}
    cref_num_h, cref_den_h = [], []
    for hi, n in enumerate(harmonics):
        cn_i = out["flow_gap_num"][idx, ch_idx, hi]
        cd_i = out["flow_gap_den"][idx, ch_idx, hi]
        cref_num_h.append(cn_i); cref_den_h.append(cd_i)
        c_ref[n] = _ratio(float((w * cn_i).sum()), float((w * cd_i).sum()))

    # ---- charged ----
    cN = out["charged_N"][idx, cw_i]
    cpt = out["charged_sum_pT"][idx, cw_i]
    # equal-event yield = mean over events of per-event density; all-oversample = total/total
    if weighting == "equal_event":
        per_ev = np.zeros(n_ev)
        nz = nsub > 0
        per_ev[nz] = cN[nz] / nsub[nz]
        dnch_deta = _event_mean(per_ev, n_ev) / deta
        se_nch = _mean_se(per_ev, n_ev, scale=1.0 / deta)
    else:
        dnch_deta = _ratio(float(cN.sum()), float(nsub.sum())) / deta
        se_nch = _jack_ratio_se(cN, nsub) / deta
    # Only charged mean pT uses the accepted moments; multiplicity above and
    # the charged reference for identified flow retain their original arrays.
    cpt_N = out["charged_mean_pT_N"][idx, cw_i] if oo_adjusted else cN
    if oo_adjusted:
        cpt = out["charged_mean_pT_sum_pT"][idx, cw_i]
    cpt_num, cpt_den = w * cpt, w * cpt_N
    charged = {"dNch_deta": (dnch_deta, se_nch),
               "mean_pT": (_ratio(float(cpt_num.sum()), float(cpt_den.sum())),
                           _jack_ratio_se(cpt_num, cpt_den, strict=oo_adjusted))}
    for hi, n in enumerate(harmonics):
        wn_i, wd_i = w * cref_num_h[hi], w * cref_den_h[hi]
        value, strict = c_ref[n], False
        if oo_adjusted and n in sx.OO_CROSS_HARMONICS:
            chi = meta["charged_cross_full_harmonics"].index(n)
            wn_i = w * out["charged_cross_full_num"][idx, chi]
            wd_i = w * out["charged_cross_full_den"][idx, chi]
            value = _ratio(float(wn_i.sum()), float(wd_i.sum()))
            strict = True
        charged[f"v{n}_2"] = (_strict_root(value), _jack_ratio_se(wn_i, wd_i, root=True, strict=strict))
        # Keep the legacy inference column names and order. In the OO profile
        # c2_gap/c3_gap explicitly alias cross_full; metadata records this.
        charged[f"c{n}_gap"] = (value, _jack_ratio_se(wn_i, wd_i, strict=strict))
        if strict:
            charged[f"c{n}_cross_full"] = charged[f"c{n}_gap"]
    res["charged"] = charged

    # ---- identified (individual cache species) ----
    nh = len(harmonics)
    for sj, sp in enumerate(species_y):
        sci = species_sc.index(sp)
        fsi = species_fl.index(sp)
        winN = out["yh_N"][idx, sj][:, y_mask].sum(axis=1)
        winPt = out["yh_sum_pT"][idx, sj][:, y_mask].sum(axis=1)
        diff_num_h = [out["flow_diff_num"][idx, sci, hi] for hi in range(nh)]
        diff_den_h = [out["flow_diff_den"][idx, sci, hi] for hi in range(nh)]
        self_num_h = [out["flow_gap_num"][idx, fsi, hi] for hi in range(nh)]
        self_den_h = [out["flow_gap_den"][idx, fsi, hi] for hi in range(nh)]
        res[sp] = _species_record(winN, winPt, diff_num_h, diff_den_h, self_num_h, self_den_h,
                                  w=w, nsub=nsub, n_ev=n_ev, weighting=weighting, dy=dy,
                                  c_ref=c_ref, cref_num_h=cref_num_h, cref_den_h=cref_den_h,
                                  harmonics=harmonics)

    # ---- charge-combined channels (sum sufficient stats, then reduce) ----
    for combo, (a, b) in COMBOS.items():
        sja, sjb = species_y.index(a), species_y.index(b)
        sca, scb = species_sc.index(a), species_sc.index(b)
        winN = (out["yh_N"][idx, sja] + out["yh_N"][idx, sjb])[:, y_mask].sum(axis=1)
        winPt = (out["yh_sum_pT"][idx, sja] + out["yh_sum_pT"][idx, sjb])[:, y_mask].sum(axis=1)
        diff_num_h = [out["flow_diff_num"][idx, sca, hi] + out["flow_diff_num"][idx, scb, hi]
                      for hi in range(nh)]
        diff_den_h = [out["flow_diff_den"][idx, sca, hi] + out["flow_diff_den"][idx, scb, hi]
                      for hi in range(nh)]
        # self-cumulant intentionally None: combined self-flow needs unstored cross terms.
        res[combo] = _species_record(winN, winPt, diff_num_h, diff_den_h, None, None,
                                     w=w, nsub=nsub, n_ev=n_ev, weighting=weighting, dy=dy,
                                     c_ref=c_ref, cref_num_h=cref_num_h, cref_den_h=cref_den_h,
                                     harmonics=harmonics)

    # ---- global ET (raw dET/deta per mask; corrected-moment combination deferred) ----
    et_w = float(meta.get("et_central_eta_window", 0.5))
    det = 2.0 * et_w
    et_masks = meta["global_et_masks"]
    fi_tot = meta["global_et_fields"].index("sum_ET_total")
    et = {}
    for mi, m in enumerate(et_masks):
        col = out["et_central"][idx, mi, fi_tot]
        if weighting == "equal_event":
            per_ev = np.zeros(n_ev)
            nz = nsub > 0
            per_ev[nz] = col[nz] / nsub[nz]
            et[f"dET_{m}_deta"] = (_event_mean(per_ev, n_ev) / det,
                                   _mean_se(per_ev, n_ev, scale=1.0 / det))
        else:
            et[f"dET_{m}_deta"] = (_ratio(float(col.sum()), float(nsub.sum())) / det,
                                   _jack_ratio_se(col, nsub) / det)
    res["global_ET"] = et

    return res


def bootstrap_class(out, meta, idx, *, n_boot=200, seed=12345, **kw):
    """Hydro-event bootstrap: resample event rows in the class with replacement; return
    {path: (value, sigma)} where value is the point estimate and sigma the bootstrap std."""
    if len(idx) == 0:
        raise ValueError("empty centrality class (check --bin-edges: too fine, repeated, or "
                         "out of range for this design's event count)")
    rng = np.random.default_rng(seed)
    point = reduce_class(out, meta, idx, **kw)
    flat_keys = [(g, k) for g, d in point.items() if isinstance(d, dict)
                 for k in d if not k.startswith("_")]
    samples = {gk: [] for gk in flat_keys}
    m = len(idx)
    for _ in range(n_boot):
        bs = idx[rng.integers(0, m, size=m)]
        r = reduce_class(out, meta, bs, **kw)
        for (g, k) in flat_keys:
            v = r.get(g, {}).get(k)            # (value, sigma) pair or None
            samples[(g, k)].append(v[0] if v is not None else np.nan)
    res = {}
    for (g, k) in flat_keys:
        arr = np.asarray(samples[(g, k)], dtype=float)
        sigma = float(np.nanstd(arr, ddof=1)) if np.sum(np.isfinite(arr)) > 1 else np.nan
        if (sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE and g == "charged"
                and k in ("mean_pT", "c2_gap", "c3_gap", "c2_cross_full", "c3_cross_full", "v2_2", "v3_2")
                and not np.all(np.isfinite(arr))):
            sigma = np.nan  # Do not hide invalid replicates for the adjusted observables.
        res.setdefault(g, {})[k] = (point[g][k][0], sigma)   # bootstrap sigma overrides analytic
    return res


def reduce_all(out, meta, *, estimator="cent_ch_eta10", edges_pct=None,
               n_boot=200, **kw):
    if edges_pct is None:
        edges_pct = (list(sx.OO_DEFAULT_CENTRALITY_EDGES)
                     if sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE
                     else list(range(0, 75, 5)))
    ci = meta["centrality_estimators"].index(estimator)
    cent_col = out["cent_mult"][:, ci]
    classes = centrality_classes(cent_col, edges_pct)
    bins = []
    for bi, (lo, hi, idx) in enumerate(classes):
        rec = {"cent_low": lo, "cent_high": hi, "n_events": int(len(idx))}
        if sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE:
            nsub = out["nsub_used"][idx]
            fallback = nsub == 1
            w = _weights(nsub.astype(float), kw.get("weighting", "equal_event"))
            # Pair denominators are the same for n=2 and n=3.
            den = w * out["charged_cross_full_den"][idx, 0]
            total_den = float(den.sum())
            rec["charged_flow_sample_usage"] = {
                "cross_full_events": int(np.count_nonzero(nsub >= 2)),
                "same_full_fallback_events": int(np.count_nonzero(fallback)),
                "fallback_events_without_pairs": int(np.count_nonzero(fallback & (den == 0))),
                "same_full_pair_weight_fraction": (float(den[fallback].sum()) / total_den
                                                    if total_den > 0 else None),
            }
        if n_boot and n_boot > 0:
            # distinct seed per class so replicate r in different classes is not artificially aligned
            rec["observables"] = bootstrap_class(out, meta, idx, n_boot=n_boot, seed=12345 + bi, **kw)
        else:
            # no bootstrap: keep the cheap analytic (jackknife / SEM) diagonal sigmas from reduce_class
            obs = reduce_class(out, meta, idx, **kw)
            rec["observables"] = {k: v for k, v in obs.items() if not k.startswith("_")}
        bins.append(rec)
    sigma_kind = "bootstrap" if (n_boot and n_boot > 0) else "jackknife/SEM (analytic)"
    return {"estimator": estimator, "edges_pct": edges_pct, "bins": bins,
            "analysis_profile": sx.analysis_profile(meta),
            "observable_definitions": (sx.oo_observable_definitions()
                if sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE else {}),
            "weighting": kw.get("weighting", "equal_event"),
            "y_window": kw.get("y_window", 0.5),
            "charged_window": kw.get("charged_window", "eta05"),
            # Sigmas here are PER-OBSERVABLE (diagonal) only -- whether analytic (n_boot=0) or
            # bootstrap (n_boot>0). They are NOT a basis for cross-bin / cross-observable
            # covariance; that needs a design-level bootstrap with SHARED replicate ids carrying
            # the whole observable vector per replicate, built downstream from the per-event artifact.
            "sigma_kind": sigma_kind,
            "bootstrap_sigmas_only": True,
            "bootstrap_note": "diagonal per-observable sigmas; not valid for cross-bin/cross-observable covariance"}


# ----------------------------------------------------------------------------------------
# Validator: independent per-event recompute from the cache + equal-vs-all report
# ----------------------------------------------------------------------------------------
def _per_event_sum(arr, offsets, nsub):
    """Independent per-event sum for cross-checking the extractor's loop. Uses an explicit loop
    rather than np.add.reduceat: reduceat mis-handles empty segments (nsub==0), and a zero-event
    at the end of a shard would corrupt the previous event's interval. Validation-only, so the
    extra cost is irrelevant."""
    ne = len(nsub)
    s = np.zeros(ne, dtype=np.float64)
    for i in range(ne):
        a = int(offsets[i])
        b = int(offsets[i + 1])
        if b > a:
            s[i] = float(np.asarray(arr[a:b], dtype=np.float64).sum())
    return s


def validate_against_cache(prefix, cache_dir, zstd="zstd", rtol=1e-9, atol=1e-6):
    out, meta = load_artifact(prefix)
    cmeta = json.loads(sx._read_text_maybe_zst(os.path.join(cache_dir, "metadata.json"), zstd=zstd))
    shard_names = cmeta["shards"]

    n = out["event_id"].size
    ch = sx.FLOW_SPECIES.index("charged")
    pij = sx.Y_HIST_SPECIES.index("pi+")
    h2 = sx.HARMONICS.index(2)
    # POIs to exercise differential flow: pi+ (charged POI) and K0S (neutral POI, 0.5-weight)
    poi_check = {"pi+": sx.FLOW_SPECIES.index("pi+"), "K0S": sx.FLOW_SPECIES.index("K0S")}
    poi_sci = {sp: sx.CORE_SPECIES.index(sp) for sp in poi_check}
    mi_tv = sx.GLOBAL_ET_MASKS.index("total_visible")
    fi_et = sx.GLOBAL_ET_FIELDS.index("sum_ET_total")
    et_w = float(meta.get("et_central_eta_window", 0.5))
    eta_mask = sx._central_eta_bin_mask(et_w)

    # independent per-event recompute of: charged gap_num/den (n=2), pi+ window-yield (|y|<0.5),
    # pi+/K0S differential num/den (n=2), and total_visible central-ET (the newest/riskiest arrays)
    rec_gap_num = np.zeros(n)
    rec_gap_den = np.zeros(n)
    rec_pi_winN = np.zeros(n)
    rec_diff_num = {sp: np.zeros(n) for sp in poi_check}
    rec_diff_den = {sp: np.zeros(n) for sp in poi_check}
    rec_et_tv = np.zeros(n)
    oo_adjusted = sx.analysis_profile(meta) == sx.OO_ANALYSIS_PROFILE
    rec_extra = {}
    if oo_adjusted:
        rec_extra = {key: np.zeros_like(out[key]) for key in
                     ("charged_cross_full_num", "charged_cross_full_den",
                      "charged_mean_pT_N", "charged_mean_pT_sum_pT")}
        pt_mask, boundary = sx._charged_mean_pt_mask(cmeta["fingerprint"])
    y_mask = window_bin_mask(meta["y_hist_inner_edges"], 0.5)

    row = 0
    for base in shard_names:
        path, is_zst = sx._resolve_shard_path(cache_dir, base)
        z, tmp = sx._load_shard(path, is_zst, zstd=zstd)
        try:
            offs = z["offsets"]
            nsub = z["nsubs"]
            ne = len(z["event_ids"])
            cqf = z[f"fl#{ch}#q2_fwd"]
            cqb = z[f"fl#{ch}#q2_bwd"]
            cmf = z[f"fl#{ch}#m_fwd"].astype(np.float64)
            cmb = z[f"fl#{ch}#m_bwd"].astype(np.float64)
            rec_gap_num[row:row + ne] = _per_event_sum((cqf * np.conj(cqb)).real, offs, nsub)
            rec_gap_den[row:row + ne] = _per_event_sum(cmf * cmb, offs, nsub)
            rec_pi_winN[row:row + ne] = z[f"yh#{pij}#N"][:, y_mask].sum(axis=1)
            for sp, fsi in poi_check.items():
                pqf = z[f"fl#{fsi}#q2_fwd"]
                pqb = z[f"fl#{fsi}#q2_bwd"]
                pmf = z[f"fl#{fsi}#m_fwd"].astype(np.float64)
                pmb = z[f"fl#{fsi}#m_bwd"].astype(np.float64)
                dnum = (pqf * np.conj(cqb)).real + (pqb * np.conj(cqf)).real
                dden = pmf * cmb + pmb * cmf
                rec_diff_num[sp][row:row + ne] = _per_event_sum(dnum, offs, nsub)
                rec_diff_den[sp][row:row + ne] = _per_event_sum(dden, offs, nsub)
            central = z[f"get#total_visible#sum_ET_total"][:, eta_mask].sum(axis=1)
            rec_et_tv[row:row + ne] = _per_event_sum(central, offs, nsub)
            if oo_adjusted:
                for wi, window in enumerate(sx.CHARGED_PTH_KEYS):
                    counts = z[f"pth#{window}#N"]
                    pts = z[f"pth#{window}#sum_pT"]
                    rec_extra["charged_mean_pT_N"][row:row+ne, wi] = (
                        counts[:, pt_mask].sum(axis=1) - .4 * counts[:, boundary])
                    rec_extra["charged_mean_pT_sum_pT"][row:row+ne, wi] = (
                        pts[:, pt_mask].sum(axis=1) - .104 * counts[:, boundary])
                for i, k in enumerate(nsub):
                    a, b = int(offs[i]), int(offs[i+1])
                    m = z[f"fl#{ch}#m_full"][a:b].astype(float)
                    for hi, harmonic in enumerate(sx.OO_CROSS_HARMONICS):
                        q = z[f"fl#{ch}#q{harmonic}_full"][a:b]
                        # Sum ordered distinct pairs via prefix sums, independently
                        # of the extractor's squared-total minus diagonal formula.
                        if k >= 2:
                            den = 2 * np.sum(m[1:] * np.cumsum(m)[:-1]) / (k-1)
                            num = 2 * np.sum((q[1:] * np.conj(np.cumsum(q)[:-1])).real) / (k-1)
                        elif k == 1 and m[0] >= 2:
                            den, num = m[0]*(m[0]-1), abs(q[0])**2-m[0]
                        else:
                            den, num = 0., 0.
                        rec_extra["charged_cross_full_num"][row+i, hi] = num
                        rec_extra["charged_cross_full_den"][row+i, hi] = den
            row += ne
        finally:
            try:
                z.close()
            except Exception:
                pass
            if tmp is not None:
                os.unlink(tmp)

    def _check(name, a, b):
        ok = np.allclose(a, b, rtol=rtol, atol=atol)
        worst = float(np.max(np.abs(a - b))) if len(a) else 0.0
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}: max|delta|={worst:.3e}")
        return ok

    print(f"Round-trip vs cache ({n} events):")
    ok = True
    ok &= _check("charged flow_gap_num (n=2)", out["flow_gap_num"][:, ch, h2], rec_gap_num)
    ok &= _check("charged flow_gap_den (n=2)", out["flow_gap_den"][:, ch, h2], rec_gap_den)
    # pi+ window yield: compare artifact-summed vs independent
    art_pi_winN = out["yh_N"][:, pij][:, y_mask].sum(axis=1)
    ok &= _check("pi+ yield counts (|y|<0.5)", art_pi_winN, rec_pi_winN)
    for sp in poi_check:
        sci = poi_sci[sp]
        ok &= _check(f"{sp} flow_diff_num (n=2)", out["flow_diff_num"][:, sci, h2], rec_diff_num[sp])
        ok &= _check(f"{sp} flow_diff_den (n=2)", out["flow_diff_den"][:, sci, h2], rec_diff_den[sp])
    ok &= _check("total_visible central ET", out["et_central"][:, mi_tv, fi_et], rec_et_tv)

    for key, recomputed in rec_extra.items():
        ok &= _check(key, out[key], recomputed)

    # equal-event vs all-oversample, a few classes (sanity + physics-impact report)
    print("\nEqual-event vs all-oversample (charged v2{2}, dNch/deta; pi+ <pT>):")
    for lo, hi in [(0, 5), (30, 40), (60, 70)]:
        ee = reduce_all(out, meta, edges_pct=[lo, hi], n_boot=0, weighting="equal_event")["bins"][0]["observables"]
        ao = reduce_all(out, meta, edges_pct=[lo, hi], n_boot=0, weighting="all_oversample")["bins"][0]["observables"]
        v2e, v2a = ee["charged"]["v2_2"][0], ao["charged"]["v2_2"][0]
        nce, nca = ee["charged"]["dNch_deta"][0], ao["charged"]["dNch_deta"][0]
        pte, pta = ee["pi+"]["mean_pT"][0], ao["pi+"]["mean_pT"][0]
        print(f"  {lo:>2}-{hi:<2}%  v2{{2}}: {v2e:.5f} vs {v2a:.5f} (d={v2e-v2a:+.5f})  "
              f"dNch/deta: {nce:.2f} vs {nca:.2f}  pi+<pT>: {pte:.4f} vs {pta:.4f}")

    # loose physical-plausibility bounds on the most-central class
    cen = reduce_all(out, meta, edges_pct=[0, 5], n_boot=0)["bins"][0]["observables"]
    nch = cen["charged"]["dNch_deta"][0]
    v2 = cen["charged"]["v2_2"][0]
    pt = cen["pi+"]["mean_pT"][0]
    print(f"\nMost-central (0-5%) plausibility: dNch/deta={nch:.1f}, charged v2{{2}}={v2:.4f}, pi+<pT>={pt:.4f} GeV")
    warn = []
    system = meta.get("system") or os.path.basename(os.path.normpath(cache_dir))
    if system == "AuAu200" and not (200 < nch < 900):
        warn.append(f"dNch/deta={nch:.1f} outside ~[200,900] for 0-5% AuAu200")
    if system == "AuAu200" and not (0.01 < v2 < 0.12):
        warn.append(f"charged v2{{2}}={v2:.4f} outside ~[0.01,0.12]")
    if system == "AuAu200" and not (0.30 < pt < 0.60):
        warn.append(f"pi+ <pT>={pt:.4f} outside ~[0.30,0.60] GeV")
    for wmsg in warn:
        print(f"  [WARN] {wmsg}")
    # Arithmetic round-trip is the acceptance gate. Plausibility is informational ONLY: a
    # correctly extracted design may legitimately sit outside these ranges (broad priors), and
    # failing the stage on that would discard exactly the bad model points the emulator must see.
    print("\nVALIDATION:", "PASS" if ok else "FAIL", "(arithmetic round-trip)")
    if warn:
        print("Physical-plausibility warnings above are informational and do NOT fail validation.")
    return ok


# ----------------------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Reduce / validate a per-design artifact.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    rp = sub.add_parser("reduce", help="Reduce to observables + bootstrap sigma.")
    rp.add_argument("--artifact", required=True, help="Artifact prefix (no extension).")
    rp.add_argument("--estimator", default="cent_ch_eta10",
                    choices=sx.CENTRALITY_LABELS)
    rp.add_argument("--bin-edges", default=None,
                    help="Comma percentile edges (OO profile: 1%% to 5%% then 5%% to 80%%; baseline: 5%% to 70%%).")
    rp.add_argument("--y-window", type=float, default=0.5)
    rp.add_argument("--charged-window", default="eta05", choices=["eta05", "eta10"])
    rp.add_argument("--weighting", default="equal_event", choices=["equal_event", "all_oversample"])
    rp.add_argument("--n-boot", type=int, default=200)
    rp.add_argument("--json", default=None, help="Write the reduction to JSON.")

    vp = sub.add_parser("validate", help="Round-trip validate the artifact against its cache.")
    vp.add_argument("--artifact", required=True)
    vp.add_argument("--cache-dir", required=True)
    vp.add_argument("--zstd", default="zstd")

    for parser in (rp, vp):
        parser.add_argument("--expected-fingerprint",
                            default=os.environ.get("HIC_EXPECTED_FINGERPRINT_V3"),
                            help="Selected scope; defaults to HIC_EXPECTED_FINGERPRINT_V3.")
    args = ap.parse_args()
    expected = sx.load_expected_fingerprint(args.expected_fingerprint)
    sx.validate_artifact_scope(args.artifact, expected)
    if args.cmd == "validate":
        sx.validate_cache_scope(args.cache_dir, expected, args.zstd)

    if args.cmd == "reduce":
        out, meta = load_artifact(args.artifact)
        edges = ([float(x) for x in args.bin_edges.split(",")] if args.bin_edges
                 else list(range(0, 75, 5)))
        result = reduce_all(out, meta, estimator=args.estimator, edges_pct=edges,
                            n_boot=args.n_boot, weighting=args.weighting,
                            y_window=args.y_window, charged_window=args.charged_window)
        if args.json:
            with open(args.json, "w") as f:
                json.dump(result, f, indent=2, default=_json_default)
            print(f"wrote {args.json}")
        else:
            for b in result["bins"]:
                o = b["observables"]
                ch = o["charged"]
                print(f"{b['cent_low']:>2.0f}-{b['cent_high']:<2.0f}%  N={b['n_events']:5d}  "
                      f"dNch/deta={ch['dNch_deta'][0]:7.2f}+/-{ch['dNch_deta'][1]:.2f}  "
                      f"v2{{2}}={ch['v2_2'][0]:.4f}+/-{ch['v2_2'][1]:.4f}  "
                      f"pi+<pT>={o['pi+']['mean_pT'][0]:.4f}")
    else:
        ok = validate_against_cache(args.artifact, args.cache_dir, zstd=args.zstd)
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
