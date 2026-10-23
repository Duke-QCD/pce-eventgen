#!/usr/bin/env python3
"""Run three O-O identified mean-pT variants sequentially, with per-run logs.

All variants are attempted. Any failed command makes the suite exit nonzero.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import shlex
import subprocess
import sys

import inference as I


VARIANTS = (
    ("idpt_XiOm", "mean_pT_K0S_vs_mult"),
    ("noidpt", "mean_pT_K0S_vs_mult,mean_pT_Xi_vs_mult,mean_pT_Omega_vs_mult"),
    ("idpt_all", ""),
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    I.add_input_arguments(parser, system=False, feeddown=False, drops=False)
    I.add_plot_arguments(parser)
    parser.add_argument("--out-root", type=Path, default=Path("."))
    parser.add_argument("--nsteps", type=int, default=I.NSTEPS)
    parser.add_argument("--nburn", type=int, default=I.NBURN)
    parser.add_argument("--nwalkers", type=int, default=I.NWALKERS)
    parser.add_argument("--seed", type=int, default=I.SEED)
    parser.add_argument("--nsplits", type=int, default=8)
    parser.add_argument("--emulator-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without writes or fitting.")
    args = parser.parse_args(argv)
    I.validate_run_sizes(args.nsteps, args.nburn, args.nwalkers, args.nsplits)
    if not args.dry_run:
        args.out_root.mkdir(parents=True, exist_ok=True)
    status_path = args.out_root / "plots_OO5360_suite.status"
    failures = 0
    for tag, drop in VARIANTS:
        output = args.out_root / f"plots_OO5360_{tag}"
        command = [sys.executable, "-u", str(I.ROOT / "inference.py"), "--system", "OO5360",
                   "--drop-specs", drop, "--out-dir", str(output)]
        for option in ("matrix", "design", "data_dir", "nsteps", "nburn", "nwalkers", "seed", "nsplits",
                       "plot_npost", "plot_nprior", "corner_font_scale"):
            value = getattr(args, option)
            if value is not None:
                command.extend(("--" + option.replace("_", "-"), str(value)))
        for flag in ("no_plots", "emulator_only"):
            if getattr(args, flag):
                command.append("--" + flag.replace("_", "-"))
        print(shlex.join(command), flush=True)
        if args.dry_run:
            continue
        with status_path.open("a") as status, output.with_suffix(".log").open("w") as log:
            status.write(f"START {tag} {datetime.now(timezone.utc).isoformat()}\n")
            status.flush()
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
            status.write(f"END {tag} exit={result.returncode} {datetime.now(timezone.utc).isoformat()}\n")
            failures += result.returncode != 0
    if not args.dry_run:
        print(f"Suite finished: {failures} failed. Status: {status_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
