#!/usr/bin/env python3
"""Leave-one-out parameter-recovery closure for the inference.py inference chain (primary,
Lambda/proton-excluded observable set).

For each of K held-out DESIGN points (known true parameters, real TRENTo->...->SMASH model output):
  1. retrain the PCA+GP emulator on all OTHER valid designs (genuine leave-one-out; current
     production kernel -- Matern nu=1.5, baked into PCAGPEmulator -- picked up automatically),
  2. synthesize pseudo-data from the held-out point's TRUE model output plus likelihood-consistent
     noise  y = Y_true + N(0, sigma_emu(theta*)^2 + sig_data^2),
  3. run the ACTUAL inference sampler (logit-space Goodman-Weare, faithful to inference.main),
     with ALL 14 params free (current primary-determination convention -- no fixed params),
  4. record, per free param: the SBC rank of theta* among the posterior draws, and whether theta*
     falls inside the 68% / 90% marginal credible interval; plus split-Rhat for a convergence flag.

Design points are a space-filling (LHC) sample of the prior box, so theta* ~ prior -> the rank
histogram is uniform under a correctly-calibrated, unbiased pipeline; deviations flag either
inference miscalibration or emulator bias (LOO tests both jointly). Results are checkpointed one
npz per design so a partial run still plots. Resumes only matching completed results.

    python loo_closure.py --K 100 --nsteps 7000 --nburn 3000 --workers 8
    python loo_closure.py --smoke        # K=3, tiny chains, proves the harness (~2 min)
"""
import os
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"                        # pin BLAS: each worker is one core (set before numpy)
import sys, argparse, time, traceback
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inference as I

DROP   = ("dN_dy_Lambda", "dN_dy_proton", "mean_pT_proton")   # primary determination observable set
SEED_D = 20260702                              # per-design noise-draw seed base
SEED_M = 70000                                 # per-design MCMC seed base

_G = {}                                        # prepared in parent; explicitly installed in spawned workers


def setup(lam_nsplits=8, matrix=None, design=None, data_dir=None, nwalkers=I.NWALKERS):
    I.use_system("AuAu200")
    I.FIXED_PARAMS = {}
    inputs = I.prepare_inputs(matrix, design, data_dir, DROP, feeddown="")
    Xv, Yv, sig_data = inputs.X, inputs.Y, inputs.sigmas
    if len(Xv) < 3:
        raise ValueError("LOO requires at least three usable designs.")
    I.validate_run_sizes(4, 0, nwalkers, lam_nsplits, len(Xv))
    free_pos, free_names, free_ranges, _ = I.resolve_free(inputs.pnames, inputs.ranges)
    config = I.input_config(matrix, design, data_dir, DROP, feeddown="")
    config.update(lam_nsplits=lam_nsplits, nwalkers=nwalkers, seed_data=SEED_D, seed_mcmc=SEED_M,
                  tool=I.file_identity(__file__))

    # per-observable variance inflation, computed once here (not per held-out design -- it's a
    # property of the observable set, not of any one design) via the same k-fold closure + floor
    # logic as inference.main(). lam_nsplits is reduced for a fast estimate pass; use 8 (the
    # production value) for the real K=100 run.
    t, p, s = I.kfold_closure(Xv, Yv, n_splits=lam_nsplits)
    lam = np.nanstd((p - t) / np.where(s > 0, s, np.nan), axis=0)
    lam = np.where(np.isfinite(lam), lam, 1.0)
    lam = np.maximum(lam, 1.0)
    print(f"  per-observable lam: range [{lam.min():.3f}, {lam.max():.3f}] median {np.median(lam):.3f} "
          f"(nsplits={lam_nsplits})", flush=True)

    _G.clear()
    _G.update(config=config, indices=inputs.indices, nwalkers=nwalkers, Xv=Xv, Yv=Yv, sig=sig_data, lam=lam,
              fpos=np.asarray(free_pos), fnames=list(free_names),
              fr=np.asarray(free_ranges, float))
    return _G["Xv"].shape[0]


