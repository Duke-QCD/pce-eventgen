"""Offline regression checks for the OSG drain and sweep deletion gates.

Run with: python3 -m unittest discover -s osg/tests -v
Only temporary directories are touched; SSH, rsync, Condor and deletes are mocked.
"""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


def load_script(name):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


drain = load_script("drain_osg_events_batched")
sweep = load_script("sweep_osg_designs")


class DrainCommitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = drain.Cfg("unused", "/unused", "1", "AuAu200",
                             str(self.root), False, False, [])

    def prepare_event(self, event, kind="particles"):
        outbox = self.root / event / "outbox"
        outbox.mkdir(parents=True)
        if kind == "particles":
            (outbox / "particle_lists.oscar").write_bytes(b"payload")
            (outbox / 'afterburner.done').touch()
            return kind, 7
        (outbox / "hydro.no_surface").touch()
        return kind, 0

    def local_ssh(self, cfg, remote_cmd, timeout, input_text):
        # Exercise the exact remote shell emitted by the production function.
        result = subprocess.run(["bash", "-c", remote_cmd], input=input_text,
                                capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr

    def verify(self, events):
        with patch.object(drain, "ssh_nersc", side_effect=self.local_ssh):
            return drain.flatten_verify_mark(self.cfg, events)

    def test_particles_and_no_surface_commit_markers(self):
        events = {"0000": self.prepare_event("0000"),
                  "0001": self.prepare_event("0001", "nosurface")}
        self.assertEqual(self.verify(events), set(events))
        for event in events:
            self.assertTrue((self.root / event / "drained.done").is_file())

    def test_marker_write_failure_never_reports_ok(self):
        payload = self.prepare_event("0000")
        (self.root / "0000" / ".drained.tmp").mkdir()
        self.assertEqual(self.verify({"0000": payload}), set())
        self.assertFalse((self.root / "0000" / "drained.done").exists())

    def test_marker_rename_failure_never_reports_ok(self):
        payload = self.prepare_event("0000")
        # A directory at the marker path must fail, rather than swallowing the
        # temporary marker as a child and falsely claiming a successful commit.
        (self.root / "0000" / "drained.done").mkdir()
        self.assertEqual(self.verify({"0000": payload}), set())
        self.assertFalse((self.root / "0000" / "drained.done").is_file())

    def test_verification_failure_never_marks(self):
        self.prepare_event("0000")
        self.assertEqual(self.verify({"0000": ("particles", 99)}), set())
        self.assertFalse((self.root / "0000" / "drained.done").exists())

    def test_failed_event_does_not_prevent_later_commit(self):
        failed = self.prepare_event("0000")
        good = self.prepare_event("0001", "nosurface")
        (self.root / "0000" / ".drained.tmp").mkdir()
        self.assertEqual(self.verify({"0000": failed, "0001": good}), {"0001"})

    def test_incomplete_and_conflicting_particle_outputs_never_mark(self):
        for problem in ('missing_done', 'both_variants', 'no_surface_conflict'):
            with self.subTest(problem=problem):
                event = str(len(list(self.root.iterdir())))
                payload = self.prepare_event(event)
                outbox = self.root / event / 'outbox'
                if problem == 'missing_done':
                    (outbox / 'afterburner.done').unlink()
                elif problem == 'both_variants':
                    (outbox / 'particle_lists.oscar.unfinished').write_bytes(b'payload')
                else:
                    (outbox / 'hydro.no_surface').touch()
                self.assertEqual(self.verify({event: payload}), set())
                self.assertFalse((self.root / event / 'drained.done').exists())

    def test_one_pass_preserves_ap_copy_when_marker_commit_fails(self):
        events = {"0000": self.prepare_event("0000")}
        (self.root / "0000" / ".drained.tmp").mkdir()
        with patch.object(drain, "list_completed_events", return_value=events), \
                patch.object(drain, "remote_drained_set", return_value=set()), \
                patch.object(drain, "rsync_push", return_value=0), \
                patch.object(drain, "ssh_nersc", side_effect=self.local_ssh), \
                patch.object(drain, "local_delete") as delete:
            tally, count = drain.one_pass(self.cfg, 0)
        self.assertEqual(count, 1)
        self.assertEqual(tally.get("FAILED-verify", 0), 1)
        self.assertEqual(tally["drained"], 0)
        delete.assert_not_called()


class SweepDrainedCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "campaign" / "design_1" / "AuAu200").mkdir(parents=True)

    def check_with_result(self, result):
        with patch.object(sweep, "run", return_value=result):
            return sweep.ap_fully_drained(self.root, "campaign", 1, "AuAu200")

    def test_failed_scan_blocks_teardown_even_without_stdout(self):
        for rc in (1, 124):
            with self.subTest(rc=rc):
                self.assertFalse(self.check_with_result((rc, "", "scan failed")))

    def test_completed_output_blocks_teardown(self):
        self.assertFalse(self.check_with_result((0, "event/outbox/hydro.no_surface\n", "")))

    def test_successful_empty_scan_allows_teardown(self):
        self.assertTrue(self.check_with_result((0, "", "")))


if __name__ == "__main__":
    unittest.main()
