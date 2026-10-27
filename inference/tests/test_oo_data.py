"""Measurement integrity and malformed-input regressions; no training or campaign files."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import audit_oo_data as audit
import inference as I
import extract_alice_uncertainties as extraction


class OODataAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "OO5360"
        shutil.copytree(audit.DEFAULT_DATA, self.root)

    def test_retained_measurements_match_their_evidence(self):
        result = audit.audit(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["checks"]), 16)
        self.assertTrue(result["warnings"])

    def test_changed_flow_value_is_not_hidden_by_matching_error(self):
        path = self.root / "centrality/v2_2_charged_ALICE.dat"
        values = np.loadtxt(path)
        values[0, 3] *= 1.1
        np.savetxt(path, values)
        self.assertTrue(any("source value" in e for e in audit.audit(self.root)["errors"]))

    def test_wrong_collision_species_in_snapshot_is_rejected(self):
        path = self.root / "uncertainties/v2_2_charged_ALICE.json"
        source = json.loads(path.read_text())
        for q in source["qualifiers"]:
            if q["name"] == "RE":
                q["value"] = "Ne-Ne"
        path.write_text(json.dumps(source))
        self.assertTrue(any("not O-O" in e for e in audit.audit(self.root)["errors"]))

    def test_changed_cms_error_fails_independent_figure_check(self):
        path = self.root / "centrality/dN_deta_charged_CMS.dat"
        values = np.loadtxt(path)
        values[0, 4] /= 2
        np.savetxt(path, values)
        self.assertTrue(any("independent CMS" in e for e in audit.audit(self.root)["errors"]))

    def test_identified_errors_record_resolved_and_obscured_points(self):
        statuses = []
        for path in (self.root / "uncertainties").glob("*_vs_mult.json"):
            source = json.loads(path.read_text())
            statuses.extend(row["status"] for row in source["rows"])
            for row in source["rows"]:
                if row["status"] == "digitized_total":
                    self.assertGreater(row["error"], row["experimental_total_symmetric"])
                    self.assertGreater(row["digitization_margin"], 0)
        self.assertEqual(statuses.count("digitized_total"), 31)
        self.assertEqual(statuses.count("assumed"), 15)
        k0s = json.loads((self.root / "uncertainties/dN_dy_K0S_vs_mult.json").read_text())
        self.assertTrue(all(row["status"] == "assumed" for row in k0s["rows"]))
        # A visible K0S mean-pT box gives ~0.00748 GeV, not the former 3% (~0.0235).
        pt = json.loads((self.root / "uncertainties/mean_pT_K0S_vs_mult.json").read_text())
        self.assertAlmostEqual(pt["rows"][0]["experimental_total_symmetric"], .00748, places=5)

    def test_changed_pixel_endpoint_fails_error_reconstruction(self):
        path = self.root / "uncertainties/mean_pT_Omega_tot_vs_mult.json"
        source = json.loads(path.read_text())
        source["rows"][0]["upper_edge_pixel"] -= 10
        path.write_text(json.dumps(source))
        self.assertTrue(any("pixel evidence" in error for error in audit.audit(self.root)["errors"]))

    def test_digitization_reproduces_outputs_without_changing_points(self):
        with mock.patch("builtins.print"):
            self.assertEqual(extraction.extract(self.root, check=True), [])
        path = self.root / "derived/mean_pT_K0S_vs_mult.dat"
        original = np.loadtxt(path)
        changed = original.copy()
        changed[0, 2] *= 2
        np.savetxt(path, changed)
        self.assertTrue(any("exported error" in error for error in audit.audit(self.root)["errors"]))
        with mock.patch("builtins.print"):
            extraction.extract(self.root)
        np.testing.assert_array_equal(np.loadtxt(path)[:, :2], original[:, :2])
        np.testing.assert_allclose(np.loadtxt(path)[:, 2], original[:, 2], rtol=1e-9)

    def test_negative_error_or_bad_midpoint_fails_before_error_squaring(self):
        path = self.root / "centrality/v2_2_charged_ALICE.dat"
        original = np.loadtxt(path)
        for column, value in ((4, -.01), (2, 20.), (0, float("nan"))):
            values = original.copy()
            values[0, column] = value
            np.savetxt(path, values)
            with self.subTest(column=column), self.assertRaises(ValueError):
                I.read_cent_file(path)

    def test_assumed_errors_warn_only_when_selected(self):
        spec = dict(name="test", species="charged", obs="dNch_deta", kind="yield",
                    sys_floor=.05, uncertainty_status="assumed", files=["data.dat"])
        path = self.root / "data.dat"
        path.write_text("0 5 2.5 100 10\n")
        with mock.patch.object(I, "DATA_SPEC", [spec]):
            with self.assertWarnsRegex(UserWarning, "assumed uncertainties"):
                assembled = I.assemble(np.array([[100.]]), np.array([[1.]]),
                                       ["charged__0_5__dNch_deta"], str(self.root))
            self.assertAlmostEqual(assembled[3][0], np.hypot(10, 5))
            with mock.patch.object(I.warnings, "warn") as warning:
                with self.assertRaisesRegex(ValueError, "No observables matched"):
                    I.assemble(np.array([[100.]]), np.array([[1.]]),
                               ["charged__0_5__dNch_deta"], str(self.root), drop_specs=["test"])
                warning.assert_not_called()

    def test_invalid_multiplicity_is_not_silently_skipped(self):
        path = self.root / "bad.dat"
        spec = dict(name="test", species="K0S", obs="mean_pT", files=["bad.dat"])
        key, mkey = ("K0S", "mean_pT"), ("charged", "dNch_deta")
        val = {key: np.array([[.8, .9]]), mkey: np.array([[10., 100.]])}
        edges = {key: np.array([0., 5.]), mkey: np.array([0., 5.])}
        for data in ("nan .8 .03\n", "20 .8 -.03\n", "20 .8 .03\n20 .9 .03\n"):
            path.write_text(data)
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, "strictly increasing"):
                I._assemble_vs_mult(spec, val, val, edges, edges, .05, str(self.root), [], [], [], [], [])


if __name__ == "__main__":
    unittest.main()
