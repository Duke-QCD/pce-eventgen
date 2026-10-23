#!/usr/bin/env python3
"""Small synthetic regression check; no NERSC data, Slurm, publication or deletion.

Run from the repository root: python models/analysis/check_cache_profiles.py
Or invoke this script by absolute path from any directory.
Optionally compare every AuAu shard array against the previous cache builder:
  python models/analysis/check_cache_profiles.py --baseline-cache /path/to/old/cache_one_design.py
"""
import argparse
import contextlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import numpy as np

ANALYSIS = Path(__file__).resolve().parent
CODE = ANALYSIS.parent / "cache"
ROOT = ANALYSIS.parents[1]
sys.path.insert(0, str(ANALYSIS))
sys.path.insert(0, str(ANALYSIS.parent))
from workflow_config import scope_environment
import extract_observables as sx
import reduce_observables as sr


def run(argv, env=None, ok=True):
    result = subprocess.run([str(v) for v in argv], env=env, text=True, capture_output=True)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return result


FINGERPRINTS = {
    "AuAu200": ROOT / "config/cache_fingerprint_AuAu200_gap015.json",
    "OO5360": ROOT / "config/cache_fingerprint_OO5360_ALICE_subevent_gap14.json",
}


def profile_environment(system):
    # The same scope environment campaign.py exports from the frozen fingerprint.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HIC_")}
    env.update(scope_environment(FINGERPRINTS[system]))
    env.update(HIC_CACHE_SYSTEM=system, HIC_EXPECTED_FINGERPRINT_V3=str(FINGERPRINTS[system]))
    return env


