#!/usr/bin/env python3
"""Lambda and proton yields -- excluded from the calibration due to baryon annihilation -- vs data.

Shows the MODEL posterior-predictive (feed-down corrected: Sigma0->Lambda; proton remains primordial) evaluated at
the PRIMARY (clean, no Lambda/proton) posterior, so the residual under-prediction visualised is the
baryon-annihilation deficit. Publication style (shares make_flow_plots). No MCMC re-run.

    python plot_excluded_baryons.py                       # uses plots_primary_noLp posterior
    python plot_excluded_baryons.py --prior               # also overlay the prior-predictive range
"""
import os, argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import inference as I
from make_flow_plots import set_pub_style, C_AMBER, C_BLUE, C_MED, C_DATA

# Sigma0->Lambda is ELECTROMAGNETIC: STAR's Lambda retains it, the model (SMASH e.m. off) omits it,
#   so we add it to the model Lambda. Sigma+->p is WEAK and PHENIX's proton is already weak-feed-down
#   corrected (primordial), so the model proton is compared primordial -- NO Sigma+ added.
PANELS = [("dN_dy_Lambda", r"$dN_\Lambda/dy$"), ("dN_dy_proton", r"$dN_p/dy$")]


def _cent_cols(slabels, name):
    cols = [j for j, l in enumerate(slabels) if l.split("__")[0] == name]
    xs = np.array([0.5 * sum(float(x) for x in slabels[j].split("__")[1].split("_")) for j in cols])
    o = np.argsort(xs)
    return xs[o], [cols[k] for k in o]


def main(run_dir, show_prior, npost=3000, nprior=5000, seed=0,
         matrix=None, design=None, data_dir=None, feeddown=None):
    post, _, _ = I.posterior_inputs(run_dir, "AuAu200", matrix, design, data_dir)
    set_pub_style()
    if min(npost, nprior) < 1:
        raise ValueError("Draw counts must be positive.")
    # This diagnostic intentionally includes excluded channels and corrects Lambda.
    inputs = I.prepare_inputs(drop_specs=(), feeddown=feeddown)
    X, Ystar, slabels = inputs.X, inputs.Y, inputs.labels
    y_data, sig_data = inputs.measured, inputs.sigmas
    emu = I.PCAGPEmulator(X, Ystar)
    fp, fn, fr, tmpl = I.resolve_free(inputs.pnames, inputs.ranges)
    samp = post["samples"]
    rng = np.random.default_rng(seed)
    pp = emu.predict(I.expand_free(samp[rng.choice(len(samp), min(npost, len(samp)), replace=False)], fp, tmpl))
    prr = emu.predict(I.expand_free(rng.uniform(fr[:, 0], fr[:, 1], size=(nprior, len(fn))), fp, tmpl)) if show_prior else None

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.3))
    handles, labels_leg = [], []
    for ax, (name, ylab) in zip(axes, PANELS):
        xs, cols = _cent_cols(slabels, name)
        q025, q16, q50, q84, q975 = np.percentile(pp[:, cols], [2.5, 16, 50, 84, 97.5], axis=0)
        if show_prior:
            pr = np.percentile(prr[:, cols], [2.5, 97.5], axis=0)
            hp = ax.fill_between(xs, pr[0], pr[1], color="0.85", lw=0, zorder=0)
        h95 = ax.fill_between(xs, q025, q975, color=C_BLUE, alpha=0.30, lw=0, zorder=1)
        h68 = ax.fill_between(xs, q16, q84, color=C_AMBER, alpha=0.65, lw=0, zorder=2)
        hm, = ax.plot(xs, q50, color=C_MED, lw=1.8, zorder=3)
        hd = ax.errorbar(xs, y_data[cols], yerr=sig_data[cols], fmt="o", ms=6.5, color=C_DATA,
                         mec=C_DATA, mfc=C_DATA, elinewidth=1.5, capsize=3, zorder=4)
        ax.set_ylabel(ylab); ax.set_xlabel("centrality [%]"); ax.margins(x=0.05)
        if not handles:
            handles = ([hp] if show_prior else []) + [h68, h95, hd]
            labels_leg = (["prior range"] if show_prior else []) + ["posterior 68%", "posterior 95%", "STAR / PHENIX data"]
    axes[0].legend(handles, labels_leg, loc="upper right", fontsize=14)
    fig.tight_layout()
    out = os.path.join(run_dir, "auau_excluded_baryons.pdf")
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Lambda/proton (excluded) yields vs data at the primary posterior")
    ap.add_argument("--run-dir", default="plots_primary_noLp")
    ap.add_argument("--prior", action="store_true", help="overlay the prior-predictive range (reachability context)")
    ap.add_argument("--matrix")
    ap.add_argument("--design")
    ap.add_argument("--data-dir")
    ap.add_argument("--feeddown", help="Matching complete Sigma0 inventory (required; default: system inventory).")
    ap.add_argument("--npost", type=int, default=3000)
    ap.add_argument("--nprior", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    main(a.run_dir, a.prior, a.npost, a.nprior, a.seed, a.matrix, a.design, a.data_dir, a.feeddown)
