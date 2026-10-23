#!/usr/bin/env python3
"""Figures for the leave-one-out parameter-recovery closure (reads scan_loo/loo_*.npz, works on a
partial run). Produces, in --out-dir (default scan_loo):

  loo_rank_hist.pdf   -- rank histograms + pointwise 99% binomial bands under a uniform-rank null
  loo_coverage.pdf    -- per-parameter 68%/95% empirical coverage from ranks, with 95% Wilson CIs

Also prints a convergence report (split-Rhat) and a coverage table. A calibrated, unbiased pipeline
gives uniform rank histograms and coverage consistent with nominal; a U-shape (or under-coverage)
flags over-confidence, a slope flags bias.
"""
import os, glob, argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.stats import binom, binomtest
import inference as I
from plot_style import dissertation_style, PAPER_LABELS, C_AMBER, C_BLUE, C_MED, C_DATA

REPORT = ("gamma_s_0", "gamma_l_0", "tau_l", "tau_s")     # headline fugacity-sector params
PLABEL = {**I.PARAM_LABELS, **PAPER_LABELS}
RHAT_OK = 1.05


def load(outdir):
    fs = sorted(glob.glob(os.path.join(outdir, "loo_*.npz")))
    if not fs:
        raise SystemExit(f"no results in {outdir}")
    d0 = I.read_npz(fs[0])
    names = [str(x) for x in d0["free_names"]]
    R, C68, C90, RH, Q16, Q50, Q84, TS = [], [], [], [], [], [], [], []
    for f in fs:
        d = I.read_npz(f)
        if d["free_names"].astype(str).tolist() != names:
            raise ValueError(f"Mixed parameter sets in LOO results: {f}")
        if I.result_scope(d) != I.result_scope(d0):
            raise ValueError(f"Mixed input/settings provenance in LOO results: {f}")
        R.append(d["rank"]); C68.append(d["cov68"]); C90.append(d["cov90"]); RH.append(d["rhat"])
        Q16.append(d["q16"]); Q50.append(d["q50"]); Q84.append(d["q84"]); TS.append(d["theta_star"])
    A = np.array
    return (names, A(R), A(C68), A(C90), A(RH), A(Q16), A(Q50), A(Q84), A(TS), len(fs))


def convergence(names, RH):
    print(f"\n== convergence (split-Rhat, {RH.shape[0]} runs) ==")
    print("  %-11s %8s %8s %8s" % ("param", "medRhat", "maxRhat", "frac<1.05"))
    for j, nm in enumerate(names):
        col = RH[:, j]
        print("  %-11s %8.3f %8.3f %8.2f" % (nm, np.median(col), np.max(col), np.mean(col < RHAT_OK)))
    ok = np.mean(np.all(RH < RHAT_OK, axis=1))
    print(f"  fraction of runs with ALL params Rhat<{RHAT_OK}: {ok:.2f}")


@dissertation_style()
def fig_ranks(names, R, RH, out, nbin=10):
    fig, axes = plt.subplots(2, 2, figsize=(13, 10)); axes = axes.ravel()
    K = R.shape[0]
    exp = K / nbin
    lower, upper = binom.interval(0.99, K, 1 / nbin)  # Pointwise count interval, including tiny K.
    for ax, nm in zip(axes, REPORT):
        j = names.index(nm)
        ax.axhspan(lower, upper, color=C_BLUE, alpha=0.18, lw=0, zorder=0)
        ax.axhline(exp, color=C_MED, ls="--", lw=1.4, zorder=1)
        counts, _, _ = ax.hist(R[:, j], bins=nbin, range=(0, 1), color=C_AMBER, alpha=0.85, ec="white", zorder=2)
        ax.set_title(PLABEL.get(nm, nm))
        ax.set_xlabel("posterior rank of truth"); ax.set_xlim(0, 1)
        ax.set_ylim(0, 1.2 * max(upper, counts.max(), 1))  # Keep the Rhat annotation above bars/bands.
        ax.text(0.03, 0.97, f"med $\\hat{{R}}$={np.median(RH[:, j]):.2f}", transform=ax.transAxes,
                ha="left", va="top", fontsize=15, color=(C_MED if np.median(RH[:, j]) < RHAT_OK else C_DATA))
    axes[0].set_ylabel("count"); axes[2].set_ylabel("count")
    fig.suptitle(f"Leave-one-out ranks  (K={K}; reference: uniform)", fontsize=19)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


def coverage_intervals(ranks, level):
    """Empirical central-interval coverage and 95% Wilson bounds across held-out cases."""
    ranks = np.asarray(ranks, float)
    if (ranks.ndim != 2 or not ranks.size or not np.isfinite(ranks).all()
            or np.any((ranks < 0) | (ranks > 1)) or not 0 < level < 1):
        raise ValueError("Coverage requires nonempty finite 2-D ranks in [0, 1] and 0 < level < 1.")
    tail = (1 - level) / 2
    counts = ((ranks >= tail) & (ranks <= 1 - tail)).sum(axis=0)
    intervals = np.array([binomtest(int(count), len(ranks)).proportion_ci(
        confidence_level=0.95, method="wilson") for count in counts])
    return counts / len(ranks), intervals[:, 0], intervals[:, 1]


