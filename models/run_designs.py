#!/usr/bin/env python3
"""Initialize and repair design bundles, then submit per-design NERSC jobs.

Previews by default: lists designs that would be initialized, runs the repair as
a dry-run audit, and prints each design's route without changing event state or
submitting anything. Act explicitly with one of:
  --submit                    initialize ([run] initialize), repair ([repair]
                              apply_repairs) and submit generation jobs
  --repair-only [--apply]     repair audit, or apply repairs; never submits
  --initialize-only [--apply] list, or initialize, uninitialized designs only
--print-config prints the resolved options and exits.

Run between event-generation passes: repair assumes no selected design has
queued or running workers. CLI values override INI values, then script defaults.
User paths are relative to the launch directory, not the INI location.
"""

import argparse
import configparser
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_config import GENERATION_SECTIONS, EXTRA_SECTIONS, validate_sections
from workflow_config import expand_path as resolve_path
from surface_archive import configure_archive_base


REPO_ROOT = Path(__file__).resolve().parents[1]
INITIALIZATION_OUTPUTS = ("music_input", "initial.hdf")

SYSTEM_CONFIG = {
    # Per-system physics constants owned HERE, not in the base parameters
    # template: cross_section (inelastic sigma_NN, fm^2) and the TRENTo
    # transverse grid (grid_step, grid_max).  They are written into each
    # design's parameters.dat, from which initialize.py takes TRENTo's
    # --cross-section/--grid-step/--grid-max and create_music_input.py DERIVES
    # the MUSIC transverse lattice.  They must NOT also appear in the base
    # template (two sources would drift).  Stored as STRINGS so the rendered
    # parameters.dat is byte-identical to what the base template produced.
    #
    # cross_section is keyed on the SYSTEM because a system key here carries the
    # ENERGY as well as the species (OO@200 and OO@5360 would be separate keys).
    # These are CAMPAIGN CONVENTIONS, frozen once and never changed mid-campaign
    # -- not necessarily the best current literature values.  Reference values
    # (each school monotonic in sqrts -- NEVER mix schools):
    #   older Glauber convention (Duke/trento-docs lineage):
    #     200: 4.23   2760: 6.4   5020: 7.0 (~70+-5 mb)   5440: 7.07 (interp.)
    #   measured parametrization (Loizides et al.; arXiv:2507.05853):
    #     200: 4.16 (41.6 mb)  2760: 6.12  5020: 6.76  5360: 6.80 (68.0+-1.2 mb)
    "PbPb5020": {
        "system_string": "Pb Pb",
        "norm_key": "norm5020",
        "cross_section": "7.0",
        "grid_step": "0.1",
        "grid_max": "15",
    },
    "PbPb2760": {
        "system_string": "Pb Pb",
        "norm_key": "norm2760",
        "cross_section": "6.4",
        "grid_step": "0.1",
        "grid_max": "15",
    },
    "AuAu200": {
        "system_string": "Au Au",
        "norm_key": "norm200",
        # 4.23 fm^2 (42.3 mb), the 200 GeV inelastic sigma_NN.  A design
        # initialized with a different value keeps its own parameters.dat and
        # reports a NOTE on every pass.
        "cross_section": "4.23",
        "grid_step": "0.1",
        "grid_max": "15",
    },
    "OO5360": {
        # NLEFT/O13 ManualNucleus configuration table, given twice (projectile
        # and target).  Read-only on CFS; see o16_nleft_o13.hdf5.meta.json.
        "nucleus_table": "${HIC_CFS_ROOT}/nuclear_tables/o16_nleft_o13.hdf5",
        "norm_key": "norm5360",
        # 7.0 matches the older-convention 5.02-5.44 TeV plateau; the measured
        # value is 6.80 (68.0 +- 1.2 mb).  Keep it fixed within a campaign.
        "cross_section": "7.0",
        # 16-O is a small system: +/-10 fm at the same 0.1 fm spacing.
        # 200x200 cells vs 300x300 -> ~2.25x less hydro work and ~0.44x monolith.
        "grid_step": "0.1",
        "grid_max": "10",
    },
}

SCRIPT_DEFAULTS: Dict[str, Any] = {
    "launcher": None,
    "music_input_template": None,
    "prefix": "design",
    "jobs_per_design": 1,
    "mode": "hydro",
    "row_start": 0,
    "row_end": None,
    "initialize": False,
    "refuse_existing": False,
    "restore_archives": False,
    "max_afterburner_retries": 3,
    "apply_repairs": False,
    "repair_script": str(REPO_ROOT / "models/repair_stale_events.py"),
    "repair_log_root": None,
    "finalize_archive_failed": False,
    "archive_root": None,
    "archive_base": None,
    "zstd_bin": None,
    "zstd_level": None,
    "archive_full_verify": False,
    "archive_remove_local": False,
    "afterburner_done_template": None,
    "skip_complete": False,
    "time": None,
    "qos": None,
    "nodes": None,
    "ntasks_per_node": None,
    "cpus_per_task": None,
    "event_log_root": None,
    "slurm_log_root": None,
}

REQUIRED_KEYS = ["params", "design", "directory", "system", "nevents"]

CONFIG_SECTION_KEYS = GENERATION_SECTIONS

# Shared-INI sections owned by the cache/analysis tools; run_designs.py ignores them.
IGNORED_CONFIG_SECTIONS = set(EXTRA_SECTIONS)

INT_KEYS = {"row_start", "row_end", "nevents", "jobs_per_design", "nodes", "ntasks_per_node", "cpus_per_task", "max_afterburner_retries", "zstd_level"}
BOOL_KEYS = {"initialize", "refuse_existing", "restore_archives", "apply_repairs", "finalize_archive_failed", "archive_full_verify", "archive_remove_local"}
VALID_MODES = {"hydro", "afterburner", "both", "auto"}


def expand_path(path_like: str) -> Path:
    return Path(resolve_path(path_like))

