#!/usr/bin/env python3
"""Showcase leave-one-out recoveries for the committee overlay: pick 3 held-out designs whose TRUE
gamma_s_0 SPANS the prior (targets ~0.15/0.5/0.75, deliberately AWAY from the real-data 0.31 so the
figure shows recovery of values we are NOT claiming), rerun each with the full LOO protocol, and save
the FULL posterior samples (the aggregate run kept only summaries). Longer chains than the aggregate
since these are hero figures.

    python loo_showcase.py            # 3 spanning designs -> scan_loo/showcase_*.npz
"""
import os
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"
import sys, time, argparse
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inference as I
import loo_closure as L                          # reuse setup(), prepared inputs and per-observable inflation

TARGETS = (0.15, 0.50, 0.75)                      # span the prior; NOT 0.31 (see module docstring)
NSTEPS, NBURN = 8000, 4000                        # richer than the aggregate; clean hero marginals


def run_showcase(k, outdir, nsteps=NSTEPS, nburn=NBURN):
    Xv, Yv, sig = L._G["Xv"], L._G["Yv"], L._G["sig"]
    fpos, fnames, fr = L._G["fpos"], L._G["fnames"], L._G["fr"]
    n, nf = Xv.shape[0], len(fnames)
    mask = np.ones(n, bool); mask[k] = False
    emu = I.PCAGPEmulator(Xv[mask], Yv[mask], var_inflation=L._G["lam"])      # genuine LOO
    xk = Xv[k]; theta_star = xk[fpos]; template_k = xk.copy()
    _, std_k = emu.predict(xk[None, :], return_std=True)
    y_sim = Yv[k] + np.random.default_rng(L.SEED_D + k).normal(0.0, np.sqrt(std_k[0] ** 2 + sig ** 2))
    lo, hi = fr[:, 0], fr[:, 1]
    rng = np.random.default_rng(L.SEED_M + k)
    th0 = rng.uniform(lo + 0.25 * (hi - lo), hi - 0.25 * (hi - lo), size=(L._G["nwalkers"], nf))
    u0 = np.log((th0 - lo) / (hi - th0))
    lp = I.make_logprob_u(emu, y_sim, sig, fr, fpos, template_k)
    chain = I.theta_from_u(I.ensemble_sample(lp, u0, nburn + nsteps, a=I.SAMPLER_A,
                                             p_de=I.SAMPLER_P_DE, rng=rng), fr)[nburn:]
    samp = chain.reshape(-1, nf)
    rhat = np.array([I._rhat(I._split_chains(chain[:, :, j])) for j in range(nf)])
    outf = os.path.join(outdir, f"showcase_{k:04d}.npz")
    np.savez(outf, k=k, free_names=np.array(fnames), theta_star=theta_star, samp=samp, rhat=rhat,
             free_ranges=fr, design_index=int(L._G["indices"][k]),
             config_json=np.array(I.config_json(dict(L._G["config"], row=k, nsteps=nsteps, nburn=nburn,
                                                    tool_showcase=I.file_identity(__file__)))))
    return outf


def _worker(args):
    k, outdir, nsteps, nburn = args
    import traceback
    try:
        t0 = time.perf_counter(); f = run_showcase(k, outdir, nsteps, nburn); return ("ok", k, f, time.perf_counter() - t0)
    except Exception as e:
        traceback.print_exc(); return (f"FAIL:{type(e).__name__}", k, "", 0.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AuAu200 showcase LOO fits at three spanning fugacity truths")
    I.add_input_arguments(ap, system=False, feeddown=False, drops=False)
    ap.add_argument("--out-dir", default="scan_loo")
    ap.add_argument("--nsteps", type=int, default=NSTEPS)
    ap.add_argument("--nburn", type=int, default=NBURN)
    ap.add_argument("--nwalkers", type=int, default=I.NWALKERS)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--lam-nsplits", type=int, default=8)
    a = ap.parse_args(argv)
    if a.workers < 1:
        ap.error("--workers must be positive")
    I.validate_run_sizes(a.nsteps, a.nburn, a.nwalkers, a.lam_nsplits)
    outdir = a.out_dir
    ndes = L.setup(a.lam_nsplits, a.matrix, a.design, a.data_dir, a.nwalkers)
    os.makedirs(outdir, exist_ok=True)
    Xv, fpos, fnames = L._G["Xv"], L._G["fpos"], L._G["fnames"]
    gs = Xv[:, fpos[fnames.index("gamma_s_0")]]                          # true gamma_s_0 per design
    picks = []
    for t in TARGETS:
        order = np.argsort(np.abs(gs - t))
        k = next(int(i) for i in order if int(i) not in picks)          # distinct designs
        picks.append(k)
    print(f"valid designs={ndes}; showcase picks (usable row / full design_index -> true gamma_s_0):")
    for t, k in zip(TARGETS, picks):
        print(f"  target {t:.2f} -> row {k:4d} / design_index {int(L._G['indices'][k])}  gamma_s_0={gs[k]:.3f}")
    import multiprocessing as mp
    t0 = time.perf_counter()
    failures = 0
    with mp.get_context("spawn").Pool(min(a.workers, len(picks)), initializer=L.initialize_worker,
                                      initargs=(L._G,)) as pool:
        tasks = [(k, outdir, a.nsteps, a.nburn) for k in picks]
        for status, k, f, dt in pool.imap_unordered(_worker, tasks):
            failures += status.startswith("FAIL")
            print(f"row {k}, design_index {int(L._G['indices'][k])}: {status} ({dt:.0f}s) -> {f}", flush=True)
    print(f"DONE showcase in {(time.perf_counter()-t0)/60:.1f} min; {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