def make_input(tmp):
    raw = tmp / "raw"
    raw.mkdir()
    masses = {211: .13957, 321: .49368, 311: .49761, 2212: .93827,
              3122: 1.11568, 3312: 1.32171, 3334: 1.67245, 333: 1.01946}
    pdg = tmp / "pdg.dat"
    pdg.write_text("".join(f"s{k} {mass} 0 1 {k}\n" for k, mass in masses.items()))
    baryon = tmp / "baryon.dat"
    baryon.write_text("".join(f"{k} {int(k in (2212,3122,3312,3334))}\n" for k in masses))
    # Explicitly exercise the old validation bug (.8<|eta|<1), the gap strips,
    # discarded central gap particles, the O-O upper pT cut, and scalar overflow.
    particles = [(211, 1, pt, eta, phi) for pt, eta, phi in
                 [(1, .75, .1), (1.5, -.75, .4), (1, .65, .2), (1, -.65, .3),
                  (1, .9, .5), (1, -.9, .6), (.1, .1, .1), (2.8, .75, .7),
                  (3.2, -.75, .8), (20.5, .1, .9), (.4, .1, .2), (.7, -.1, .4)]]
    particles += [(pd, charge, .6, .2, .1) for pd, charge in
                  [(-211,-1),(321,1),(-321,-1),(2212,1),(-2212,-1),
                   (311,0),(3122,0),(-3122,0),(3312,-1),(-3312,1),
                   (3334,-1),(-3334,1),(333,0)]]
    for i in range(24):
        event = raw / f"{i:02d}"
        event.mkdir()
        if i == 23:
            (event / "hydro.no_surface").touch()
            continue
        rows = []
        for pd, charge, pt, eta, phi in particles * (1 + i % 4):
            mass = masses[abs(pd)]
            pz = pt * math.sinh(eta)
            energy = math.sqrt(mass**2 + pt**2 + pz**2)
            rows.append(f"0 0 0 0 {mass} {energy:.17g} {pt*math.cos(phi):.17g} "
                        f"{pt*math.sin(phi):.17g} {pz:.17g} {pd} 0 {charge}\n")
        text = ("#!OSCAR2013 particle_lists t x y z mass p0 px py pz pdg ID charge\n"
                "# Units: fm fm fm fm GeV GeV GeV GeV GeV none none e\n# SMASH-test\n")
        for j in range(1 + i % 3):
            text += f"# event {j} out {len(rows)}\n" + "".join(rows) + f"# event {j} end 0\n"
        (event / "particle_lists.oscar").write_text(text)
        (event / "afterburner.done").touch()
    return raw, pdg, baryon


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-cache", type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="oo-cache-check-") as directory:
        tmp = Path(directory)
        raw, pdg, baryon = make_input(tmp)
        results = {}
        for system in ("AuAu200", "OO5360"):
            env = profile_environment(system)
            env.update(HIC_PDG_MASS=str(pdg), HIC_BARYON_DICT=str(baryon))
            cache = tmp / "cache/design_000" / system
            fp_path = tmp / f"{system}.json"
            run([sys.executable, CODE / "cache_one_design.py", "--dump-fingerprint", fp_path], env)
            fp = json.loads(fp_path.read_text())
            if system == "OO5360":
                frozen = ROOT / "config/cache_fingerprint_OO5360_ALICE_subevent_gap14.json"
                assert fp_path.read_bytes() == frozen.read_bytes()
            build = [sys.executable, CODE / "cache_one_design.py", "--input-design", raw,
                     "--output-cache", cache, "--num-events", "24", "--max-events", "24",
                     "--validate-events", "24", "--events-per-shard", "12", "--nprocs", "2"]
            run(build, env)
            meta = json.loads((cache / "metadata.json").read_text())
            assert meta["validation"]["closures_passed"] and meta["n_zero_records"] == 1
            assert meta["validation"]["raw_reference_enabled"]
            assert meta["validation"]["raw_reference_events"] > 0
            out, ameta = sx.extract_design(str(cache), require_verified=False)
            assert out["nsub_used"].tolist() == [1+i%3 for i in range(23)] + [0]
            assert ameta["reconstruction_assumes"]["flow_eta_gap"] == fp["subevent_eta_gap"]
            prefix = str(tmp / f"design_000_{system}_stageA")
            sx.write_artifact(out, ameta, prefix)
            with contextlib.redirect_stdout(io.StringIO()):
                assert sr.validate_against_cache(prefix, str(cache))
            reduced = sr.reduce_class(out, ameta, np.array([0,1,23]), weighting="equal_event")
            expected_nch = (out["charged_N"][0,0] + out["charged_N"][1,0]/2) / 3
            assert np.isclose(reduced["charged"]["dNch_deta"][0], expected_nch)
            results[system] = (env, cache, fp_path, out)
            if system == "OO5360":
                shard = np.load(cache / meta["shards"][0])
                # Event zero has exactly three accepted charged particles in these strips.
                assert shard["fl#0#m_fwd"][0] == 2
                assert shard["fl#0#m_bwd"][0] == 1
            if system == "AuAu200" and args.baseline_cache:
                old_cache = tmp / "baseline"
                old = build.copy()
                old[1] = args.baseline_cache.resolve()
                old[old.index("--output-cache") + 1] = old_cache
                run(old, env)
                old_meta = json.loads((old_cache / "metadata.json").read_text())
                assert old_meta["fingerprint"] == fp
                for shard_name in meta["shards"]:
                    with np.load(cache / shard_name) as a, np.load(old_cache / shard_name) as b:
                        assert set(a.files) == set(b.files)
                        for key in a.files:
                            np.testing.assert_array_equal(a[key], b[key], err_msg=key)
        # Identified observables and fixed charged scalar acceptances are unchanged.
        for key in ("yh_N", "yh_sum_pT", "charged_N", "charged_sum_pT", "cent_mult"):
            np.testing.assert_array_equal(results["AuAu200"][3][key], results["OO5360"][3][key])
        # A uniform O-O matrix succeeds and records its cuts; same-shaped mixed
        # fingerprints fail before writing a misleading matrix.
        artifacts = tmp / "artifacts"
        artifacts.mkdir()
        for suffix in (".npz", ".meta.json"):
            shutil.copyfile(tmp / f"design_000_OO5360_stageA{suffix}",
                            artifacts / f"design_000_OO5360_stageA{suffix}")
        aggregate = [sys.executable, ANALYSIS / "run_analysis.py", "aggregate", "--out-dir", artifacts,
                     "--system", "OO5360", "--bin-edges", "0", "100", "--no-csv",
                     "--expected-fingerprint", results["OO5360"][2]]
        run(aggregate)
        matrix_meta = json.loads((artifacts / "model_matrix.meta.json").read_text())
        assert matrix_meta["cache_fingerprint"]["subevent_eta_gap"] == 1.4
        for suffix in (".npz", ".meta.json"):
            shutil.copyfile(tmp / f"design_000_AuAu200_stageA{suffix}",
                            artifacts / f"design_001_OO5360_stageA{suffix}")
        rejected = run(aggregate, ok=False)
        assert "mixed cache fingerprints" in rejected.stderr
        # Uniform but wrong scopes must also fail, including pre-existing
        # artifacts that formerly would have bypassed extraction via skip-done.
        for suffix in (".npz", ".meta.json"):
            (artifacts / f"design_000_OO5360_stageA{suffix}").unlink()
        rejected = run(aggregate, ok=False)
        assert "does not match selected expected fingerprint" in rejected.stderr
        wrong_cache = tmp / "wrong_cache/design_001/OO5360"
        shutil.copytree(results["AuAu200"][1], wrong_cache)
        (wrong_cache / "cache.verified").write_text("{}")  # discovery-only fixture
        scope_flag = ["--expected-fingerprint", results["OO5360"][2]]
        reject_commands = [
            [ANALYSIS / "extract_observables.py", "--cache-dir", wrong_cache,
             "--out-dir", artifacts, "--allow-unverified"],
            [ANALYSIS / "analyze_design.py", "run", "--cache-dir", wrong_cache,
             "--out-dir", artifacts, "--allow-unverified"],
            [ANALYSIS / "run_analysis.py", "run", "--cache-root", tmp / "wrong_cache",
             "--out-dir", artifacts, "--system", "OO5360"],
            [ANALYSIS / "analyze_design.py", "reduce", "--artifact",
             artifacts / "design_001_OO5360_stageA"],
            [ANALYSIS / "reduce_observables.py", "reduce", "--artifact",
             artifacts / "design_001_OO5360_stageA"],
        ]
        for cmd in reject_commands:
            rejected = run([sys.executable] + cmd + scope_flag, ok=False)
            assert "does not match selected expected fingerprint" in rejected.stderr
        # A correct old Au artifact remains analyzable under its own scope.
        run([sys.executable, ANALYSIS / "analyze_design.py", "reduce", "--artifact",
             tmp / "design_000_AuAu200_stageA", "--expected-fingerprint", results["AuAu200"][2],
             "--n-boot", "0", "--bin-edges", "0,100"])
        no_scope_env = {k: v for k, v in os.environ.items() if not k.startswith("HIC_")}
        rejected = run(aggregate[:-2], no_scope_env, ok=False)
        assert "expected fingerprint is required" in rejected.stderr
        env, cache, fp_path, out = results["OO5360"]
        # Test actual CLI default selection and environment capture without submission.
        manifest = tmp / "oo.manifest"
        manifest.write_text("design_000 OO5360\n")
        stub = tmp / "unused_reaper.py"
        stub.write_text("raise SystemExit('Synthetic test must never invoke the reaper')\n")
        env.update(HIC_EXPECTED_FINGERPRINT_V3=str(fp_path))
        argv = [sys.executable, ROOT / "models/submit_cache_reap_campaign.py",
                "--stage", "cache", "--manifest", manifest,
                "--raw-root", raw.parent, "--cache-local-root", tmp / "batch_local",
                "--cache-cfs-root", tmp / "batch_cfs", "--record-dir", tmp / "records",
                "--slurm-log-root", tmp / "slurm", "--wrapper", stub,
                "--cache-script", CODE / "cache_one_design.py", "--publish-script", stub,
                "--reaper-script", stub]
        run(argv, env)
        sbatch = (tmp / "records/_submit_cache.sbatch").read_text()
        assert "export HIC_ANALYSIS_CHARGED_ETA_MAX=0.8" in sbatch
        assert "export HIC_ANALYSIS_SUBEVENT_ETA_GAP=1.4" in sbatch
        run(["bash", "-n", tmp / "records/_submit_cache.sbatch"])
        # Execute only the preflight prefix with no inherited scientific settings.
        preflight_lines = []
        for line in sbatch.splitlines():
            if "<<'PCE_CAMPAIGN_TASK'" in line:
                break
            preflight_lines.append(line)
        preflight = "\n".join(preflight_lines)
        run(["bash", "-c", preflight], {"PATH": os.environ["PATH"]})
        # Execute generated batches with a capture-only wrapper: verify that
        # policy knobs actually reach a child under a conflicting launch env.
        observed = tmp / "observed_policy.json"
        stub.write_text("import json, os, pathlib, sys\n"
                        f"pathlib.Path({str(observed)!r}).write_text(json.dumps({{"
                        "'drop': os.environ.get('HIC_MAX_DROPPED_EVENTS'), "
                        "'rows': os.environ.get('HIC_ALLOW_MALFORMED_ROWS'), 'argv': sys.argv}))\n")
        policy_cases = [({}, [], "0", "0"),
                        ({"HIC_MAX_DROPPED_EVENTS": "50", "HIC_ALLOW_MALFORMED_ROWS": "1000"},
                         [], "50", "1000"),
                        ({"HIC_MAX_DROPPED_EVENTS": "50", "HIC_ALLOW_MALFORMED_ROWS": "1000"},
                         ["--allow-malformed-rows", "7"], "50", "7"),
                        ({"HIC_MAX_DROPPED_EVENTS": "50", "HIC_ALLOW_MALFORMED_ROWS": "1000"},
                         ["--allow-malformed-rows", "0"], "50", "0")]
        for overrides, flags, drop, rows in policy_cases:
            run(argv + flags, dict(env, **overrides))
            run(["bash", tmp / "records/_submit_cache.sbatch"],
                {"PATH": os.environ["PATH"], "SLURM_ARRAY_TASK_ID": "0",
                 "HIC_MAX_DROPPED_EVENTS": "999", "HIC_ALLOW_MALFORMED_ROWS": "999"})
            got = json.loads(observed.read_text())
            assert got["drop"] == drop and got["rows"] == rows
            if rows != "0":
                assert got["argv"][got["argv"].index("--allow-malformed-rows") + 1] == rows
        no_system = {k: v for k, v in env.items() if k != "HIC_CACHE_SYSTEM"}
        rejected = run(argv + ["--system", "OO5360"], no_system, ok=False)
        assert "HIC_CACHE_SYSTEM is unset" in rejected.stderr
        bad_env = dict(env, HIC_ANALYSIS_SUBEVENT_ETA_GAP="0.15")
        rejected = run(argv, bad_env, ok=False)
        assert "subevent_eta_gap" in rejected.stderr
        changed_fp = json.loads(fp_path.read_text())
        changed_fp["subevent_eta_gap"] = .15
        fp_path.write_text(json.dumps(changed_fp))
        rejected = run(["bash", "-c", preflight], {"PATH": os.environ["PATH"]}, ok=False)
        assert "changed since submission" in rejected.stderr
        run(argv + ["--system", "AuAu200"], env, ok=False)
    print("PASS: both profiles, raw validation, per-design analysis round-trip, variable oversampling, zero event,")
    print("      acceptance cuts, scalar invariance, matrix scope checks, batch defaults, environment capture")
    print("      policy inheritance/overrides, missing-profile rejection, and wrong-scope rejection.")
    if args.baseline_cache:
        print("PASS: all AuAu shard arrays and fingerprint match the previous builder exactly.")


if __name__ == "__main__":
    main()
