#!/usr/bin/env python3
"""
stage_osg_design.py -- stage ONE OSG design end-to-end, on the AP.

Runs ON THE AP. For a given design it:
  1. PULLS the per-design monolith (initial.hdf) + parameters.dat + music_input
     from NERSC via rsync over the sshproxy cert (NERSC->AP, the proven direction).
  2. QUERIES PSCRATCH/design_<N> for already-DRAINED OSG events (drained.done) ->
     these are skipped so a resumed/partial design only stages its GAP.
     A design-level reaped.done skips staging altogether; the reaper removed
     its per-event markers, so their absence must not trigger regeneration.
     PARTITION-VIOLATION GUARD: if the design has terminal events WITHOUT a
     drained.done marker (afterburner.done / hydro.no_surface only), those are
     almost certainly NERSC events. Staging OSG over them would MIX generic-OSG and
     native-NERSC builds in one design. The script ABORTS and tells you to resolve
     it manually (move the NERSC events aside, as was done for design_499) -- it
     never silently fills gaps around NERSC events.
  3. SPLITS the monolith via split_design.py with the gap skip-list -> per-event
     slots + a manifest for this design.
  4. DELETES the pulled monolith (it's ~GBs and only needed as split input) to
     reclaim AP space. Slots (~tiny) remain for the run; the drain + a teardown
     step reclaim those later.

PIPELINING: safe to run for design N+1 while design N runs/drains -- it only
touches design N+1's monolith/slots and reads (not writes) PSCRATCH.

ASSUMPTION: the monolith, parameters.dat, and music_input for a design all live in
--nersc-ic-dir on NERSC (with {design} substituted). If yours live elsewhere,
select a preserved directory containing the matching three-file bundle with
--nersc-ic-dir; there are no separate per-file source flags.
"""

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


def run(cmd, timeout=3600, input_text=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=input_text)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def ssh_nersc(nersc_host, remote_cmd, ssh_opts, timeout=300):
    return run(["ssh"] + ssh_opts + [nersc_host, remote_cmd], timeout=timeout)


def query_done_and_guard(nersc_host, pscratch_design_dir, ssh_opts):
    """ONE ssh: classify terminal events in the PSCRATCH design dir.
    Returns (drained_set, suspect_nersc_set, reaped).
      drained_set     : events with drained.done  (OSG, already done -> skip/resume)
      suspect_nersc   : events with afterburner.done OR hydro.no_surface but NO
                        drained.done (likely NERSC -> partition violation)
    A missing design dir yields two empty sets and reaped=False (fresh design)."""
    # emit, per event dir: "<ev> <hasDrained> <hasTerminal>"
    root = shlex.quote(pscratch_design_dir)
    cmd = (
        f'if [ ! -e {root} ]; then exit 0; fi; '
        f'cd {root} || exit 1; '
        'if [ -f reaped.done ]; then echo REAPED; exit 0; fi; '
        'for d in */; do '
        'e="${d%/}"; '
        'case "$e" in ""|*[!0-9]*) continue;; esac; '
        'dr=0; te=0; '
        '[ -f "${d}drained.done" ] && dr=1; '
        '{ [ -f "${d}afterburner.done" ] || [ -f "${d}hydro.no_surface" ]; } && te=1; '
        'echo "$e $dr $te"; '
        'done; exit 0'
    )
    rc, out, err = ssh_nersc(nersc_host, cmd, ssh_opts, timeout=180)
    if rc != 0:
        sys.exit(f"[query] ssh to NERSC failed rc={rc}: {err.strip()}\n"
                 f" -> is the sshproxy cert valid? run sshproxy.sh on the AP.")
    drained, suspect = set(), set()
    reaped = False
    for line in out.splitlines():
        if line.strip() == 'REAPED':
            reaped = True
            continue
        parts = line.split()
        if len(parts) != 3:
            continue
        ev, dr, te = parts[0], parts[1] == "1", parts[2] == "1"
        if dr:
            drained.add(ev)
        elif te:
            suspect.add(ev)
    return drained, suspect, reaped


