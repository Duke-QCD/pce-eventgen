"""Check relocated iS3D data discovery and executable selection."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
VARIABLES = ("HIC_IS3D_CONFIG", "HIC_IS3D_PDG_DIR", "HIC_IS3D_TABLE_DIR",
             "HIC_IS3D_DELTAF_DIR", "HIC_IS3D_CHOSEN_PARTICLES",
             "HIC_CHARGE_DICT", "HIC_BARYON_DICT", "HIC_PDG_MASS")


class Is3dPathTests(unittest.TestCase):
    def resolve(self, helper, overrides=None):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("HIC_") and key != "BASH_ENV"}
        env.update(overrides or {})
        result = subprocess.check_output(
            ["bash", "-c", 'source "$1"; env', "paths", str(helper)],
            cwd="/tmp", env=env, text=True,
        )
        return dict(line.split("=", 1) for line in result.splitlines() if "=" in line)

    def test_source_tree_data_and_dictionaries_from_an_event_directory(self):
        resolved = self.resolve(ROOT / "models/iS3D/paths.sh")
        for variable in VARIABLES:
            with self.subTest(variable=variable):
                self.assertTrue(Path(resolved[variable]).exists(), resolved[variable])
        self.assertEqual(resolved["HIC_CHARGE_DICT"],
                         str(ROOT / "models/iS3D/PDG/charge_dict.dat"))
        self.assertEqual(resolved["HIC_PDG_MASS"],
                         str(ROOT / "models/iS3D/PDG/pdg_smash_pce.dat"))

    def test_installed_helper_and_production_overrides(self):
        with tempfile.TemporaryDirectory(prefix="is3d environment with spaces ") as directory:
            data = Path(directory) / "share/iS3D"
            (data / "PDG").mkdir(parents=True)
            helper = data / "paths.sh"
            shutil.copy(ROOT / "models/iS3D/paths.sh", helper)
            for name in ("charge_dict.dat", "baryon_dict.dat"):
                (data / "PDG" / name).write_text("111 0\n")
            resolved = self.resolve(helper)
            self.assertEqual(resolved["HIC_IS3D_CONFIG"], str(data / "iS3D_parameters.dat"))
            self.assertEqual(resolved["HIC_IS3D_DELTAF_DIR"],
                             str(data / "deltaf_coefficients/vh"))
            self.assertEqual(resolved["HIC_CHARGE_DICT"], str(data / "PDG/charge_dict.dat"))
            self.assertEqual(resolved["HIC_PDG_MASS"], str(data / "PDG/pdg_smash_pce.dat"))
            overrides = {name: "/production/" + name for name in VARIABLES}
            resolved = self.resolve(helper, overrides)
            for name, expected in overrides.items():
                self.assertEqual(resolved[name], expected)


if __name__ == "__main__":
    unittest.main()
