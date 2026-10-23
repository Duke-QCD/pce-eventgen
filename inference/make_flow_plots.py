#!/usr/bin/env python3
"""Publication Observable{Posterior,Prior}Summary figures, charged flow shown as v_n{2}=sqrt(max(c,0)).

Reuses inference.py's emulator + v-space transform (no MCMC re-run); only rendering is bespoke. Emits
FOUR figures per run: {Posterior,Prior} x {yields+dET, pT+flow}, each a 2-column large-panel grid with
nested amber 68%/95% credible bands + median versus measurements.

    python make_flow_plots.py                                             # primary determination (no Lambda/proton)
    python make_flow_plots.py --run-dir plots_reduced8d --drop-specs ""   # a different run/observable set
    python make_flow_plots.py --system OO5360 --run-dir plots_OO5360_idpt_all --drop-specs ""

System-aware (2026-10-12): files are <prefix>_*; multiplicity-binned specs (*_vs_mult) are drawn vs
<dNch/deta>; the data legend and per-panel experiment tags come from DATA_LEGEND / EXPERIMENT_TAG.
The same renderer is used by the default inference workflow with its already-trained emulator.
"""
import os, argparse
from collections import defaultdict
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import inference as I

# Re-export these names for existing plotting scripts and saved-result users.
from plot_style import set_pub_style, set_talk_style, C_AMBER, C_BLUE, C_MED, C_DATA
DATA_LEGEND = {"AuAu200": "experimental data", "OO5360": "data"}
# Per-panel source tag, keyed by observable-group name after the v-space rename (systems not listed: none).
EXPERIMENT_TAG = {"OO5360": {"dNch_deta": "CMS", "mean_pT_charged": "ATLAS",
                             "v2_charged": "ALICE", "v3_charged": "ALICE",
                             **{f"{o}_{s}_vs_mult": "ALICE prelim." for o in ("dN_dy", "mean_pT")
                                for s in ("K0S", "Xi", "Omega")}}}


def to_v_space(pred, slabels, y_data, sig_data):
    """Map the charged-flow cumulant columns to v-space; leave all other observables untouched."""
    pred = np.array(pred, float); y = np.array(y_data, float); s = np.array(sig_data, float)
    lab = list(slabels)
    for j, l in enumerate(lab):
        if l.split("__")[0] in ("c2_charged", "c3_charged"):
            pred[:, j] = np.sqrt(np.clip(pred[:, j], 0.0, None))          # v = sqrt(max(c,0))
            v = float(np.sqrt(max(y[j], 0.0)))                            # data c=v^2 -> v
            s[j] = s[j] / (2.0 * v) if v > 0 else 0.0                     # inverse delta: sig_v = sig_c/(2v)
            y[j] = v
            lab[j] = l.replace("c2_charged", "v2_charged").replace("c3_charged", "v3_charged")
    return pred, lab, y, s


def group_of(name):
    """Split observables into the two figures."""
    return "ptflow" if (name.startswith("mean_pT_") or name in ("v2_charged", "v3_charged")) else "yields"


