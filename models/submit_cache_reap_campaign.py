#!/usr/bin/env python3
"""Run cache/reap campaigns from a manifest (dry-run unless --submit).

cache builds and publishes caches, then audits reaping without deleting. It can
use a throttled Slurm array (default) or --execution local inside an allocation.
reap runs local reapers inside an allocation, independently re-verifies every
design, and requires the clean-only reap manifest produced by the summarizer.

Batch and interactive runs share the same per-design argument builder and
policy gates. Batch scripts freeze selected rows, resolved paths, cache cuts,
and corruption limits. Config is never reread by a running array task.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_config import parse_args as parse_workflow_args


def env(name, default=None):
    return os.environ.get(name, default)


# Pin the exact interpreter (the conda env's python) rather than relying on PATH resolution in
# the batch job, which may not inherit the env -- a literal "python" can resolve to a system
# interpreter without numpy. sys.executable is an absolute path under the shared software tree,
# valid on both login and compute nodes.
PYTHON = sys.executable

# Explicitly freeze the scientific cuts and parser policies into each batch
# script. Do not rely on the login shell's later state or Slurm export settings.
CACHE_ENV_DEFAULTS = {
    "HIC_ANALYSIS_CHARGED_ETA_MAX": "1.0",
    "HIC_ANALYSIS_IDENTIFIED_Y_MAX": "0.5",
    "HIC_ANALYSIS_IDENTIFIED_FLOW_Y_MAX": "1.0",
    "HIC_ANALYSIS_FLOW_PT_MIN": "0.2",
    "HIC_ANALYSIS_FLOW_PT_MAX": "5.0",
    "HIC_ANALYSIS_SUBEVENT_ETA_GAP": "0.15",
    "HIC_ANALYSIS_INVENTORY_ETA_MAX": "2.0",
    "HIC_MAX_DROPPED_EVENTS": "0",
}
SCOPE_CHECK_CODE = '''import hashlib, json, runpy, sys
module = runpy.run_path(sys.argv[1], run_name="cache_scope_preflight")
actual = module["build_fingerprint"]()
with open(sys.argv[2]) as f:
    expected = json.load(f)
digest = hashlib.sha256(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
if digest != sys.argv[3]:
    raise SystemExit("Frozen fingerprint changed since submission; regenerate and review the batch script")
if actual != expected:
    keys = sorted(k for k in actual.keys() | expected.keys() if actual.get(k) != expected.get(k))
    raise SystemExit("Cache environment does not match expected fingerprint: " + ", ".join(keys))
'''


def cache_environment(args):
    result = {key: env(key, default) for key, default in CACHE_ENV_DEFAULTS.items()}
    for key in ("HIC_PDG_DIR", "HIC_BARYON_DICT", "HIC_PDG_MASS"):
        if env(key) is not None:
            result[key] = expand(env(key))
    # Explicit provenance-table flags must select the actual parser tables too.
    if args.pdg_mass:
        result["HIC_PDG_MASS"] = args.pdg_mass
    if args.baryon_dict:
        result["HIC_BARYON_DICT"] = args.baryon_dict
    result["HIC_ALLOW_MALFORMED_ROWS"] = str(args.allow_malformed_rows)
    result["HIC_MAX_DROPPED_EVENTS"] = str(args.max_dropped_events)
    return result


def scope_check_argv(args):
    return [PYTHON, "-c", SCOPE_CHECK_CODE, args.cache_script,
            args.fingerprint, args.fingerprint_hash]


def expand(s):
    return None if s is None else os.path.expandvars(os.path.expanduser(str(s)))


def read_manifest(path):
    """Return list of (design, system) from a 'design system' rows file (blank/comment-skipping)."""
    rows = []
    seen = set()
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = ln.split()
            if len(parts) != 2:
                raise SystemExit(f"manifest line not 'design system': {ln!r}")
            if any('/' in part or part in ('.', '..') for part in parts):
                raise SystemExit(f"manifest design/system must be directory names: {ln!r}")
            row = tuple(parts)
            if row in seen:
                raise SystemExit(f"duplicate manifest row would run concurrent writers: {ln!r}")
            seen.add(row)
            rows.append(row)
    return rows


def must_exist(path, label):
    if path is None or not Path(path).exists():
        raise SystemExit(f"ERROR: {label} does not exist: {path}")


def wrapper_argv(args, design, system):
    """One argument builder for batch caching, interactive caching, and local reaping."""
    argv = [PYTHON, args.wrapper, args.stage,
            "--design", design, "--system", system,
            "--raw-design", str(Path(args.raw_root) / design / system),
            "--cfs-cache", str(Path(args.cache_cfs_root) / design / system),
            "--expected-fingerprint", args.fingerprint,
            "--record-dir", args.record_dir,
            "--reaper-script", args.reaper_script,
            "--cache-script", args.cache_script]
    if args.tmp_dir:
        argv += ["--tmp-dir", args.tmp_dir]
    if args.stage == "reap":
        return argv + ["--apply"]
    argv += ["--local-cache", str(Path(args.cache_local_root) / design / system),
             "--target-events", str(args.target_events),
             "--num-events", str(args.num_events), "--max-events", str(args.max_events),
             "--validate-events", str(args.validate_events), "--nprocs", str(args.nprocs),
             "--events-per-shard", str(args.events_per_shard),
             "--publish-script", args.publish_script,
             "--allow-malformed-rows", str(args.allow_malformed_rows)]
    if args.accept_shortfall:
        argv += ["--accept-shortfall", "--shortfall-reason", args.shortfall_reason]
    if args.pdg_mass:
        argv += ["--pdg-mass", args.pdg_mass]
    if args.baryon_dict:
        argv += ["--baryon-dict", args.baryon_dict]
    return argv


def run_wrapper_commands(args, rows, jobs):
    """Execute argument lists without a shell; propagate errors and quarantines."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    environment = {**os.environ, **getattr(args, "cache_environment", {})}

    def run_one(row):
        design, system = row
        log = Path(args.record_dir) / f'{design}_{system}_{args.stage}_dispatch.log'
        try:
            with log.open('w') as stream:
                rc = subprocess.run(wrapper_argv(args, design, system), env=environment,
                                    stdout=stream, stderr=subprocess.STDOUT).returncode
        except OSError as error:
            return row, 1, f'{log} ({error})'
        return row, rc, str(log)

    codes = []
    failures = 0
    interval = max(1, (len(rows) + 9) // 10)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(run_one, row) for row in rows]
        for future in as_completed(futures):
            (design, system), rc, log = future.result()
            codes.append(rc)
            if rc:
                failures += 1
            if getattr(args, 'details', False) or (rc and failures <= 10):
                result = ('cached / reap reviewed' if args.stage == 'cache' else 'reaped') if rc == 0 else (
                    'quarantined' if rc == 2 else f'failed (rc={rc})')
                print(f'{design}/{system}: {result}; log: {log}', flush=True)
            elif len(codes) % interval == 0 or len(codes) == len(rows):
                print(f'Progress: {len(codes)}/{len(rows)} designs; {failures} need review', flush=True)
    print(f'Completed: {codes.count(0)} successful, {codes.count(2)} quarantined, '
          f'{sum(code not in (0, 2) for code in codes)} failed. Logs: {args.record_dir}')
    # Preserve the wrapper's quarantine code; all other failures are execution errors.
    if any(code not in (0, 2) for code in codes):
        return 1
    return 2 if 2 in codes else 0


def build_sbatch(args, manifest_abspath, n_tasks, rows=None):
    """Freeze selected rows and resolved arguments into the script.

    Workers never reread the mutable selection manifest or interpolate user paths
    into shell commands. The existing fingerprint digest is checked again on-node.
    """
    rows = read_manifest(manifest_abspath) if rows is None else rows
    sb = ["#!/bin/bash",
          f"#SBATCH --job-name=cachereap_{args.stage}",
          *([f"#SBATCH --account={args.account}"] if args.account else []), f"#SBATCH --qos={args.qos}",
          f"#SBATCH --constraint={args.constraint}", f"#SBATCH --nodes={args.nodes_per_task}",
          f"#SBATCH --ntasks=1", f"#SBATCH --cpus-per-task={args.cores}",
          f"#SBATCH --time={args.time}", f"#SBATCH --array=0-{n_tasks - 1}%{args.throttle}",
          f"#SBATCH --output={shlex.quote(str(Path(args.slurm_log_root) / ('cachereap_' + args.stage + '_%A_%a.out')))}",
          "set -euo pipefail"]
    for key, value in sorted(args.cache_environment.items()):
        sb.append(f"export {key}={shlex.quote(value)}")
    sb.append(shlex.join(scope_check_argv(args)))
    sb.append(shlex.quote(PYTHON) + " - <<'PCE_CAMPAIGN_TASK'")
    sb += ["import argparse, os, runpy",
           f"workflow = runpy.run_path({str(Path(__file__).resolve())!r}, run_name='cache_campaign_worker')",
           f"args = argparse.Namespace(**{vars(args)!r})",
           f"rows = {rows!r}",
           "start = int(os.environ['SLURM_ARRAY_TASK_ID']) * args.designs_per_task",
           "selected = rows[start:start + args.designs_per_task]",
           "jobs = min(args.designs_per_task, max(1, args.cores // args.nprocs))",
           "raise SystemExit(workflow['run_wrapper_commands'](args, selected, jobs))",
           "PCE_CAMPAIGN_TASK"]
    return "\n".join(sb) + "\n", shlex.join(wrapper_argv(args, *rows[0]))


def run_local(args, rows):
    action = 'Cache/publish' if args.stage == 'cache' else 'Reap raw events'
    print(f"{action}: {'EXECUTE' if args.submit else 'PREVIEW'} locally, jobs={args.jobs}")
    print(f"manifest: {args.manifest} ({len(rows)} designs)")
    if args.details:
        print("First command: " + shlex.join(wrapper_argv(args, *rows[0])))
    print(f'Detailed per-design logs: {args.record_dir}')
    if not args.submit:
        print("Nothing executed. Re-run with --submit inside a Slurm allocation.")
        return
    raise SystemExit(run_wrapper_commands(args, rows, args.jobs))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", required=True, help="'design system' rows (make_cache_reap_manifest.py "
                    "for cache; summarizer --reap-manifest for reap).")
    ap.add_argument("--stage", required=True, choices=["cache", "reap"],
                    help="Action to run.")
    ap.add_argument("--system", default=env("HIC_CACHE_SYSTEM", "AuAu200"))
    ap.add_argument("--target-events", type=int, default=10000)

    # Paths (default from the centralized env file; all overridable).
    ap.add_argument("--raw-root", default=expand(env("HIC_RAW_ROOT")),
                    help="Dir containing design_*/SYSTEM (per-design --raw-design = raw-root/design/system).")
    ap.add_argument("--cache-cfs-root", default=None,
                    help="CFS cache root; per-design --cfs-cache = this/design/system.")
    ap.add_argument("--cache-local-root", default=None,
                    help="Local (scratch) cache root; per-design --local-cache = this/design/system.")
    ap.add_argument("--fingerprint", default=expand(env("HIC_EXPECTED_FINGERPRINT_V3")),
                    help="Frozen expected-fingerprint JSON for this system.")
    ap.add_argument("--record-dir", default=None,
                    help="Cache/reap verdict records (campaign.py supplies [pipeline] record_dir).")
    ap.add_argument("--slurm-log-root", default=expand("$PSCRATCH/slurm"))
    ap.add_argument("--tmp-dir", default=None)

    # Scripts (default from env).
    ap.add_argument("--wrapper", default=str(Path(__file__).resolve().parent / "cache/cache_reap_one_design.py"))
    ap.add_argument("--cache-script", default=expand(env("HIC_CACHE_SCRIPT", str(Path(__file__).resolve().parent / "cache/cache_one_design.py"))))
    ap.add_argument("--publish-script", default=expand(env("HIC_PUBLISH_SCRIPT", str(Path(__file__).resolve().parent / "cache/publish_cache_to_cfs.py"))))
    ap.add_argument("--reaper-script", default=expand(env("HIC_REAPER_SCRIPT", str(Path(__file__).resolve().parent / "cache/reap_raw_particle_lists.py"))))
    ap.add_argument("--pdg-mass", default=expand(env("HIC_PDG_MASS")))
    ap.add_argument("--baryon-dict", default=expand(env("HIC_BARYON_DICT")))

    # Slurm sizing.
    ap.add_argument("--account", default=None,
                    help="Slurm account for the cache array (default: your Slurm default account).")
    ap.add_argument("--qos", default="regular")
    ap.add_argument("--constraint", default="cpu")
    ap.add_argument("--nodes-per-task", type=int, default=1)
    ap.add_argument("--cores", type=int, default=128, help="Cores per node (for intra-task packing).")
    ap.add_argument("--nprocs", type=int, default=16, help="Procs the cache build uses per design.")
    ap.add_argument("--time", default="04:00:00")
    ap.add_argument("--throttle", type=int, default=8, help="Max concurrent array tasks (array %%N).")
    ap.add_argument("--jobs", type=int, default=16,
                    help="Parallel designs for --execution local (cache or reap; default 16).")
    ap.add_argument("--designs-per-task", type=int, default=1,
                    help="Designs per array task, run in parallel on the node (packs nodes).")

    # cache cache-build knobs.
    ap.add_argument("--num-events", type=int, default=10000)
    ap.add_argument("--max-events", type=int, default=10000)
    ap.add_argument("--validate-events", type=int, default=50)
    ap.add_argument("--events-per-shard", type=int, default=50)
    ap.add_argument("--allow-malformed-rows", type=int,
                    default=int(env("HIC_ALLOW_MALFORMED_ROWS", "0")),
                    help="Malformed-row limit; defaults to HIC_ALLOW_MALFORMED_ROWS or 0.")
    ap.add_argument("--accept-shortfall", action="store_true")
    ap.add_argument("--shortfall-reason", default=None)

    ap.add_argument("--force-reap-manifest", action="store_true",
                    help="Permit reap from a manifest not named like the summarizer's reap output.")
    ap.add_argument("--allow-reap-outside-slurm", action="store_true",
                    help="Permit reap --submit outside a Slurm allocation (default refuses, to "
                         "avoid launching local reapers on a login node).")
    ap.add_argument("--submit", action="store_true",
                    help="Execute for real. cache: sbatch the array. reap: run the local reap "
                         "loop. Default without --submit is a dry-run (print only).")
    ap.add_argument("--execution", choices=["batch", "local"], default=None,
                    help="cache: batch by default, or local inside salloc; reap is always local.")
    ap.add_argument("--max-dropped-events", type=int,
                    default=int(env("HIC_MAX_DROPPED_EVENTS", "0")),
                    help="Bad-event drop limit, frozen into batch and local cache environments.")
    ap.add_argument('--details', action='store_true', help='Show resolved commands and individual design completions.')
    args = parse_workflow_args(ap, "cache")
    args.execution = args.execution or ("batch" if args.stage == "cache" else "local")
    if args.stage == "reap" and args.execution != "local":
        ap.error("reap runs locally inside an allocation; use --execution local")
    for key in ("manifest", "raw_root", "cache_cfs_root", "cache_local_root", "fingerprint",
                "record_dir", "slurm_log_root", "tmp_dir", "wrapper", "cache_script",
                "publish_script", "reaper_script", "pdg_mass", "baryon_dict"):
        value = getattr(args, key)
        if value is not None:
            setattr(args, key, str(Path(expand(value)).resolve()))

    # ---- validation (fail before submitting) ----
    # Positive concurrency (guards div-by-zero in chunk math and cores//nprocs).
    for name in ("designs_per_task", "throttle", "nprocs", "cores", "jobs", "nodes_per_task", "target_events",
                 "num_events", "max_events", "validate_events", "events_per_shard"):
        if getattr(args, name) <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be > 0 (got {getattr(args, name)})")

    for name, needed in (("raw_root", True), ("cache_cfs_root", True), ("fingerprint", True),
                         ("record_dir", True),
                         ("cache_local_root", args.stage == "cache")):
        if needed and not getattr(args, name):
            raise SystemExit(f"--{name.replace('_', '-')} is required "
                             "(set it in the campaign INI and run through models/campaign.py)")

    must_exist(args.manifest, "manifest")
    must_exist(args.fingerprint, "expected-fingerprint")
    must_exist(args.wrapper, "wrapper script")
    must_exist(args.raw_root, "raw-root")
    for s, lbl in ((args.cache_script, "cache-script"), (args.reaper_script, "reaper-script")):
        must_exist(s, lbl)
    if args.stage == "cache":
        must_exist(args.publish_script, "publish-script")
        if args.accept_shortfall and not args.shortfall_reason:
            raise SystemExit("--accept-shortfall requires --shortfall-reason")
    if args.stage == "reap" and not args.force_reap_manifest:
        if "reap" not in os.path.basename(args.manifest).lower():
            raise SystemExit(
                "Refusing reap: --manifest does not look like the summarizer's reap output "
                "(name lacks 'reap'). Reaping deletes raw "
                "data; submit it against the clean-only "
                "manifest from summarize_cache_reap_records.py --reap-manifest, or pass "
                "--force-reap-manifest if you are certain.")
    # reap --submit is local parallel deletion; refuse it on a login node (no Slurm
    # allocation) so a forgotten salloc cannot launch reapers against the shared MDS from login.
    if (args.stage == "reap" and args.submit and "SLURM_JOB_ID" not in os.environ
            and not args.allow_reap_outside_slurm):
        raise SystemExit(
            "Refusing reap --submit outside a Slurm allocation (no SLURM_JOB_ID). Reap runs "
            "local parallel reapers; launching them on a login node hammers the shared MDS. Run "
            "inside salloc, or pass --allow-reap-outside-slurm if you really mean it.")

    if args.execution == "local" and args.submit and "SLURM_JOB_ID" not in os.environ:
        if args.stage != "reap" or not args.allow_reap_outside_slurm:
            ap.error("local --submit requires a Slurm allocation")
    if args.execution == "batch" and args.nodes_per_task != 1:
        ap.error("cache workers run on one node; --nodes-per-task must be 1")
    if args.stage == "cache" and args.execution == "batch" and args.nprocs > args.cores:
        ap.error("--nprocs exceeds the CPUs requested by --cores")
    if args.max_dropped_events < 0 or args.allow_malformed_rows < 0:
        ap.error("cache corruption limits must be nonnegative")

    rows = read_manifest(args.manifest)
    if not rows:
        print(f'No designs selected in {args.manifest}; nothing to cache/reap.')
        return
    other_sys = sorted({s for _d, s in rows if s != args.system})
    if other_sys:
        raise SystemExit(f"manifest contains systems other than --system {args.system}: {other_sys}")

    configured_system = env("HIC_CACHE_SYSTEM")
    if not configured_system:
        raise SystemExit("HIC_CACHE_SYSTEM is unset; run cache/reap through "
                         "models/campaign.py --config CFG, which exports the cache scope")
    if configured_system != args.system:
        raise SystemExit(f"the exported cache environment is for {configured_system}, but --system is "
                         f"{args.system}; use the campaign INI for that system")
    if args.stage == "cache":
        args.cache_environment = cache_environment(args)
        with open(args.fingerprint) as f:
            expected = json.load(f)
        args.fingerprint_hash = hashlib.sha256(
            json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checked = subprocess.run(scope_check_argv(args),
                                 env={**os.environ, **args.cache_environment})
        if checked.returncode:
            raise SystemExit("Cache scope preflight failed; no batch was submitted")

    Path(args.record_dir).mkdir(parents=True, exist_ok=True)

    # reap (reap) runs LOCALLY in the current allocation — metadata-bound, MDS is the shared
    # bottleneck, so a Slurm array would add overhead without speeding it up.
    if args.execution == "local":
        run_local(args, rows)
        return

    # cache (cache build) is CPU-bound and long; submit it as an unattended Slurm array.
    Path(args.slurm_log_root).mkdir(parents=True, exist_ok=True)
    n_designs = len(rows)
    chunk = args.designs_per_task
    n_tasks = (n_designs + chunk - 1) // chunk

    manifest_abspath = str(Path(args.manifest).resolve())
    script_text, wrapper_call = build_sbatch(args, manifest_abspath, n_tasks, rows=rows)
    script_path = Path(args.record_dir) / f"_submit_{args.stage}.sbatch"
    script_path.write_text(script_text)

    print(f"Cache/publish: {'SUBMIT' if args.submit else 'PREVIEW'} (raw files retained)")
    print(f"manifest        : {manifest_abspath}  ({n_designs} designs)")
    print(f"array tasks     : {n_tasks}  (designs-per-task={chunk}, throttle={args.throttle})")
    print(f"record-dir      : {args.record_dir}")
    print(f"sbatch script   : {script_path}")
    if args.details:
        print(f"cfs cache root  : {args.cache_cfs_root}")
        print(f"local cache root: {args.cache_local_root}")
        print(f"fingerprint     : {args.fingerprint}")
        print("First command: " + wrapper_call)

    if not args.submit:
        print("DRY-RUN. Wrote sbatch script above; not submitted.")
        print("Review the saved script (--details shows the first command); re-run with --submit.")
        print("After caching finishes, run campaign.py review to prepare the clean-only reap manifest.")
        return

    print(f"Submitting: sbatch {script_path}")
    r = subprocess.run(["sbatch", str(script_path)], capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    if r.returncode != 0:
        raise SystemExit(f"sbatch failed (rc={r.returncode})")



if __name__ == "__main__":
    main()