def _parse_config_value(parser: configparser.ConfigParser, section: str, key: str) -> Any:
    if key in INT_KEYS:
        return parser.getint(section, key)
    if key in BOOL_KEYS:
        return parser.getboolean(section, key)
    value = parser.get(section, key)
    if value == "":
        return None
    return value


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """Load an INI config file and return argparse-style flat options."""
    if path is None:
        return {}

    config_path = expand_path(path)
    if not config_path.is_file():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    parser = configparser.ConfigParser(interpolation=None)
    read_files = parser.read(config_path)
    if not read_files:
        raise RuntimeError(f"Could not read config file: {config_path}")

    validate_sections(parser)
    known_sections = set(CONFIG_SECTION_KEYS) | IGNORED_CONFIG_SECTIONS
    unknown_sections = sorted(set(parser.sections()) - known_sections)
    if unknown_sections:
        raise ValueError(
            "Unknown config section(s): "
            + ", ".join(f"[{section}]" for section in unknown_sections)
        )

    flat: Dict[str, Any] = {}

    for section, allowed_keys in CONFIG_SECTION_KEYS.items():
        if not parser.has_section(section):
            continue

        section_keys = set(parser[section].keys())
        unknown_keys = sorted(section_keys - allowed_keys)
        if unknown_keys:
            raise ValueError(
                f"Unknown key(s) in [{section}]: "
                + ", ".join(unknown_keys)
            )

        for key in sorted(section_keys):
            flat[key] = _parse_config_value(parser, section, key)

    return flat

def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--config", default=None, help="INI config file with [files], [run], and [submit] sections")

    ap.add_argument("--params", default=None, help="base parameters.dat template file")
    ap.add_argument("--design", default=None, help="design_params.dat file")
    ap.add_argument(
        "--music-input-template",
        default=None,
        help=(
            "path to MUSIC input template passed to initialize.py "
            "(default: <directory>/music_input_template)"
        ),
    )
    ap.add_argument("--directory", default=None, help="root directory for all design runs")
    ap.add_argument(
        "--system",
        default=None,
        choices=sorted(SYSTEM_CONFIG.keys()),
        help="Which collision system to prepare and submit",
    )
    ap.add_argument(
        "--initialize",
        dest="initialize",
        action="store_true",
        default=None,
        help="run initialize.py before submitting jobs",
    )
    ap.add_argument(
        "--no-initialize",
        dest="initialize",
        action="store_false",
        help="do not run initialize.py, overriding config",
    )
    ap.add_argument(
        "--launcher",
        default=None,
        help="Slurm generation launcher (a profile's pce-eventgen)",
    )
    ap.add_argument("--prefix", default=None, help="prefix for design directories")
    ap.add_argument(
        "--jobs-per-design",
        type=int,
        default=None,
        help="number of jobs to submit per design point",
    )
    ap.add_argument(
        "--nevents",
        type=int,
        default=None,
        help="design event bound passed as NEVENTS (keep fixed across reruns)",
    )
    ap.add_argument(
        "--mode",
        choices=["hydro", "afterburner", "both", "auto"],
        default=None,
        help=(
            "stage run by run-events inside the Slurm job. 'auto' selects a real "
            "mode per design from the post-repair campaign_state.json: hydro-incomplete "
            "designs -> both; hydro-complete afterburner tails -> afterburner "
            "--restore-archives; ambiguous/exhausted -> skipped and flagged."
        ),
    )
    ap.add_argument(
        "--restore-archives",
        dest="restore_archives",
        action="store_true",
        default=None,
        help=(
            "For mode=afterburner only, allow run-events to restore missing "
            "surface.dat files from the mirrored CFS archive."
        ),
    )
    ap.add_argument(
        "--no-restore-archives",
        dest="restore_archives",
        action="store_false",
        help="Disable archived surface restoration, overriding config.",
    )
    ap.add_argument(
        "--max-afterburner-retries",
        type=int,
        default=None,
        help=(
            "Maximum afterburner attempts per event, counted across reruns via a "
            "persisted marker, exported to run-events as MAX_AFTERBURNER_RETRIES."
        ),
    )
    ap.add_argument(
        "--apply-repairs",
        dest="apply_repairs",
        action="store_true",
        default=None,
        help=(
            "Run repair_stale_events.py with --apply (delete safe-stale event "
            "dirs) before submitting. Without this the repair step runs as a "
            "dry-run audit only."
        ),
    )
    ap.add_argument(
        "--no-apply-repairs",
        dest="apply_repairs",
        action="store_false",
        help="Force the repair step to dry-run audit mode, overriding config.",
    )
    ap.add_argument(
        "--skip-complete",
        dest="skip_complete",
        action="store_true",
        default=None,
        help=(
            "Skip job submission for designs that already have >= nevents terminal "
            "events (afterburner.done or hydro.no_surface). Repair still runs for "
            "every selected design; only submission is skipped. Lets a single run over "
            "a full row range repair all designs but submit only to incomplete ones."
        ),
    )
    ap.add_argument(
        "--no-skip-complete",
        dest="skip_complete",
        action="store_false",
        help="Submit jobs for every selected design regardless of completeness (default).",
    )
    ap.add_argument(
        "--repair-script",
        default=None,
        help="path to repair_stale_events.py",
    )
    ap.add_argument(
        "--repair-log-root",
        default=None,
        help="directory for repair_stale_events.py logs (default: each run_dir)",
    )
    ap.add_argument(
        "--finalize-archive-failed",
        dest="finalize_archive_failed",
        action="store_true",
        default=None,
        help="During repair, finalize archive_failed events in place (re-archive the "
             "local surface, verify, write afterburner.done) instead of routing them to "
             "blocked. Recovers completed-but-unarchived events without re-running "
             "afterburner. Requires --archive-root and apply_repairs.",
    )
    ap.add_argument(
        "--no-finalize-archive-failed",
        dest="finalize_archive_failed",
        action="store_false",
        help="Disable archive finalization, overriding config.",
    )
    ap.add_argument(
        "--archive-root",
        dest="archive_root",
        default=None,
        help="Surface archive root used by both repair finalization and submitted event jobs.",
    )
    ap.add_argument("--zstd-bin", dest="zstd_bin", default=None,
                    help="zstd executable, forwarded to repair finalization.")
    ap.add_argument("--archive-base", default=None,
                    help="Original source base for surface archive paths; overrides [repair] archive_base "
                         "and HIC_SURFACE_ARCHIVE_BASE for repair and submitted jobs.")
    ap.add_argument("--zstd-level", dest="zstd_level", type=int, default=None,
                    help="zstd compression level, forwarded to repair finalization.")
    ap.add_argument("--archive-full-verify", dest="archive_full_verify", action="store_true",
                    default=None,
                    help="Forward decompress-and-compare verification to repair finalization.")
    ap.add_argument("--archive-remove-local", dest="archive_remove_local", action="store_true",
                    default=None,
                    help="Forward 'delete local surface after verified finalize' to repair.")
    ap.add_argument("--afterburner-done-template", dest="afterburner_done_template", default=None,
                    help="Forward a known-good afterburner.done template to repair finalization.")
    ap.add_argument(
        "--time",
        default=None,
        help="Slurm wall time, e.g. 05:00:00. If omitted, use pce-eventgen default.",
    )
    ap.add_argument(
        "--qos",
        default=None,
        help="Slurm QOS, e.g. regular, premium, debug, preempt. If omitted, use pce-eventgen default.",
    )
    ap.add_argument(
        "--nodes",
        type=int,
        default=None,
        help="Slurm node count. If omitted, use pce-eventgen default.",
    )
    ap.add_argument(
        "--ntasks-per-node",
        type=int,
        default=None,
        help="Slurm tasks per node. If omitted, use pce-eventgen/default srun behavior.",
    )
    ap.add_argument(
        "--cpus-per-task",
        type=int,
        default=None,
        help="Slurm CPUs per task. If omitted, use pce-eventgen default.",
    )
    ap.add_argument(
        "--event-log-root",
        default=None,
        help=(
            "Directory for run-events per-task logs, exported to pce-eventgen as LOG_ROOT. "
            "If omitted, pce-eventgen uses its default."
        ),
    )
    ap.add_argument(
        "--slurm-log-root",
        default=None,
        help=(
            "Directory for Slurm stdout/stderr files passed via sbatch --output/--error. "
            "If omitted, pce-eventgen #SBATCH defaults are used."
        ),
    )
    ap.add_argument(
        "--row-start",
        type=int,
        default=None,
        help=(
            "0-based first row of the design file to process. "
            "Default: 0."
        ),
    )
    ap.add_argument(
        "--row-end",
        type=int,
        default=None,
        help=(
            "0-based inclusive final row of the design file to process. "
            "If omitted, process through the last row."
        ),
    )
    ap.add_argument(
        "--refuse-existing",
        dest="refuse_existing",
        action="store_true",
        default=None,
        help="refuse to initialize or submit when a selected design directory already exists",
    )
    ap.add_argument(
        "--allow-existing",
        dest="refuse_existing",
        action="store_false",
        help="permit existing design directories (resume them), overriding config; "
             "existing initial conditions are never replaced",
    )
    ap.add_argument(
        "--print-config",
        action="store_true",
        help="print the resolved options before doing any work",
    )
    action = ap.add_mutually_exclusive_group()
    action.add_argument(
        "--submit",
        action="store_true",
        help="act: initialize (if [run] initialize), repair (applied if [repair] "
             "apply_repairs) and submit generation jobs. Without an action flag the "
             "command only previews.",
    )
    action.add_argument(
        "--repair-only",
        action="store_true",
        help="run only the repair step (a dry-run audit unless --apply); never submits",
    )
    action.add_argument(
        "--initialize-only",
        action="store_true",
        help="only list uninitialized designs (or initialize them with --apply); "
             "no repair or submission",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="with --repair-only, apply repairs; with --initialize-only, run initialization",
    )

