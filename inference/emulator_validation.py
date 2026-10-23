#!/usr/bin/env python3
"""Emulator validation + closure for the standalone inference.py PCA+GP emulator, on the primary
(clean, no Lambda/proton) observable set. k-fold cross-validation over the designs (emulator only,
no MCMC). Publication-styled (shares make_flow_plots.set_pub_style). Produces, in --out-dir:

  auau_closure_parity_z.pdf  -- standardized parity (emu vs held-out truth) + z-score calibration
  auau_closure_holdout.pdf   -- one held-out design: emulator +/-1sigma band vs the true model
  auau_pca_spectrum.pdf      -- PCA cumulative explained variance

Caches the k-fold arrays to <out-dir>/validation_kfold.npz; re-plot is instant. --refit recomputes.

    python emulator_validation.py                    # primary determination observable set
    python emulator_validation.py --refit --nsplits 8
    python emulator_validation.py --system OO5360 --model-grid default --out-dir plots_validation_OO5360

--model-grid emulates matrix columns directly on the MODEL centrality grid (no data matching), for a
system whose data do not yet tile the model grid. "default" = MODEL_GRID_DEFAULT[system], or pass
species:obs[,species:obs...] with obs in the matrix label vocabulary (dN_dy, mean_pT, dNch_deta, ...).
"""
import os, argparse
from collections import defaultdict
import numpy as np
from sklearn.model_selection import KFold
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MaxNLocator
import inference as I
from make_flow_plots import set_pub_style, C_BLUE, C_MED, C_DATA

GROUP_COLOR = {"yields": "#4C72B0", "meanpt": "#DD8452", "flow": "#55A868"}
GROUP_NAME  = {"yields": "yields / $dE_T$", "meanpt": r"$\langle p_T\rangle$", "flow": "flow $c_n\\{2\\}$"}


def obs_group(name):
    if name.startswith("mean_pT_"):
        return "meanpt"
    if name in ("c2_charged", "c3_charged"):
        return "flow"
    return "yields"


# Prospective likelihood observables per system, for --model-grid default. O-O: charged + the ALICE
# strange-hadron species (+ pi/K); proton excluded as for Au-Au; flow off (MC-noise dominated). The
# model charged mean_pT is pT>0 -- a proxy for the ATLAS fiducial observable, not the same thing.
MODEL_GRID_DEFAULT = {
    "OO5360": ["charged:dNch_deta", "charged:mean_pT"]
              + [f"{sp}:{ob}" for ob in ("dN_dy", "mean_pT")
                 for sp in ("pi_tot", "K_tot", "K0S", "Lambda_tot", "Xi_tot", "Omega_tot")],
}


def grid_name(sp, ob):
    """Model-grid observable name, following the DATA_SPEC naming (dN_dy_<sp>, mean_pT_<sp>, ...)."""
    return ob if (sp, ob) == ("charged", "dNch_deta") else f"{ob}_{sp}"


def build_model_grid(items):
    """Y columns for the requested (species, obs) on the model's own centrality bins. Hard-errors on an
    unknown pair; drops designs with any NaN in the kept columns (printed)."""
    Y, Yerr, labels, didx, dname = I.load_matrix(I.MATRIX_NPZ)
    darr, active, pnames, ranges = I.load_design(I.DESIGN_DAT)
    X = I.build_X(darr, didx, active)
    val, _, elo, ehi = I.build_lookup(Y, Yerr, labels)
    cols, slabels = [], []
    for it in items:
        sp, ob = it.split(":")
        if (sp, ob) not in val:
            raise KeyError(f"matrix lacks {(sp, ob)}")
        for k in range(len(elo[(sp, ob)])):
            cols.append(val[(sp, ob)][:, k])
            slabels.append(f"{grid_name(sp, ob)}__{elo[(sp, ob)][k]:g}_{ehi[(sp, ob)][k]:g}")
    Ystar = np.column_stack(cols)
    valid = ~np.isnan(Ystar).any(axis=1)
    if (~valid).any():
        bad = sorted({slabels[j] for j in np.where(np.isnan(Ystar).any(axis=0))[0]})
        print(f"  dropping {int((~valid).sum())} design(s) with NaN in: {bad}")
    print(f"  model grid: {Ystar.shape[1]} columns, {int(valid.sum())} designs")
    if not valid.any() or not np.isfinite(Ystar[valid]).all():
        raise ValueError("No finite model-grid rows.")
    return X[valid], Ystar[valid], slabels


