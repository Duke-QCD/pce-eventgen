#!/usr/bin/env python3
"""
split_design.py -- Split a per-design monolithic initial.hdf into per-event
single-event initial.hdf files, one per OSG/OSPool slot.

WHY THIS EXISTS
---------------
On NERSC, run-events reads events out of a single per-design initial.hdf
positionally:

    with h5py.File("initial.hdf") as f:
        for n, dset in enumerate(f.values()):   # n = 0, 1, 2, ...
            if n >= nevents: break
            yield n, np.array(dset)

So event N is simply the N-th dataset in h5py's iteration order, and the NERSC
event directory is event_dirname(n, nevents) (zero-padded n). OSG runs one event
per HTCondor job with --nevents 1, so each job needs its own single-event
initial.hdf. This script extracts the first K events from the monolith VERBATIM
(byte-faithful: data + dtype + attrs preserved) and writes each into a slot
directory named to match the corresponding NERSC event dir, so the OSG event
drains back to the NERSC event position it replaces.

CORRECTNESS NOTES
-----------------
- run-events reads ONLY the array (np.array(dset)). grid_n / grid_step come from
  parameters.dat, NOT from the HDF5 file. Dataset attributes are copied for
  provenance; monolith-level file attributes are not copied or needed by the runner.
- Identity is POSITIONAL. h5py's default iteration order is a fixed property of
  the file, so iterating it here reproduces exactly the order NERSC saw. We do
  NOT sort or reorder the keys.
- The zero-pad WIDTH of a slot name must match the FULL NERSC run
  (--total-nevents, default 10000 -> width 4 -> 0000..9999), independent of how
  many events (--n-events) we actually split. event_dirname() below is copied
  verbatim from run-events so the naming is provably identical.

Each slot is made self-contained (initial.hdf + parameters.dat + music_input) so
events_fanout.submit can transfer it as-is.
"""

import argparse
import shutil
import sys
from pathlib import Path

import h5py
import numpy as np


def event_dirname(event_number, nevents):
    """Identical to run-events' event_dirname: zero-pad n to width of nevents-1."""
    width = max(1, len(str(nevents - 1)))
    return str(event_number).zfill(width)


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--monolith", required=True, type=Path,
                   help="The design/system's initial.hdf on NERSC, pulled to the AP.")
    p.add_argument("--params", required=True, type=Path,
                   help="The design/system's parameters.dat, copied into each slot.")
    p.add_argument("--music-input", required=True, type=Path,
                   help="The design/system's music_input, copied into each slot.")
    p.add_argument("--out-root", type=Path, default=Path("stage"),
                   help="Staging root on the AP. Slots are created under "
                        "<out-root>/<campaign>/<design>/<system>/<event>. "
                        "Default: ./stage")
    p.add_argument("--design", required=True, help="Design directory name, e.g. design_000.")
    p.add_argument("--system", default="AuAu200")
    p.add_argument("--campaign", required=True,
                   help="AP-side campaign directory for the slot tree. This name "
                        "does not select the NERSC drain destination; use a separate "
                        "--pscratch-root on the drain for an isolated cross-check. "
                        "Event identity is the design/system/event tail below it.")
    p.add_argument("--n-events", type=int, default=None,
                   help="Optional CAP on how many (non-skipped) events to stage this "
                        "invocation, for chunked staging. Default None = stage ALL "
                        "non-skipped events in [0, total-nevents). With no --skip-events "
                        "this reproduces the old 'first K' behavior.")
    p.add_argument("--skip-events", type=Path, default=None,
                   help="Path to a file listing already-DONE event names (one per "
                        "line, e.g. '0000'; full slot paths are also accepted, the "
                        "basename is used). These positions are skipped so only the "
                        "GAP (not-yet-done events) is staged. stage_osg_design.py "
                        "queries NERSC drained markers and supplies this list automatically.")
    p.add_argument("--total-nevents", type=int, default=10000,
                   help="Fixed FULL NERSC event bound: selects positions [0, N) and "
                        "sets event-directory padding. Default 10000 (0000..9999). "
                        "Use --n-events to cap staging without changing identities.")
    p.add_argument("--manifest", type=Path, default=Path("manifest.txt"),
                   help="Output manifest of slot paths (relative to --out-root) for "
                        "HTCondor 'queue ... from'. Default: ./manifest.txt")
    p.add_argument("--no-verify", action="store_true",
                   help="Skip the per-event read-back equality check (not recommended).")
    return p.parse_args()


