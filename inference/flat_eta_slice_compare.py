#!/usr/bin/env python3
"""Crude flat-eta/s comparison WITHOUT a new MCMC: the slopes~0 slice of the 14-D primary.

Conditioning the emulator at eta_low=eta_high=0 (run_flat_eta.py) samples the same likelihood
restricted to that slice, so keeping only the 14-D samples with |eta_low|<LOW_WIN and
|eta_high|<HIGH_WIN approximates the flat-eta/s posterior (exact as the window -> 0, up to MCMC
noise). If the flat run's posterior exists it is overlaid; it should agree with the slice.

Writes to --out-dir:
  auau_posterior_full.pdf, auau_posterior_<group>.pdf  slice corners, house style (same as main())
  marginals_compare.pdf   1-D marginals: 14-D (all) vs slice [vs flat run] + the slice window
  summary.csv             median/16/84 per param per source, slice shift in 14-D sigma units

    python flat_eta_slice_compare.py [--baseline-dir D] [--flat-dir D] [--out-dir D]
                                     [--low-win 0.5] [--high-win 0.25]
"""
import os, argparse, csv
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import inference as I

SLOPES = ("eta_low", "eta_high")
C_ALL, C_SLICE, C_FLAT = "#898781", "#2a78d6", "#eb6834"   # neutral reference, categorical 1, 2
ETA_LABEL = r"$(\eta/s)_{\rm kink}$  (= const. if flat)"


def load(run_dir):
    d = I.restore_posterior(os.path.join(run_dir, f"{I.OUT_PREFIX}_posterior.npz"))
    return d["samples"], [str(x) for x in d["param_names"]], np.asarray(d["ranges"], float)


def pct(x):
    return np.percentile(x, [16, 50, 84])