def build(drop_specs):
    inputs = I.prepare_inputs(drop_specs=drop_specs, feeddown="")
    return inputs.X, inputs.Y, inputs.labels


def run_kfold(X, Ystar, n_splits, seed=I.SEED):
    """k-fold: train on the rest, predict the held-out fold. Returns stacked true/pred/std and the
    design row-order (so a specific held-out design can be pulled out for the example figure)."""
    kf = KFold(n_splits, shuffle=True, random_state=seed)
    T, P, S, IDX = [], [], [], []
    for k, (tr, te) in enumerate(kf.split(X)):
        print(f"  fold {k+1}/{n_splits}")
        emu = I.PCAGPEmulator(X[tr], Ystar[tr])
        m, sd = emu.predict(X[te], return_std=True)
        T.append(Ystar[te]); P.append(m); S.append(sd); IDX.append(te)
    return np.vstack(T), np.vstack(P), np.vstack(S), np.concatenate(IDX)


def fig_parity_z(t, p, s, slabels, out):
    mu = t.mean(0); sc = np.where(t.std(0) > 0, t.std(0), 1.0)          # standardize each observable
    ts, ps = (t - mu) / sc, (p - mu) / sc
    z = (p - t) / np.where(s > 0, s, np.nan)
    grp = np.array([obs_group(l.split("__")[0]) for l in slabels])
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6.2))

    for g in ("yields", "meanpt", "flow"):
        col = grp == g
        ax1.scatter(ts[:, col].ravel(), ps[:, col].ravel(), s=7, alpha=0.25, lw=0,
                    color=GROUP_COLOR[g], label=GROUP_NAME[g])
    lim = [min(ts.min(), ps.min()) - 0.3, max(ts.max(), ps.max()) + 0.3]
    ax1.plot(lim, lim, "--", color=C_MED, lw=1.3, zorder=0)
    ax1.set_xlim(lim); ax1.set_ylim(lim); ax1.set_aspect("equal", "box")
    ax1.set_xlabel("held-out truth (standardized)"); ax1.set_ylabel("emulator (standardized)")
    r = np.sqrt(np.mean((p - t) ** 2, 0)) / sc                          # per-obs RMSE / spread
    ax1.set_title("closure parity")
    ax1.text(0.96, 0.06, f"median RMSE/spread = {np.median(r):.2f}", transform=ax1.transAxes,
             ha="right", va="bottom", fontsize=14)
    ax1.legend(markerscale=2.4, handletextpad=0.2, loc="upper left")

    zz = z[np.isfinite(z)]
    ax2.hist(zz, bins=55, range=(-4, 4), density=True, color=C_BLUE, alpha=0.6, ec="white", lw=0.3)
    xs = np.linspace(-4, 4, 200)
    ax2.plot(xs, np.exp(-xs**2 / 2) / np.sqrt(2 * np.pi), color=C_MED, lw=2.2, label=r"$\mathcal{N}(0,1)$")
    c68, c95 = np.mean(np.abs(zz) < 1.0), np.mean(np.abs(zz) < 1.96)
    ax2.set_xlabel(r"standardized residual  $(\mathrm{emu}-\mathrm{truth})/\sigma_{\mathrm{emu}}$")
    ax2.set_ylabel("density"); ax2.set_xlim(-4, 4)
    ax2.set_title("z-score calibration")
    ax2.text(0.03, 0.97, f"mean {zz.mean():+.2f}\nstd  {zz.std():.2f}\ncov 68% = {c68:.2f}\ncov 95% = {c95:.2f}",
             transform=ax2.transAxes, ha="left", va="top", fontsize=14, linespacing=1.4)
    ax2.legend(loc="upper right")
    fig.tight_layout(); fig.savefig(out); plt.close(fig)
    print(f"  wrote {out}   (z-std {zz.std():.2f}, cov68 {c68:.2f}, cov95 {c95:.2f})")


HOLDOUT_FONT_SCALE = 1.4   # larger fonts than the shared pub style; layout/lines/markers unchanged