def pull_file(nersc_host, remote_path, dest_dir, ssh_opts, timeout=3600):
    dest_dir.mkdir(parents=True, exist_ok=True)
    rsync = ["rsync", "-a", "--timeout=1200"]
    if ssh_opts:
        rsync += ["-e", shlex.join(['ssh'] + ssh_opts)]
    src = f"{nersc_host}:{remote_path}"
    rc, _, err = run(rsync + [src, str(dest_dir) + "/"], timeout=timeout)
    if rc != 0:
        sys.exit(f"[pull] rsync of {remote_path} failed rc={rc}: {err.strip()}")
    return dest_dir / Path(remote_path).name


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True,
                    help="Literal design suffix, e.g. 498 or 000; preserve NERSC's leading zeros.")
    ap.add_argument("--nersc-host", default="perlmutter")
    ap.add_argument("--nersc-ic-dir", required=True,
                    help="NERSC dir holding the design's initial.hdf, parameters.dat, "
                         "music_input. Use {design} for substitution, e.g. "
                         "/pscratch/sd/u/USER/CAMPAIGN/design_{design}/AuAu200")
    ap.add_argument("--pscratch-root", required=True,
                    help="ABSOLUTE NERSC raw-root; design_<N>/<system> is appended for "
                         "the done-query, e.g. /pscratch/sd/u/USER/CAMPAIGN")
    ap.add_argument("--ap-stage-root", required=True,
                    help="AP stage root (the 'stage' dir split_design.py writes under)")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--campaign", required=True,
                    help="AP-side campaign dir name for the slot tree")
    ap.add_argument("--total-nevents", type=int, default=10000,
                    help="Original full campaign event bound, used for gap counting "
                         "and event-directory padding, including in --dry-run. "
                         "This is not a submission limit.")
    ap.add_argument("--split-script", default=str(Path(__file__).resolve().with_name('split_design.py')),
                    help="path to split_design.py")
    ap.add_argument("--work-dir", default=None,
                    help="where to pull the monolith to (default: <ap-stage-root>/../_monoliths)")
    ap.add_argument('--manifest', default=None,
                    help='Output manifest; default <ap-stage-root>/../manifest_<design>.txt.')
    ap.add_argument("--keep-monolith", action="store_true",
                    help="do NOT delete the pulled monolith after splitting")
    ap.add_argument("--ssh-opt", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true",
                    help="Query NERSC completion markers and partition guard, then "
                         "print the plan. Do not read/copy the input bundle, write "
                         "a manifest, delete files, or submit jobs.")
    args = ap.parse_args()
    if args.total_nevents <= 0:
        ap.error('--total-nevents must be positive')

    design = args.design
    design_dir = f"design_{design}"
    ic_dir = args.nersc_ic_dir.replace("{design}", design)
    pscratch_design_dir = f"{args.pscratch_root}/{design_dir}/{args.system}"
    work_dir = Path(args.work_dir) if args.work_dir else \
        Path(args.ap_stage_root).parent / "_monoliths" / design_dir
    manifest = Path(args.manifest) if args.manifest else Path(args.ap_stage_root).parent / f'manifest_{design}.txt'

    print(f"=== staging {design_dir} ({args.system}) ===")
    print(f"NERSC IC dir:    {args.nersc_host}:{ic_dir}")
    print(f"PSCRATCH design: {pscratch_design_dir}")
    print(f"AP stage root:   {args.ap_stage_root}  (campaign {args.campaign})")

    # preflight: cert/host reachable
    rc, out, err = ssh_nersc(args.nersc_host, "echo ok", args.ssh_opt, timeout=30)
    if rc != 0 or out.strip() != "ok":
        sys.exit(f"cannot reach NERSC via '{args.nersc_host}' (rc={rc}): {err.strip()}\n"
                 f" -> run sshproxy.sh -u <user> on the AP first.")

    # 2 (first): done-query + PARTITION GUARD (read-only; do this BEFORE pulling GBs)
    drained, suspect, reaped = query_done_and_guard(args.nersc_host, pscratch_design_dir, args.ssh_opt)
    print(f"already-drained OSG events: {len(drained)}")
    if suspect:
        sys.exit(
            f"\nABORT: {design_dir} has {len(suspect)} terminal event(s) WITHOUT a "
            f"drained.done marker (e.g. {sorted(suspect)[:5]}).\n"
            f"These are almost certainly NERSC events. Staging OSG over them would mix\n"
            f"generic-OSG and native-NERSC builds in one design. Resolve first: confirm\n"
            f"whether this design should be OSG-owned, and if so move the NERSC events\n"
            f"aside (as was done for design_499) before re-running staging.")
    gap_count = args.total_nevents - len(drained)
    if reaped or gap_count <= 0:
        evidence = 'reaped.done present on NERSC' if reaped else f'{len(drained)} events already drained'
        print(f'{design_dir}: {evidence}; nothing to stage.')
        # Replace a prior gap manifest: leaving it intact would let the sweep
        # submit stale slots when this invocation has no work at all.
        if not args.dry_run:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            tmp = manifest.with_name(manifest.name + '.tmp')
            tmp.write_text('')
            tmp.replace(manifest)
        return
    print(f"gap to stage: {gap_count} events (total {args.total_nevents} - {len(drained)} done)")

    if args.dry_run:
        print("\n[DRY-RUN] would now: pull monolith -> split gap -> delete monolith. Stopping.")
        return

    # write the skip-list for split_design.py
    skip_path = work_dir.parent / f"done_events_{design}.txt"
    skip_path.parent.mkdir(parents=True, exist_ok=True)
    skip_path.write_text("\n".join(sorted(drained)) + ("\n" if drained else ""))
    print(f"wrote skip-list: {skip_path} ({len(drained)} events)")

    # 1: pull monolith + params + music_input
    print(f"pulling monolith + inputs to {work_dir} ...")
    monolith = pull_file(args.nersc_host, f"{ic_dir}/initial.hdf", work_dir, args.ssh_opt)
    params = pull_file(args.nersc_host, f"{ic_dir}/parameters.dat", work_dir, args.ssh_opt, timeout=300)
    music = pull_file(args.nersc_host, f"{ic_dir}/music_input", work_dir, args.ssh_opt, timeout=300)
    mono_gb = monolith.stat().st_size / 1e9
    print(f"pulled monolith ({mono_gb:.2f} GB) + params + music_input")

    # 3: split the gap via split_design.py (already tested)
    split_cmd = [
        sys.executable, args.split_script,
        "--monolith", str(monolith),
        "--params", str(params),
        "--music-input", str(music),
        "--out-root", args.ap_stage_root,
        "--campaign", args.campaign,
        "--design", design_dir,
        "--system", args.system,
        "--total-nevents", str(args.total_nevents),
        "--skip-events", str(skip_path),
        "--manifest", str(manifest),
    ]
    print("splitting: " + " ".join(shlex.quote(c) for c in split_cmd))
    rc, out, err = run(split_cmd, timeout=7200)
    sys.stdout.write(out)
    if rc != 0:
        sys.exit(f"[split] split_design.py failed rc={rc}: {err.strip()}\n"
                 f"(monolith kept at {monolith} for inspection)")

    # 4: delete the pulled monolith (reclaim GBs); keep params/music (tiny)
    if args.keep_monolith:
        print(f"kept monolith at {monolith} (--keep-monolith)")
    else:
        try:
            monolith.unlink()
            print(f"deleted monolith {monolith} (reclaimed {mono_gb:.2f} GB)")
        except OSError as e:
            sys.stderr.write(f"[cleanup] could not delete monolith {monolith}: {e}\n")

    print(f"\n=== {design_dir} staged. manifest: {manifest} ===")
    print("next: inspect this manifest and copy it to manifest.txt in the submit "
          "directory (beside stage/). Preview with condor_submit -dry-run FILE "
          "events_fanout.submit before submitting. Keep the separate drain active.")


if __name__ == "__main__":
    main()