def main(baseline_dir, flat_dir, out_dir, low_win, high_win):
    I.use_system("AuAu200")
    if not np.isfinite([low_win, high_win]).all() or min(low_win, high_win) <= 0:
        raise ValueError("Slice windows must be finite and positive.")
    os.makedirs(out_dir, exist_ok=True)
    S, P, R = load(baseline_dir)
    for nm in SLOPES:
        if nm not in P:
            raise ValueError(f"{baseline_dir} has {nm} fixed -- need a run with both slopes free")
    il, ih = P.index("eta_low"), P.index("eta_high")
    mask = (np.abs(S[:, il]) < low_win) & (np.abs(S[:, ih]) < high_win)
    if mask.sum() < 10000:
        raise ValueError(f"only {mask.sum()} samples in the slice window; widen it")
    print(f"baseline {baseline_dir}: {len(S)} samples; slice |eta_low|<{low_win}, "
          f"|eta_high|<{high_win}: {mask.sum()} ({mask.mean():.1%})")

    keep = [i for i, nm in enumerate(P) if nm not in SLOPES]
    names = [P[i] for i in keep]
    ranges = R[keep]
    edges = {nm: np.linspace(*R[i], 41) for i, nm in enumerate(P)}
    # 14-D marginal histograms + percentiles, then drop the big array
    h_all = {nm: np.histogram(S[:, i], edges[nm], density=True)[0] for i, nm in enumerate(P)}
    q_all = {nm: pct(S[:, i]) for i, nm in enumerate(P)}
    rng = np.random.default_rng(0)
    win_sub = S[rng.choice(len(S), 200000, replace=False)][:, [il, ih]]
    sl = S[mask][:, keep]
    del S

    flat = None
    flat_npz = os.path.join(flat_dir, f"{I.OUT_PREFIX}_posterior.npz") if flat_dir else None
    if flat_npz and os.path.exists(flat_npz):
        F, FP, _ = load(flat_dir)
        if FP != names:
            raise ValueError(f"flat-run params {FP} != slice params {names}")
        flat = {nm: (np.histogram(F[:, j], edges[nm], density=True)[0], pct(F[:, j]))
                for j, nm in enumerate(names)}
        print(f"flat run {flat_dir}: {len(F)} samples (overlaid)")
        del F
    else:
        print(f"no flat-run posterior at {flat_npz} -- slice vs 14-D only")

    # (1) slice corners, house style, same files main() writes for the flat run
    I.PARAM_LABELS = {**I.PARAM_LABELS, "eta_min": r"$\eta/s$"}
    thin = sl[rng.choice(len(sl), min(I.PLOT_THIN_N, len(sl)), replace=False)]
    I.make_corner_plots(thin, names, ranges, os.path.join(out_dir, I.OUT_PREFIX))

    # (2) 1-D marginal overlays + the slice window in the slope plane
    labels = {**I.PARAM_LABELS, "eta_min": ETA_LABEL}
    ncol = 4
    nrow = int(np.ceil((len(names) + 1) / ncol))
    with plt.rc_context({**I.POSTERIOR_RC, "font.size": 9, "axes.labelsize": 9,
                         "xtick.labelsize": 7.5, "ytick.labelsize": 7.5, "legend.fontsize": 9}):
        fig, axes = plt.subplots(nrow, ncol, figsize=(2.6 * ncol, 1.9 * nrow))
        axes = axes.ravel()
        for ax, (j, nm) in zip(axes, enumerate(names)):
            e = edges[nm]; x = 0.5 * (e[1:] + e[:-1])
            ax.fill_between(x, h_all[nm], step="mid", color=C_ALL, alpha=0.35, lw=0,
                            label="14-D, all samples")
            ax.step(x, np.histogram(sl[:, j], e, density=True)[0], where="mid", color=C_SLICE,
                    lw=1.5, label="14-D, slopes≈0 slice")
            if flat is not None:
                ax.step(x, flat[nm][0], where="mid", color=C_FLAT, lw=1.5, ls="--",
                        label="flat-η/s run")
            ax.set_xlim(e[0], e[-1]); ax.set_ylim(bottom=0); ax.set_yticks([])
            ax.set_xlabel(labels.get(nm, nm))
        axw = axes[len(names)]
        axw.hist2d(win_sub[:, 0], win_sub[:, 1], bins=40, range=(R[il], R[ih]), cmap="Greys", cmin=1)
        axw.add_patch(plt.Rectangle((-low_win, -high_win), 2 * low_win, 2 * high_win, fill=False,
                                    ec=C_SLICE, lw=1.5))
        axw.set_xlabel(labels["eta_low"] + r" [GeV$^{-1}$]")
        axw.set_ylabel(labels["eta_high"] + r" [GeV$^{-1}$]")
        axw.set_title(f"slice window ({mask.mean():.1%} of samples)", fontsize=9)
        for ax in axes[len(names) + 1:]:
            ax.set_axis_off()
        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="upper center", ncol=len(l), frameon=False)
        fig.tight_layout(rect=(0, 0, 1, 0.95))
        fig.savefig(os.path.join(out_dir, "marginals_compare.pdf")); plt.close(fig)

    # (3) numbers
    with open(os.path.join(out_dir, "summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["param", "all_q16", "all_q50", "all_q84", "slice_q16", "slice_q50", "slice_q84",
                    "slice_shift_sigma"] + (["flat_q16", "flat_q50", "flat_q84"] if flat else []))
        print(f"\n{'param':11s} {'14-D all':>22s} {'slice':>22s} {'shift/σ':>8s}"
              + (f" {'flat run':>22s}" if flat else ""))
        for j, nm in enumerate(names):
            a, s = q_all[nm], pct(sl[:, j])
            shift = (s[1] - a[1]) / (0.5 * (a[2] - a[0]))
            row = [nm, *a, *s, shift] + (list(flat[nm][1]) if flat else [])
            w.writerow([row[0]] + [f"{v:.5g}" for v in row[1:]])
            fmt = lambda q: f"{q[1]:7.3f} [{q[0]:6.3f},{q[2]:6.3f}]"
            print(f"{nm:11s} {fmt(a):>22s} {fmt(s):>22s} {shift:+8.2f}"
                  + (f" {fmt(flat[nm][1]):>22s}" if flat else ""))
    for pair in (("gamma_s_0", "gamma_l_0", "<"), ("tau_s", "tau_l", ">")):
        a, b, op = pair
        ja, jb = names.index(a), names.index(b)
        v = np.mean(sl[:, ja] < sl[:, jb]) if op == "<" else np.mean(sl[:, ja] > sl[:, jb])
        print(f"slice P({a} {op} {b}) = {v:.3f}")
    print(f"\nwrote {out_dir}/: auau_posterior_full.pdf, auau_posterior_<group>.pdf, "
          f"marginals_compare.pdf, summary.csv")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--baseline-dir", default="plots_primary_14d_500_2x_matern15")
    ap.add_argument("--flat-dir", default="plots_flat_eta_500_2x")
    ap.add_argument("--out-dir", default="plots_flat_eta_slice")
    ap.add_argument("--low-win", type=float, default=0.5)
    ap.add_argument("--high-win", type=float, default=0.25)
    a = ap.parse_args()
    main(a.baseline_dir, a.flat_dir, a.out_dir, a.low_win, a.high_win)
