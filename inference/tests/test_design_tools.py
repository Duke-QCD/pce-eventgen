"""Design geometry, row provenance, and inference interface regressions."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import design_subset as subset
import design_tools as D
import inference as I
import inspect_design as inspect
import make_design as make


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="design tools ")
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    def write(self, path, values):
        D.write_designs([(path, values, {"operation": "test-fixture"})])

    def test_seed_stratification_and_best_candidate_score(self):
        first = D.generate_design(32, seed=7, candidates=1)
        best = D.generate_design(32, seed=7, candidates=8)
        np.testing.assert_array_equal(best, D.generate_design(32, seed=7, candidates=8))
        self.assertFalse(np.array_equal(best, D.generate_design(32, seed=8, candidates=8)))
        unit = D.normalize_design(best)
        for column in unit.T:
            np.testing.assert_array_equal(np.sort(np.floor(column * 32).astype(int)), np.arange(32))
        self.assertGreaterEqual(D.min_pairwise_distance(unit),
                                D.min_pairwise_distance(D.normalize_design(first)))
        for kwargs in ({"ndesign": 0}, {"seed": -1}, {"candidates": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                D.generate_design(**kwargs)

    def test_generated_table_roundtrips_through_inference_readers(self):
        path = self.work / "full.dat"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(make.main(["--out", str(path), "--ndesign", "24", "--candidates", "2"]), 0)
        values = D.read_design(path)
        metadata = json.loads(path.with_suffix(".meta.json").read_text())
        self.assertEqual(metadata["design_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(metadata["seed"], 12345)
        self.assertEqual(list(metadata["parameter_ranges"]), D.PARAM_NAMES)
        for system in ("AuAu200", "OO5360"):
            with self.subTest(system=system):
                dropped = set(I.NORM_PARAMS) - {I.SYSTEMS[system]["norm"]}
                loaded, active, names, ranges = I.load_design(path, drop_params=dropped)
                np.testing.assert_array_equal(loaded, values)
                self.assertEqual(len(active), 14)
                np.testing.assert_array_equal(ranges, [(low, high) for name, low, high in I.PARAMS_ALL
                                                      if name in names])
                # Model matrices may be gapped and unsorted; full-table IDs still align.
                np.testing.assert_array_equal(I.build_X(loaded, np.array([7, 0, 4]), active),
                                              values[[7, 0, 4]][:, active])

    def test_subset_remainder_and_full_order_preserve_every_row_and_mapping(self):
        full = D.generate_design(24, candidates=2)
        source = self.work / "source.dat"
        self.write(source, full)
        selected, remaining, ordered = [self.work / name for name in ("selected.dat", "remaining.dat", "ordered.dat")]
        with contextlib.redirect_stdout(io.StringIO()):
            subset.main(["--design", str(source), "--n-select", "6", "--n-starts", "100",
                         "--out", str(selected), "--remainder-out", str(remaining), "--ordered-out", str(ordered)])
        mappings = []
        for output in (selected, remaining, ordered):
            metadata = json.loads(output.with_suffix(".meta.json").read_text())
            indices = metadata["source_row_indices"]
            mappings.append(indices)
            np.testing.assert_array_equal(D.read_design(output), full[indices])
            self.assertEqual(metadata["source"], D.source_identity(source))
            self.assertEqual(metadata["n_starts"], len(full))
        self.assertEqual(mappings[2], mappings[0] + mappings[1])
        self.assertEqual(sorted(mappings[2]), list(range(len(full))))
        self.assertEqual(mappings[1], sorted(mappings[1]))
        self.assertEqual(len(mappings[0]), 6)
        np.testing.assert_array_equal(D.read_design(ordered),
                                      np.concatenate((D.read_design(selected), D.read_design(remaining))))

    def test_subset_edge_cases_and_determinism(self):
        values = D.generate_design(12, candidates=1)
        chosen = D.greedy_maximin_subset(values, 5, seed=4)
        np.testing.assert_array_equal(chosen, D.greedy_maximin_subset(values, 5, seed=4))
        self.assertEqual(len(set(chosen)), 5)
        self.assertEqual(sorted(D.greedy_maximin_subset(values, 12)), list(range(12)))
        self.assertEqual(len(D.greedy_maximin_subset(values[:1], 1)), 1)
        self.assertIsNone(D.min_pairwise_distance(D.normalize_design(values[:1])))
        for n in (0, 13):
            with self.assertRaises(ValueError):
                D.greedy_maximin_subset(values, n)

    def test_all_outputs_are_preflighted_before_any_table_is_written(self):
        values = D.generate_design(8, candidates=1)
        first, second = self.work / "first.dat", self.work / "second.dat"
        second.with_suffix(".meta.json").write_text("existing provenance")
        with self.assertRaises(FileExistsError):
            D.write_designs([(first, values, {}), (second, values, {})])
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(second.with_suffix(".meta.json").read_text(), "existing provenance")
        with self.assertRaises(ValueError):
            D.write_designs([(first, values, {}), (first, values, {})])
        self.assertFalse(first.exists())
        with self.assertRaises(ValueError):
            D.write_designs([(first, values, {})], protected=(first,))

    def test_strict_header_shape_finiteness_and_bounds_validation(self):
        path = self.work / "input.dat"
        values = D.generate_design(2, candidates=1)
        data = "\n".join(" ".join(map(str, row)) for row in values)
        header = "Parameter " + " ".join(D.PARAM_NAMES)
        path.write_text("\n# explanatory comment\n" + header + "\n" + data)
        np.testing.assert_array_equal(D.read_design(path), values)
        for content in (data, "# " + header, "# " + header + "\n1 2",
                        "# " + header.replace("norm5360 norm5020", "norm5020 norm5360") + "\n" + data,
                        "# " + header + "\n" + header + "\n" + data):
            with self.subTest(content=content[:80]):
                path.write_text(content)
                with self.assertRaises(ValueError):
                    D.read_design(path)
        for value in (np.nan, np.inf, D.LOWER[0] - 1, D.UPPER[0] + 1):
            bad = values.copy()
            bad[0, 0] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                D.validate_design(bad)

    def test_inspection_uses_zero_based_rows_and_refuses_csv_overwrite(self):
        source, csv_path = self.work / "source.dat", self.work / "selected.csv"
        self.write(source, D.generate_design(8, candidates=1))
        self.assertEqual(inspect.parse_rows("3,0-2,3", 8), [3, 0, 1, 2])
        for selection in ("-1", "4-2", "0-8", "8", "0-999999999999", "", "0,,1"):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                inspect.parse_rows(selection, 8)
        with contextlib.redirect_stdout(io.StringIO()):
            inspect.main(["--design", str(source), "--rows", "3,0", "--out", str(csv_path)])
        lines = csv_path.read_text().splitlines()
        self.assertTrue(lines[1].startswith("3,design_003,"))
        self.assertTrue(lines[2].startswith("0,design_000,"))
        before = csv_path.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            inspect.main(["--design", str(source), "--rows", "0", "--out", str(csv_path)])
        self.assertEqual(csv_path.read_bytes(), before)

    def test_entrypoints_from_another_working_directory_and_no_import_side_effects(self):
        for name in ("make_design.py", "design_subset.py", "inspect_design.py"):
            with self.subTest(script=name):
                result = subprocess.run([sys.executable, str(ROOT / name), "--help"],
                                        cwd=self.work, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
        command = "import sys; sys.path.insert(0, sys.argv[1]); import make_design, design_subset, inspect_design"
        result = subprocess.run([sys.executable, "-c", command, str(ROOT)],
                                cwd=self.work, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