def resolve_options(args: argparse.Namespace) -> argparse.Namespace:
    config_values = load_config(args.config)

    resolved: Dict[str, Any] = dict(SCRIPT_DEFAULTS)
    resolved.update(config_values)

    # Command-line arguments override config values when explicitly provided.
    for key, value in vars(args).items():
        if key in {"config", "print_config", "submit", "repair_only", "initialize_only", "apply"}:
            continue
        if value is not None:
            resolved[key] = value

    missing = [key for key in REQUIRED_KEYS if resolved.get(key) is None]
    if missing:
        raise ValueError(
            "Missing required option(s): "
            + ", ".join(f"--{key.replace('_', '-')}" for key in missing)
            + ". Provide them on the command line or in the INI config file."
        )

    if resolved["system"] not in SYSTEM_CONFIG:
        raise ValueError(
            f"Unknown system {resolved['system']!r}. Allowed: {', '.join(sorted(SYSTEM_CONFIG))}"
        )

    if resolved["mode"] not in VALID_MODES:
        raise ValueError(
            f"Unknown mode '{resolved['mode']}'. "
            f"Allowed values: {', '.join(sorted(VALID_MODES))}"
        )
        
    if resolved.get("restore_archives") and resolved["mode"] not in ("afterburner", "auto"):
        raise ValueError(
            "restore_archives=True is only allowed with mode in {afterburner, auto}. "
            f"Got mode={resolved['mode']!r}."
        )

    if resolved.get("row_start") is not None and int(resolved["row_start"]) < 0:
        raise ValueError(f"row_start must be >= 0, got {resolved['row_start']}")
    
    if resolved.get("row_end") is not None:
        if int(resolved["row_end"]) < int(resolved["row_start"]):
            raise ValueError(
                f"row_end ({resolved['row_end']}) must be >= "
                f"row_start ({resolved['row_start']})"
            )

    for key in INT_KEYS:
        if resolved.get(key) is not None:
            resolved[key] = int(resolved[key])

    for key in BOOL_KEYS:
        if resolved.get(key) is not None:
            resolved[key] = bool(resolved[key])

    if args.apply and not (args.repair_only or args.initialize_only):
        raise ValueError("--apply is only used with --repair-only or --initialize-only")

    # The action flags decide what this invocation may change; the INI only
    # supplies policy for an acting command.
    resolved.update(submit=args.submit, repair_only=args.repair_only,
                    initialize_only=args.initialize_only, apply=args.apply,
                    initialize_policy=bool(resolved.get("initialize")) or args.initialize_only)
    if args.initialize_only:
        resolved["initialize"] = args.apply
        resolved["apply_repairs"] = False
    elif args.repair_only:
        resolved["initialize"] = False
        resolved["apply_repairs"] = args.apply
    elif not args.submit:
        resolved["initialize"] = False
        resolved["apply_repairs"] = False
    if not resolved["apply_repairs"]:
        resolved["finalize_archive_failed"] = False

    return argparse.Namespace(
        **resolved,
        config=args.config,
        print_config=args.print_config,
    )


def print_config(args: argparse.Namespace) -> None:
    print("[resolved run_designs.py options]")
    for key in [
        "params",
        "design",
        "music_input_template",
        "directory",
        "system",
        "initialize",
        "launcher",
        "prefix",
        "row_start",
        "row_end",
        "jobs_per_design",
        "nevents",
        "mode",
        "restore_archives",
        "max_afterburner_retries",
        "qos",
        "time",
        "nodes",
        "ntasks_per_node",
        "cpus_per_task",
        "event_log_root",
        "slurm_log_root",
        "refuse_existing",
        "apply_repairs",
        "repair_script",
        "repair_log_root",
        "finalize_archive_failed",
        "archive_root",
        "archive_base",
        "zstd_bin",
        "zstd_level",
        "archive_full_verify",
        "archive_remove_local",
        "afterburner_done_template",
        "skip_complete",
        "submit",
        "repair_only",
        "initialize_only",
        "apply",
    ]:
        print(f"{key} = {getattr(args, key)}")