def summary_grid(pred, labels, y_data, sig_data, path, kind, group, ncol=2, talk=False, prior_pct=99.0,
                 system=None, obs_labels=None):
    """One 2-col grid for `group` vs data. kind in {posterior,prior}.

    Default (thesis): nested amber 68%/95% bands + median. Talk mode: compacted panels, larger marks,
    and for the PRIOR a single robust-99% blue band (no nested bands, no median) so the slide reads as
    'the prior can reach the data' in one colour."""
    prior_band = talk and kind == "prior"
    system = I.SYSTEM if system is None else system
    obs_labels = I.OBS_LABELS if obs_labels is None else obs_labels
    g = defaultdict(list)
    for j, l in enumerate(labels):
        nm, ce = l.split("__")
        if group_of(nm) == group:
            g[nm].append((0.5 * sum(float(x) for x in ce.split("_")), j))
    names = sorted(g, key=lambda nm: nm in ("v2_charged", "v3_charged"))   # stable: <pT> panels, then flow
    if not names:
        print(f"  no selected {group} observables; skipping {kind} {group} summary")
        return None
    nc = min(ncol, len(names)); nr = int(np.ceil(len(names) / nc))
    pw, ph = (5.8, 3.9) if talk else (6.5, 4.6)          # talk: tighter panels -> fonts read larger on a slide
    ms, elw = (8.0, 1.8) if talk else (6.5, 1.5)
    fig, axes = plt.subplots(nr, nc, figsize=(pw * nc, ph * nr), squeeze=False)
    axes = axes.ravel(); handles = None
    for i, (ax, nm) in enumerate(zip(axes, names)):
        pts = sorted(g[nm]); xs = np.array([c for c, _ in pts]); cols = [j for _, j in pts]
        if prior_band:
            plo = 0.5 * (100.0 - prior_pct)                                # e.g. 99 -> [0.5,99.5]; 100 -> [0,100] min-max
            qlo, qhi = np.percentile(pred[:, cols], [plo, 100.0 - plo], axis=0)
            hband = ax.fill_between(xs, qlo, qhi, color=C_BLUE, alpha=0.40, lw=0, zorder=1)
        else:
            q025, q16, q50, q84, q975 = np.percentile(pred[:, cols], [2.5, 16, 50, 84, 97.5], axis=0)
            h95 = ax.fill_between(xs, q025, q975, color=C_BLUE, alpha=0.30, lw=0, zorder=1)
            h68 = ax.fill_between(xs, q16, q84, color=C_AMBER, alpha=0.65, lw=0, zorder=2)
            hm, = ax.plot(xs, q50, color=C_MED, lw=2.0 if not talk else 2.8, zorder=3)
        hd = ax.errorbar(xs, y_data[cols], yerr=sig_data[cols], fmt="o", ms=ms, color=C_DATA,
                         mec=C_DATA, mfc=C_DATA, elinewidth=elw, capsize=3, zorder=4)
        ax.set_ylabel(obs_labels.get(nm, nm)); ax.margins(x=0.04)
        if not talk or i + nc >= len(names):        # talk: x-label only on the bottom row of each column
            ax.set_xlabel(r"$\langle dN_{\rm ch}/d\eta\rangle$" if nm.endswith("_vs_mult") else "centrality [%]")
        tag = EXPERIMENT_TAG.get(system, {}).get(nm)
        if tag:                                     # corner away from the data: rising data -> upper left
            yd = y_data[cols]; left = yd[-1] > yd[0]
            ax.text(0.04 if left else 0.96, 0.94, tag, transform=ax.transAxes, ha="left" if left else "right",
                    va="top", fontsize=plt.rcParams["legend.fontsize"] * 0.85, color="0.35")
        handles = (hband, hd) if prior_band else (h68, h95, hm, hd)
    for ax in axes[len(names):]:
        ax.axis("off")
    dlab = DATA_LEGEND[system]
    leg_labels = ([f"{prior_pct:g}% prior band", dlab] if prior_band
                  else [f"68% {kind} band", f"95% {kind} band", "median", dlab])
    fig.legend(handles, leg_labels, loc="lower center", ncol=len(leg_labels),
               bbox_to_anchor=(0.5, 1.0), borderaxespad=0.4)
    fig.tight_layout(rect=(0, 0, 1, 0.985))
    fig.savefig(path); plt.close(fig)
    print(f"  wrote {path}")
    return path


def _validate_options(npost, nprior, prior_pct):
    if (not 0 < prior_pct <= 100
            or any(not isinstance(value, (int, np.integer)) or value < 1 for value in (npost, nprior))):
        raise ValueError("Require 0 < prior-pct <= 100 and positive integer draw counts.")


