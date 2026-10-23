"""Portable, small-grid regression tests. No campaign products or external workspace needed.

Run: python -m unittest discover -s inference/tests -v
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
for name in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[name] = "1"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import inference as I
import emulator_validation as V
import loo_closure as L
import loo_showcase as S
import robustness_scan as R
import run_oo_suite as O
import make_flow_plots as F
import regen_corners as C
import plot_robustness_forest as B
import make_demo_inputs as D
import plot_loo_closure as P
import plot_showcase_overlay as Q
import plot_style as T

DEFAULT_DESIGN = str(ROOT.parent / "config/initialization/design_params_combined500.dat")


def make_grid(directory):
    """Low-rank synthetic model on real observable bins, with gapped/unsorted design IDs."""
    full, active, _, _ = I.load_design(DEFAULT_DESIGN)
    indices = np.array([9, 2, 7, 0, 5, 3, 6, 1])
    pairs = {(spec["species"], spec["obs"]) for spec in I.DATA_SPEC_AUAU}
    pairs.add(("charged", "dNch_deta"))
    labels, base = [], []
    for species, observable in sorted(pairs):
        for low in range(80):
            labels.append(f"{species}__{low}_{low+1}__{observable}")
            scale = 0.005 if observable.startswith("c") else (0.5 if observable == "mean_pT" else 100.)
            base.append(scale * (1 - low / 200.))
    latent = np.sin(full[indices, active[0]]) * 0.1 + 1
    values = latent[:, None] * np.array(base)[None, :]
    path = directory / "matrix.npz"
    arrays = dict(Y=values, Y_err=values * 0.01, feature_labels=np.array(labels),
                  design_index=indices, design_name=np.array([f"design_{n:03d}" for n in indices]))
    np.savez(path, **arrays)
    path.with_suffix(".meta.json").write_text(json.dumps(dict(system="AuAu200", analysis_profile="baseline")))
    return path, arrays


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pce-inference-tests-")
        cls.work = Path(cls.tmp.name)
        I.use_system("AuAu200")
        cls.matrix, cls.arrays = make_grid(cls.work)
        cls.fits = {}
        # Exercise real PCA/GP training and sampling through each public launcher.
        # Defaults are the primary analysis: primary exclusions, no feed-down, all parameters.
        for script, mode, extra in (("inference.py", "system", ["--parameter-mode", "system"]),
                                    ("inference.py", "all", []), ("run_flat_eta.py", "flat", [])):
            output = cls.work / mode
            command = [sys.executable, str(ROOT / script), "--matrix", str(cls.matrix), *extra,
                       "--out-dir", str(output), "--nburn", "2", "--nsteps", "8",
                       "--nwalkers", "4", "--nsplits", "2", "--no-plots"]
            run = subprocess.run(command, cwd=cls.work, capture_output=True, text=True, timeout=120)
            if run.returncode:
                raise AssertionError(f"{script}: {run.stdout}\n{run.stderr}")
            cls.fits[mode] = output

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        I.use_system("AuAu200")
        I.DESIGN_DAT = DEFAULT_DESIGN

    def prepared(self):
        return I.prepare_inputs(str(self.matrix), drop_specs=I.PRIMARY_DROP, feeddown="")

    def test_all_entrypoints_help_outside_repository(self):
        def check(script):
            run = subprocess.run([sys.executable, str(script), "--help"], cwd=self.work,
                                 capture_output=True, text=True, timeout=60)
            return script.name, run.returncode, run.stderr
        with ThreadPoolExecutor(max_workers=3) as pool:
            for script, status, stderr in pool.map(check, sorted(ROOT.glob("*.py"))):
                with self.subTest(script=script):
                    self.assertEqual(status, 0, stderr)

    def test_launchers_save_correct_slice_and_no_figures(self):
        for mode, dimension, fixed in (("system", 8, I.FIXED_PARAMS_AUAU),
                                       ("all", 14, {}), ("flat", 12, {"eta_low": 0., "eta_high": 0.})):
            with self.subTest(mode=mode):
                output = self.fits[mode]
                post = I.restore_posterior(output / "auau_posterior.npz")
                self.assertEqual(post["samples"].shape, (32, dimension))
                self.assertEqual(I.FIXED_PARAMS, fixed)
                self.assertFalse(list(output.glob("*.pdf")))
                config = json.loads(str(post["config_json"]))
                self.assertEqual(config["usable_design_indices"], self.arrays["design_index"].tolist())
                self.assertEqual(config["nburn"], 2)
                diagnostics = json.loads((output / "auau_diagnostics.json").read_text())
                self.assertFalse(diagnostics["converged"])  # Tiny chains cannot pass production ESS.

    def test_noncontiguous_design_alignment(self):
        prepared = self.prepared()
        full, active, _, _ = I.load_design(DEFAULT_DESIGN)
        np.testing.assert_array_equal(prepared.X, full[self.arrays["design_index"]][:, active])

    def test_student_demo_inputs_use_normal_pipeline_contract(self):
        output = self.work / "student-demo"
        with contextlib.redirect_stdout(io.StringIO()):
            D.make_inputs(output)
        inputs = I.prepare_inputs(output / "model_matrix_demo.npz", output / "design.dat",
                                  drop_specs=I.PRIMARY_DROP, feeddown="")
        self.assertEqual(inputs.X.shape, (16, 14))
        self.assertEqual(inputs.Y.shape, (16, 70))
        self.assertTrue(inputs.valid.all())
        meta = json.loads((output / "model_matrix_demo.meta.json").read_text())
        self.assertTrue(meta["synthetic"])
        self.assertEqual(meta["ndesign"], 16)

    def test_student_demo_refuses_overwrite_and_invalid_size(self):
        existing = self.work / "existing-demo"
        existing.mkdir()
        marker = existing / "keep.txt"
        marker.write_text("original inputs")
        with self.assertRaises(FileExistsError):
            D.make_inputs(existing)
        self.assertEqual(marker.read_text(), "original inputs")
        missing = self.work / "invalid-demo"
        for size in (0, 7, 501):
            with self.subTest(size=size), self.assertRaises(ValueError):
                D.make_inputs(missing, size)
        self.assertFalse(missing.exists())

    def test_student_demo_is_reproducible(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first = D.make_inputs(self.work / "demo-repeat-a", 8)
            second = D.make_inputs(self.work / "demo-repeat-b", 8)
        for name in ("Y", "Y_err", "feature_labels", "design_index", "design_name"):
            np.testing.assert_array_equal(I.read_npz(first / "model_matrix_demo.npz")[name],
                                          I.read_npz(second / "model_matrix_demo.npz")[name])
        self.assertEqual((first / "design.dat").read_bytes(), (second / "design.dat").read_bytes())

    def test_preflight_from_other_directory(self):
        run = subprocess.run([sys.executable, str(ROOT / "check_inputs.py"), "--matrix", str(self.matrix)],
                             cwd=self.work, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("8 usable designs", run.stdout)

    def test_reject_ambiguous_or_invalid_matrix(self):
        changes = (
            dict(design_index=np.array([0.] * 8)),
            dict(design_index=np.array([0, 0, 2, 3, 4, 5, 6, 7])),
            dict(feature_labels=np.repeat("duplicate", len(self.arrays["feature_labels"]))),
            dict(Y_err=np.zeros((1, 2))),
            dict(Y_err=-np.ones_like(self.arrays["Y_err"])),
            dict(Y=np.full_like(self.arrays["Y"], np.inf)),
        )
        with tempfile.TemporaryDirectory(dir=self.work) as directory:
            path = Path(directory) / "bad.npz"
            path.with_suffix(".meta.json").write_text('{"system":"AuAu200"}')
            for change in changes:
                with self.subTest(fields=list(change)):
                    np.savez(path, **(self.arrays | change))
                    with self.assertRaises(ValueError):
                        I.load_matrix(path)

    def test_reject_wrong_system_and_out_of_bounds_ids(self):
        I.use_system("OO5360")
        with self.assertRaisesRegex(ValueError, "active system"):
            I.load_matrix(self.matrix)
        with self.assertRaisesRegex(ValueError, "out of bounds"):
            I.build_X(np.zeros((2, 17)), np.array([5]), [0])

    def test_missing_feeddown_fails_before_output_creation(self):
        output = self.work / "should-not-exist"
        with self.assertRaises(FileNotFoundError):
            I.main(matrix_path=str(self.matrix), out_dir=str(output), feeddown=str(self.work / "absent.npz"))
        self.assertFalse(output.exists())

    def test_default_selections_are_the_primary_analysis(self):
        parser = argparse.ArgumentParser()
        I.add_input_arguments(parser)
        for argv, drop, feeddown in (([], I.PRIMARY_DROP, ""),
                                     (["--drop-specs", ""], (), ""),
                                     (["--feeddown"], I.PRIMARY_DROP, None),
                                     (["--feeddown", "inventory.npz"], I.PRIMARY_DROP, "inventory.npz")):
            with self.subTest(argv=argv):
                args = parser.parse_args(argv)
                self.assertEqual(I.drop_selection(args), drop)
                self.assertEqual(I.feeddown_selection(args), feeddown)
        I.use_system("OO5360")
        self.assertEqual(I.drop_selection(parser.parse_args([])), ("mean_pT_K0S_vs_mult",))

    def test_empty_selection_has_clear_error(self):
        with self.assertRaisesRegex(ValueError, "No observables matched"):
            I.prepare_inputs(str(self.matrix), drop_specs=[s["name"] for s in I.DATA_SPEC], feeddown="")

    def test_predictive_replay_restores_14d_and_flat(self):
        for mode in ("all", "flat"):
            post, drop, feeddown = I.posterior_inputs(self.fits[mode])
            self.assertEqual(set(drop), set(I.PRIMARY_DROP))
            self.assertEqual(feeddown, "")
            self.assertEqual(len(I.FIXED_PARAMS), 0 if mode == "all" else 2)
            self.assertTrue(post["samples"].size)

    def test_predictive_replay_rejects_changed_input(self):
        path = self.work / "other-matrix.npz"
        np.savez(path, **(self.arrays | dict(Y=self.arrays["Y"] * 2)))
        path.with_suffix(".meta.json").write_text('{"system":"AuAu200"}')
        with self.assertRaisesRegex(ValueError, "differ"):
            I.posterior_inputs(self.fits["all"], matrix=str(path))

    def test_predictive_replay_accepts_relocated_identical_inputs(self):
        relocated = self.work / "relocated.npz"
        shutil.copyfile(self.matrix, relocated)
        shutil.copyfile(self.matrix.with_suffix(".meta.json"), relocated.with_suffix(".meta.json"))
        post, _, _ = I.posterior_inputs(self.fits["all"], matrix=str(relocated))
        self.assertEqual(post["samples"].shape[1], 14)

    def test_predictive_figures_for_14d_and_flat_slices(self):
        for mode in ("all", "flat"):
            with self.subTest(mode=mode), contextlib.redirect_stdout(io.StringIO()):
                F.main(str(self.fits[mode]), None, npost=12, nprior=12)
                figures = list(self.fits[mode].glob("auau_*Summary_*.pdf"))
                self.assertEqual(len(figures), 4)
                self.assertTrue(all(path.read_bytes().startswith(b"%PDF") for path in figures))

    def test_corner_regeneration_uses_saved_14d_names(self):
        labels_before, rc_before = dict(I.PARAM_LABELS), dict(I.POSTERIOR_RC)
        with mock.patch.object(I, "make_corner_plots") as plot:
            with contextlib.redirect_stdout(io.StringIO()):
                C.main(str(self.fits["all"]), 1.)
            self.assertEqual(len(plot.call_args.args[1]), 14)
            self.assertEqual(plot.call_args.kwargs["param_labels"]["norm200"], C.PAPER_LABELS["norm200"])
        self.assertEqual(I.PARAM_LABELS, labels_before)
        self.assertEqual(I.POSTERIOR_RC, rc_before)

    def test_default_pipeline_reuses_emulator_and_preserves_samples(self):
        output = self.work / "dissertation-default"
        with mock.patch.object(I, "PCAGPEmulator", wraps=I.PCAGPEmulator) as train:
            with mock.patch.object(I, "make_corner_plots") as corners:
                with mock.patch.object(C, "plot_from_samples", wraps=C.plot_from_samples) as renderer:
                    with contextlib.redirect_stdout(io.StringIO()):
                        I.main(matrix_path=str(self.matrix), out_dir=str(output), feeddown="",
                               drop_specs=I.PRIMARY_DROP, nsteps=8, nburn=2, nwalkers=4,
                               n_splits=2, plot_npost=12, plot_nprior=12)
                self.assertIs(renderer.call_args.kwargs["engine"], I)
                self.assertEqual(renderer.call_args.kwargs["font_scale"], 2.6)
                self.assertEqual(corners.call_args.kwargs["rc"]["font.size"], I.POSTERIOR_RC["font.size"] * 2.6)
        self.assertEqual(train.call_count, 3)  # Two closure folds plus full fit; plots reuse that fit.
        for kind in ("Posterior", "Prior"):
            for group in ("yields", "ptflow"):
                self.assertTrue((output / f"auau_{kind}Summary_{group}.pdf").read_bytes().startswith(b"%PDF"))
        self.assertFalse((output / "auau_ObservablePosteriorSummary.pdf").exists())
        self.assertTrue((output / "auau_MCMCSamples.pdf").is_file())
        post = I.read_npz(output / "auau_posterior.npz")
        baseline = I.read_npz(self.fits["system"] / "auau_posterior.npz")
        np.testing.assert_array_equal(post["samples"], baseline["samples"])
        config = json.loads(str(post["config_json"]))
        self.assertEqual(config["plotting"]["npost"], 12)

    def test_dissertation_plot_context_is_explicit_and_scoped(self):
        inputs = SimpleNamespace(pnames=["theta"], ranges=np.array([[0., 1.]]),
                                 labels=["dNch_deta__0_5", "c2_charged__0_5"],
                                 measured=np.array([100., .01]), sigmas=np.array([5., .001]))
        engine = SimpleNamespace(SYSTEM="OO5360", OBS_LABELS={"dNch_deta": "custom"},
                                 resolve_free=lambda names, ranges: ([0], names, ranges, np.array([0.])),
                                 expand_free=I.expand_free)
        emu = mock.Mock()
        emu.predict.side_effect = lambda X: np.tile([100., .01], (len(X), 1))
        font_before = F.plt.rcParams["font.size"]
        with mock.patch.object(F, "summary_grid") as grid:
            F.plot_from_fit(emu, np.array([[.25], [.5], [.75]]), inputs, str(self.work / "oo"),
                            engine=engine, npost=2, nprior=3)
        self.assertEqual(grid.call_count, 4)
        for call in grid.call_args_list:
            self.assertEqual(call.kwargs["system"], "OO5360")
            self.assertEqual(call.kwargs["obs_labels"], engine.OBS_LABELS)
            np.testing.assert_array_equal(call.args[0][:, 1], .1)  # Display transform only.
        self.assertEqual(F.plt.rcParams["font.size"], font_before)
        self.assertEqual(I.SYSTEM, "AuAu200")

    def test_yield_only_publication_plots_skip_absent_group(self):
        inputs = SimpleNamespace(pnames=["theta"], ranges=np.array([[0., 1.]]),
                                 labels=["dNch_deta__0_5"], measured=np.array([100.]), sigmas=np.array([5.]))
        engine = SimpleNamespace(SYSTEM="AuAu200", OBS_LABELS=I.OBS_LABELS,
                                 resolve_free=lambda names, ranges: ([0], names, ranges, np.array([0.])),
                                 expand_free=I.expand_free)
        emu = mock.Mock()
        emu.predict.side_effect = lambda X: np.full((len(X), 1), 100.)
        with contextlib.redirect_stdout(io.StringIO()):
            paths = F.plot_from_fit(emu, np.array([[.25], [.5], [.75]]), inputs,
                                    str(self.work / "yield-only"), engine=engine, npost=2, nprior=3)
        self.assertEqual(len(paths), 2)
        self.assertTrue(all(Path(path).is_file() and path.endswith("yields.pdf") for path in paths))

    def test_flat_corner_labels_are_local(self):
        post = I.restore_posterior(self.fits["flat"] / "auau_posterior.npz")
        labels_before, rc_before = dict(I.PARAM_LABELS), dict(I.POSTERIOR_RC)
        with mock.patch.object(I, "make_corner_plots") as plot:
            C.plot_from_samples(post["samples"], post["param_names"].astype(str).tolist(), post["ranges"],
                                str(self.work / "flat"), font_scale=1.4)
        self.assertEqual(plot.call_args.kwargs["param_labels"]["eta_min"], r"$\eta/s$")
        self.assertEqual(I.PARAM_LABELS, labels_before)
        self.assertEqual(I.POSTERIOR_RC, rc_before)

    def test_invalid_plot_options_fail_before_creating_outputs(self):
        output = self.work / "invalid-plots"
        for settings in (dict(plot_npost=0), dict(plot_nprior=-1), dict(corner_font_scale=np.nan)):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                I.main(matrix_path=str(self.matrix), out_dir=str(output), feeddown="", **settings)
        self.assertFalse(output.exists())

    def test_cache_requires_settings_and_contents(self):
        config = I.input_config(str(self.matrix), drop_specs=I.PRIMARY_DROP, feeddown="")
        result = {"config_json": np.array(I.config_json(config))}
        I.require_cache_config(result, config)
        with self.assertRaisesRegex(ValueError, "provenance"):
            I.require_cache_config(result, config | dict(nsplits=2))
        with self.assertRaises(ValueError):
            I.require_cache_config({}, config)
        self.assertNotEqual(I.file_identity(self.matrix)["sha256"], I.file_identity(DEFAULT_DESIGN)["sha256"])

    def test_recovery_scope_ignores_only_held_out_row(self):
        def result(**config):
            return dict(config_json=np.array(I.config_json(config)))
        self.assertEqual(I.result_scope(result(row=0, design_index=9, nburn=20)),
                         I.result_scope(result(row=1, design_index=2, nburn=20)))
        self.assertNotEqual(I.result_scope(result(row=0, nburn=20)), I.result_scope(result(row=0, nburn=40)))

    def test_invalid_sampler_and_fold_counts_fail_early(self):
        for arguments in ((4, -1, 4, 2), (4, 0, 3, 2), (4, 0, 5, 2), (3, 0, 4, 2), (4, 0, 4, 1)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                I.validate_run_sizes(*arguments)

    def test_validation_cache_rejects_changed_folds(self):
        I.MATRIX_NPZ = str(self.matrix)
        output = self.work / "validation"
        figures = {name: mock.Mock() for name in ("fig_parity_z", "fig_holdout", "fig_pca", "fig_metrics")}
        with mock.patch.multiple(V, **figures), contextlib.redirect_stdout(io.StringIO()):
            V.main(I.PRIMARY_DROP, str(output), 2, False, -1)
            with mock.patch.object(V, "run_kfold", side_effect=AssertionError("cache was not reused")):
                V.main(I.PRIMARY_DROP, str(output), 2, False, -1)
            with self.assertRaisesRegex(ValueError, "provenance"):
                V.main(I.PRIMARY_DROP, str(output), 3, False, -1)

    def test_loo_and_showcase_use_shared_inflation_and_design_ids(self):
        directory = self.work / "loo-direct"
        directory.mkdir(exist_ok=True)
        with contextlib.redirect_stdout(io.StringIO()):
            L.setup(2, str(self.matrix), nwalkers=4)
            status, _, _ = L.run_one(0, 8, 2, str(directory))
            self.assertEqual(status, "ok")
            self.assertEqual(L.run_one(0, 8, 2, str(directory))[0], "cached")
            with self.assertRaisesRegex(ValueError, "provenance"):
                L.run_one(0, 10, 2, str(directory))
            result = I.read_npz(directory / "loo_0000.npz")
            self.assertEqual(int(result["design_index"]), 9)
            showcase = I.read_npz(S.run_showcase(0, str(directory), 8, 2))
            self.assertEqual(showcase["samp"].shape, (32, 14))
            self.assertEqual(int(showcase["design_index"]), 9)

    def test_loo_spawn_worker_runs_with_explicit_inputs(self):
        output = self.work / "loo-spawn"
        command = [sys.executable, str(ROOT / "loo_closure.py"), "--matrix", str(self.matrix),
                   "--K", "1", "--workers", "1", "--nsteps", "8", "--nburn", "2",
                   "--nwalkers", "4", "--lam-nsplits", "2", "--out-dir", str(output)]
        run = subprocess.run(command, cwd=self.work, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(len(list(output.glob("loo_*.npz"))), 1)

    def test_loo_smoke_respects_explicit_case_count(self):
        output = self.work / "loo-four-smoke"
        command = [sys.executable, str(ROOT / "loo_closure.py"), "--matrix", str(self.matrix),
                   "--smoke", "--K", "4", "--workers", "2", "--nsteps", "8", "--nburn", "2",
                   "--nwalkers", "4", "--lam-nsplits", "2", "--out-dir", str(output)]
        run = subprocess.run(command, cwd=self.work, capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(len(list(output.glob("loo_*.npz"))), 4)
        self.assertIn("K=4", run.stdout)

    def test_tiny_coverage_intervals_keep_boundary_uncertainty(self):
        ranks = np.array([[0., .5], [1., .4], [.01, .6]])
        estimate, lower, upper = P.coverage_intervals(ranks, .68)
        np.testing.assert_array_equal(estimate, [0., 1.])
        self.assertAlmostEqual(lower[0], 0.)
        self.assertAlmostEqual(upper[1], 1.)
        self.assertGreater(upper[0], .5)
        self.assertLess(lower[1], .5)
        self.assertAlmostEqual(upper[0], 1 - lower[1])

    def test_coverage_rejects_invalid_rank_arrays(self):
        for ranks in (np.array([]), np.zeros((0, 2)), np.array([[np.nan]]), np.array([[-.1]]), np.array([[1.1]])):
            with self.subTest(ranks=ranks), self.assertRaises(ValueError):
                P.coverage_intervals(ranks, .68)

    def test_small_coverage_plot_displays_entire_probability_range(self):
        ranks = np.array([[0., .5], [1., .4], [.01, .6]])
        try:
            with mock.patch.object(P.plt, "close"), contextlib.redirect_stdout(io.StringIO()):
                P.fig_coverage(["gamma_s_0", "gamma_l_0"], ranks, str(self.work / "coverage-boundaries.pdf"))
                figure = P.plt.gcf()
                axes = figure.axes[0]
            self.assertLessEqual(axes.get_xlim()[0], 0.)
            self.assertGreaterEqual(axes.get_xlim()[1], 1.)
            figure.canvas.draw()
            self.assertFalse(axes.get_window_extent().overlaps(axes.get_legend().get_window_extent()))
            self.assertTrue((self.work / "coverage-boundaries.pdf").read_bytes().startswith(b"%PDF"))
        finally:
            P.plt.close("all")

    def test_scan_style_restores_settings_and_labels_all_parameters(self):
        original_font = P.plt.rcParams["font.size"]
        names = self.prepared().pnames
        ranks = np.random.default_rng(7).uniform(size=(3, len(names)))
        original = ranks.copy()
        try:
            with P.plt.rc_context({"font.size": 9, "font.family": "sans-serif"}):
                with mock.patch.object(P.plt, "close"), contextlib.redirect_stdout(io.StringIO()):
                    P.fig_coverage(names, ranks, str(self.work / "coverage-paper.pdf"))
                self.assertEqual(P.plt.rcParams["font.size"], 9)
                self.assertEqual(P.plt.rcParams["font.family"], ["sans-serif"])
                axes = P.plt.gcf().axes[0]
                labels = [label.get_text() for label in axes.get_yticklabels()]
                self.assertEqual(labels, [{**I.PARAM_LABELS, **T.PAPER_LABELS}[name] for name in names])
                self.assertTrue(all(label.startswith("$") for label in labels))
            np.testing.assert_array_equal(ranks, original)
            with P.plt.rc_context({"font.size": 9}):
                with self.assertRaises(ValueError):
                    with T.dissertation_style():
                        raise ValueError("renderer failed")
                self.assertEqual(P.plt.rcParams["font.size"], 9)
            self.assertEqual(P.plt.rcParams["font.size"], original_font)
        finally:
            P.plt.close("all")

    def test_showcase_style_handles_single_and_three_cases_without_changing_inputs(self):
        names = np.array(Q.REPORT)
        ranges = np.array([[.1, 1.], [.1, 1.], [0., 10.], [0., 10.]])
        rng = np.random.default_rng(7)
        for count in (1, 3):
            directory = self.work / f"showcase-style-{count}"
            directory.mkdir()
            for k in range(count):
                np.savez(directory / f"showcase_{k:04d}.npz", free_names=names,
                         free_ranges=ranges, samp=rng.uniform(ranges[:, 0], ranges[:, 1], size=(96, 4)),
                         theta_star=ranges.mean(axis=1), k=k, design_index=k)
            identities = {path: path.read_bytes() for path in directory.glob("*.npz")}
            try:
                with Q.plt.rc_context({"font.size": 9}):
                    with mock.patch.object(Q.plt, "close"), contextlib.redirect_stdout(io.StringIO()):
                        Q.main(str(directory))
                    self.assertEqual(Q.plt.rcParams["font.size"], 9)
                    figure = Q.plt.gcf()
                    self.assertEqual(len(figure.axes), 4 * count)
                    self.assertIn(T.PAPER_LABELS["tau_l"], figure.axes[2 * count].get_ylabel())
                    figure.canvas.draw()
                    legend = figure.legends[0].get_window_extent()
                    self.assertTrue(all(not axes.get_window_extent().overlaps(legend) for axes in figure.axes))
                for path, data in identities.items():
                    self.assertEqual(path.read_bytes(), data)
            finally:
                Q.plt.close("all")

    def test_oo_suite_dry_run_and_failure_status(self):
        output = self.work / "suite-dry"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(O.main(["--dry-run", "--out-root", str(output)]), 0)
        self.assertFalse(output.exists())
        output = self.work / "suite"
        with mock.patch.object(O.subprocess, "run", side_effect=[subprocess.CompletedProcess([], n) for n in (0, 1, 0)]) as run:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(O.main(["--out-root", str(output)]), 1)
            self.assertEqual(run.call_count, 3)
            self.assertEqual(run.call_args.args[0][0], sys.executable)

    def test_robustness_reports_unexpected_failure(self):
        with mock.patch.object(R.I, "main", side_effect=RuntimeError("fit failed")):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(R.run(True, str(self.work / "failed-scan"), 8, 2), 1)

    def test_robustness_smoke_resume_and_forest(self):
        output = self.work / "robust-smoke"
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = R.run(True, str(output), 8, 2, matrix=str(self.matrix), nburn=2, nwalkers=4)
            self.assertEqual(result, 0)
            self.assertIn("EXPECTED failure", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            summary = (output / "summary.csv").read_text()
            self.assertIn("expected_failure", summary)
            result = R.run(True, str(output), 8, 2, matrix=str(self.matrix), nburn=2, nwalkers=4)
            self.assertEqual(result, 0)
            self.assertIn("resumed", (output / "summary.csv").read_text())
            B.main(str(output / "summary.csv"), str(output / "forest.pdf"))
            self.assertTrue((output / "forest.pdf").read_bytes().startswith(b"%PDF"))
            result = R.run(True, str(output), 10, 2, matrix=str(self.matrix), nburn=2, nwalkers=4)
            self.assertEqual(result, 1)  # Changed step count cannot silently reuse a posterior.


if __name__ == "__main__":
    unittest.main()
