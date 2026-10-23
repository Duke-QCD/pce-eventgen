#!/usr/bin/env python3

"""
Prepare parameters.dat, music_input, and initial.hdf for one design/system.

CLI paths and relative ManualNucleus table names are resolved from the launch
directory before subprocesses switch to run_dir. Existing initial.hdf is never
overwritten; this is an initializer, not an event-resume command.
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
import re
import shlex

CREATE_MUSIC_INPUT = Path(__file__).resolve().parent / "music-hydro/create_music_input.py"


def expand_path(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def die(msg: str, code: int = 1):
    print(f"[initialize] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def parse_args():
    p = argparse.ArgumentParser(description="Initialize a design-point run directory.")
    p.add_argument("run_dir", help="Directory in which to run events (created if missing).")
    p.add_argument("parameters_dat", help="Path to parameters.dat to copy into run_dir.")
    p.add_argument("nevents", type=int, help="Number of initial-condition events to generate.")
    p.add_argument("template", help="Path to music_input_template to copy into run_dir.")
    return p.parse_args()

def load_parameters(path):
    params = {}
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise ValueError(f"Bad line in parameters.dat: {raw}")
        k, v = [x.strip() for x in line.split("=", 1)]
        if re.match(r"^-?\d+$", v):
            v = int(v)
        elif re.match(r"^-?\d+(\.\d+)?([eE][-+]?\d+)?$", v):
            v = float(v)
        params[k] = v
    return params

def main():
    args = parse_args()

    run_dir = expand_path(args.run_dir)
    params_src = expand_path(args.parameters_dat)
    nevents = args.nevents
    template_path = expand_path(args.template)
    initial_file = 'initial.hdf'

    # ------------------------------------------------------------------
    # Check required inputs and the no-overwrite guard before staging files.
    # Helper/model failures can still leave a partial initialization; run_designs
    # checks for the complete bundle before repairing or submitting it.
    # ------------------------------------------------------------------
    if not template_path.is_file():
        die(f"template_path does not exist or is not a file: {template_path}")
    if not params_src.is_file():
        die(f"parameters.dat not found: {params_src}")
    if not CREATE_MUSIC_INPUT.is_file():
        die(f"MUSIC input helper not found: {CREATE_MUSIC_INPUT}")
    if nevents <= 0:
        die("nevents must be positive")

    if (run_dir / initial_file).exists():
        die(f"{initial_file} already exists in {run_dir}; refusing to overwrite "
            f"(delete it yourself if a re-run is intended)")

    try:
        params = load_parameters(params_src)
    except ValueError as e:
        die(str(e))

    for key in ("normalization", "nucleon_width", "gamma_fluctuation",
                "system", "cross_section", "grid_step", "grid_max"):
        if key not in params:
            die(f"parameters.dat is missing required key: {key}")
    normalization      = params["normalization"]
    nucleon_width      = params["nucleon_width"]
    gamma_fluctuation  = params["gamma_fluctuation"]
    system             = params["system"]
    cross_section      = params["cross_section"]
    grid_step          = params["grid_step"]
    grid_max           = params["grid_max"]

    # --- Species: Woods-Saxon names ('O', 'Au', ...) or ManualNucleus table
    #     paths (*.hdf5 etc). Table paths are checked up front so a purged or
    #     mistyped path fails here rather than mid-run. ---
    species = shlex.split(str(system))
    if len(species) != 2:
        die(f"system must be exactly two species tokens, got: {system!r}")
    for index, tok in enumerate(species):
        if tok.endswith((".hdf5", ".hdf", ".hd5", ".h5")):
            table = expand_path(tok)
            if not table.is_file():
                die(f"nucleus table not found: {table}")
            species[index] = str(table)

    # ------------------------------------------------------------------
    # WRITES start here.
    # ------------------------------------------------------------------
    run_dir.mkdir(parents=True, exist_ok=True)

    # Destinations inside run_dir
    template_dst = run_dir / "music_input_template"
    params_dst = run_dir / "parameters.dat"

    # Copy files (overwrite template, but don't re-copy params if it's the same file)
    try:
        if template_path != template_dst.resolve():
            shutil.copy2(template_path, template_dst)

        # Only copy parameters.dat if source and dest differ
        if params_src != params_dst.resolve():
            shutil.copy2(params_src, params_dst)
        else:
            print("[initialize] parameters.dat already in run_dir; not copying it again")

    except Exception as e:
        die(f"Failed to copy files: {e}")

    # Run the input creation step in run_dir so relative names match
    cmd = [
        sys.executable,
        str(CREATE_MUSIC_INPUT),
        "--params", "parameters.dat",
        "--template", "music_input_template",
        "--out", "music_input",
    ]

    try:
        print(f"[initialize] Running: {' '.join(cmd)} in {run_dir}")
        subprocess.run(cmd, cwd=run_dir, check=True)
    except FileNotFoundError:
        die(f"Could not execute MUSIC input helper: {CREATE_MUSIC_INPUT}")
    except subprocess.CalledProcessError as e:
        die(f"create_music_input.py failed with exit code {e.returncode}")

    trento_cmd = [
       'trento',
       '--grid-step', str(grid_step),
       '--grid-max', str(grid_max),
       '--output', initial_file,
       '--nucleon-width', str(nucleon_width),
       '--normalization', str(normalization),
       '--cross-section', str(cross_section),
       '--fluctuation', str(gamma_fluctuation),
       '--nucleon-min-dist', '1.0',   # active for WS species; inert for table (ManualNucleus) species
       *species,
       str(nevents)]

    try:
        print(f"[initialize] Running: {' '.join(trento_cmd)} in {run_dir}")
        subprocess.run(trento_cmd, cwd=run_dir, check=True)
    except FileNotFoundError:
        die("trento not found on PATH")
    except subprocess.CalledProcessError as e:
        die(f"trento failed with exit code {e.returncode}")

    print(f"[initialize] Done. Prepared {run_dir}")
    print(f"  - Template: {template_dst}")
    print(f"  - Params  : {params_dst}")
    print(f"  - Output  : {run_dir / 'music_input'}")
    print(f"  - Output  : {run_dir / initial_file}")
    

if __name__ == "__main__":
    main()
