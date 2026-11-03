"""Shear parameter policies and emulator conditioning for both supported systems."""
import contextlib
import io
import os
from pathlib import Path
import sys
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import inference as I


class ShearModeTests(unittest.TestCase):
    def setUp(self):
        self.system, self.fixed = I.SYSTEM, dict(I.FIXED_PARAMS)

    def tearDown(self):
        I.use_system(self.system)
        I.FIXED_PARAMS = self.fixed

    def test_each_system_can_switch_both_ways_without_changing_design(self):
        for system in I.SYSTEMS:
            with self.subTest(system=system):
                I.use_system(system)
                design, active, names, ranges = I.load_design(I.DESIGN_DAT)
                labels = dict(I.PARAM_LABELS)
                I.configure_parameters(shear_mode="flat")
                positions, free, _, template = I.resolve_free(names, ranges)
                self.assertEqual(len(free), 12)
                self.assertIn("eta_min", free)
                self.assertNotIn("eta_low", free)
                self.assertNotIn("eta_high", free)
                expanded = I.expand_free(np.ones((2, len(free))), positions, template)
                np.testing.assert_array_equal(expanded[:, [names.index("eta_low"), names.index("eta_high")]], 0.)
                self.assertEqual(I.selected_shear_mode(), "flat")
                # Training still sees both slopes' original variation in its full 14-D design.
                after, after_active, _, _ = I.load_design(I.DESIGN_DAT)
                np.testing.assert_array_equal(design, after)
                self.assertEqual(active, after_active)
                I.configure_parameters(shear_mode="temperature-dependent")
                self.assertEqual(len(I.resolve_free(names, ranges)[1]), 14)
                self.assertEqual(I.FIXED_PARAMS, {})
                self.assertEqual(I.selected_shear_mode(), "temperature-dependent")
                self.assertEqual(I.PARAM_LABELS, labels)

    def test_flat_policy_applies_to_joint_union(self):
        names = [p for p, _, _ in I.PARAMS_ALL if p not in {"norm2760", "norm5020"}]
        ranges = np.array([[lo, hi] for p, lo, hi in I.PARAMS_ALL if p in names])
        I.FIXED_PARAMS = I.shear_fixed_params("flat")
        _, free, _, _ = I.resolve_free(names, ranges)
        self.assertEqual(len(free), 13)
        self.assertTrue({"norm200", "norm5360", "eta_min"}.issubset(free))
        I.FIXED_PARAMS = I.shear_fixed_params("temperature-dependent")
        self.assertEqual(len(I.resolve_free(names, ranges)[1]), 15)

    def test_legacy_alias_and_system_slice_have_explicit_precedence(self):
        for system in I.SYSTEMS:
            with self.subTest(system=system):
                legacy = I.parameter_fixed_params("flat-eta", system=system)
                self.assertEqual(legacy, I.parameter_fixed_params("all", "flat", system=system))
                expected = dict(I.SYSTEMS[system]["fixed"], eta_low=0., eta_high=0.)
                self.assertEqual(I.parameter_fixed_params("system", "flat", system=system), expected)
        with self.assertRaisesRegex(ValueError, "conflicts"):
            I.parameter_fixed_params("flat-eta", "temperature-dependent")
        with self.assertRaises(ValueError):
            I.shear_fixed_params("typo")

    def test_flat_label_reaches_trace_plot(self):
        flat = I.shear_fixed_params("flat")
        self.assertEqual(I.param_labels(flat)["eta_min"], r"$\eta/s$")
        self.assertEqual(I.param_labels({})["eta_min"], I.PARAM_LABELS["eta_min"])
        I.FIXED_PARAMS = flat
        with mock.patch.object(I.plt.Figure, "savefig"), mock.patch.object(I.plt, "close") as close:
            I.trace_plot(np.zeros((4, 4, 2)), ["eta_min", "w"], "unused.pdf", 1)
        figure = close.call_args.args[0]
        self.assertEqual(figure.axes[0].get_ylabel(), r"$\eta/s$")
        I.plt.close(figure)

    def test_flat_launcher_rejects_non_flat_selections_before_fitting(self):
        for argv, accepted in [([], True), (["--parameter-mode", "system", "--shear-mode", "flat"], True),
                               (["--parameter-mode", "all"], False), (["--parameter-mode", "system"], False)]:
            stderr = io.StringIO()
            with self.subTest(argv=argv), mock.patch.object(I, "main") as fit, contextlib.redirect_stderr(stderr):
                if accepted:
                    self.assertEqual(I.cli(argv, mode="flat-eta", auau_only=True, require_flat=True), 0)
                    self.assertEqual(I.selected_shear_mode(), "flat")
                else:
                    with self.assertRaises(SystemExit) as caught:
                        I.cli(argv, mode="flat-eta", auau_only=True, require_flat=True)
                    self.assertEqual(caught.exception.code, 1)
                    self.assertIn("requires flat shear", stderr.getvalue())
                self.assertEqual(fit.called, accepted)


if __name__ == "__main__":
    unittest.main()