def parse_parameters_text(text: str, strict: bool = False) -> Dict[str, str]:
    params = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" in line:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
        if "=" not in line:
            if strict:
                raise ValueError(f"malformed line: {raw!r}")
            continue
        key, val = [x.strip() for x in line.split("=", 1)]
        params[key] = val
    return params


def load_base_parameters(path: Path) -> Dict[str, str]:
    params = parse_parameters_text(path.read_text())
    for k in ("cross_section", "grid_step", "grid_max"):
        if k in params:
            print(
                f"[run_designs] WARNING: base parameters file defines {k}={params[k]!r}; "
                f"it is IGNORED (SYSTEM_CONFIG owns it). Delete it from the "
                f"base template to avoid confusion.",
                file=sys.stderr,
            )
            del params[k]
    return params


def diff_parameters(old_text: str, new_text: str):
    """Key-level diff of two parameters.dat contents. Returns a list of
    human-readable change lines, empty if the two agree. Parsing of the existing
    file is STRICT: a malformed line is reported as a difference rather than
    silently skipped (create_music_input.py and initialize.py both reject such
    files, so a corrupt file must not compare equal here)."""
    try:
        old = parse_parameters_text(old_text, strict=True)
    except ValueError as e:
        return [f"    ! existing parameters.dat is malformed ({e})"]
    new = parse_parameters_text(new_text)
    lines = []
    for k in sorted(set(old) | set(new)):
        o, n = old.get(k), new.get(k)
        if o == n:
            continue
        if o is None:
            lines.append(f"    + {k} = {n}   (added)")
        elif n is None:
            lines.append(f"    - {k} = {o}   (removed)")
        else:
            lines.append(f"    ~ {k}: {o} -> {n}")
    return lines


def parse_design_params(path: Path):
    header = None
    designs = []

    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        
        # Allow commented headers
        if line.startswith("#"):
            uncommented = line[1:].strip()
            parts = uncommented.split()
            if parts and parts[0].lower() == "parameter":
                header = parts[1:]
            continue

        parts = line.split()

        if parts[0].lower() == "parameter":
            header = parts[1:]
            continue

        if header is None:
            raise ValueError("design_params.dat missing 'Parameter ...' header")

        if len(parts) != len(header):
            raise ValueError(
                f"Expected {len(header)} columns, got {len(parts)} in line: {line}"
            )

        row = {k: float(v) for k, v in zip(header, parts)}
        designs.append(row)

    return designs


def render_parameters_dat(
    base_params: Dict[str, str],
    design_row: Dict[str, float],
    selected_system: str,
) -> str:
    params = dict(base_params)
    scfg = SYSTEM_CONFIG[selected_system]

    # Initial condition
    if "nucleus_table" in scfg:
        table = expand_path(os.environ.get("HIC_O16_NUCLEUS_TABLE", scfg["nucleus_table"]))
        params["system"] = shlex.join([str(table), str(table)])
    else:
        params["system"] = scfg["system_string"]
    params["normalization"] = f"{design_row[scfg['norm_key']]}"
    params["nucleon_width"] = f"{design_row['w']}"
    params["gamma_fluctuation"] = f"{design_row['k']}"

    # Per-system constants owned by SYSTEM_CONFIG (see note there).
    params["cross_section"] = scfg["cross_section"]
    params["grid_step"] = scfg["grid_step"]
    params["grid_max"] = scfg["grid_max"]

    # Quark fugacities
    params["Initial_light_quark_fugacity"] = f"{design_row['gamma_l_0']}"
    params["Initial_strange_quark_fugacity"] = f"{design_row['gamma_s_0']}"
    params["Light_quark_equilibration_time"] = f"{design_row['tau_l']}"
    params["Strange_quark_equilibration_time"] = f"{design_row['tau_s']}"

    # Shear viscosity
    params["eta_over_s_low_T_slope_in_GeV"] = f"{design_row['eta_low']}"
    params["eta_over_s_high_T_slope_in_GeV"] = f"{design_row['eta_high']}"
    params["eta_over_s_at_kink"] = f"{design_row['eta_min']}"

    # Bulk viscosity
    params["zeta_over_s_max"] = f"{design_row['zeta_max']}"
    params["zeta_over_s_width_in_GeV"] = f"{design_row['zeta_width']}"
    params["zeta_over_s_T_peak_in_GeV"] = f"{design_row['zeta_T']}"
    params["zeta_over_s_lambda_asymm"] = f"{design_row['zeta_skew']}"

    lines = []
    sections = [
        ("# initial condition", [
            "system",
            "normalization",
            "nucleon_width",
            "gamma_fluctuation",
            "cross_section",
            "grid_step",
            "grid_max",
        ]),
        ("# quark fugacities", [
            "Initial_light_quark_fugacity",
            "Initial_strange_quark_fugacity",
            "Light_quark_equilibration_time",
            "Strange_quark_equilibration_time",
        ]),
        ("# shear viscosity", [
            "eta_over_s_T_kink_in_GeV",
            "eta_over_s_low_T_slope_in_GeV",
            "eta_over_s_high_T_slope_in_GeV",
            "eta_over_s_at_kink",
        ]),
        ("# bulk viscosity", [
            "zeta_over_s_max",
            "zeta_over_s_width_in_GeV",
            "zeta_over_s_T_peak_in_GeV",
            "zeta_over_s_lambda_asymm",
        ]),
    ]

    used = set()
    for header, keys in sections:
        lines.append("")
        lines.append(header)
        lines.append("")
        for k in keys:
            if k in params:
                lines.append(f"{k} = {params[k]}")
                used.add(k)

    extra_keys = sorted(k for k in params.keys() if k not in used)
    if extra_keys:
        lines.append("")
        lines.append("# other parameters")
        lines.append("")
        for k in extra_keys:
            lines.append(f"{k} = {params[k]}")

    lines.append("")
    lines.append("# end file")
    lines.append("")

    return "\n".join(lines)


