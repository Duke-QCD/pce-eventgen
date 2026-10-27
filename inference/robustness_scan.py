#!/usr/bin/env python3
"""System-aware observable-group robustness scan for AuAu200 or OO5360.

O-O accepts --oo-yield-proxies and --drop-specs, including an empty exclusion
list for the all-optional fit. Each variant reassembles the selected joint
covariance, retrains its emulator and samples all active parameters.

Legacy AuAu200 primary (Lambda/proton-excluded) fugacity determination: observable
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


def variant_list(smoke, specs=None):
    specs = JACKKNIFE_SPECS if specs is None else list(specs)
    if smoke:
        example = next((name for name in specs if name.startswith("dN_dy_Xi")), specs[0])
        return [("baseline",              (),                     "baseline"),
                (f"drop_{example}",        (example,),            "re-assemble/re-train path"),
                ("BROKEN_drop_everything", tuple(specs),          "everything dropped -> fail-soft test")]
    V = [("baseline", (), "baseline")]
    for sp in specs:
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
        diagnostic_fields = ["converged", "max_rank_rhat", "min_bulk_ess", "min_tail_ess"]
        w.writerow(["variant", "status"] + [f"{p}_{q}" for p in REPORT for q in ("q16", "q50", "q84")] + ordtags + diagnostic_fields)
        for label, status, med, order in rows:
            r = [label, status]
            for p in REPORT:
                r += list(med[p]) if (med and p in med) else ["", "", ""]
            r += [order[t] if (order and t in order) else "" for t in ordtags]
            path = os.path.join(out_root, label, f"{I.OUT_PREFIX}_diagnostics.json")
            diagnostics = json.load(open(path)) if os.path.exists(path) else None
            if diagnostics:
                params = diagnostics["parameters"]
                def extreme(key, function):
                    values = [p[key] for p in params]
                    return function(values) if all(v is not None for v in values) else ""
                r += [diagnostics["converged"], extreme("rank_rhat", max),
                      extreme("bulk_ess", min), extreme("tail_ess", min)]
            else:
                r += [""] * len(diagnostic_fields)
            w.writerow(r)
    nfail = sum(1 for r in rows if r[1] not in ("ok", "resumed", "expected_failure"))
    print(f"wrote {out_root}/summary.csv  ({len(rows)} variants, {nfail} failed)")


def run(smoke, out_root, nsteps, nsplits, *, matrix=None, design=None, data_dir=None,
        nburn=I.NBURN, nwalkers=I.NWALKERS, seed=I.SEED, refit=False,
        system="AuAu200", drop_specs=None, oo_yield_proxies=False,
        only_variants=None, dry_run=False):
    I.use_system(system)
    if oo_yield_proxies:
        I.enable_oo_yield_proxies()
    I.validate_run_sizes(nsteps, nburn, nwalkers, nsplits)
    base_drop = tuple(I.SYSTEM_DROP if drop_specs is None else drop_specs)
    known = {s["name"] for s in I.DATA_SPEC}
    if set(base_drop) - known:
        raise ValueError(f"Unknown excluded observables: {sorted(set(base_drop) - known)}")
    specs = [s["name"] for s in I.DATA_SPEC if s["name"] not in base_drop]
    if not specs:
        raise ValueError("No observables available for robustness variants.")
    variants = variant_list(smoke, specs)
    if only_variants is not None:
        selected = set(only_variants)
        unknown = selected - {v[0] for v in variants}
        if unknown:
            raise ValueError(f"Unknown variants: {sorted(unknown)}")
        variants = [v for v in variants if v[0] in selected]
    if dry_run:
        for label, extra_drop, _ in variants:
            print(f"{system} {label}: exclude {','.join(base_drop + tuple(extra_drop)) or 'none'}; "
                  f"proxies={oo_yield_proxies}; {nwalkers} walkers, {nburn}+{nsteps} steps, {nsplits} folds")
        return 0
    os.makedirs(out_root, exist_ok=True)
    I.FIXED_PARAMS = {}   # all 14 params always free -- this scan only varies the observable set
    rows = []
    failures = 0
    for label, extra_drop, note in variants:
        d = os.path.join(out_root, label)
        pnpz = os.path.join(d, f"{I.OUT_PREFIX}_posterior.npz")
        print(f"\n{'#'*72}\n# variant: {label:24s} ({note}) -> {d}\n{'#'*72}")
        expected_failure = smoke and label == "BROKEN_drop_everything"
        try:
            drop = base_drop + tuple(extra_drop)
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
    ap = argparse.ArgumentParser(description="Observable-jackknife robustness scan (all active parameters free)")
    I.add_input_arguments(ap, feeddown=False, proxies=True)
    ap.add_argument("--smoke", action="store_true", help="Two fits plus an intentional empty-observable failure.")
    ap.add_argument("--out-root")
    ap.add_argument("--nsteps", type=int, help="Post-burn steps (15000; smoke: 300).")
    ap.add_argument("--nburn", type=int, help="Burn-in steps (5000; smoke: 100).")
    ap.add_argument("--nwalkers", type=int, default=I.NWALKERS)
    ap.add_argument("--nsplits", type=int, help="Closure folds (8; smoke: 2).")
    ap.add_argument("--seed", type=int, default=I.SEED)
    ap.add_argument("--refit", action="store_true", help="Explicitly overwrite existing variant fits.")
    ap.add_argument("--only-variants", help="Comma-separated variant names. Concurrent invocations require distinct output roots.")
    ap.add_argument("--dry-run", action="store_true", help="Print the selected variants without fitting or writing.")
    a = ap.parse_args(argv)
    default_root = ("scan_smoke" if a.smoke else "scan_robust_500") if a.system == "AuAu200" else f"scan_robust_{a.system}"
    return run(a.smoke, a.out_root or default_root,
               a.nsteps if a.nsteps is not None else (300 if a.smoke else 15000),
               a.nsplits if a.nsplits is not None else (2 if a.smoke else 8),
               matrix=a.matrix, design=a.design, data_dir=a.data_dir,
               nburn=a.nburn if a.nburn is not None else (100 if a.smoke else I.NBURN),
               nwalkers=a.nwalkers, seed=a.seed, refit=a.refit, system=a.system,
               drop_specs=None if a.drop_specs is None else I.split_specs(a.drop_specs),
               oo_yield_proxies=a.oo_yield_proxies,
               only_variants=None if a.only_variants is None else I.split_specs(a.only_variants),
               dry_run=a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