def fig_holdout(t, p, s, slabels, out, row, ncol=3, font_scale=HOLDOUT_FONT_SCALE):
    tt, pp, ss = t[row], p[row], s[row]
    g = defaultdict(list)
    for j, l in enumerate(slabels):
        nm, ce = l.split("__"); g[nm].append((0.5 * sum(float(x) for x in ce.split("_")), j))
    names = list(g); nc = min(ncol, len(names)); nr = int(np.ceil(len(names) / nc))
    fkeys = ("font.size", "axes.labelsize", "axes.titlesize",
             "xtick.labelsize", "ytick.labelsize", "legend.fontsize")
    scaled = {k: plt.rcParams[k] * font_scale for k in fkeys}   # bump only fonts, nothing else
    with plt.rc_context(scaled):
        fig, axes = plt.subplots(nr, nc, figsize=(4.7 * nc, 3.5 * nr), squeeze=False); axes = axes.ravel()
        hb = hm = hd = None
        for ax, nm in zip(axes, names):
            pts = sorted(g[nm]); xs = np.array([c for c, _ in pts]); cols = [j for _, j in pts]
            hb = ax.fill_between(xs, pp[cols] - ss[cols], pp[cols] + ss[cols], color=C_BLUE, alpha=0.40, lw=0)
            hm, = ax.plot(xs, pp[cols], color=C_MED, lw=1.8)
            hd, = ax.plot(xs, tt[cols], "o", color=C_DATA, ms=6, mec=C_DATA)
            ax.set_ylabel(I.OBS_LABELS.get(nm, nm)); ax.set_xlabel(I.xlabel_for(nm)); ax.margins(x=0.04)
        for ax in axes[len(names):]:
            ax.axis("off")
        fig.legend([hb, hm, hd], ["emulator $\\pm1\\sigma$", "emulator mean", "true model (held out)"],
                   loc="lower center", ncol=3, bbox_to_anchor=(0.5, 1.0), borderaxespad=0.4)
        fig.tight_layout(rect=(0, 0, 1, 0.985)); fig.savefig(out); plt.close(fig)
    print(f"  wrote {out}")


def fig_pca(X, Ystar, out):
    emu = I.PCAGPEmulator(X, Ystar)
    cum = np.cumsum(emu.pca.explained_variance_ratio_)
    fig, ax = plt.subplots(figsize=(7, 5))
    n = min(20, len(cum))
    ax.plot(np.arange(1, n + 1), cum[:n], "o-", color=C_MED, ms=6, lw=1.8)
    ax.axhline(I.PCA_VAR_THRESHOLD, ls="--", color=C_DATA, lw=1.4, label=f"{I.PCA_VAR_THRESHOLD:.2f} threshold")
    ax.axvline(emu.npc, ls=":", color=C_BLUE, lw=1.8, label=f"$n_{{PC}}={emu.npc}$")
    ax.set_xlabel("number of principal components"); ax.set_ylabel("cumulative explained variance")
    ax.set_xlim(0.5, n + 0.5); ax.set_ylim(min(cum[0] - 0.02, 0.9), 1.005); ax.legend(loc="lower right")
    ax.xaxis.set_major_locator(MaxNLocator(integer=True))   # PC count is integer-valued
    fig.tight_layout(); fig.savefig(out); plt.close(fig)
    print(f"  wrote {out}  (npc={emu.npc})")


def obs_metrics(t, p, s, slabels):
    """Per-observable closure metrics, pooled over centrality bins + designs, grouped."""
    g = defaultdict(list)
    for j, l in enumerate(slabels):
        g[l.split("__")[0]].append(j)
    names = sorted(g, key=lambda nm: (["yields", "meanpt", "flow"].index(obs_group(nm)), nm))
    rows = []
    for nm in names:
        c = g[nm]
        rmse_sd = np.sqrt(np.mean((p[:, c] - t[:, c]) ** 2)) / t[:, c].std()
        z = ((p[:, c] - t[:, c]) / np.where(s[:, c] > 0, s[:, c], np.nan))
        z = z[np.isfinite(z)]
        rows.append((nm, rmse_sd, z.std(), np.mean(np.abs(z) < 1.0), np.mean(np.abs(z) < 1.96)))
    return rows


def fig_metrics(rows, out):
    import matplotlib.patches as mpatches
    names = [r[0] for r in rows]
    rmse = [r[1] for r in rows]; c68 = [r[3] for r in rows]; c95 = [r[4] for r in rows]
    colors = [GROUP_COLOR[obs_group(nm)] for nm in names]
    lab = [I.OBS_LABELS.get(nm, nm) for nm in names]
    y = np.arange(len(names))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 0.55 * len(names) + 2.4), sharey=True)

    ax1.barh(y, rmse, color=colors, alpha=0.85, ec="white")
    ax1.set_yticks(y); ax1.set_yticklabels(lab); ax1.invert_yaxis()
    ax1.set_xlabel("RMSE / spread"); ax1.set_title("emulator accuracy")
    ax1.legend(handles=[mpatches.Patch(color=GROUP_COLOR[g], label=GROUP_NAME[g])
                        for g in ("yields", "meanpt", "flow")], loc="upper right", fontsize=13)

    ax2.axvline(0.68, ls="--", color=C_MED, lw=1.3); ax2.axvline(0.95, ls="--", color=C_MED, lw=1.3)
    ax2.scatter(c68, y, color=C_BLUE, s=75, zorder=3, label="68%")
    ax2.scatter(c95, y, color=C_DATA, s=75, marker="s", zorder=3, label="95%")
    ax2.set_xlabel("empirical coverage"); ax2.set_xlim(0.45, 1.01)
    ax2.set_title("calibration  (dashed = nominal)"); ax2.legend(loc="lower left", fontsize=13)
    fig.tight_layout(); fig.savefig(out); plt.close(fig)
    print(f"  wrote {out}")


