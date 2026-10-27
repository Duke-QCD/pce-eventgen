"""O-O scan selection and plot completeness, without campaign-dependent fits."""
import contextlib
import csv
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inference as I
import robustness_scan as R
import plot_robustness_forest as F


class OORobustnessTests(unittest.TestCase):
    def setUp(self):
        self.system = I.SYSTEM

    def tearDown(self):
        I.use_system(self.system)

    def dry(self, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(R.main(["--system", "OO5360", "--dry-run", *extra]), 0)
        return output.getvalue().splitlines()

    def test_primary_uses_oo_groups_and_primary_exclusion(self):
        rows = self.dry()
        self.assertEqual(len(rows), 10)
        self.assertTrue(all("mean_pT_K0S_vs_mult" in row for row in rows))
        self.assertFalse(any("drop_mean_pT_K0S_vs_mult:" in row for row in rows))
        self.assertTrue(any("drop_dN_dy_Xi_vs_mult:" in row for row in rows))
        self.assertFalse(any("Lambda" in row or "proton" in row for row in rows))

    def test_optional_selection_keeps_k0s_and_all_three_proxies(self):
        rows = self.dry("--oo-yield-proxies", "--drop-specs", "")
        self.assertEqual(len(rows), 14)
        self.assertIn("exclude none", rows[0])
        for species in ("pion", "kaon", "proton"):
            self.assertTrue(any(f"drop_proxy_dN_dy_{species}_vs_mult:" in row for row in rows))
        self.assertTrue(any("drop_mean_pT_K0S_vs_mult:" in row for row in rows))

    def test_selected_variant_and_unknown_variant(self):
        rows = self.dry("--only-variants", "drop_dN_dy_Xi_vs_mult")
        self.assertEqual(len(rows), 1)
        with self.assertRaisesRegex(ValueError, "Unknown variants"):
            self.dry("--only-variants", "drop_dN_dy_Xi")

    def test_smoke_empty_selection_uses_only_active_specs(self):
        rows = self.dry("--smoke")
        self.assertEqual(len(rows), 3)
        self.assertIn("drop_dN_dy_Xi_vs_mult:", rows[1])
        self.assertIn("BROKEN_drop_everything", rows[2])

    def test_empty_primary_selection_and_misplaced_proxy_flag_fail(self):
        I.use_system("OO5360")
        with self.assertRaisesRegex(ValueError, "No observables"):
            R.run(False, "unused", 4, 2, system="OO5360",
                  drop_specs=[s["name"] for s in I.DATA_SPEC], dry_run=True)
        with self.assertRaisesRegex(ValueError, "only for OO5360"):
            R.run(False, "unused", 4, 2, oo_yield_proxies=True, dry_run=True)

    def test_forest_recognizes_every_oo_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            I.use_system("OO5360")
            I.enable_oo_yield_proxies()
            keys = ["baseline"] + ["drop_"+s["name"] for s in I.DATA_SPEC]
            path = Path(directory)/"summary.csv"
            fields = ["variant", "status", "converged", "P(gs<gl)", "P(ts>tl)"]
            fields += [f"{p}_{q}" for p, _ in F.PARAMS for q in ("q16", "q50", "q84")]
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                for key in keys:
                    row = dict(variant=key,status="ok",converged="False",**{"P(gs<gl)":.7,"P(ts>tl)":.6})
                    for p, _ in F.PARAMS:
                        lo, hi = next((lo,hi) for name,lo,hi in I.PARAMS_ALL if name==p)
                        for q, fraction in zip(("q16","q50","q84"),(.25,.5,.75)):
                            row[f"{p}_{q}"] = lo+(hi-lo)*fraction
                    writer.writerow(row)
            with contextlib.redirect_stdout(io.StringIO()):
                F.main(str(path), str(Path(directory)/"forest.pdf"))
            self.assertTrue((Path(directory)/"forest.pdf").read_bytes().startswith(b"%PDF"))
            content = path.read_text().replace("drop_c2_charged", "unrecognized_variant")
            path.write_text(content)
            with self.assertRaisesRegex(ValueError, "would be omitted"):
                F.main(str(path), str(Path(directory)/"bad.pdf"))


if __name__ == "__main__":
    unittest.main()
