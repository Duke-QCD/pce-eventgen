"""Standalone EOS numerics and binary layout, using a synthetic HRG only."""

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "models/music-hydro/EOS/PCE/eos-strange.py"
SPEC = importlib.util.spec_from_file_location("standalone_eos", SCRIPT)
eos = importlib.util.module_from_spec(SPEC)
# Importing must not require frzout or start a production-sized calculation.
with patch.dict(sys.modules, {"frzout": None}):
    SPEC.loader.exec_module(eos)


class SyntheticHRG:
    """Deterministic nonphysical stand-in, shared with the pre-cleanup comparison."""

    def __init__(self, T, **kwargs):
        self.T = T
        self.coefficient = 1.5 * (0.5 + kwargs["fug_l"] + kwargs["fug_s"])

    def pressure(self):
        return self.coefficient * self.T**4 * np.exp(-0.15 / self.T) / 0.1973269718**3

    def energy_density(self):
        return (3 + 0.15 / self.T) * self.pressure()


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _, args = eos.parse_args(["--output-dir", "unused", "--nsteps", "64"])
        with patch.dict(sys.modules, {"frzout": types.SimpleNamespace(HRG=SyntheticHRG)}):
            cls.tables = eos.generate_tables(args, fugacity_points=3, progress=lambda _: None)

    def test_small_grid_matches_supplied_generator(self):
        # Samples from the original unmodified calculation, with only grid sizes
        # reduced and SyntheticHRG substituted. This checks scientific constants,
        # connection/integration, common q grid, pressure repair and entropy/cs2.
        samples = [
            ((0, 0, 0), [0.0, 0.0, 0.0, 0.15829356980590342, 0.8843168741815751]),
            (
                (0, 2, 20),
                [
                    2.369899668849099,
                    0.9149212977625432,
                    23.394085439968837,
                    0.3051817270695829,
                    0.22292221197363227,
                ],
            ),
            (
                (1, 1, 40),
                [
                    4.739799337698198,
                    29.341171383193085,
                    261.10591400121893,
                    0.5384074023464578,
                    0.31075158860907237,
                ],
            ),
            (
                (2, 0, 63),
                [
                    7.465183956874662,
                    192.88079611845544,
                    1003.4551716508486,
                    0.8029484519969323,
                    0.32180594398990614,
                ],
            ),
            (
                (2, 2, 32),
                [
                    3.7918394701585583,
                    11.4028928353022,
                    133.0340788555949,
                    0.39235019728331494,
                    0.300992216805672,
                ],
            ),
        ]
        q, *quantities = self.tables
        self.assertEqual(q.shape, (64,))
        for (j, k, i), expected in samples:
            actual = [q[i]] + [values[j, k, i] for values in quantities]
            np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-11)

    def test_vectorized_fugacity_derivatives_match_quadratic_pressure_at_fixed_T(self):
        fugacities = np.linspace(0, 1, 5)
        temperatures = np.array([0.15, 0.3, 0.8])
        curves = [lambda T, lam=lam: (1 + lam**2) * T**2 + lam * T for lam in fugacities]
        for index, lam in enumerate(fugacities):
            actual = eos.pressure_fugacity_derivative(curves, fugacities, index, temperatures)
            np.testing.assert_allclose(actual, 2 * lam * temperatures**2 + temperatures)

    def test_binary_record_order_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            eos.write_tables(output, *self.tables)
            self.assertEqual({p.name for p in output.iterdir()}, {"0.dat", "1.dat", "2.dat"})
            q, *quantities = self.tables
            for k in range(3):
                data = np.fromfile(output / f"{k}.dat", dtype="<f8").reshape(64, 3, 5)
                for j in range(3):
                    np.testing.assert_array_equal(data[:, j, 0], q)
                    for column, values in enumerate(quantities, start=1):
                        np.testing.assert_array_equal(data[:, j, column], values[j, k])
            before = (output / "0.dat").read_bytes()
            with self.assertRaises(FileExistsError):
                eos.write_tables(output, *self.tables)
            self.assertEqual((output / "0.dat").read_bytes(), before)

    def test_help_does_not_start_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--help"],
                cwd=directory,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("--output-dir", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_existing_directory_and_missing_custom_HRG_fail_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                eos.main(["--output-dir", directory])
            self.assertEqual(error.exception.code, 2)
            for name, module in (
                ("missing", None),
                ("stock", types.SimpleNamespace(HRG=lambda T, species, res_width: None)),
            ):
                output = Path(directory) / name
                messages = io.StringIO()
                with patch.dict(sys.modules, {"frzout": module}), contextlib.redirect_stderr(
                    messages
                ), self.assertRaises(SystemExit) as error:
                    eos.main(["--output-dir", str(output)])
                self.assertEqual(error.exception.code, 2)
                self.assertIn("Custom frzout.HRG", messages.getvalue())
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