def run_initialize(
    initialize_py: Path,
    run_dir: Path,
    params_path: Path,
    nevents: int,
    template: Path,
) -> None:
    cmd = [
        sys.executable,
        str(initialize_py),
        str(run_dir),
        str(params_path),
        str(nevents),
        str(template),
    ]
    print(f"[run_designs] Running initialize: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def run_repair(
    repair_script: Path,
    run_dir: Path,
    apply_repairs: bool,
    repair_log_root: Optional[Path] = None,
    target_events: Optional[int] = None,
    system: Optional[str] = None,
    max_afterburner_retries: Optional[int] = None,
    finalize_archive_failed: bool = False,
    archive_root: Optional[str] = None,
    archive_base: Optional[str] = None,
    zstd_bin: Optional[str] = None,
    zstd_level: Optional[int] = None,
    archive_full_verify: bool = False,
    archive_remove_local: bool = False,
    afterburner_done_template: Optional[str] = None,
) -> None:
    cmd = [sys.executable, str(repair_script), str(run_dir)]
    if archive_base is not None:
        cmd.extend(["--archive-base", str(archive_base)])
    if apply_repairs:
        cmd.append("--apply")
    # Clear stale afterburner.lock dirs so events whose afterburner was interrupted
    # (walltime/OOM/node death) and abandoned get rerun instead of being skipped
    # forever on a dead lock. Safe here because repair runs strictly between passes
    # with no active workers for this design, so any lock present is dead by
    # construction; --lock-stale-hours 0 means "clear regardless of age". This only
    # actually removes locks when --apply is also set (apply_repairs); in dry-run it
    # just reports them as stale_lock_would_clear.
    cmd.extend(["--clear-stale-locks", "--lock-stale-hours", "0"])
    if repair_log_root is not None:
        cmd.extend(["--log-root", str(repair_log_root)])
    if target_events is not None:
        cmd.extend(["--target-events", str(target_events)])
    if system is not None:
        cmd.extend(["--system", str(system)])
    if max_afterburner_retries is not None:
        cmd.extend(["--max-afterburner-retries", str(max_afterburner_retries)])
    # Archive-finalize pass-through: when enabled, repair re-archives completed-but-
    # unarchived events (archive_failed) and writes afterburner.done in place, so a
    # mode=auto sweep clears that backlog inline instead of routing it to blocked.
    if finalize_archive_failed:
        cmd.append("--finalize-archive-failed")
        if archive_root is not None:
            cmd.extend(["--archive-root", str(archive_root)])
        if zstd_bin is not None:
            cmd.extend(["--zstd-bin", str(zstd_bin)])
        if zstd_level is not None:
            cmd.extend(["--zstd-level", str(zstd_level)])
        if archive_full_verify:
            cmd.append("--archive-full-verify")
        if archive_remove_local:
            cmd.append("--archive-remove-local")
        if afterburner_done_template is not None:
            cmd.extend(["--afterburner-done-template", str(afterburner_done_template)])
    mode = "apply" if apply_repairs else "dry-run"
    print(f"[run_designs] Running stale-event repair ({mode}): {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def count_terminal_events(run_dir: Path) -> int:
    """Count event directories that have reached a terminal state: a successful
    afterburner (afterburner.done) or a clean no-surface hydro (hydro.no_surface).
    Used by --skip-complete to avoid submitting jobs to already-finished designs."""
    if not run_dir.is_dir():
        return 0
    n = 0
    for d in run_dir.iterdir():
        if d.is_dir() and d.name.isdigit():
            if (d / "afterburner.done").is_file() or (d / "hydro.no_surface").is_file():
                n += 1
    return n


CAMPAIGN_STATE = "campaign_state.json"
# Written by reap_raw_particle_lists.py after a fully verified reap. Its presence means the design
# is complete and its raw event dirs are gone, so run_designs must skip it entirely -- before
# repair and before any routing -- independent of --skip-complete.
REAPED_MARKER = "reaped.done"


def _read_marker_payload(marker: Path, label: str, integer_fields=()) -> Optional[Dict[str, Any]]:
    """Read a marker object, ignoring corrupt payloads before routing uses them."""
    if not marker.is_file():
        return None
    try:
        payload = json.loads(marker.read_text())
        if not isinstance(payload, dict):
            raise ValueError("expected a JSON object")
        # Check conversions without changing the returned payload or the existing
        # optional-field behavior. The callers still enforce count comparisons.
        for field in integer_fields:
            if payload.get(field) is not None:
                int(payload[field])
    except Exception as exc:
        print(f"[run_designs] ignoring unreadable {label} {marker}: {exc}", file=sys.stderr)
        return None
    return payload


def read_campaign_state(run_dir: Path, expected_system: str, expected_events: int) -> Optional[Dict[str, Any]]:
    """Return campaign_state.json payload (written by repair_stale_events.py) if it
    matches this run, else None.  This is the routing input for --mode auto."""
    marker = run_dir / CAMPAIGN_STATE
    payload = _read_marker_payload(marker, "campaign_state", ("target_events",))
    if payload is None:
        return None
    if payload.get("schema") != "campaign_state.v1":
        print(f"[run_designs] ignoring campaign_state with unknown schema: {marker}", file=sys.stderr)
        return None
    marker_system = payload.get("system")
    if marker_system is not None and marker_system != expected_system:
        print(
            f"[run_designs] ignoring campaign_state for wrong system: "
            f"{marker_system!r} != {expected_system!r}",
            file=sys.stderr,
        )
        return None
    marker_target = payload.get("target_events")
    if marker_target is not None and int(marker_target) != int(expected_events):
        print(
            f"[run_designs] ignoring campaign_state for wrong target_events: "
            f"{marker_target} != {expected_events}",
            file=sys.stderr,
        )
        return None
    return payload


def read_ready_to_cache_marker(run_dir: Path, expected_system: str, expected_events: int) -> Optional[Dict[str, Any]]:
    """Return ready_to_cache.json payload if it matches this run, else None.

    This is a production-readiness convenience signal written by
    repair_stale_events.py.  It is not a substitute for cache_one_design.py
    parsing/validation.
    """
    marker = run_dir / "ready_to_cache.json"
    payload = _read_marker_payload(
        marker, "ready marker", ("target_events", "terminal_ready_events")
    )
    if payload is None:
        return None
    if payload.get("schema") != "ready_to_cache.v1":
        print(f"[run_designs] ignoring ready marker with unknown schema: {marker}")
        return None

    marker_system = payload.get("system")
    if marker_system is not None and marker_system != expected_system:
        print(
            f"[run_designs] ignoring ready marker for wrong system: "
            f"{marker_system!r} != {expected_system!r}"
        )
        return None

    marker_target = payload.get("target_events")
    if marker_target is not None and int(marker_target) != int(expected_events):
        print(
            f"[run_designs] ignoring ready marker for wrong target_events: "
            f"{marker_target} != {expected_events}"
        )
        return None

    terminal_ready = payload.get("terminal_ready_events")
    if terminal_ready is not None and int(terminal_ready) < int(expected_events):
        print(
            f"[run_designs] ignoring ready marker with too few terminal-ready events: "
            f"{terminal_ready} < {expected_events}"
        )
        return None

    return payload


def submit_job(
    submit_script: Path,
    run_dir: Path,
    design_name: str,
    job_index: int,
    nevents_per_job: int,
    mode: str,
    restore_archives: bool = False,
    max_afterburner_retries: int = 3,
    qos: Optional[str] = None,
    time: Optional[str] = None,
    nodes: Optional[int] = None,
    ntasks_per_node: Optional[int] = None,
    cpus_per_task: Optional[int] = None,
    event_log_root: Optional[Path] = None,
    slurm_log_root: Optional[Path] = None,
    no_submit: bool = False,
    archive_root: Optional[Path] = None,
    archive_base: Optional[str] = None,
) -> None:
    env_items = [
        "ALL",
        f"RUNDIR={run_dir}",
        f"DESIGN_ID={design_name}",
        f"JOB_INDEX={job_index}",
        f"NEVENTS={nevents_per_job}",
        f"MODE={mode}",
        f"RESTORE_ARCHIVES={'true' if restore_archives else 'false'}",
        f"MAX_AFTERBURNER_RETRIES={max_afterburner_retries}",
    ]

    if event_log_root is not None:
        env_items.append(f"LOG_ROOT={event_log_root}")
    if archive_root is not None:
        env_items.append(f"HIC_SURFACE_ARCHIVE_ROOT={archive_root}")
    if archive_base is not None:
        env_items.append(f"HIC_SURFACE_ARCHIVE_BASE={archive_base}")

    cmd = ["sbatch"]

    # These must be sbatch options because #SBATCH directives are parsed
    # before the script body runs. Passing them here overrides the defaults
    # in pce-eventgen without needing separate submit scripts for hydro and
    # afterburner jobs.
    if qos is not None:
        cmd.append(f"--qos={qos}")
    if time is not None:
        cmd.append(f"--time={time}")
    if nodes is not None:
        cmd.append(f"--nodes={nodes}")
    if ntasks_per_node is not None:
        cmd.append(f"--ntasks-per-node={ntasks_per_node}")
    if cpus_per_task is not None:
        cmd.append(f"--cpus-per-task={cpus_per_task}")
    if slurm_log_root is not None:
        slurm_log_root.mkdir(parents=True, exist_ok=True)
        cmd.append(f"--output={slurm_log_root}/%x-%j.out")
        cmd.append(f"--error={slurm_log_root}/%x-%j.err")

    cmd.extend([
        f"--export={','.join(env_items)}",
        str(submit_script),
    ])

    if no_submit:
        print(f"[run_designs] Would submit: {' '.join(cmd)}")
        return
    print(f"[run_designs] Submitting job: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Generate per-design parameters, optionally initialize, and submit NERSC jobs. "
            "Options may be supplied on the command line or via --config INI; "
            "command-line values override config values."
        )
    )
    add_arguments(ap)
    raw_args = ap.parse_args()

    try:
        args = resolve_options(raw_args)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    if args.print_config:
        print_config(args)
        return

    base_params_path = expand_path(args.params)
    design_file = expand_path(args.design)
    run_root = expand_path(args.directory)
    initialize_py = REPO_ROOT / "models/initialize.py"
    if args.submit and args.launcher is None:
        ap.error("--submit needs a launcher: set [files] launcher or pass --launcher")
    submit_script = expand_path(args.launcher) if args.launcher is not None else None
    event_log_root = (
        expand_path(args.event_log_root)
        if args.event_log_root is not None
        else None
    )
    slurm_log_root = (
        expand_path(args.slurm_log_root)
        if args.slurm_log_root is not None
        else None
    )
    repair_script = expand_path(args.repair_script)
    repair_log_root = (
        expand_path(args.repair_log_root)
        if args.repair_log_root is not None
        else None
    )
    archive_root = expand_path(args.archive_root) if args.archive_root is not None else None
    try:
        archive_base = configure_archive_base(args.archive_base, run_root)
    except ValueError as error:
        ap.error(str(error))
    # Archive-finalize options forwarded to every run_repair call.  Validate up front:
    # finalizing without an archive root, or without apply_repairs, would be a silent
    # no-op, so fail loudly instead.
    if args.finalize_archive_failed:
        if not args.archive_root:
            ap.error("--finalize-archive-failed requires --archive-root")
        if not args.apply_repairs:
            ap.error("--finalize-archive-failed requires apply_repairs (otherwise repair "
                     "only dry-runs the finalize and nothing is recovered)")
    finalize_repair_kwargs = dict(
        finalize_archive_failed=args.finalize_archive_failed,
        archive_root=archive_root,
        archive_base=archive_base,
        zstd_bin=args.zstd_bin,
        zstd_level=args.zstd_level,
        archive_full_verify=args.archive_full_verify,
        archive_remove_local=args.archive_remove_local,
        afterburner_done_template=args.afterburner_done_template,
    )
    music_input_template = (
        expand_path(args.music_input_template)
        if args.music_input_template is not None
        else run_root / "music_input_template"
    )

    if not base_params_path.is_file():
        print(f"ERROR: base parameters file not found: {base_params_path}", file=sys.stderr)
        sys.exit(1)
    if not design_file.is_file():
        print(f"ERROR: design file not found: {design_file}", file=sys.stderr)
        sys.exit(1)
    if args.initialize and not initialize_py.is_file():
        print(f"ERROR: initialize.py not found: {initialize_py}", file=sys.stderr)
        sys.exit(1)
    if args.submit and not submit_script.is_file():
        print(f"ERROR: submit script not found: {submit_script}", file=sys.stderr)
        sys.exit(1)
    if args.initialize and not music_input_template.is_file():
        print(f"ERROR: MUSIC input template not found: {music_input_template}", file=sys.stderr)
        sys.exit(1)
    if not repair_script.is_file():
        print(f"ERROR: repair script not found: {repair_script}", file=sys.stderr)
        sys.exit(1)

    run_root.mkdir(parents=True, exist_ok=True)
    if event_log_root is not None:
        event_log_root.mkdir(parents=True, exist_ok=True)
    if slurm_log_root is not None:
        slurm_log_root.mkdir(parents=True, exist_ok=True)

    base_params = load_base_parameters(base_params_path)
    designs = parse_design_params(design_file)
    
    row_start = args.row_start
    row_end = args.row_end if args.row_end is not None else len(designs) - 1
    
    if not designs:
        print("ERROR: No designs found in the design table", file=sys.stderr)
        sys.exit(1)

    if row_start >= len(designs):
        print(
            f"ERROR: row_start={row_start} is outside design file with "
            f"{len(designs)} rows",
            file=sys.stderr,
        )
        sys.exit(1)
    
    if row_end >= len(designs):
        print(
            f"ERROR: row_end={row_end} is outside design file with "
            f"{len(designs)} rows",
            file=sys.stderr,
        )
        sys.exit(1)
    
    selected_designs = designs[row_start:row_end + 1]

    print(f"[run_designs] Found {len(designs)} designs")
    print(
        f"[run_designs] Selected rows {row_start}..{row_end} "
        f"({len(selected_designs)} designs)"
    )
    selected_system = args.system
    acting = args.submit or (args.initialize_only and args.apply)
    if not (acting or args.repair_only):
        print("[run_designs] PREVIEW: no initialization, applied repair or submission. "
              "Repair runs as a dry-run audit. Act with --submit.")

    # Refuse before changing anything when existing (unreaped) design directories
    # are selected and the profile protects them; previews only report them.
    existing = [
        f"{args.prefix}_{row:03d}" for row in range(row_start, row_end + 1)
        if (run_root / f"{args.prefix}_{row:03d}" / selected_system).exists()
        and not (run_root / f"{args.prefix}_{row:03d}" / selected_system / REAPED_MARKER).is_file()
    ]
    if existing and args.refuse_existing and not args.repair_only:
        message = (f"{len(existing)} selected design directories already exist "
                   f"({', '.join(existing[:5])}{', ...' if len(existing) > 5 else ''}); "
                   f"refuse_existing is set. After checking they belong to this campaign, "
                   f"resume them with --allow-existing.")
        if acting:
            print(f"ERROR: {message}", file=sys.stderr)
            sys.exit(1)
        print(f"[run_designs] NOTE: {message}")

    # --- parameters.dat gating bookkeeping ---------------------------------
    params_uninitialized: list = []  # no parameters.dat and initialization not enabled
    params_incomplete: list = []     # parameters.dat present but the bundle is not
    params_would_initialize: list = []  # preview: --submit would initialize these

    # Preflight the design table against the selected system, so a missing
    # column (e.g. norm5360 for OO5360) reports clearly instead of raising a
    # bare KeyError partway through the range.
    try:
        render_parameters_dat(base_params, designs[row_start], selected_system)
    except KeyError as e:
        print(
            f"ERROR: design table {args.design} has no column {e} required for "
            f"system {selected_system}",
            file=sys.stderr,
        )
        sys.exit(1)

    # --- mode=auto bookkeeping ---------------------------------------------
    auto_mode = args.mode == "auto"
    auto_routes: Dict[str, list] = {
        "both": [], "afterburner": [], "ready": [],
        "blocked": [], "in_progress": [], "recheck": [], "no_state": [],
        "mixed_blocked": [], "reaped": [],
    }
    if auto_mode:
        print(
            "[run_designs] mode=auto: per-design route is read from the "
            "campaign_state.json written by repair.\n"
            "[run_designs] IMPORTANT: run this BETWEEN passes only -- repair's "
            "safety relies on no Slurm worker writing event dirs for the selected "
            "designs.  Do not launch while jobs for these designs are queued/running."
        )
        if args.submit and not args.apply_repairs:
            print(
                "[run_designs] WARNING: mode=auto with apply_repairs=False. "
                "Safe-stale/broken dirs will be classified but not cleaned, so "
                "'both' routes may not make progress.  Prefer apply_repairs=True.",
                file=sys.stderr,
            )

    for row_index in range(row_start, row_end + 1):
        design_row = designs[row_index]
        design_name = f"{args.prefix}_{row_index:03d}"
        design_dir = run_root / design_name
        run_dir = design_dir / selected_system

        # A fully-reaped design carries reaped.done (written by the reaper). Skip it entirely --
        # before the overwrite check, before repair, before routing, and regardless of
        # --skip-complete. Its raw event dirs are gone, so repair would misclassify it and auto
        # routing could resubmit it off a stale campaign_state.json. Presence == done.
        if run_dir.exists() and (run_dir / REAPED_MARKER).is_file():
            print(
                f"[run_designs] {design_name}/{selected_system}: {REAPED_MARKER} present; "
                f"design fully reaped, skipping repair and submission"
            )
            if auto_mode:
                auto_routes["reaped"].append(design_name)
            continue

        params_path = run_dir / "parameters.dat"
        desired_params = render_parameters_dat(base_params, design_row, selected_system)

        if not params_path.exists():
            if not args.initialize:
                # parameters.dat is a shipped artifact (OSG staging pulls it) and the
                # source create_music_input.py derives the MUSIC lattice from.  An
                # uninitialized design must not be repaired or submitted into.
                if args.initialize_policy and not args.repair_only:
                    print(f"[run_designs] {design_name}/{selected_system}: not initialized; "
                          f"would initialize")
                    params_would_initialize.append(design_name)
                else:
                    print(
                        f"[run_designs] {design_name}/{selected_system}: no parameters.dat; "
                        f"design is not initialized (set [run] initialize = true).  Skipping.",
                        file=sys.stderr,
                    )
                    params_uninitialized.append(design_name)
                continue
            print(f"[run_designs] Writing parameters for {design_name}/{selected_system}")
            run_dir.mkdir(parents=True, exist_ok=True)
            params_path.write_text(desired_params)
            run_initialize(initialize_py, run_dir, params_path, args.nevents, music_input_template)
        else:
            # parameters.dat, music_input and initial.hdf are ONE bundle, created
            # together at initialization.  Never rewrite it under a live design:
            # the file that actually drives the run is music_input, so a silent
            # rewrite makes the authoritative-looking file the stale one.
            # Per-design edits to parameters.dat are legitimate (the base template
            # is a starting point, not a spec), so a difference is reported and
            # never blocks: the existing file is what generated this design's
            # events and is left exactly as it is.
            drift = diff_parameters(params_path.read_text(), desired_params)
            if drift:
                print(
                    f"[run_designs] {design_name}/{selected_system}: NOTE parameters.dat "
                    f"differs from the base-template render ("
                    + "; ".join(d.strip() for d in drift)
                    + "); keeping the existing file."
                )
            else:
                print(
                    f"[run_designs] {design_name}/{selected_system}: parameters.dat present "
                    f"and unchanged; already initialized"
                )

            # parameters.dat alone does not prove initialization finished.  Require
            # the whole bundle before repairing or submitting into this design.
            missing = [f for f in INITIALIZATION_OUTPUTS
                       if not (run_dir / f).exists()]
            if missing:
                print(
                    f"[run_designs] {design_name}/{selected_system}: REFUSING -- "
                    f"incomplete initialization, missing {', '.join(missing)}.  "
                    f"Delete parameters.dat (and initial.hdf if present) and re-run "
                    f"with --initialize.",
                    file=sys.stderr,
                )
                params_incomplete.append(design_name)
                continue

        if args.initialize_only:
            continue

        # Repair runs between event-running passes, after any initialization and
        # before submitting jobs, so no Slurm worker is writing the event dirs.
        # apply_repairs=False audits in dry-run; True deletes safe-stale dirs.
        run_repair(
            repair_script,
            run_dir,
            args.apply_repairs,
            repair_log_root,
            target_events=args.nevents,
            system=selected_system,
            max_afterburner_retries=args.max_afterburner_retries,
            **finalize_repair_kwargs,
        )

        if auto_mode:
            state = read_campaign_state(run_dir, selected_system, args.nevents)
            if state is None:
                print(
                    f"[run_designs] {design_name}/{selected_system}: "
                    f"no usable campaign_state.json after repair; skipping (flagged).",
                    file=sys.stderr,
                )
                auto_routes["no_state"].append(design_name)
                continue

            route = state.get("route")
            tallies = state.get("tallies", {})

            if route == "both":
                real_mode, real_restore = "both", False
            elif route == "afterburner":
                # restore is forced on: afterburner-routed tails may have local OR
                # archived surfaces; restore is a no-op when the surface is present.
                real_mode, real_restore = "afterburner", True
                if not args.restore_archives:
                    print(
                        f"[run_designs] {design_name}/{selected_system}: NOTE "
                        f"--mode auto forces restore_archives=True for afterburner "
                        f"routes; overriding restore_archives={args.restore_archives}.",
                        file=sys.stderr,
                    )
            elif route == "ready":
                print(
                    f"[run_designs] {design_name}/{selected_system}: route=ready "
                    f"({tallies.get('ready')}/{args.nevents} terminal-ready); "
                    f"skipping (cache separately)."
                )
                auto_routes["ready"].append(design_name)
                continue
            else:
                # blocked / in_progress / recheck / unknown -> never auto-run.
                auto_routes.setdefault(route, []).append(design_name)
                print(
                    f"[run_designs] {design_name}/{selected_system}: route={route}; "
                    f"skipping and flagging.  tallies={tallies}",
                    file=sys.stderr,
                )
                continue

            print(
                f"[run_designs] {design_name}/{selected_system}: route={route} -> "
                f"mode={real_mode} restore_archives={real_restore}; "
                f"{'submitting' if args.submit else 'would submit'} "
                f"{args.jobs_per_design} job(s).  tallies={tallies}"
            )
            auto_routes[route].append(design_name)
            # A productive route can co-exist with blocked/ambiguous events
            # (priority is productive-first).  Those are NOT handled this pass and
            # would otherwise only surface a pass later; flag them now.
            if tallies.get("blocked", 0) > 0:
                auto_routes["mixed_blocked"].append(design_name)
                print(
                    f"[run_designs] {design_name}/{selected_system}: NOTE route={route} "
                    f"but {tallies['blocked']} blocked/ambiguous event(s) remain and will "
                    f"not be resolved by this pass (see campaign_mixed_blocked.txt).",
                    file=sys.stderr,
                )
        else:
            real_mode, real_restore = args.mode, args.restore_archives

        if args.repair_only:
            continue

        if not auto_mode and args.skip_complete:
            ready_marker = read_ready_to_cache_marker(run_dir, selected_system, args.nevents)
            if ready_marker is not None:
                n_ready = ready_marker.get("terminal_ready_events", "?")
                print(
                    f"[run_designs] {design_name}/{selected_system}: "
                    f"ready_to_cache marker present ({n_ready}/{args.nevents} terminal-ready); "
                    f"skipping job submission"
                )
                continue

            n_terminal = count_terminal_events(run_dir)
            if n_terminal >= args.nevents:
                print(
                    f"[run_designs] {design_name}/{selected_system}: "
                    f"{n_terminal}/{args.nevents} terminal events; design complete by marker count, "
                    f"skipping job submission"
                )
                continue
            print(
                f"[run_designs] {design_name}/{selected_system}: "
                f"{n_terminal}/{args.nevents} terminal events; submitting "
                f"{args.jobs_per_design} job(s)"
            )

        for job_index in range(1, args.jobs_per_design + 1):
            submit_job(
                submit_script=submit_script,
                run_dir=run_dir,
                design_name=design_name,
                job_index=job_index,
                nevents_per_job=args.nevents,
                mode=real_mode,
                restore_archives=real_restore,
                max_afterburner_retries=args.max_afterburner_retries,
                qos=args.qos,
                time=args.time,
                nodes=args.nodes,
                ntasks_per_node=args.ntasks_per_node,
                cpus_per_task=args.cpus_per_task,
                event_log_root=event_log_root,
                slurm_log_root=slurm_log_root,
                no_submit=not args.submit,
                archive_root=archive_root,
                archive_base=archive_base,
            )

    if auto_mode:
        manifest_root = run_root
        summary_lines = []
        for route_name in ("both", "afterburner", "ready", "blocked",
                            "in_progress", "recheck", "no_state", "mixed_blocked", "reaped"):
            names = auto_routes.get(route_name, [])
            summary_lines.append(f"  {route_name}: {len(names)}")
            manifest = manifest_root / f"campaign_{route_name}.txt"
            manifest.write_text("\n".join(names) + ("\n" if names else ""))
        verb = "submitted" if args.submit else "would submit"
        print("[run_designs] mode=auto route summary "
              f"({verb}; manifests in {manifest_root}/campaign_*.txt):")
        for line in summary_lines:
            print(line)
        flagged = (auto_routes.get("blocked", []) + auto_routes.get("recheck", [])
                   + auto_routes.get("no_state", []))
        if flagged:
            print("[run_designs] designs needing manual attention (blocked/recheck/no_state): "
                  + ", ".join(flagged), file=sys.stderr)
        if auto_routes.get("mixed_blocked"):
            print("[run_designs] designs submitted productively but still carrying "
                  "blocked/ambiguous events (campaign_mixed_blocked.txt): "
                  + ", ".join(auto_routes["mixed_blocked"]), file=sys.stderr)

    if params_would_initialize:
        print(f"[run_designs] would initialize {len(params_would_initialize)} design(s): "
              + ", ".join(params_would_initialize))

    if params_uninitialized or params_incomplete:
        print("[run_designs] parameters.dat gating summary:", file=sys.stderr)
        if params_incomplete:
            print(f"  incomplete initialization (refused): {len(params_incomplete)}: "
                  + ", ".join(params_incomplete), file=sys.stderr)
        if params_uninitialized:
            print(f"  uninitialized (enable [run] initialize): {len(params_uninitialized)}: "
                  + ", ".join(params_uninitialized), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