def plot_from_fit(emu, samples, inputs, prefix, *, engine=I, npost=I.PLOT_NPOST,
                  nprior=I.PLOT_NPRIOR, seed=0, talk=False, prior_pct=99.0):
    """Publication figures from an in-memory fit; no file reload, assembly or GP training.

    Pass the active inference module explicitly when called by inference.py as
    __main__, so its system, fixed slice and labels cannot revert on import.
    """
    _validate_options(npost, nprior, prior_pct)
    free_pos, free_names, free_ranges, template = engine.resolve_free(inputs.pnames, inputs.ranges)
    rng = np.random.default_rng(seed)
    sub = samples[rng.choice(len(samples), min(npost, len(samples)), replace=False)]
    pp, lab_v, y_v, sig_v = to_v_space(emu.predict(engine.expand_free(sub, free_pos, template)),
                                      inputs.labels, inputs.measured, inputs.sigmas)
    pri = rng.uniform(free_ranges[:, 0], free_ranges[:, 1], size=(nprior, len(free_names)))
    prr, _, _, _ = to_v_space(emu.predict(engine.expand_free(pri, free_pos, template)),
                             inputs.labels, inputs.measured, inputs.sigmas)

    suffix, fext = ("_talk", ".png") if talk else ("", ".pdf")
    paths = []
    with plt.rc_context():
        set_talk_style() if talk else set_pub_style()
        for pred, kind in ((pp, "posterior"), (prr, "prior")):
            for group in ("yields", "ptflow"):
                tag = f"_p{prior_pct:g}" if (talk and kind == "prior" and prior_pct != 99.0) else ""
                path = summary_grid(pred, lab_v, y_v, sig_v,
                                    f"{prefix}_{kind.capitalize()}Summary_{group}{suffix}{tag}{fext}",
                                    kind, group, talk=talk, prior_pct=prior_pct,
                                    system=engine.SYSTEM, obs_labels=engine.OBS_LABELS)
                if path is not None:
                    paths.append(path)
    print("  (charged flow shown as v_n{2}=sqrt(max(c_n,0)); calibration in cumulant space)")
    return paths


def main(run_dir, drop_specs, npost=I.PLOT_NPOST, nprior=I.PLOT_NPRIOR, seed=0, talk=False, prior_pct=99.0,
         system=I.DEFAULT_SYSTEM, matrix=None, design=None, data_dir=None):
    _validate_options(npost, nprior, prior_pct)
    post, drop_specs, feeddown = I.posterior_inputs(run_dir, system, matrix, design, data_dir, drop_specs)
    inputs = I.prepare_inputs(drop_specs=drop_specs, feeddown=feeddown)
    emu = I.PCAGPEmulator(inputs.X, inputs.Y)  # mean-only plots; variance inflation is irrelevant
    return plot_from_fit(emu, post["samples"], inputs, os.path.join(run_dir, I.OUT_PREFIX),
                         npost=npost, nprior=nprior, seed=seed, talk=talk, prior_pct=prior_pct)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="publication v-space flow summary figures from a saved posterior")
    ap.add_argument("--system", default=I.DEFAULT_SYSTEM, choices=sorted(I.SYSTEMS))
    ap.add_argument("--matrix", help="Legacy fit matrix; otherwise replay the saved run input.")
    ap.add_argument("--design", help="Legacy fit design; otherwise replay the saved run input.")
    ap.add_argument("--data-dir", help="Legacy fit data; otherwise replay the saved run input.")
    ap.add_argument("--npost", type=int, default=I.PLOT_NPOST)
    ap.add_argument("--nprior", type=int, default=I.PLOT_NPRIOR)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--run-dir", default="plots_primary_noLp", help="dir holding <prefix>_posterior.npz")
    ap.add_argument("--drop-specs", default=None,
                    help="comma-separated DATA_SPEC names excluded in that run")
    ap.add_argument("--talk", action="store_true",
                    help="defense-slide variant: bigger fonts, compacted panels, PNG _talk.png output; "
                         "prior figures use a single blue band (no median)")
    ap.add_argument("--prior-pct", type=float, default=99.0,
                    help="talk prior-band coverage %% (99 -> robust [0.5,99.5]; 100 -> full min-max envelope). "
                         "Non-99 values are written with a _p<pct> filename tag so sets coexist")
    a = ap.parse_args()
    main(a.run_dir, I.split_specs(a.drop_specs) if a.drop_specs is not None else None,
         talk=a.talk, prior_pct=a.prior_pct, system=a.system, matrix=a.matrix, design=a.design,
         data_dir=a.data_dir, npost=a.npost, nprior=a.nprior, seed=a.seed)
