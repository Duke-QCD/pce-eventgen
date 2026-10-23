"""Exercise runner handoffs and wrapper selection with temporary model stand-ins."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader("model_runner_under_test", str(ROOT / "models/run-events"))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
runner = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(runner)


class ModelHandoffTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"HIC_SURFACE_ARCHIVE_BASE": ""})
        environment.start()
        self.addCleanup(environment.stop)

    @staticmethod
    def create_eos_tables(directory):
        for index in range(30):
            with (Path(directory) / f"{index}.dat").open("wb") as stream:
                stream.truncate(12000000)  # sparse fixture; no physics data copied

    def test_selected_numbered_eos_directory_is_passed_directly(self):
        with tempfile.TemporaryDirectory(prefix="selected EOS with spaces ") as directory:
            data = Path(directory)
            self.create_eos_tables(data)
            with patch.dict(os.environ, {"HIC_EOS_PCE_DIR": str(data), "HYDROPROGRAMPATH": ""}):
                with runner.music_environment() as env:
                    self.assertEqual(env["HIC_EOS_PCE_DIR"], str(data))
                    self.assertEqual(env["HYDROPROGRAMPATH"], "")
            self.assertEqual(len(list(data.glob("*.dat"))), 30)

    def test_default_eos_selection_uses_canonical_installed_or_source_directory(self):
        for installed in (False, True):
            with self.subTest(installed=installed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                software, code = root / 'software', root / 'code'
                source = code / 'models/music-hydro/EOS/PCE/strange_30_10000'
                packaged = software / 'share/music-hydro/EOS/PCE/strange_30_10000'
                source.mkdir(parents=True)
                self.create_eos_tables(source)
                if installed:
                    packaged.mkdir(parents=True)
                    self.create_eos_tables(packaged)
                with patch.dict(os.environ), \
                     patch.object(runner, 'HIC_SOFTWARE_ROOT', software), \
                     patch.object(runner, 'HIC_CODE_HOME', code):
                    os.environ.pop('HIC_EOS_PCE_DIR', None)
                    with runner.music_environment() as env:
                        self.assertEqual(env['HIC_EOS_PCE_DIR'], str(packaged if installed else source))

    def test_native_music_data_override_does_not_override_pce_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            self.create_eos_tables(directory)
            with patch.dict(os.environ, {"HIC_EOS_PCE_DIR": directory, "HYDROPROGRAMPATH": "/selected/native/root"}):
                with runner.music_environment() as env:
                    self.assertEqual(env["HYDROPROGRAMPATH"], "/selected/native/root")
                    self.assertEqual(env["HIC_EOS_PCE_DIR"], directory)

    def test_missing_selected_eos_is_reported_without_switching_tables(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"HIC_EOS_PCE_DIR": directory, "HYDROPROGRAMPATH": ""}):
                with self.assertRaisesRegex(FileNotFoundError, "MUSIC PCE table is missing"):
                    with runner.music_environment():
                        self.fail("missing selected table was accepted")

    def test_short_local_archive_paths_stay_under_selected_root(self):
        with patch.dict(os.environ, {"PSCRATCH": "/different/scratch", "SCRATCH": "/different/scratch"}), \
             patch.object(runner, "SURFACE_ARCHIVE_ROOT", Path("/selected/archive")):
            event = Path("/tmp/local-run/0")
            self.assertFalse(runner.archive_relative_event_path(event).is_absolute())
            self.assertEqual(runner.archived_surface_path(event),
                             Path("/selected/archive/tmp/local-run/0/surface.dat.zst"))

    def test_archive_layout_matches_the_shared_rule_for_any_root_depth(self):
        sys.path.insert(0, str(ROOT / "models"))
        self.addCleanup(sys.path.remove, str(ROOT / "models"))
        import surface_archive
        events = [Path("/scratch/user/calibration/design_027/AuAu200/0000"),
                  Path("/scratch/user/nested/campaigns/oo1/design_000/OO5360/12"),
                  Path("/tmp/local-run/0")]
        with patch.dict(os.environ, {"PSCRATCH": "/scratch/user", "SCRATCH": "/scratch/user"}):
            for event in events:
                with self.subTest(event=event):
                    self.assertEqual(runner.archive_relative_event_path(event),
                                     surface_archive.archive_relative_path(event))
            self.assertEqual(surface_archive.archived_surface_path("/archive", events[1]),
                             Path("/archive/nested/campaigns/oo1/design_000/OO5360/12/surface.dat.zst"))

    def test_archive_paths_keep_four_components_without_scratch_environment(self):
        with patch.dict(os.environ, {"PSCRATCH": "", "SCRATCH": ""}):
            self.assertEqual(runner.archive_relative_event_path(
                "/scratch/user/nested/campaign/design_000/AuAu200/0000"),
                Path("campaign/design_000/AuAu200/0000"))

    def test_archive_paths_use_scratch_when_pscratch_does_not_contain_event(self):
        with patch.dict(os.environ, {"PSCRATCH": "/unrelated", "SCRATCH": "/scratch/user"}):
            self.assertEqual(runner.archive_relative_event_path(
                "/scratch/user/nested/campaign/design_000/AuAu200/0000"),
                Path("nested/campaign/design_000/AuAu200/0000"))

    def exercise(self, fail_is3d=False, unfinished=False):
        with tempfile.TemporaryDirectory(prefix="model handoff with spaces ") as directory:
            root = Path(directory)
            event = root / "0"
            event.mkdir()
            (event / "hydro.done").touch()
            (event / "surface.dat").write_text("fixture surface\n")
            charge = root / "charge_dict.dat"
            charge.write_text("211 1\n")
            is3d = root / "iS3D"
            is3d.write_text(f'''#!{sys.executable}
from pathlib import Path
Path('results/particle_list_osc.dat').write_text('# sample 0\\n211 1 0 0 0 1 0 0 0\\n# sample 1\\n211 1 0 0 0 1 0 0 0\\n')
raise SystemExit({7 if fail_is3d else 0})
''')
            smash = root / "smash"
            filename = "particle_lists.oscar.unfinished" if unfinished else "particle_lists.oscar"
            smash.write_text(f'''#!{sys.executable}
import json,sys
from pathlib import Path
args=sys.argv[1:]
overrides=json.loads(args[args.index('-c')+1])
Path('smash-handoff.json').write_text(json.dumps(overrides))
assert overrides['General']['Nevents']==2
assert Path(overrides['Modi']['List']['File_Directory'])==Path.cwd()/'results'
output=Path(args[args.index('-o')+1])
(output/{filename!r}).write_text('# completed fixture output\\n')
raise SystemExit({1 if unfinished else 0})
''')
            is3d.chmod(0o755)
            smash.chmod(0o755)
            original = Path.cwd()
            try:
                os.chdir(event)
                with patch.object(runner, "IS3D_EXEC", is3d), \
                     patch.object(runner, "SMASH_EXEC", smash), \
                     patch.object(runner, "IS3D_OSCAR_SCRIPT", ROOT / "models/iS3D/iS3D_OSCAR.py"), \
                     patch.object(runner, "SMASH_CONFIG", ROOT / "config/config.yaml"), \
                     patch.object(runner, "SMASH_TMP_ROOT", "smash scratch"), \
                     patch.object(runner, "archive_surface_if_possible", return_value=True), \
                     patch.dict(os.environ, {"HIC_CHARGE_DICT": str(charge)}):
                    if fail_is3d:
                        with self.assertLogs(level="ERROR"), self.assertRaisesRegex(RuntimeError, "iS3D failed"):
                            runner.run_afterburner_stage(event)
                        self.assertFalse((event / "smash-handoff.json").exists())
                        self.assertFalse((event / "afterburner.done").exists())
                        self.assertTrue((event / "afterburner.failed").is_file())
                        self.assertEqual(runner.read_afterburner_retry_count(event), 1)
                    else:
                        self.assertTrue(runner.run_afterburner_stage(event))
                        self.assertTrue((event / filename).is_file())
                        self.assertTrue((event / "afterburner.done").is_file())
                        self.assertFalse((event / "afterburner.failed").exists())
                    self.assertFalse((event / "afterburner.lock").exists())
            finally:
                os.chdir(original)

    def test_actual_sample_count_reaches_smash_and_converter_markers_match(self):
        self.exercise()

    def test_failed_is3d_partial_output_never_reaches_smash(self):
        self.exercise(fail_is3d=True)

    def test_existing_unfinished_output_policy_is_preserved(self):
        self.exercise(unfinished=True)


@unittest.skipUnless(shutil.which("zstd"), "zstd is required for archive regressions")
class SurfaceArchiveTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="surface archive regression ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scratch = self.root / "scratch"
        self.archive = self.root / "archive"
        environment = patch.dict(os.environ, {
            "PSCRATCH": str(self.scratch), "SCRATCH": str(self.scratch),
            "HIC_SURFACE_ARCHIVE_BASE": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        for name, value in (("SURFACE_ARCHIVE_ROOT", self.archive),
                            ("ZSTD_EXEC", shutil.which("zstd"))):
            override = patch.object(runner, name, value)
            override.start()
            self.addCleanup(override.stop)

    def event(self, project):
        event = self.scratch / project / "campaign/design_000/AuAu200/0000"
        event.mkdir(parents=True)
        return event

    def compress(self, destination, content):
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([runner.ZSTD_EXEC, "-q", "-f", "-o", str(destination)],
                       input=content, check=True, capture_output=True)

    def test_same_named_nested_campaigns_keep_independent_surfaces(self):
        first, second = self.event("projectA"), self.event("projectB")
        for event, content in ((first, b"first surface\n"), (second, b"second surface\n")):
            (event / "surface.dat").write_bytes(content)
            self.assertTrue(runner.archive_surface_if_possible(event))
            self.assertFalse((event / "surface.dat").exists())
        self.assertNotEqual(runner.archived_surface_path(first), runner.archived_surface_path(second))
        for event, content in ((first, b"first surface\n"), (second, b"second surface\n")):
            self.assertTrue(runner.restore_surface_from_archive_if_needed(event))
            self.assertEqual((event / "surface.dat").read_bytes(), content)

    def test_nested_archive_is_found_by_runner_and_maintenance(self):
        event = self.event("projectA")
        archive = self.archive / "projectA/campaign/design_000/AuAu200/0000/surface.dat.zst"
        self.compress(archive, b"existing surface\n")
        (event / "hydro.done").touch()
        sys.path.insert(0, str(ROOT / "models"))
        self.addCleanup(sys.path.remove, str(ROOT / "models"))
        import repair_stale_events
        from maintenance import regen_stuck
        self.assertEqual(repair_stale_events._derive_archive_target(
            event, event.parent, self.archive), archive)
        self.assertFalse(regen_stuck.is_target(event, self.archive))
        self.assertTrue(any("archived surface exists" in reason
                            for reason in regen_stuck.safety_refusals(event, self.archive)))
        self.assertTrue(runner.restore_surface_from_archive_if_needed(event))
        self.assertEqual((event / "surface.dat").read_bytes(), b"existing surface\n")

    def test_saved_base_restores_nested_archives_after_handoff(self):
        sys.path.insert(0, str(ROOT / "models"))
        self.addCleanup(sys.path.remove, str(ROOT / "models"))
        import repair_stale_events
        from surface_archive import archived_surface_path
        from maintenance import regen_stuck
        for project in ("projectA", "projectB"):
            event = self.event(project)
            content = project.encode() + b" surface\n"
            (event / "surface.dat").write_bytes(content)
            (event / "hydro.done").touch()
            # These are pre-existing archives written using the old scratch environment.
            self.assertTrue(runner.archive_surface_if_possible(event))
            archive = self.archive / project / "campaign/design_000/AuAu200/0000/surface.dat.zst"
            for scratch in ("", str(self.root / "recipient/scratch")):
                with self.subTest(project=project, scratch=scratch), patch.dict(os.environ, {
                    "HIC_SURFACE_ARCHIVE_BASE": str(self.scratch),
                    "PSCRATCH": scratch, "SCRATCH": scratch,
                }):
                    self.assertEqual(runner.archived_surface_path(event), archive)
                    self.assertEqual(archived_surface_path(self.archive, event), archive)
                    self.assertEqual(repair_stale_events._derive_archive_target(
                        event, event.parent, self.archive), archive)
                    self.assertFalse(regen_stuck.is_target(event, self.archive))
                    self.assertTrue(runner.restore_surface_from_archive_if_needed(event))
                    self.assertEqual((event / "surface.dat").read_bytes(), content)
                    self.assertTrue(runner.archive_surface_if_possible(event))
                    self.assertFalse((event / "surface.dat").exists())

    def test_invalid_base_never_falls_back_or_removes_a_surface(self):
        sys.path.insert(0, str(ROOT / "models"))
        self.addCleanup(sys.path.remove, str(ROOT / "models"))
        from surface_archive import archive_relative_path
        event = self.event("projectA")
        (event / "surface.dat").write_bytes(b"keep surface")
        for base, error in ((str(self.root / "other"), "outside"),
                            ("relative/path", "absolute"),
                            ("${UNSET_ARCHIVE_BASE}/scratch", "Unresolved")):
            with self.subTest(base=base), patch.dict(os.environ, {"HIC_SURFACE_ARCHIVE_BASE": base}):
                for lookup in (archive_relative_path, runner.archive_relative_event_path,
                               runner.archive_surface_if_possible):
                    with self.assertRaisesRegex(ValueError, error):
                        lookup(event)
                self.assertEqual((event / "surface.dat").read_bytes(), b"keep surface")
                self.assertFalse(self.archive.exists())

    def test_regeneration_and_deletion_tools_find_existing_archives_after_handoff(self):
        event = self.event("projectA")
        archive = self.archive / "projectA/campaign/design_000/AuAu200/0000/surface.dat.zst"
        self.compress(archive, b"existing surface")
        (event / "hydro.done").touch()
        env = dict(os.environ, PSCRATCH="", SCRATCH="", HIC_SURFACE_ARCHIVE_BASE="")
        common = ["--archive-root", str(self.archive), "--archive-base", str(self.scratch)]
        raw = self.scratch / "projectA/campaign"
        result = subprocess.run([sys.executable, str(ROOT / "models/maintenance/regen_stuck.py"),
                                 "000", "--run-root", str(raw), "--nevents", "1", "--apply", *common],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((event / "hydro.done").is_file())
        (event / "afterburner.done").touch()
        manifest = self.root / "deletion-plan.json"
        result = subprocess.run([sys.executable, str(ROOT / "models/maintenance/delete_archived_surfaces.py"),
                                 "--raw-root", str(raw), "--target", "1", "--reaped", "design_000",
                                 "--manifest", str(manifest), *common], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(manifest.read_text())["entries"][0]["zst"], [str(archive)])
        self.assertTrue(archive.is_file())

    def test_runner_rejects_wrong_base_before_starting_events(self):
        event = self.event("projectA")
        env = dict(os.environ, HIC_SURFACE_ARCHIVE_BASE=str(self.root / "other"))
        result = subprocess.run([sys.executable, str(ROOT / "models/run-events"),
                                 "--mode", "afterburner", "--nevents", "1", str(event.parent)],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside HIC_SURFACE_ARCHIVE_BASE", result.stderr)
        self.assertEqual(list(event.iterdir()), [])

    def test_conflicting_existing_archive_preserves_both_copies(self):
        event = self.event("projectA")
        archive = runner.archived_surface_path(event)
        original = b"existing surface\n"
        self.compress(archive, original)
        compressed = archive.read_bytes()
        for content in (b"different surface\n", original + b"extra", original[:-1]):
            with self.subTest(content=content):
                (event / "surface.dat").write_bytes(content)
                with self.assertRaisesRegex(RuntimeError, "does not match local surface"):
                    runner.archive_surface_if_possible(event)
                self.assertEqual((event / "surface.dat").read_bytes(), content)
                self.assertEqual(archive.read_bytes(), compressed)

    def test_matching_existing_archive_allows_retry_cleanup(self):
        event = self.event("projectA")
        content = b"same surface\n"
        self.compress(runner.archived_surface_path(event), content)
        (event / "surface.dat").write_bytes(content)
        self.assertTrue(runner.archive_surface_if_possible(event))
        self.assertFalse((event / "surface.dat").exists())


class WrapperTests(unittest.TestCase):
    def exercise(self, installed, nersc=False):
        with tempfile.TemporaryDirectory(prefix="runner wrapper with spaces ") as directory:
            root = Path(directory)
            entry = root / ("bin" if installed else "models")
            entry.mkdir()
            wrapper = entry / "run-events-wrapper"
            shutil.copy(ROOT / "models/run-events-wrapper", wrapper)
            sibling = entry / "run-events"
            sibling.write_text('''#!/usr/bin/env python3
import json,os,sys
print(json.dumps(dict(args=sys.argv[1:],cwd=os.getcwd(),software=os.getenv('HIC_SOFTWARE_ROOT'),code=os.getenv('HIC_CODE_HOME'),venv=os.getenv('VIRTUAL_ENV'),config=os.getenv('HIC_IS3D_CONFIG'))))
''')
            sibling.chmod(0o755)
            other = root / "wrong runner"
            other.mkdir()
            (other / "run-events").write_text("#!/bin/sh\necho WRONG\nexit 99\n")
            (other / "run-events").chmod(0o755)
            helper_dir = root / ("share/iS3D" if installed else "models/iS3D")
            helper_dir.mkdir(parents=True)
            shutil.copy(ROOT / "models/iS3D/paths.sh", helper_dir / "paths.sh")
            if installed:
                (root / "pyvenv.cfg").touch()
            event = root / "event directory"
            event.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("HIC_") and key not in ("BASH_ENV", "NERSC_HOST", "APPTAINER_CONTAINER", "SINGULARITY_CONTAINER")}
            env.update(PATH=str(other) + os.pathsep + env["PATH"],
                       VIRTUAL_ENV="/active/source/environment", MODULE_LOG=str(root / "module.log"))
            env["BASH_FUNC_module%%"] = '() { printf "%s\\n" "$*" > "$MODULE_LOG"; }'
            if nersc:
                env.update(NERSC_HOST="test", HIC_RUNTIME_GCC_MODULE="chosen-runtime")
            result = subprocess.run(["bash", str(wrapper), "--mode", "afterburner", "a path with spaces"],
                                    cwd=event, env=env, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            values = json.loads(result.stdout)
            self.assertEqual(values["args"], ["--mode", "afterburner", "a path with spaces"])
            self.assertEqual(values["cwd"], str(event))
            self.assertEqual(values["config"], str(helper_dir / "iS3D_parameters.dat"))
            self.assertEqual(values["venv"], str(root) if installed else "/active/source/environment")
            if installed:
                self.assertEqual(values["software"], str(root))
            else:
                self.assertEqual(values["code"], str(root))
            if nersc:
                self.assertEqual((root / "module.log").read_text(), "load chosen-runtime\n")
            else:
                self.assertFalse((root / "module.log").exists())
                self.assertEqual(result.stderr, "")

    def test_installed_wrapper_uses_sibling_and_packaged_data(self):
        self.exercise(installed=True)

    def test_source_wrapper_keeps_active_environment(self):
        self.exercise(installed=False)

    def test_nersc_wrapper_loads_requested_runtime(self):
        self.exercise(installed=True, nersc=True)


if __name__ == "__main__":
    unittest.main()