def main(drop_specs, out_dir, nsplits, refit, holdout_pick, model_grid=None):
    set_pub_style()
    os.makedirs(out_dir, exist_ok=True)
    X, Ystar, slabels = build_model_grid(model_grid) if model_grid else build(drop_specs)
    I.validate_run_sizes(4, 0, I.NWALKERS, nsplits, len(X))
    config = I.input_config(drop_specs=tuple(s["name"] for s in I.DATA_SPEC) if model_grid else drop_specs,
                            feeddown="")
    config.update(nsplits=nsplits, model_grid=model_grid or [], tool=I.file_identity(__file__))
    pre = os.path.join(out_dir, I.OUT_PREFIX)
    cache = os.path.join(out_dir, "validation_kfold.npz")
    if refit or not os.path.exists(cache):
        print(f"== k-fold ({nsplits} folds, {X.shape[0]} designs) ==")
        t, p, s, order = run_kfold(X, Ystar, nsplits)
        np.savez(cache, t=t, p=p, s=s, order=order, slabels=np.array(slabels), config_json=np.array(I.config_json(config)))
    else:
        print(f"== loading cached k-fold: {cache} (use --refit to recompute) ==")
        d = I.read_npz(cache)
        I.require_cache_config(d, config)
        t, p, s, order = d["t"], d["p"], d["s"], d["order"]
        slabels = [str(x) for x in d["slabels"]]

    fig_parity_z(t, p, s, slabels, f"{pre}_closure_parity_z.pdf")
    # choose a representative held-out design: the one with median central charged multiplicity
    dnch0 = None
    for j, l in enumerate(slabels):
        if l.split("__")[0] == "dNch_deta" and l.split("__")[1].startswith("0"):
            dnch0 = j; break
    row = int(np.argsort(t[:, dnch0])[len(t) // 2]) if (dnch0 is not None and holdout_pick < 0) else max(holdout_pick, 0)
    if row >= len(t):
        raise ValueError(f"--holdout must be smaller than {len(t)} (stacked k-fold rows).")
    fig_holdout(t, p, s, slabels, f"{pre}_closure_holdout.pdf", row)
    fig_pca(X, Ystar, f"{pre}_pca_spectrum.pdf")

    rows = obs_metrics(t, p, s, slabels)
    print("\n== per-observable emulator metrics ==")
    print("  %-18s %9s %7s %7s %7s" % ("observable", "RMSE/sd", "z-std", "cov68", "cov95"))
    for nm, r, zs, c68, c95 in rows:
        print("  %-18s %9.3f %7.2f %7.2f %7.2f" % (nm, r, zs, c68, c95))
    fig_metrics(rows, f"{pre}_closure_metrics.pdf")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="emulator validation + closure figures")
    I.add_input_arguments(ap, feeddown=False, drops=False)
    ap.add_argument("--model-grid", default="",
                    help='"default" or species:obs[,...]: emulate matrix columns on the model grid (no data)')
    ap.add_argument("--out-dir", default="plots_validation")
    ap.add_argument("--drop-specs", default=None,
                    help="Exclusions (default: the system's primary exclusions; '' keeps all).")
    ap.add_argument("--nsplits", type=int, default=8)
    ap.add_argument("--refit", action="store_true", help="recompute the k-fold (else use cache)")
    ap.add_argument("--holdout", type=int, default=-1, help="stacked-row index for the example; -1 = median-mult design")
    a = ap.parse_args()
    I.configure_inputs(a)
    mg = MODEL_GRID_DEFAULT[a.system] if a.model_grid == "default" else \
        [x for x in a.model_grid.split(",") if x]
    main(I.drop_selection(a), a.out_dir, a.nsplits, a.refit, a.holdout, model_grid=mg)
