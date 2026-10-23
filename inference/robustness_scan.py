#!/usr/bin/env python3
"""Robustness scan for the primary (Lambda/proton-excluded) fugacity determination: observable
jackknife only. Drops one observable GROUP at a time from the likelihood (re-assembles + re-trains
the emulator on the remaining set, full 14-D free, current production defaults -- Matern nu=1.5 +
per-observable variance inflation) and tabulates gamma_s_0/gamma_l_0/tau_l/tau_s/eta_min so we can
see which observable each headline number actually leans on. Fail-soft per variant: a variant that
raises is logged and skipped, the batch continues.

Baseline = the primary determination: drop Lambda+proton+mean_pT_proton (annihilation/normalization
tensions, never in), feed-down off, all 14 params free. Each other variant additionally drops ONE
more observable group from DATA_SPEC.

    python robustness_scan.py --smoke     # 2+baseline cheap variants, proves the harness (~minutes)
    python robustness_scan.py             # full scan; cost depends on the grid and host
"""
import os, argparse, traceback, json
import numpy as np
import inference as I

BASE_DROP = ("dN_dy_Lambda", "dN_dy_proton", "mean_pT_proton")   # always excluded (annihilation/normalization
                                                                   # tensions -- see BARYON_ANNIHILATION_ASSESSMENT_HANDOFF.md)
JACKKNIFE_SPECS = [s["name"] for s in I.DATA_SPEC_AUAU if s["name"] not in BASE_DROP]   # everything else, one at a time
REPORT    = ("gamma_s_0", "gamma_l_0", "tau_l", "tau_s", "eta_min")   # fugacity sector + shear
ORDERINGS = (("P(gs<gl)", "gamma_s_0", "gamma_l_0", "lt"),            # equilibration-hierarchy probabilities
             ("P(ts>tl)", "tau_s", "tau_l", "gt"))


def variant_list(smoke):
    if smoke:
        return [("baseline",              (),                     "baseline"),
                ("drop_dN_dy_Xi",          ("dN_dy_Xi",),          "re-assemble/re-train path"),
                ("BROKEN_drop_everything", tuple(JACKKNIFE_SPECS), "everything dropped -> fail-soft test")]
    V = [("baseline", (), "baseline")]
    for sp in JACKKNIFE_SPECS:
        V.append((f"drop_{sp}", (sp,), "observable jackknife"))
    return V


def _load_row(pnpz):
    """(medians dict, ordering-probabilities dict) from a posterior; raises on a truncated/corrupt npz."""
    post = I.read_npz(pnpz)
    names = [str(x) for x in post["param_names"]]; s = post["samples"]
    med = {p: tuple(np.percentile(s[:, names.index(p)], [16, 50, 84])) for p in REPORT if p in names}
    order = {}
    for tag, a, b, rel in ORDERINGS:
        if a in names and b in names:
            col, ref = s[:, names.index(a)], s[:, names.index(b)]
            order[tag] = float(np.mean(col < ref if rel == "lt" else col > ref))
    return med, order


