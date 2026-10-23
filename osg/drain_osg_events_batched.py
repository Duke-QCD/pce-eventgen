#!/usr/bin/env python3
"""
drain_osg_events_batched.py -- AP-SIDE batched drain. Runs ON THE OSG access point,
pushes completed OSG event outputs to NERSC PSCRATCH, frees AP /home. Uses a FIXED,
SMALL number of SSH/rsync connections PER PASS (not per event), to defeat the NERSC
per-connection shell-init tax (~3-6s/connection from module loads in ~/.bashrc).

WHY BATCHED: the per-event drain paid ~5 SSH round-trips x ~Ns each x N events ->
hours for a full design. This version pays ~3 connections per PASS regardless of N,
so a full 10k-event design's overhead is seconds, not hours. (NERSC's ~/.bashrc
module loads run on every SSH session and cost several seconds; that user env is
load-bearing for the pipeline and must not be changed, so we minimize connections.)

WHY PUSH: NERSC->AP needs browser auth + AP forbids SSH multiplexing -> infeasible.
AP->NERSC uses an sshproxy cert (run `sshproxy.sh -u <user>` on the AP once/24h) and
is non-interactive.

ONE PASS = 3 remote ops + local deletes, independent of event count:
  L. LOCAL find: list completed events (outbox has non-empty particle_lists.oscar*
     older than --min-age, or a clean hydro.no_surface marker).         [no SSH]
  1. ONE bulk marker-check ssh: which events already have drained.done on NERSC ->
     skip those (and, if their AP copy lingers, local-delete it: crash cleanup).
  2. ONE rsync push of only the remaining events' outboxes, STRUCTURE-PRESERVED (each lands
     as <ev>/outbox/... on NERSC). No flatten trick in rsync.
  3. ONE bulk flatten+verify+mark ssh: a single remote shell loops the batch ->
     moves each <ev>/outbox/* up to <ev>/, removes the emptied outbox, verifies a
     particle_lists.oscar* of the expected size is present, and writes <ev>/
     drained.done ONLY if verified. Emits "<ev> OK" / "<ev> FAIL" per event.
  D. LOCAL delete: rm -rf only the events the remote step reported OK.   [no SSH]

DELETION-SAFETY INVARIANT (preserved at batch granularity): an AP event dir is
deleted only after the remote NERSC copy is verified AND its drained.done marker is
written (the remote step writes the marker only on a passed verify, then reports OK;
only OK events are deleted). rsync partial failure -> those events fail verify ->
FAIL -> not deleted. Crash mid-batch -> markers already written are seen by the next
pass's marker-check (skipped + AP copy cleaned), the rest are re-pushed. Commit point
is the remote marker write; local delete strictly follows it.

MODES: default one pass; --loop polls until --stop-empty empty cycles; --no-delete
pushes+verifies+marks but keeps AP copies; --dry-run lists only.
"""

import argparse
import shlex
import subprocess
import sys
import time
from pathlib import Path


def run(cmd, timeout=3600, input_text=None):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, input=input_text)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"


class Cfg:
    def __init__(self, nersc_host, ap_root, design, system, pscratch_design_root,
                 no_delete, dry_run, ssh_opts):
        self.nersc_host = nersc_host
        self.ap_root = ap_root.rstrip("/")
        self.design = design
        self.system = system
        self.pscratch_design_root = pscratch_design_root.rstrip("/")
        self.no_delete = no_delete
        self.dry_run = dry_run
        self.ssh_opts = ssh_opts

    @property
    def ap_design_root(self):
        return Path(f"{self.ap_root}/design_{self.design}/{self.system}")


def ssh_nersc(cfg, remote_cmd, timeout=600, input_text=None):
    return run(["ssh"] + cfg.ssh_opts + [cfg.nersc_host, remote_cmd],
               timeout=timeout, input_text=input_text)


