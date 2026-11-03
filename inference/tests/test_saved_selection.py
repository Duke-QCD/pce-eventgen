"""Saved optional selections replay with verified inputs, using temporary fixtures."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pce-inference-test-matplotlib")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import inference as I
import emulator_validation as V


class SavedSelectionTests(unittest.TestCase):
    def setUp(self):
        self.previous_system, self.previous_design = I.SYSTEM, I.DESIGN_DAT
        self.tmp = tempfile.TemporaryDirectory(prefix="pce-saved-selection-")
        self.root = Path(self.tmp.name)
        self.data = self.root / "data/centrality"
        self.data.mkdir(parents=True)
        self.matrix = self.root / "matrix.npz"
        self.design = self.root / "design.dat"
        # Replay hashes these files; it does not assemble or fit their contents.
        relatives = ["centrality/measurement.dat", "optional/projection_ratios.json",
                     "optional/yield_proxy_metadata.json", "optional/yield_proxy_covariance.npz",
                     "sources/ALI-SIMUL-480342.pdf", "sources/ALI-SIMUL-480342.png",
                     "derived/dN_dy_K0S_vs_mult.dat", "uncertainties/dN_dy_K0S_vs_mult.json"]
        relatives += [f"optional/proxy_dN_dy_{s}_vs_mult.dat" for s in ("pion", "kaon", "proton")]
        for relative in relatives:
            path = self.data.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic provenance fixture\n")
        self.matrix.write_bytes(b"synthetic matrix identity")
        self.matrix.with_suffix(".meta.json").write_text('{}')
        self.design.write_text("synthetic design identity\n")
        spec = dict(name="dNch_deta", species="charged", obs="dNch_deta", kind="yield",
                    files=["measurement.dat"])
        registry = I.SYSTEMS["OO5360"] | dict(matrix=str(self.matrix), data_dir=str(self.data),
                                             data_spec=[spec], primary_drop=())
        self.patch = mock.patch.dict(I.SYSTEMS, {"OO5360": registry})
        self.patch.start()
        self.addCleanup(self.restore)
        self.optional = self.save("optional", True)
        self.primary = self.save("primary", False)

    def restore(self):
        self.patch.stop()
        I.use_system(self.previous_system)
        I.DESIGN_DAT = self.previous_design
        self.tmp.cleanup()

    def save(self, name, proxies):
        I.use_system("OO5360")
        I.DESIGN_DAT = str(self.design)
        if proxies:
            I.enable_oo_yield_proxies()
        config = I.input_config(drop_specs=(), feeddown="")
        active = [p for p in I.PARAMS_ALL if p[0] not in I.DROP_PARAMS]
        ranges = np.array([p[1:] for p in active])
        folder = self.root / name
        folder.mkdir()
        np.savez(folder / "oo_posterior.npz", samples=np.tile(ranges.mean(1), (4, 1)),
                 param_names=np.array([p[0] for p in active]), ranges=ranges,
                 fixed_names=np.array([], dtype=str), fixed_values=np.array([]),
                 config_json=np.array(I.config_json(config)))
        return folder

    def test_replay_restores_optional_specifications_and_verified_selection(self):
        post, drops, feeddown = I.posterior_inputs(self.optional, "OO5360")
        self.assertEqual(sum(bool(s.get("assumed_proxy")) for s in I.DATA_SPEC), 3)
        self.assertEqual(post["samples"].shape, (4, 14))
        self.assertEqual(drops, ())
        self.assertEqual(feeddown, "")

    def test_replay_still_rejects_changed_proxy_inputs(self):
        path = self.data.parent / "optional/proxy_dN_dy_pion_vs_mult.dat"
        path.write_text("changed inputs\n")
        with self.assertRaisesRegex(ValueError, "Plot inputs differ"):
            I.posterior_inputs(self.optional, "OO5360")

    def test_plot_replay_allows_code_updates_but_cache_reuse_stays_strict(self):
        original = I.file_identity
        changed = {I.ROOT/'inference.py', I.ROOT/'build_oo_yield_proxies.py'}

        def edited_code(path):
            result = original(path)
            if Path(result['path']) in changed:
                result['sha256'] = '0'*64
            return result

        with mock.patch.object(I, 'file_identity', side_effect=edited_code):
            post, _, _ = I.posterior_inputs(self.optional, 'OO5360')
            current = I.input_config(drop_specs=(), feeddown='')
        with self.assertRaisesRegex(ValueError, 'different input/settings provenance'):
            I.require_cache_config(post, current)

    def test_plot_replay_still_checks_covariance_anchor_evidence_and_metadata(self):
        for relative in ('optional/yield_proxy_covariance.npz', 'optional/yield_proxy_metadata.json',
                         'optional/projection_ratios.json', 'derived/dN_dy_K0S_vs_mult.dat',
                         'uncertainties/dN_dy_K0S_vs_mult.json', 'sources/ALI-SIMUL-480342.pdf'):
            path = self.data.parent/relative
            original = path.read_bytes()
            try:
                path.write_bytes(b'changed physical input')
                with self.subTest(input=relative), self.assertRaisesRegex(ValueError, 'Plot inputs differ'):
                    I.posterior_inputs(self.optional, 'OO5360')
            finally:
                path.write_bytes(original)

    def test_primary_replay_clears_previous_optional_selection(self):
        I.posterior_inputs(self.optional, "OO5360")
        I.posterior_inputs(self.primary, "OO5360")
        self.assertFalse(any(s.get("assumed_proxy") for s in I.DATA_SPEC))

    def test_validation_cli_accepts_same_optional_selection_as_fitter(self):
        with mock.patch.object(V, "main") as validate:
            V.cli(["--system", "OO5360", "--oo-yield-proxies", "--drop-specs", "",
                   "--matrix", str(self.matrix), "--design", str(self.design),
                   "--data-dir", str(self.data), "--out-dir", str(self.root / "validation")])
        self.assertTrue(any(s.get("assumed_proxy") for s in I.DATA_SPEC))
        self.assertEqual(tuple(validate.call_args.args[0]), ())
        self.assertEqual(I.MATRIX_NPZ, str(self.matrix))


if __name__ == "__main__":
    unittest.main()
