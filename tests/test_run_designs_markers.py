"""Marker-reader regressions; never submit jobs or touch campaign directories."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "models" / "run_designs.py"
SPEC = importlib.util.spec_from_file_location("run_designs_under_test", SCRIPT)
run_designs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_designs)


class MarkerReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.run_dir = Path(self.temp_dir.name)

    def readers(self):
        return (
            ("campaign_state.json", "campaign_state.v1", run_designs.read_campaign_state),
            ("ready_to_cache.json", "ready_to_cache.v1", run_designs.read_ready_to_cache_marker),
        )

    def read(self, reader):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return reader(self.run_dir, "AuAu200", 10000)

    def test_missing_and_malformed_json_are_ignored(self):
        for name, _, reader in self.readers():
            with self.subTest(marker=name):
                self.assertIsNone(self.read(reader))
                (self.run_dir / name).write_text("{")
                self.assertIsNone(self.read(reader))

    def test_non_object_json_is_ignored(self):
        for name, _, reader in self.readers():
            for payload in ([], [1], None, "marker", 12, True):
                with self.subTest(marker=name, payload=payload):
                    (self.run_dir / name).write_text(json.dumps(payload))
                    self.assertIsNone(self.read(reader))

    def test_invalid_target_event_counts_are_ignored(self):
        for name, schema, reader in self.readers():
            for count in ("invalid", [], {}):
                with self.subTest(marker=name, count=count):
                    payload = {"schema": schema, "target_events": count}
                    (self.run_dir / name).write_text(json.dumps(payload))
                    self.assertIsNone(self.read(reader))

    def test_invalid_terminal_ready_count_is_ignored(self):
        for count in ("invalid", [], {}):
            with self.subTest(count=count):
                payload = {"schema": "ready_to_cache.v1", "terminal_ready_events": count}
                (self.run_dir / "ready_to_cache.json").write_text(json.dumps(payload))
                self.assertIsNone(self.read(run_designs.read_ready_to_cache_marker))

    def test_matching_markers_are_returned_without_changing_the_payload(self):
        for name, schema, reader in self.readers():
            for count in (10000, "10000"):
                with self.subTest(marker=name, count=count):
                    payload = {
                        "schema": schema,
                        "system": "AuAu200",
                        "target_events": count,
                        "terminal_ready_events": count,
                        "route": "ready",
                        "tallies": {"ready": 10000},
                    }
                    (self.run_dir / name).write_text(json.dumps(payload))
                    self.assertEqual(self.read(reader), payload)

    def test_existing_schema_system_and_target_guards_are_preserved(self):
        for name, schema, reader in self.readers():
            for overrides in (
                {"schema": "unknown"},
                {"system": "OO5360"},
                {"target_events": 9999},
            ):
                with self.subTest(marker=name, overrides=overrides):
                    payload = {"schema": schema, "system": "AuAu200", "target_events": 10000}
                    payload.update(overrides)
                    (self.run_dir / name).write_text(json.dumps(payload))
                    self.assertIsNone(self.read(reader))

    def test_ready_marker_with_shortfall_is_ignored(self):
        payload = {"schema": "ready_to_cache.v1", "terminal_ready_events": 9999}
        (self.run_dir / "ready_to_cache.json").write_text(json.dumps(payload))
        self.assertIsNone(self.read(run_designs.read_ready_to_cache_marker))

    def test_existing_optional_field_behavior_is_preserved(self):
        for name, schema, reader in self.readers():
            with self.subTest(marker=name):
                payload = {"schema": schema}
                (self.run_dir / name).write_text(json.dumps(payload))
                self.assertEqual(self.read(reader), payload)


if __name__ == "__main__":
    unittest.main()
