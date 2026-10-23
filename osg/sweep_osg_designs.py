#!/usr/bin/env python3
"""
sweep_osg_designs.py -- orchestrate a descending OSG design campaign on the AP.

Sweeps designs from --start DOWN to --floor (e.g. 498 -> 400), with at most
--max-running designs in flight (default 2). A design is staged when a running
slot is free, then promoted on the next cycle. There is no extra staged design
while all running slots are occupied.

PER-CYCLE STATE MACHINE:
  1. COMPLETION + TEARDOWN: if the current design's HTCondor cluster is empty
     (all jobs terminal) AND its AP events are fully drained (no completed-undrained
     output remains), tear down its AP staging. The drained-check is the safety
     gate -- teardown NEVER deletes un-drained output.
  2. PROMOTE: if no design is currently running and one is staged-ahead, submit it
     (condor_submit) -> it becomes the current design.
  3. STAGE AHEAD: if nothing is staged-ahead and we're still above the floor, stage
     the next design (stage_osg_design.py, which pulls the monolith, runs the
     PARTITION GUARD, and splits the gap). If staging ABORTS (guard fires on NERSC
     events, or any error), the WHOLE SWEEP HALTS and alerts -- it never silently
     skips or works around a partition collision.
  4. DONE when nothing is running, nothing staged, and we're past the floor.

DRAINING is handled SEPARATELY: run the multi-design drain in another tmux window:
    drain_osg_events_batched.py --auto-discover --loop ...
It auto-discovers and drains whatever this sweep stages; the sweep only stages,
submits, and tears down.

PREREQ each day: sshproxy.sh -u <user> (staging pulls from NERSC; cert ~24h).
Run this in tmux so it survives disconnects. NOT crash-resumable -- if it dies,
check condor_q / staged state before restarting (a blind restart could double-submit
a running design).
"""

import argparse
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path


