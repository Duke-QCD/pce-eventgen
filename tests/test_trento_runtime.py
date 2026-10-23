"""Native TRENTo regressions; set TRENTO_TEST_EXECUTABLE to a freshly built binary."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.environ.get("TRENTO_TEST_EXECUTABLE"),
                     "set TRENTO_TEST_EXECUTABLE to run native TRENTo regressions")
class TrentoRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.executable = str(Path(os.environ["TRENTO_TEST_EXECUTABLE"]).resolve())
        directory = tempfile.TemporaryDirectory(prefix="trento runtime regression ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data_home = self.root / "new parent/share"
        self.cache = self.data_home / "trento/cross_section.cache"

    def run_trento(self, *options):
        return subprocess.run(
            [self.executable, "p", "p", "1", "--random-seed", "1", *options],
            env={**os.environ, "XDG_DATA_HOME": str(self.data_home)},
            cwd=self.root, capture_output=True, text=True, timeout=10,
        )

    def test_missing_cache_parents_are_created_and_cache_is_reused(self):
        first = self.run_trento()
        self.assertEqual(first.returncode, 0, first.stderr)
        cache = self.cache.read_bytes()
        self.assertTrue(cache)
        second = self.run_trento()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout, first.stdout)
        self.assertEqual(self.cache.read_bytes(), cache)

    def test_malformed_and_truncated_records_fail_promptly_without_modification(self):
        self.cache.parent.mkdir(parents=True)
        for content in ("incomplete cache row\n", "1 0.5 0.5 6.4",
                        "1 0.5 0.5\n1 0.5 0.5 6.4 10\n",
                        "1 0.5 0.5 6.4 10 extra\n"):
            with self.subTest(content=content):
                self.cache.write_text(content)
                result = self.run_trento()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid TRENTo cross-section cache record", result.stderr)
                self.assertIn(str(self.cache), result.stderr)
                self.assertEqual(self.cache.read_text(), content)

    def test_empty_cache_can_be_populated(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.touch()
        result = self.run_trento()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.cache.read_text().strip())

    def test_nonpositive_constituent_count_is_rejected_before_cache_creation(self):
        for count in ("0", "-1"):
            with self.subTest(count=count):
                result = self.run_trento("--constit-number", count)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("constituent number must be at least 1", result.stderr)
                self.assertFalse(self.data_home.exists())

    def test_invalid_constituent_count_from_config_is_rejected(self):
        config = self.root / "invalid.conf"
        config.write_text("constit-number = 0\n")
        result = self.run_trento("--config-file", config.name)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("constituent number must be at least 1", result.stderr)
        self.assertFalse(self.data_home.exists())


if __name__ == "__main__":
    unittest.main()