# ---------- L. local listing ----------
def list_completed_events(cfg, min_age):
    """LOCAL scan: {event_id: (kind, size)} for events in a terminal state older
    than min_age, where kind is "particles" (size = particle-list bytes) or
    "nosurface" (size = 0; a clean peripheral zero-output event marked
    hydro.no_surface). Both kinds drain."""
    root = cfg.ap_design_root
    if not root.is_dir():
        sys.stderr.write(f"[list] no such AP dir: {root}\n")
        return {}
    now = time.time()
    out = {}
    blocked = 0
    for ev_dir in sorted(root.iterdir()):
        if not ev_dir.is_dir() or not ev_dir.name.isdigit():
            continue
        outbox = ev_dir / "outbox"
        if not outbox.is_dir():
            continue
        # An event has TWO valid terminal states (matching the NERSC pipeline):
        #   (a) particles: outbox has a non-empty particle_lists.oscar*  -> ("particles", size)
        #   (b) no-surface: outbox has hydro.no_surface (clean peripheral zero-output
        #       event; legitimately no particle list)                    -> ("nosurface", 0)
        # No-surface events MUST drain too -- dropping them depletes peripheral
        # centrality bins and biases the analysis.
        pls = [outbox / name for name in ('particle_lists.oscar', 'particle_lists.oscar.unfinished')
               if (outbox / name).exists()]
        nosurf = (outbox / "hydro.no_surface").is_file()
        done = (outbox / 'afterburner.done').is_file()
        if (len(pls) > 1 or (pls and (nosurf or not done)) or
                (nosurf and (done or (outbox / 'hydro.done').exists()))):
            blocked += 1
            continue
        if pls:
            try:
                st = pls[0].stat()
            except OSError:
                continue
            # freshness/size guard applies to the particle list (mid-transfer guard)
            if not pls[0].is_file() or st.st_size <= 0:
                blocked += 1
                continue
            if (now - st.st_mtime) < min_age:
                continue
            out[ev_dir.name] = ("particles", st.st_size)
        elif nosurf:
            # freshness guard on the marker itself (it's the payload here)
            try:
                mt = (outbox / "hydro.no_surface").stat().st_mtime
            except OSError:
                continue
            if (now - mt) < min_age:
                continue
            out[ev_dir.name] = ("nosurface", 0)
        # else: not a terminal state yet -> skip
    if blocked:
        print(f'[list] design_{cfg.design}: {blocked} incomplete/conflicting outbox(es) retained for inspection')
    return out


# ---------- 1. bulk marker-check (also ensures the dest design dir exists) ----------
def remote_drained_set(cfg):
    """ONE ssh: ensure the destination design dir exists (rsync won't create the
    parent path above its --relative root), then return the set of event ids that
    already have drained.done on NERSC. Folding the mkdir in here keeps the pass at
    a fixed connection count. Returns None on failure or a reaped destination.
    Dry-run only queries: it never creates a destination directory."""
    root = shlex.quote(cfg.pscratch_design_root)
    # Dry-run is read-only, including on NERSC. A failed query is not evidence
    # of an empty destination; stop this pass before any transfer or cleanup.
    prepare = f'if [ ! -e {root} ]; then exit 0; fi; ' if cfg.dry_run else f'mkdir -p {root} || exit 1; '
    cmd = (prepare + f'cd {root} || exit 1; '
           'if [ -f reaped.done ]; then echo "design is already reaped; AP output retained" >&2; exit 3; fi; '
           'for d in */; do [ -f "${d}drained.done" ] && echo "${d%/}"; done; exit 0')
    rc, out, err = ssh_nersc(cfg, cmd, timeout=120)
    if rc != 0:
        sys.stderr.write(f"[marker-check] ssh rc={rc}: {err.strip()}\n")
        return None
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


# ---------- 2. batched rsync push (structure-preserved) ----------
def rsync_push(cfg, events):
    """ONE rsync: push only the given events' outboxes, preserving structure
    (each lands as <ev>/outbox/...). Uses --relative so listed relative paths are
    recreated under the destination root. Returns rsync rc.

    Note: NERSC ~/.bashrc emits module chatter (e.g. "Lmod ... replacing gpu with
    cpu") on stderr for every ssh session. That is harmless here -- rsync's protocol
    runs over stdout, so stderr noise does not corrupt the transfer; it only shows up
    appended to error messages. The destination design dir is created by the
    marker-check step (rsync --relative does not create the path above its root)."""
    if not events:
        return 0
    # rsync --relative: a "." in the source path marks the relative root, so
    #   src/./<ev>  ->  dest/<ev>   (structure preserved, no flatten)
    src_root = str(cfg.ap_design_root)
    # Transfer the deliverable only. Slot inputs and Condor logs are AP-side
    # artifacts and can dwarf a no-surface event's actual output.
    srcs = [f"{src_root}/./{ev}/outbox/" for ev in events]
    dst = f"{cfg.nersc_host}:{cfg.pscratch_design_root}/"
    rsync = ["rsync", "-a", "--relative", "--timeout=1200"]
    if cfg.ssh_opts:
        rsync += ["-e", shlex.join(['ssh'] + cfg.ssh_opts)]
    rc, _, err = run(rsync + srcs + [dst], timeout=3600)
    if rc != 0:
        sys.stderr.write(f"[rsync] rc={rc}: {err.strip()}\n")
    return rc