def run(cmd, timeout=7200, cwd=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


def condor_cluster_empty(cluster):
    """True iff no jobs remain in the queue for this cluster (all terminal)."""
    rc, out, err = run(["condor_q", str(cluster), "-af", "ProcId"], timeout=120)
    if rc != 0:
        # if condor_q errors, be conservative: treat as NOT empty (don't teardown)
        sys.stderr.write(f"[condor_q] rc={rc}: {err.strip()}\n")
        return False
    return out.strip() == ""


def ap_fully_drained(ap_stage_root, campaign, design, system):
    """True iff NO completed-undrained events remain on the AP for this design --
    i.e. no event dir has an outbox holding a particle_lists.oscar* or
    hydro.no_surface that the drain hasn't yet cleared. This is the teardown safety
    gate: drained events are DELETED by the drain, so any surviving outbox terminal
    artifact means draining is not finished -> NOT safe to tear down."""
    design_dir = Path(ap_stage_root) / campaign / f"design_{design}" / system
    if not design_dir.is_dir():
        return True  # nothing there at all -> nothing undrained
    # find first completed-undrained artifact; -quit stops at the first hit
    rc, out, err = run([
        "find", str(design_dir), "-path", "*/outbox/*",
        "(", "-name", "particle_lists.oscar*", "-o", "-name", "hydro.no_surface", ")",
        "-print", "-quit",
    ], timeout=300)
    if rc != 0:
        sys.stderr.write(f"[drained-check] rc={rc}: {err.strip()}\n")
        return False
    return out.strip() == ""


def is_complete(cluster, ap_stage_root, campaign, design, system):
    return (condor_cluster_empty(cluster)
            and ap_fully_drained(ap_stage_root, campaign, design, system))


def stage_design(design, a):
    """Run stage_osg_design.py for one design. Returns its return code (0 = ok;
    nonzero = guard abort or error -> sweep should HALT)."""
    cmd = [
        sys.executable, a.stage_script,
        "--design", str(design),
        "--nersc-host", a.nersc_host,
        "--nersc-ic-dir", a.nersc_ic_dir,
        "--pscratch-root", a.pscratch_root,
        "--ap-stage-root", a.ap_stage_root,
        "--system", a.system,
        "--campaign", a.campaign,
        "--total-nevents", str(a.total_nevents),
        "--split-script", a.split_script,
        "--manifest", str(Path(a.submit_dir).resolve() / f'manifest_{design}.txt'),
    ]
    for o in a.ssh_opt:
        cmd.append('--ssh-opt=' + o)
    print(f"\n[stage] design {design}: " + " ".join(shlex.quote(c) for c in cmd))
    rc, out, err = run(cmd, timeout=10800)  # staging can take a while (pull + split)
    sys.stdout.write(out)
    if err.strip():
        sys.stderr.write(err)
    return rc


def submit_design(design, a):
    """cp the design's manifest to the submit's manifest.txt, condor_submit, and
    return the cluster id (or None on failure). Submissions are serialized (gentle
    pipeline) so the shared manifest.txt is safe."""
    manifest = Path(a.submit_dir) / f"manifest_{design}.txt"
    if not manifest.is_file():
        sys.stderr.write(f"[submit] manifest not found: {manifest}\n")
        return None
    target = Path(a.submit_dir) / "manifest.txt"
    rc, _, err = run(["cp", str(manifest), str(target)], timeout=60)
    if rc != 0:
        sys.stderr.write(f"[submit] cp manifest failed: {err.strip()}\n")
        return None
    rc, out, err = run(["condor_submit", a.submit_file], timeout=600, cwd=a.submit_dir)
    sys.stdout.write(out)
    if rc != 0:
        sys.stderr.write(f"[submit] condor_submit failed rc={rc}: {err.strip()}\n")
        return None
    # parse "N job(s) submitted to cluster M."
    m = re.search(r"submitted to cluster (\d+)", out)
    if not m:
        sys.stderr.write(f"[submit] could not parse cluster id from:\n{out}\n")
        return None
    cluster = m.group(1)
    print(f"[submit] design {design} -> cluster {cluster}")
    return cluster


def teardown_design(design, a):
    """Delete the AP staging subtree for a completed+drained design, and its
    manifest/skip files. Safe ONLY after is_complete() confirmed no undrained
    output. Reclaims the IC-only slots left by failed/removed jobs."""
    design_dir = Path(a.ap_stage_root) / a.campaign / f"design_{design}" / a.system
    # final guard: refuse if path looks wrong
    if "design_" not in str(design_dir) or not design_dir.exists():
        print(f"[teardown] nothing to remove for design {design} ({design_dir})")
    else:
        rc, _, err = run(["rm", "-rf", str(design_dir)], timeout=600)
        if rc == 0:
            print(f"[teardown] removed AP staging {design_dir}")
        else:
            sys.stderr.write(f"[teardown] rm failed: {err.strip()}\n")
    # tidy manifest + skip-list (best effort)
    for f in (Path(a.submit_dir) / f"manifest_{design}.txt",):
        try:
            f.unlink()
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, required=True, help="first design (descending), e.g. 498")
    ap.add_argument("--floor", type=int, required=True, help="lowest design to run (inclusive), e.g. 400")
    ap.add_argument("--design-width", type=int, default=3,
                    help="Zero-pad design IDs to this width (default 3, matching NERSC's design_000).")
    ap.add_argument("--nersc-host", default="perlmutter")
    ap.add_argument("--nersc-ic-dir", required=True,
                    help="NERSC IC dir template with {design}, e.g. "
                         "/pscratch/sd/u/USER/CAMPAIGN/design_{design}/AuAu200")
    ap.add_argument("--pscratch-root", required=True,
                    help="ABSOLUTE NERSC raw-root, e.g. /pscratch/sd/u/USER/CAMPAIGN")
    ap.add_argument("--ap-stage-root", required=True,
                    help="AP stage root (the 'stage' dir), e.g. /home/.../osgrun/stage")
    ap.add_argument("--campaign", required=True,
                    help="AP-side campaign dir name for the slot tree")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--total-nevents", type=int, default=10000)
    ap.add_argument("--submit-dir", required=True,
                    help="dir containing events_fanout.submit and the manifests; "
                         "condor_submit runs here")
    ap.add_argument("--submit-file", default="events_fanout.submit")
    ap.add_argument("--stage-script", default=str(Path(__file__).resolve().with_name('stage_osg_design.py')))
    ap.add_argument("--split-script", default=str(Path(__file__).resolve().with_name('split_design.py')))
    ap.add_argument("--poll-interval", type=int, default=300,
                    help="seconds between completion checks while waiting")
    ap.add_argument("--max-running", type=int, default=2,
                    help="how many designs may run concurrently (default 2). 1 = the "
                         "old strictly-gentle pipeline; 2 overlaps one design's tail "
                         "with the next design's body to keep OSPool slots filled. "
                         "Higher increases home output flow ~linearly -- watch home if "
                         "you raise it past ~3.")
    ap.add_argument("--ssh-opt", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the design order; stage/submit nothing")
    args = ap.parse_args()

    if args.start < args.floor:
        sys.exit(f"--start ({args.start}) must be >= --floor ({args.floor})")
    if args.max_running <= 0 or args.total_nevents <= 0 or args.poll_interval <= 0:
        ap.error('--max-running, --total-nevents, and --poll-interval must be positive')
    if args.floor < 0 or args.design_width < 0:
        ap.error('--floor and --design-width must be nonnegative')
    args.submit_dir = str(Path(args.submit_dir).expanduser().resolve())
    args.ap_stage_root = str(Path(args.ap_stage_root).expanduser().resolve())

    order = list(range(args.start, args.floor - 1, -1))
    print(f"=== OSG sweep: designs {args.start} -> {args.floor} "
          f"({len(order)} designs), up to {args.max_running} running at a time ===")
    print(f"order: {order[:8]}{' ...' if len(order) > 8 else ''}")
    print(f"design names: design_{str(args.start).zfill(args.design_width)} -> "
          f"design_{str(args.floor).zfill(args.design_width)}")
    print("NOTE: run the drain SEPARATELY:  drain_osg_events_batched.py "
          "--auto-discover --loop ...")
    if args.dry_run:
        print(f"\n[DRY-RUN] would stage/submit each design in order, up to "
              f"{args.max_running} running at a time, next staged ahead. Stopping.")
        return

    nxt = args.start
    running = {}         # {design: cluster}, up to args.max_running concurrently
    staged = None        # design staged-ahead, ready to submit (buffer depth 1)

    while True:
        # 1. completion + teardown -- check EACH running design independently.
        #    Teardown is per-design (scoped to that design's dir) and gated on its
        #    own cluster being empty AND its AP events fully drained, so tearing
        #    down one running design never touches another.
        for design in sorted(running, reverse=True):
            if is_complete(running[design], args.ap_stage_root,
                           args.campaign, design, args.system):
                print(f"\n[complete] design {design} (cluster {running[design]}) "
                      f"queue empty; available outputs drained (event target not checked).")
                teardown_design(design, args)
                del running[design]

        # 2. promote staged -> running, if there's a free running slot
        if len(running) < args.max_running and staged is not None:
            manifest = Path(args.submit_dir) / f'manifest_{staged}.txt'
            if manifest.is_file() and not manifest.read_text().strip():
                print(f'[skip] design {staged}: no gap events to submit.')
                staged = None
                continue
            cl = submit_design(staged, args)
            if cl is None:
                sys.exit(f"\nHALT: failed to submit staged design {staged}. "
                         f"Resolve and restart the sweep (check for partial submit).")
            running[staged] = cl
            staged = None

        # 3. stage the next design ahead (buffer depth 1) if we'll have a slot for it
        if staged is None and nxt >= args.floor and len(running) < args.max_running:
            design = str(nxt).zfill(args.design_width)
            rc = stage_design(design, args)
            if rc != 0:
                sys.exit(
                    f"\nHALT: staging design {nxt} returned {rc}.\n"
                    f"This is most likely the PARTITION GUARD firing (design {nxt} has "
                    f"NERSC events present), or a staging error. The sweep has stopped "
                    f"so nothing is mixed/lost. Resolve design {nxt} (decide ownership; "
                    f"move NERSC events aside if it should be OSG), then restart the "
                    f"sweep with --start {nxt}.")
            staged = design
            nxt -= 1

        # 4. done?
        if not running and staged is None and nxt < args.floor:
            print(f"\n=== sweep complete: designs {args.start} -> {args.floor} all "
                  f"processed; queues empty and available outputs drained. "
                  f"Event totals require a NERSC survey. ===")
            return

        # status line + wait
        run_str = ", ".join(f"{d}(cl {c})" for d, c in sorted(running.items(), reverse=True)) or "(none)"
        print(f"[sweep] running={{{run_str}}}; staged={staged}; "
              f"next-to-stage={nxt if nxt >= args.floor else '(floor reached)'}")
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