@dissertation_style()
def fig_coverage(names, R, out, levels=(0.68, 0.95)):
    """Empirical coverage per parameter at the given central credible levels, computed from the SBC
    ranks: coverage at level L = fraction of runs whose rank lies in [(1-L)/2, (1+L)/2].
    95% Wilson binomial intervals remain meaningful at zero/full observed coverage;
    dashed lines indicate nominal coverage. Tiny ensembles are not a calibration proof."""
    K = R.shape[0]; y = np.arange(len(names))
    pal = [C_AMBER, C_BLUE, C_MED]; mk = ["o", "s", "D"]
    offs = np.linspace(-0.14, 0.14, len(levels)) if len(levels) > 1 else [0.0]
    fig, ax = plt.subplots(figsize=(11, 0.55 * len(names) + 2.5))
    for i, lev in enumerate(levels):
        p, lower, upper = coverage_intervals(R, lev)
        ax.axvline(lev, color=pal[i], ls="--", lw=1.6, zorder=0, label=f"{lev*100:.0f}% nominal")
        ax.errorbar(p, y + offs[i], xerr=np.maximum([p - lower, upper - p], 0),
                    fmt=mk[i], color=pal[i], ms=7, capsize=3, zorder=3)
    ax.set_yticks(y); ax.set_yticklabels([PLABEL.get(n, n) for n in names]); ax.invert_yaxis()
    ax.set_xlabel("empirical coverage (95% Wilson interval)"); ax.set_xlim(-0.02, 1.02)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")
    print(f"\n== coverage table (K={K}; estimate [95% Wilson interval]) ==")
    print("  %-11s" % "param" + "".join(f"{'cov'+str(int(l*100)):>12}" for l in levels))
    for j, nm in enumerate(names):
        cells = ""
        for lev in levels:
            p, lower, upper = coverage_intervals(R[:, j:j+1], lev)
            cells += f"  {p[0]:.2f} [{lower[0]:.2f}, {upper[0]:.2f}]"
        print(f"  {nm:11}{cells}")


@dissertation_style()
def fig_recovery(names, Q16, Q50, Q84, TS, ranges, out):
    """Aggregate recovery: posterior median +/-68% vs the injected truth, on the y=x line, for the
    fugacity sector. Points tracking the diagonal across the whole prior (not a horizontal smear at
    one value) show there is NO attractor; ~68% of bars crossing the line = calibrated coverage."""
    K = TS.shape[0]
    fig, axes = plt.subplots(2, 2, figsize=(13, 11)); axes = axes.ravel()
    for ax, nm in zip(axes, REPORT):
        j = names.index(nm); lo, hi = ranges[nm]
        t, m = TS[:, j], Q50[:, j]; el, eu = m - Q16[:, j], Q84[:, j] - m
        covered = (Q16[:, j] <= t) & (t <= Q84[:, j])
        ax.plot([lo, hi], [lo, hi], ls="--", color=C_MED, lw=1.5, zorder=1)
        for msk, col, lab in [(covered, C_BLUE, "68% CI covers truth"), (~covered, C_DATA, "misses")]:
            if msk.any():
                ax.errorbar(t[msk], m[msk], yerr=[el[msk], eu[msk]], fmt="o", ms=4.5, color=col,
                            ecolor=col, elinewidth=1.0, alpha=0.7, capsize=0, zorder=2, label=lab)
        ax.set_xlim(lo, hi); ax.set_ylim(lo, hi); ax.set_aspect("equal", "box")
        ax.set_title(PLABEL.get(nm, nm), fontsize=19)
        ax.set_xlabel("true (injected)"); ax.set_ylabel(r"posterior median $\pm$68%")
        ax.text(0.04, 0.96, f"cov68={covered.mean():.2f}", transform=ax.transAxes, ha="left", va="top", fontsize=15)
    axes[0].legend(loc="lower right", fontsize=14, framealpha=0.9)
    fig.suptitle(f"Leave-one-out parameter recovery  (K={K})", fontsize=19)
    fig.tight_layout(rect=(0, 0, 1, 0.97)); fig.savefig(out); plt.close(fig)
    print(f"wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="scan_loo", help="Directory holding loo_*.npz.")
    a = ap.parse_args()
    names, R, C68, C90, RH, Q16, Q50, Q84, TS, K = load(a.out_dir)
    print(f"loaded {K} LOO runs from {a.out_dir}  ({len(names)} free params)")
    convergence(names, RH)
    fig_ranks(names, R, RH, os.path.join(a.out_dir, "loo_rank_hist.pdf"))
    fig_coverage(names, R, os.path.join(a.out_dir, "loo_coverage.pdf"), levels=(0.68, 0.95))
    # fig_recovery deprecated: for a moderately-constrained Bayesian fit the median shrinks toward the
    # prior mean, so the recovery cloud sits off-diagonal (correct behaviour that reads as bias). Use
    # the coverage plot instead. The recovery function remains available for exploratory use.


if __name__ == "__main__":
    main()