# ---------- 3. bulk flatten + verify + mark ----------
# Embedded remote shell (tested against local dirs). Reads "<ev> <kind> <size>" on
# stdin; flattens outbox up, verifies per kind, writes drained.done only if verified;
# prints "<ev> OK" / "<ev> FAIL". $1 = pscratch design root.
#   kind=particles : verify a particle_lists.oscar* of <size> bytes is present
#   kind=nosurface : verify the hydro.no_surface marker is present (no particle list;
#                    the marker IS the payload for a clean peripheral zero-output event)
_REMOTE_FLATTEN = r'''cd "$1" || exit 1
stamp=$(date -u +%FT%TZ)
while read ev kind size; do
  [ -n "$ev" ] || continue
  d="$ev"
  if [ -d "$d/outbox" ]; then
    if ! mv "$d/outbox/"* "$d/" 2>/dev/null || ! rmdir "$d/outbox" 2>/dev/null; then
      echo "$ev FAIL"
      continue
    fi
  fi
  ok=0
  if [ "$kind" = "nosurface" ]; then
    # valid terminal zero-output event: require the marker, not a particle list
    if [ -f "$d/hydro.no_surface" ] && [ ! -e "$d/hydro.done" ] &&
       [ ! -e "$d/afterburner.done" ] && [ ! -e "$d/particle_lists.oscar" ] &&
       [ ! -e "$d/particle_lists.oscar.unfinished" ]; then ok=1; fi
  else
    # Match the maintained runner/survey: one nonempty variant + done marker,
    # without a contradictory no-surface marker.
    n=0
    for f in "$d/particle_lists.oscar" "$d/particle_lists.oscar.unfinished"; do
      [ ! -e "$f" ] || n=$((n + 1))
    done
    for f in "$d/particle_lists.oscar" "$d/particle_lists.oscar.unfinished"; do
      [ -f "$f" ] || continue
      z=$(stat -c %s "$f")
      if [ "$n" = 1 ] && [ "$z" = "$size" ] && [ "$z" -gt 0 ] &&
         [ -f "$d/afterburner.done" ] && [ ! -e "$d/hydro.no_surface" ]; then ok=1; fi
    done
  fi
  if [ "$ok" = 1 ]; then
    # Report success only after committing the marker. The AP copy must survive
    # quota/write/rename failures, even when the particle payload verified.
    if printf '%s' "drained $stamp kind=$kind size=$size" > "$d/.drained.tmp" && mv -T "$d/.drained.tmp" "$d/drained.done"; then
      echo "$ev OK"
    else
      echo "$ev FAIL"
    fi
  else
    echo "$ev FAIL"
  fi
done'''


def flatten_verify_mark(cfg, events):
    """ONE ssh: flatten+verify+mark all events in one remote shell.
    events: dict {ev: (kind, size)}. Returns set of event ids reported OK."""
    if not events:
        return set()
    stdin = "".join(f"{ev} {kind} {sz}\n" for ev, (kind, sz) in events.items())
    remote = "bash -c " + shlex.quote(_REMOTE_FLATTEN) + " _ " + shlex.quote(cfg.pscratch_design_root)
    rc, out, err = ssh_nersc(cfg, remote, timeout=1800, input_text=stdin)
    if rc != 0:
        sys.stderr.write(f"[flatten/verify/mark] ssh rc={rc}: {err.strip()}\n")
    ok = set()
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) == 2 and parts[1] == "OK":
            ok.add(parts[0])
    return ok


# ---------- D. local delete ----------
def local_delete(cfg, ev):
    ev_dir = cfg.ap_design_root / ev
    p = str(ev_dir)
    if "/design_" not in p or ev in ("", ".", "..") or not ev_dir.is_dir():
        sys.stderr.write(f"[delete] refusing unsafe path: {p!r}\n")
        return 1
    rc, _, err = run(["rm", "-rf", p], timeout=120)
    if rc != 0:
        sys.stderr.write(f"[delete] {ev} rc={rc}: {err.strip()}\n")
    return rc


