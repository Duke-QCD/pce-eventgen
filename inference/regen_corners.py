#!/usr/bin/env python3
"""Regenerate the STAT-style corner plots from a saved posterior with LARGER fonts for readability.
ONLY the font sizes change; layout, colours, bins, CI format are untouched. No MCMC re-run.

    python regen_corners.py                             # primary determination, fonts x2.6
    python regen_corners.py --run-dir plots_reduced8d --font-scale 2.4
    python regen_corners.py --system OO5360 --run-dir plots_OO5360_prelim
"""
import os, argparse
import numpy as np
import inference as I


from plot_style import PAPER_LABELS


def plot_from_samples(samples, pnames, ranges, prefix, *, font_scale=I.CORNER_FONT_SCALE, engine=I):
    """Render dissertation full/group corners directly from samples, without fitting or global changes."""
    if not np.isfinite(font_scale) or font_scale <= 0:
        raise ValueError("Font scale must be finite and positive.")
    labels = I.param_labels(engine.FIXED_PARAMS, {**engine.PARAM_LABELS, **PAPER_LABELS})
    rc = dict(engine.POSTERIOR_RC)
    for k in ("font.size", "legend.fontsize", "axes.labelsize", "axes.titlesize",
              "xtick.labelsize", "ytick.labelsize"):
        rc[k] = rc[k] * font_scale
    thin = samples[np.random.default_rng(0).choice(len(samples), min(engine.PLOT_THIN_N, len(samples)), replace=False)]
    # The full corner's canvas is capped (corner_full_scale < 1 beyond ~8 params), so its panels shrink
    # while fonts don't; scaled tick labels then collide. Shrink ONLY the tick labels by that factor
    # (axis labels and CI annotations keep font_scale). 8-param: factor 1, output unchanged.
    full_rc = dict(rc)
    for k in ("xtick.labelsize", "ytick.labelsize"):
        full_rc[k] = rc[k] * engine.corner_full_scale(len(pnames))
    engine.make_corner_plots(thin, pnames, ranges, prefix, full_rc=full_rc, rc=rc, param_labels=labels)
    print(f"wrote {prefix}_posterior_full.pdf + _posterior_<group>.pdf  (fonts x{font_scale})")


def main(run_dir, font_scale, system=I.DEFAULT_SYSTEM):
    I.use_system(system)
    prefix = os.path.join(run_dir, I.OUT_PREFIX)
    post = I.restore_posterior(f"{prefix}_posterior.npz")
    plot_from_samples(post["samples"], post["param_names"].astype(str).tolist(), post["ranges"],
                      prefix, font_scale=font_scale)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="regenerate STAT corner plots with larger fonts")
    ap.add_argument("--system", default=I.DEFAULT_SYSTEM, choices=sorted(I.SYSTEMS))
    ap.add_argument("--run-dir", default="plots_primary_noLp")
    ap.add_argument("--font-scale", type=float, default=I.CORNER_FONT_SCALE)
    a = ap.parse_args()
    main(a.run_dir, a.font_scale, a.system)