def main():
    args = parse_args()
    if args.total_nevents <= 0 or (args.n_events is not None and args.n_events <= 0):
        sys.exit('--total-nevents and --n-events must be positive')

    for f in (args.monolith, args.params, args.music_input):
        if not f.is_file():
            sys.exit(f"missing input file: {f}")

    rel_system = Path(args.campaign) / args.design / args.system
    out_system = args.out_root / rel_system
    out_system.mkdir(parents=True, exist_ok=True)

    manifest_lines = []

    # Load the DONE set (events to skip), if provided. Accept either bare event
    # names ("0000") or full slot paths (basename is taken). This is what turns the
    # splitter into a GAP-FILL: only positions NOT already done get staged.
    skip = set()
    if args.skip_events is not None:
        if not args.skip_events.is_file():
            sys.exit(f"--skip-events file not found: {args.skip_events}")
        for line in args.skip_events.read_text().splitlines():
            line = line.strip()
            if line:
                skip.add(Path(line).name)
        print(f"skip set: {len(skip)} already-done events loaded from {args.skip_events}")

    with h5py.File(args.monolith, "r") as src:
        names = list(src.keys())  # native iteration order == the reader's order

        n_avail = len(names)
        if n_avail < args.total_nevents:
            sys.exit(f"monolith has {n_avail} top-level objects but --total-nevents="
                     f"{args.total_nevents} was requested (the full design needs that "
                     f"many positions). Select the correct original bundle and fixed "
                     f"event target; do not lower the bound to stage a smaller gap.")

        cap = args.n_events  # None = no cap (stage all gap events)
        staged = 0
        skipped = 0
        # Iterate the FULL design range; stage positions not in the skip set, honoring
        # the optional cap. Identity stays positional: event n -> event_dirname(n).
        for n in range(args.total_nevents):
            event = event_dirname(n, args.total_nevents)
            if event in skip:
                skipped += 1
                continue
            if cap is not None and staged >= cap:
                break

            name = names[n]
            # Structural guard, per staged event (reader assumes flat datasets):
            obj = src[name]
            if not isinstance(obj, h5py.Dataset):
                sys.exit(f"monolith object {name!r} is a {type(obj).__name__}, not a "
                         f"Dataset. run-events assumes flat datasets; investigate "
                         f"before splitting.")

            slot_dir = out_system / event
            slot_dir.mkdir(parents=True, exist_ok=True)

            # 1) verbatim single-dataset initial.hdf (data + dtype + attrs)
            dst_hdf = slot_dir / "initial.hdf"
            with h5py.File(dst_hdf, "w") as dst:
                src.copy(name, dst)

            # 2) read-back equality check
            if not args.no_verify:
                with h5py.File(dst_hdf, "r") as chk:
                    chk_names = list(chk.keys())
                    if len(chk_names) != 1:
                        sys.exit(f"{dst_hdf} has {len(chk_names)} datasets, expected 1")
                    a = np.array(src[name])
                    b = np.array(chk[chk_names[0]])
                if a.shape != b.shape or a.dtype != b.dtype or not np.array_equal(a, b):
                    sys.exit(f"VERIFY FAILED for event {event} (dataset {name!r}): "
                             f"copied array differs from the monolith")

            # 3) make the slot self-contained for transfer
            shutil.copy(args.params, slot_dir / "parameters.dat")
            shutil.copy(args.music_input, slot_dir / "music_input")

            manifest_lines.append(str(rel_system / event))
            staged += 1

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.manifest.with_name(args.manifest.name + '.tmp')
    tmp.write_text(''.join(line + '\n' for line in manifest_lines))
    tmp.replace(args.manifest)

    print(f"staged {staged} gap events -> {out_system}")
    print(f"  (skipped {skipped} already-done; width set by total-nevents={args.total_nevents})")
    print(f"manifest: {args.manifest} ({len(manifest_lines)} lines)")
    print()
    print("Next, from the directory that CONTAINS the stage/ tree:")
    print("  condor_submit events_fanout.submit")


if __name__ == "__main__":
    main()