def run_one(k, nsteps, nburn, outdir, refit=False):
    outf = os.path.join(outdir, f"loo_{k:04d}.npz")
    config = dict(_G["config"], row=k, design_index=int(_G["indices"][k]), nsteps=nsteps, nburn=nburn)
    if os.path.exists(outf) and not refit:
        cached = I.read_npz(outf)
        I.require_cache_config(cached, config)
        required = ("rank", "cov68", "cov90", "q16", "q50", "q84", "rhat", "theta_star")
        if any(cached[name].shape != (len(_G["fnames"]),) for name in required):
            raise ValueError("Incomplete LOO result; rerun with --refit.")
        return ("cached", k, 0.0)
    Xv, Yv, sig, lam = _G["Xv"], _G["Yv"], _G["sig"], _G["lam"]
    fpos, fnames, fr = _G["fpos"], _G["fnames"], _G["fr"]
    n, nf = Xv.shape[0], len(fnames)
    t0 = time.perf_counter()

    mask = np.ones(n, bool); mask[k] = False
    emu = I.PCAGPEmulator(Xv[mask], Yv[mask], var_inflation=lam)   # genuine leave-one-out train

    xk = Xv[k]                                    # full active truth vector for design k
    theta_star = xk[fpos]                         # true free (calibrated) params
    template_k = xk.copy()                        # fixed slots = design k's TRUE fixed values

    _, std_k = emu.predict(xk[None, :], return_std=True)
    var = std_k[0] ** 2 + sig ** 2                # likelihood variance at the truth
    y_sim = Yv[k] + np.random.default_rng(SEED_D + k).normal(0.0, np.sqrt(var))

    lo, hi = fr[:, 0], fr[:, 1]
    rng_m = np.random.default_rng(SEED_M + k)
    th0 = rng_m.uniform(lo + 0.25 * (hi - lo), hi - 0.25 * (hi - lo), size=(_G["nwalkers"], nf))
    u0 = np.log((th0 - lo) / (hi - th0))
    lp = I.make_logprob_u(emu, y_sim, sig, fr, fpos, template_k)
    chain = I.theta_from_u(I.ensemble_sample(lp, u0, nburn + nsteps, a=I.SAMPLER_A,
                                             p_de=I.SAMPLER_P_DE, rng=rng_m), fr)[nburn:]
    samp = chain.reshape(-1, nf)

    q05, q16, q50, q84, q95 = np.percentile(samp, [5, 16, 50, 84, 95], axis=0)
    rank = (samp < theta_star).mean(axis=0)                       # SBC rank in [0,1]
    cov68 = (q16 <= theta_star) & (theta_star <= q84)
    cov90 = (q05 <= theta_star) & (theta_star <= q95)
    rhat = np.array([I._rhat(I._split_chains(chain[:, :, j])) for j in range(nf)])

    np.savez(outf, k=k, free_names=np.array(fnames), theta_star=theta_star, rank=rank,
             cov68=cov68, cov90=cov90, q16=q16, q50=q50, q84=q84, rhat=rhat,
             design_index=int(_G["indices"][k]), free_ranges=fr, config_json=np.array(I.config_json(config)))
    return ("ok", k, time.perf_counter() - t0)


def _worker(args):
    try:
        return run_one(*args)
    except Exception as e:
        traceback.print_exc()
        return (f"FAIL:{type(e).__name__}", args[0], 0.0)


def initialize_worker(state):
    """Install prepared arrays in each worker; no implicit fork/global inheritance."""
    _G.clear()
    _G.update(state)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AuAu200 leave-one-out parameter recovery (all 14 free)")
    I.add_input_arguments(ap, system=False, feeddown=False, drops=False)
    ap.add_argument("--K", type=int, default=None,
                    help="Held-out usable rows (100; smoke: 3). Explicit values override smoke defaults; capped at available rows.")
    ap.add_argument("--nsteps", type=int, default=None, help="Post-burn steps (7000; smoke: 400).")
    ap.add_argument("--nburn", type=int, default=None, help="Burn-in steps (3000; smoke: 200).")
    ap.add_argument("--nwalkers", type=int, default=I.NWALKERS)
    ap.add_argument("--workers", type=int, default=None, help="Worker processes (8; smoke: 3).")
    ap.add_argument("--outdir", "--out-dir", default=None, help="Output directory (scan_loo; smoke: scan_loo_smoke).")
    ap.add_argument("--seed", type=int, default=12345, help="Held-out-row selection seed; noise/MCMC seeds fixed per row.")
    ap.add_argument("--lam-nsplits", type=int, default=None, help="Inflation-calibration folds (8; smoke: 2).")
    ap.add_argument("--smoke", action="store_true", help="Use reduced defaults; explicit options still take precedence.")
    ap.add_argument("--refit", action="store_true", help="Explicitly overwrite existing per-design results.")
    a = ap.parse_args(argv)
    a.K = a.K if a.K is not None else (3 if a.smoke else 100)
    a.nsteps = a.nsteps if a.nsteps is not None else (400 if a.smoke else 7000)
    a.nburn = a.nburn if a.nburn is not None else (200 if a.smoke else 3000)
    a.workers = a.workers if a.workers is not None else (3 if a.smoke else 8)
    a.outdir = a.outdir or ("scan_loo_smoke" if a.smoke else "scan_loo")
    a.lam_nsplits = a.lam_nsplits if a.lam_nsplits is not None else (2 if a.smoke else 8)
    if a.K < 1 or a.workers < 1:
        ap.error("--K and --workers must be positive")
    I.validate_run_sizes(a.nsteps, a.nburn, a.nwalkers, a.lam_nsplits)
    ndes = setup(a.lam_nsplits, a.matrix, a.design, a.data_dir, a.nwalkers)
    K = min(a.K, ndes)
    order = np.random.default_rng(a.seed).permutation(ndes)[:K]
    os.makedirs(a.outdir, exist_ok=True)
    print(f"valid designs={ndes} K={K} nsteps={a.nsteps} nburn={a.nburn} workers={a.workers}")
    tasks = [(int(k), a.nsteps, a.nburn, a.outdir, a.refit) for k in order]
    import multiprocessing as mp
    t0 = time.perf_counter()
    failures = 0
    with mp.get_context("spawn").Pool(a.workers, initializer=initialize_worker, initargs=(_G,)) as pool:
        for i, (status, k, dt) in enumerate(pool.imap_unordered(_worker, tasks), 1):
            failures += status.startswith("FAIL")
            print(f"[{i}/{K}] row {k}, design_index {int(_G['indices'][k])}: {status} ({dt:.1f}s)", flush=True)
    print(f"DONE: {K} designs in {(time.perf_counter()-t0)/60:.1f} min; {failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
