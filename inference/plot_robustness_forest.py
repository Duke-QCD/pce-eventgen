#!/usr/bin/env python3
"""Robustness forest for the fugacity sector across the observable-jackknife robustness-scan
variants (baseline + drop-one-observable-group; no fixed-parameter variants -- all 14 params are
always free, see robustness_scan.py).

Four parameter panels (gamma_s0, gamma_l0, tau_l, tau_s), each drawn on its FULL PRIOR RANGE so the
reader sees how much of the prior each posterior actually occupies (constraint level), with the
baseline's 68% band as the reference envelope. A fifth panel shows the equilibration-hierarchy
probabilities P(gamma_s0<gamma_l0) and P(tau_s>tau_l). Reads scan_robust_500/summary.csv. Pub style.
"""
import csv
import argparse
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import lines as mlines
import inference as I
from plot_style import dissertation_style, PAPER_LABELS, C_AMBER, C_BLUE, C_MED, C_DATA, C_GREEN

# variant groups (label -> display, colour); order follows the scan. Same yields/meanpt/flow
# categorization as emulator_validation.py's obs_group(), for a consistent color convention.
GROUPS = [
    ("baseline", [("baseline", "baseline")], C_MED),
    ("yields / $dE_T$", [("drop_dN_dy_pion", r"drop $\pi$"), ("drop_dN_dy_kaon", r"drop $K$"),
                          ("drop_dN_dy_Xi", r"drop $\Xi$"), ("drop_dN_dy_Omega", r"drop $\Omega$"),
                          ("drop_dNch_deta", r"drop $dN_{\mathrm{ch}}/d\eta$"),
                          ("drop_dET", r"drop $dE_T/d\eta$")], C_BLUE),
    (r"$\langle p_T\rangle$", [("drop_mean_pT_pion", r"drop $\langle p_T\rangle_\pi$"),
                                ("drop_mean_pT_kaon", r"drop $\langle p_T\rangle_K$")], C_AMBER),
    (r"flow $c_n\{2\}$", [("drop_c2_charged", r"drop $c_2\{2\}$"),
                           ("drop_c3_charged", r"drop $c_3\{2\}$")], C_GREEN),
]
PLABEL = {**I.PARAM_LABELS, **PAPER_LABELS}
PARAMS = [(name, PLABEL[name] + (r" [fm/$c$]" if name.startswith("tau_") else ""))
          for name in ("gamma_s_0", "gamma_l_0", "tau_l", "tau_s")]
C_GORD, C_TORD = C_BLUE, C_DATA   # markers in the ordering panel