def one_pass(cfg, min_age):
    completed = list_completed_events(cfg, min_age)
    if not completed:
        return {}, 0

    already = remote_drained_set(cfg)
    if already is None:
        print(f'[pass] design_{cfg.design}: destination query failed; AP copies retained, no transfer attempted')
        return {'FAILED-marker-check': len(completed)}, len(completed)
    # events already drained on NERSC: clean up any lingering AP copy, drop from work
    cleanup = [ev for ev in completed if ev in already]
    todo = {ev: kv for ev, kv in completed.items() if ev not in already}

    total_gb = sum(sz for (_kind, sz) in todo.values()) / 1e9
    n_nosurf = sum(1 for (kind, _s) in todo.values() if kind == "nosurface")
    print(f"[pass] {len(completed)} completed; {len(already & completed.keys())} "
          f"already drained; {len(todo)} to push ({total_gb:.2f} GB, "
          f"{n_nosurf} no-surface){'  [DRY-RUN]' if cfg.dry_run else ''}")

    tally = {}
    # crash-cleanup of already-drained AP copies
    if not cfg.dry_run and not cfg.no_delete:
        for ev in cleanup:
            if local_delete(cfg, ev) == 0:
                tally["already-cleaned"] = tally.get("already-cleaned", 0) + 1
            else:
                tally['already-delete-pending'] = tally.get('already-delete-pending', 0) + 1

    if cfg.dry_run:
        tally["would-push"] = len(todo)
        print("[pass] " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        return tally, len(completed)

    if not todo:
        print("[pass] " + (", ".join(f"{k}={v}" for k, v in sorted(tally.items())) or "nothing to push"))
        return tally, len(completed)

    # 2. one rsync push
    if rsync_push(cfg, list(todo.keys())) != 0:
        tally["FAILED-rsync-batch"] = len(todo)
        print("[pass] " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
        return tally, len(completed)

    # 3. one flatten+verify+mark
    ok = flatten_verify_mark(cfg, todo)
    failed = set(todo) - ok
    tally["drained" if not cfg.no_delete else "drained-nodel"] = len(ok)
    if failed:
        tally["FAILED-verify"] = len(failed)

    # D. local delete only verified events
    if not cfg.no_delete:
        for ev in ok:
            if local_delete(cfg, ev) != 0:
                tally['drained'] -= 1
                tally['drained-delete-pending'] = tally.get('drained-delete-pending', 0) + 1

    print("[pass] " + ", ".join(f"{k}={v}" for k, v in sorted(tally.items())))
    return tally, len(completed)


def make_cfg(nersc_host, ap_root, pscratch_root, design, system,
             no_delete, dry_run, ssh_opts):
    """Build a per-design Cfg from base params (used for multi-design draining)."""
    pscratch_design_root = f"{pscratch_root}/design_{design}/{system}"
    return Cfg(nersc_host, ap_root, design, system, pscratch_design_root,
               no_delete, dry_run, ssh_opts)


def discover_designs(ap_root, system):
    """Scan the AP stage root for design_<N> dirs that have a <system>/ subdir
    containing at least one event dir. Returns design numbers (strings), sorted
    DESCENDING (matches the OSG top-down sweep; also drains newest first).
    Re-scanned every loop cycle, so freshly-staged designs are picked up and
    fully-drained/torn-down ones drop off automatically."""
    base = Path(ap_root)
    if not base.is_dir():
        return []
    designs = []
    for d in sorted(base.iterdir()):
        if not (d.is_dir() and d.name.startswith("design_")):
            continue
        sysdir = d / system
        if not sysdir.is_dir():
            continue
        # require at least one subdir (an event slot) so empty/torn-down designs
        # don't keep getting scanned
        if any(c.is_dir() for c in sysdir.iterdir()):
            designs.append(d.name[len("design_"):])
    # sort descending numerically when possible, else lexically
    try:
        designs.sort(key=lambda x: int(x), reverse=True)
    except ValueError:
        designs.sort(reverse=True)
    return designs


def drain_design_set(designs, base, min_age):
    """Run one_pass per design; return (total events found, any failures).
    `base` is a dict of the shared params for make_cfg."""
    total_n = 0
    failed = False
    for design in designs:
        cfg = make_cfg(base["nersc_host"], base["ap_root"], base["pscratch_root"],
                       design, base["system"], base["no_delete"], base["dry_run"],
                       base["ssh_opts"])
        if len(designs) > 1:
            print(f"-- design {design} --")
        tally, n = one_pass(cfg, min_age)
        failed |= any(count and (key.startswith('FAILED') or key.endswith('delete-pending'))
                      for key, count in tally.items())
        total_n += n
    return total_n, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--nersc-host", default="perlmutter")
    ap.add_argument("--ap-root", required=True,
                    help="ABSOLUTE local campaign root on the AP")
    ap.add_argument("--design", default=None,
                    help="single design number; omit and use --auto-discover to "
                         "drain ALL staged designs each cycle")
    ap.add_argument("--auto-discover", action="store_true",
                    help="drain every design_<N> currently staged under --ap-root "
                         "(re-scanned each cycle). Use this for a multi-design sweep.")
    ap.add_argument("--system", default="AuAu200")
    ap.add_argument("--pscratch-root", required=True,
                    help="ABSOLUTE NERSC raw-root (expand $PSCRATCH yourself)")
    ap.add_argument("--min-age", type=int, default=60)
    ap.add_argument("--no-delete", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ssh-opt", action="append", default=[])
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=int, default=300,
                    help="loop poll seconds (batched, so larger is fine; default 300)")
    ap.add_argument("--stop-empty", type=int, default=3)
    args = ap.parse_args()
    if args.min_age < 0 or args.interval <= 0 or args.stop_empty <= 0:
        ap.error('--min-age must be nonnegative; --interval and --stop-empty must be positive')

    if "$" in args.pscratch_root:
        sys.exit(f"--pscratch-root must be literal absolute (got {args.pscratch_root!r})")
    if not args.auto_discover and not args.design:
        sys.exit("specify --design <N> or --auto-discover")

    base = {
        "nersc_host": args.nersc_host, "ap_root": args.ap_root,
        "pscratch_root": args.pscratch_root, "system": args.system,
        "no_delete": args.no_delete, "dry_run": args.dry_run,
        "ssh_opts": args.ssh_opt,
    }

    def current_designs():
        if args.auto_discover:
            return discover_designs(args.ap_root, args.system)
        return [args.design]

    print(f"AP root:  {args.ap_root}")
    print(f"NERSC:    {args.nersc_host}:{args.pscratch_root}/design_*/{args.system}")
    print(f"mode:     {'DRY-RUN' if args.dry_run else ('NO-DELETE' if args.no_delete else 'push+delete')}"
          f"{'  AUTO-DISCOVER' if args.auto_discover else ''}{'  LOOP' if args.loop else ''}")

    # preflight: cert/host reachable (build a throwaway cfg just for the check)
    probe = make_cfg(args.nersc_host, args.ap_root, args.pscratch_root,
                     (args.design or "0"), args.system, args.no_delete,
                     args.dry_run, args.ssh_opt)
    rc, out, err = ssh_nersc(probe, "echo ok", timeout=30)
    if rc != 0 or out.strip() != "ok":
        sys.exit(f"cannot reach NERSC via '{args.nersc_host}' (rc={rc}): {err.strip()}\n"
                 f" -> run sshproxy.sh on the AP and check ~/.ssh/config")

    if not args.loop:
        designs = current_designs()
        if not designs:
            print("[drain] no staged designs found.")
        else:
            print(f"[drain] designs: {', '.join(designs)}")
            _, failed = drain_design_set(designs, base, args.min_age)
            if failed:
                raise SystemExit(1)
        return

    empty = cycle = 0
    while empty < args.stop_empty:
        cycle += 1
        designs = current_designs()
        print(f"\n=== cycle {cycle} === designs: {', '.join(designs) if designs else '(none)'}")
        total_n, _ = drain_design_set(designs, base, args.min_age) if designs else (0, False)
        empty = empty + 1 if total_n == 0 else 0
        if empty < args.stop_empty:
            time.sleep(args.interval)
    print(f"\n[loop] no eligible output for {args.stop_empty} cycles; stopping (event totals not checked)")


if __name__ == "__main__":
    main()
