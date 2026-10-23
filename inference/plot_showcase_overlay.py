#!/usr/bin/env python3
"""Committee overlay figure: leave-one-out recovery of the fugacity sector at THREE injected truths
that span the prior (gamma_s_0 ~0.15 / 0.50 / 0.75 -- deliberately none at the 0.31 result, so the
figure shows recovery of values we are NOT claiming and rules out an 'attractor at 0.31'). Rows =
params, columns = injected cases; each panel overlays the recovered posterior (amber, 68% shaded) on
the injected truth (red dashed). Reads scan_loo/showcase_*.npz (written by loo_showcase.py).
"""
import glob
import argparse
import numpy as np
import inference as I
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from plot_style import dissertation_style, PAPER_LABELS, C_AMBER, C_DATA

REPORT = ("gamma_s_0", "gamma_l_0", "tau_l", "tau_s")
PLABEL = {**I.PARAM_LABELS, **PAPER_LABELS}
for name in ("tau_l", "tau_s"):
    PLABEL[name] += r" [fm/$c$]"


@dissertation_style()
def main(outdir="scan_loo"):
    fs = sorted(glob.glob(f"{outdir}/showcase_*.npz"))
    if not fs:
        raise SystemExit("no showcase npz")
    D = [I.read_npz(f) for f in fs]
    names = [str(x) for x in D[0]["free_names"]]
    for result in D[1:]:
        if I.result_scope(result) != I.result_scope(D[0]):
            raise ValueError("Showcase files have different input/settings provenance.")
        if result["free_names"].astype(str).tolist() != names or not np.array_equal(result["free_ranges"], D[0]["free_ranges"]):
            raise ValueError("Showcase files have different parameter names/ranges.")
    jgs = names.index("gamma_s_0")
    D = sorted(D, key=lambda d: float(d["theta_star"][jgs]))       # columns low -> high gamma_s_0
    fr = D[0]["free_ranges"]
    nrow, ncol = len(REPORT), len(D)
    fig, axes = plt.subplots(nrow, ncol, figsize=(5.0 * ncol, 3.4 * nrow), squeeze=False)
    for c, d in enumerate(D):
        samp, ts = d["samp"], d["theta_star"]
        axes[0][c].set_title(rf"injected $\gamma_s^0={ts[jgs]:.2f}$")
        for r, nm in enumerate(REPORT):
            j = names.index(nm); ax = axes[r][c]; lo, hi = fr[j]
            q16, q50, q84 = np.percentile(samp[:, j], [16, 50, 84])
            ax.hist(samp[:, j], bins=45, range=(lo, hi), density=True, color=C_AMBER, alpha=0.80, ec="white", lw=0.2)
            ax.axvspan(q16, q84, color=C_AMBER, alpha=0.20, zorder=0)
            ht = ax.axvline(ts[j], color=C_DATA, lw=2.3, ls="--", zorder=3, label="injected truth")
            ax.set_xlim(lo, hi); ax.set_yticks([])
            if c == 0:
                ax.set_ylabel(PLABEL.get(nm, nm))
            if r == nrow - 1:
                ax.set_xlabel("value")
    fig.legend(handles=[ht], loc="upper center", bbox_to_anchor=(0.5, 0.97))
    fig.suptitle(f"Leave-one-out recovery ({ncol} injected cases)", fontsize=19)
    fig.tight_layout(rect=(0, 0, 1, 0.935))
    fig.savefig(f"{outdir}/loo_showcase_overlay.pdf"); plt.close(fig)
    print(f"wrote {outdir}/loo_showcase_overlay.pdf")
    for d in D:                                                    # recovery summary line
        ts = d["theta_star"]
        cells = "  ".join(f"{PLABEL.get(nm,nm).split('[')[0].strip()}: post {np.median(d['samp'][:, names.index(nm)]):.2f} vs true {ts[names.index(nm)]:.2f}"
                          for nm in REPORT)
        print(f"design_index {int(d.get('design_index', d['k'])):4d} (usable row {int(d['k'])}): {cells}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="scan_loo", help="Directory holding showcase_*.npz.")
    args = parser.parse_args()
    main(args.out_dir)