def write_summary(out_root, rows):
    """Summary table + CSV, rewritten after EVERY variant so an interruption never loses it."""
    import csv
    ordtags = [t[0] for t in ORDERINGS]
    print("\n" + "=" * 110 + "\nROBUSTNESS SUMMARY (median +68%; ordering probabilities):")
    print("  %-24s %-11s" % ("variant", "status") + "".join(f"{p:>17s}" for p in REPORT)
          + "".join(f"{t:>11s}" for t in ordtags))
    for label, status, med, order in rows:
        cells = "".join((f"{med[p][1]:6.2f}+{med[p][2]-med[p][1]:.2f}/-{med[p][1]-med[p][0]:.2f} " if (med and p in med)
                         else f"{'--':>17s}") for p in REPORT)
        ocells = "".join((f"{order[t]:>11.3f}" if (order and t in order) else f"{'--':>11s}") for t in ordtags)
        print(f"  {label:24s} {status:11s}{cells}{ocells}")
    with open(os.path.join(out_root, "summary.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "status"] + [f"{p}_{q}" for p in REPORT for q in ("q16", "q50", "q84")] + ordtags)
        for label, status, med, order in rows:
            r = [label, status]
            for p in REPORT:
                r += list(med[p]) if (med and p in med) else ["", "", ""]
            r += [order[t] if (order and t in order) else "" for t in ordtags]
            w.writerow(r)
    nfail = sum(1 for r in rows if r[1] not in ("ok", "resumed", "expected_failure"))
    print(f"wrote {out_root}/summary.csv  ({len(rows)} variants, {nfail} failed)")


def run(smoke, out_root, nsteps, nsplits, *, matrix=None, design=None, data_dir=None,
        nburn=I.NBURN, nwalkers=I.NWALKERS, seed=I.SEED, refit=False):
    I.use_system("AuAu200")
    I.validate_run_sizes(nsteps, nburn, nwalkers, nsplits)
    variants = variant_list(smoke)
    os.makedirs(out_root, exist_ok=True)
    I.FIXED_PARAMS = {}   # all 14 params always free -- this scan only varies the observable set
    rows = []
    failures = 0
    for label, extra_drop, note in variants:
        d = os.path.join(out_root, label)
        pnpz = os.path.join(d, "auau_posterior.npz")
        print(f"\n{'#'*72}\n# variant: {label:24s} ({note}) -> {d}\n{'#'*72}")
        expected_failure = smoke and label == "BROKEN_drop_everything"
        try:
            drop = BASE_DROP + tuple(extra_drop)
            if os.path.exists(pnpz) and not refit:
                result = I.read_npz(pnpz)
                config = I.input_config(matrix, design, data_dir, drop, feeddown="")
                config.update(nsteps=nsteps, nburn=nburn, nwalkers=nwalkers, seed=seed, nsplits=nsplits,
                              emulator_only=False)
                actual = json.loads(str(result.get("config_json", "{}")))
                if any(actual.get(key) != value for key, value in config.items()):
                    raise ValueError("Existing posterior has different/missing provenance; use --refit or a new directory.")
                I.restore_posterior(pnpz)
                rows.append((label, "resumed", *_load_row(pnpz)))
                write_summary(out_root, rows)
                continue
            I.main(matrix_path=matrix, design_path=design, data_dir=data_dir,
                   out_dir=d, drop_specs=drop, feeddown="", nsteps=nsteps,
                   nburn=nburn, nwalkers=nwalkers, seed=seed, make_plots=False, n_splits=nsplits)
            if expected_failure:
                raise RuntimeError("Intentional empty-observable test unexpectedly succeeded.")
            med, order = _load_row(pnpz)
            rows.append((label, "ok", med, order))
            print(f"# variant {label} OK: " + "  ".join(f"{p}={med[p][1]:.3f}" for p in med))
        except Exception as e:
            if expected_failure and isinstance(e, ValueError) and "No observables matched" in str(e):
                print(f"# variant {label}: EXPECTED failure (empty observable selection): {e}")
                rows.append((label, "expected_failure", None, None))
            else:
                print(f"# !!! variant {label} FAILED: {e}")
                traceback.print_exc()
                failures += 1
                rows.append((label, f"FAIL: {type(e).__name__}", None, None))
        write_summary(out_root, rows)
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="AuAu200 observable-jackknife robustness scan (all 14 free)")
    I.add_input_arguments(ap, system=False, feeddown=False, drops=False)
    ap.add_argument("--smoke", action="store_true", help="Two fits plus an intentional empty-observable failure.")
    ap.add_argument("--out-root")
    ap.add_argument("--nsteps", type=int, help="Post-burn steps (15000; smoke: 300).")
    ap.add_argument("--nburn", type=int, help="Burn-in steps (5000; smoke: 100).")
    ap.add_argument("--nwalkers", type=int, default=I.NWALKERS)
    ap.add_argument("--nsplits", type=int, help="Closure folds (8; smoke: 2).")
    ap.add_argument("--seed", type=int, default=I.SEED)
    ap.add_argument("--refit", action="store_true", help="Explicitly overwrite existing variant fits.")
    a = ap.parse_args(argv)
    return run(a.smoke, a.out_root or ("scan_smoke" if a.smoke else "scan_robust_500"),
               a.nsteps if a.nsteps is not None else (300 if a.smoke else 15000),
               a.nsplits if a.nsplits is not None else (2 if a.smoke else 8),
               matrix=a.matrix, design=a.design, data_dir=a.data_dir,
               nburn=a.nburn if a.nburn is not None else (100 if a.smoke else I.NBURN),
               nwalkers=a.nwalkers, seed=a.seed, refit=a.refit)


if __name__ == "__main__":
    raise SystemExit(main())