@dissertation_style()
def main(summary="scan_robust_500/summary.csv", out="scan_robust_500/robustness_forest.pdf"):
    plt.rcParams.update({"ytick.labelsize": 17})
    with open(summary, newline="") as stream:
        S = {r["variant"]: r for r in csv.DictReader(stream)
             if r["status"] in ("ok", "resumed")}
    if "baseline" not in S:
        raise ValueError("Summary must contain a successful baseline fit.")
    prior = {name: (lo, hi) for name, lo, hi in I.PARAMS_ALL}
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    # y positions with a gap between groups; baseline at top
    ys, cols, labs, keys, boundaries = [], [], [], [], []
    yy = 0.0
    present_groups = [(name, [(key, label) for key, label in members if key in S], color)
                      for name, members, color in GROUPS]
    present_groups = [group for group in present_groups if group[1]]
    for gi, (gname, members, col) in enumerate(present_groups):
        if gi:
            boundaries.append(yy - 0.5); yy += 1.0
        for key, disp in members:
            if key in S:
                ys.append(yy); cols.append(col); labs.append(disp); keys.append(key); yy += 1.0
    ys = np.array(ys); yplot = ys.max() - ys                                  # invert: first row on top
    bnd = [ys.max() - b for b in boundaries]
    y_prior = yplot.max() + 1.35                                              # reference row above baseline

    fig, axes = plt.subplots(1, 5, figsize=(24, max(6, 0.6 * (len(keys) + 5))),
                             gridspec_kw={"width_ratios": [1, 1, 1, 1, 1.25]})

    for k, (pkey, plabel) in enumerate(PARAMS):
        ax = axes[k]
        lo, hi = prior[pkey]
        b16, b50, b84 = (float(S["baseline"][f"{pkey}_{q}"]) for q in ("q16", "q50", "q84"))
        sig_post = (b84 - b16) / 2.0; sig_prior = (hi - lo) / np.sqrt(12); pmean = (lo + hi) / 2.0
        ax.axvspan(b16, b84, color=C_BLUE, alpha=0.15, lw=0, zorder=0)        # baseline 68% envelope
        ax.axvline(b50, color="0.55", lw=1.0, ls="--", zorder=1)
        for yp, key, col in zip(yplot, keys, cols):
            m = float(S[key][f"{pkey}_q50"]); l = float(S[key][f"{pkey}_q16"]); u = float(S[key][f"{pkey}_q84"])
            ax.errorbar(m, yp, xerr=[[m - l], [u - m]], fmt="o", ms=7.5, color=col, ecolor=col,
                        elinewidth=2.2, capsize=3.5, zorder=3)
        # faint prior +/-sigma reference, drawn like an error bar so its length vs the posterior bars IS sigma_post/sigma_prior
        ax.errorbar(pmean, y_prior, xerr=sig_prior, fmt="none", ecolor="0.6", elinewidth=2.4, capsize=6, zorder=2)
        ax.plot([pmean], [y_prior], marker="|", color="0.6", ms=14, mew=2.2, zorder=2)
        for b in bnd:
            ax.axhline(b, color="0.9", lw=0.8, zorder=0)
        ax.axhline((yplot.max() + y_prior) / 2, color="0.8", lw=0.8, zorder=0)
        ax.set_title(plabel + "\n" + rf"$\sigma_{{\rm post}}/\sigma_{{\rm prior}}={sig_post/sig_prior:.2f}$",
                     fontsize=19, pad=14)
        ax.set_xlim(lo, hi); ax.set_ylim(yplot.min() - 0.8, y_prior + 1.6)
        ax.set_yticks(list(yplot) + [y_prior])
        ax.set_yticklabels((labs + [r"prior $\pm\sigma$"]) if k == 0 else [])
        ax.tick_params(axis="y", length=0)

    # ordering panel
    axo = axes[4]
    axo.axvline(0.5, color="0.55", lw=1.0, ls="--", zorder=1)
    for yp, key in zip(yplot, keys):
        pg, pt = float(S[key]["P(gs<gl)"]), float(S[key]["P(ts>tl)"])
        axo.plot(pg, yp, "o", ms=9, color=C_GORD, zorder=3)
        axo.plot(pt, yp, "s", ms=8.5, color=C_TORD, zorder=3)
    for b in bnd:
        axo.axhline(b, color="0.9", lw=0.8, zorder=0)
    axo.axhline((yplot.max() + y_prior) / 2, color="0.8", lw=0.8, zorder=0)
    axo.set_title("Hierarchy probability\n", fontsize=19, pad=14)
    axo.set_xlim(-0.03, 1.03); axo.set_ylim(yplot.min() - 0.8, y_prior + 1.6)
    axo.set_yticks(yplot); axo.set_yticklabels([]); axo.tick_params(axis="y", length=0)
    axo.legend(handles=[mlines.Line2D([], [], marker="o", ls="none", color=C_GORD, label=r"$P(\gamma_s^0<\gamma_\ell^0)$"),
                        mlines.Line2D([], [], marker="s", ls="none", color=C_TORD, label=r"$P(\tau_{\mathrm{eq},s}>\tau_{\mathrm{eq},\ell})$")],
               loc="upper center", bbox_to_anchor=(0.5, 1.0), fontsize=15, handletextpad=0.3)

    # group legend across the top
    fig.legend(handles=[mlines.Line2D([], [], marker="o", ls="none", color=c, label=gn)
                        for gn, _, c in present_groups],
               loc="upper center", ncol=len(present_groups), bbox_to_anchor=(0.5, 1.0), columnspacing=1.6)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", default="scan_robust_500/summary.csv")
    parser.add_argument("--out", help="PDF destination (default: alongside summary.csv).")
    args = parser.parse_args()
    main(args.summary, args.out or str(Path(args.summary).with_name("robustness_forest.pdf")))
