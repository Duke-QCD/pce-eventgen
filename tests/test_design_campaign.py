"""Integration between the standalone inference design tools and event generation."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "models"))
sys.path.insert(0, str(ROOT / "inference"))

import run_designs as campaign
try:
    import design_tools as design
except ImportError:
    design = None


@unittest.skipIf(design is None, "NumPy and SciPy are required for design generation")
class DesignCampaignTests(unittest.TestCase):
    def test_legacy_campaign_path_points_to_the_canonical_inference_table(self):
        legacy = ROOT / "config/initialization/design_params_combined500.dat"
        canonical = ROOT / "inference/model/design_params_combined500.dat"
        self.assertTrue(legacy.is_symlink())
        self.assertEqual(legacy.resolve(), canonical)
        self.assertEqual(len(campaign.parse_design_params(legacy)), 500)

    def test_generated_design_renders_all_campaign_systems(self):
        with tempfile.TemporaryDirectory(prefix="design campaign ") as directory:
            path = Path(directory) / "design.dat"
            design.write_designs([(path, design.generate_design(8, candidates=1), {})])
            rows = campaign.parse_design_params(path)
            self.assertEqual(list(rows[0]), design.PARAM_NAMES)
            base = campaign.load_base_parameters(ROOT / "config/initialization/base_parameters.dat")
            for system, settings in campaign.SYSTEM_CONFIG.items():
                with self.subTest(system=system), patch.dict(os.environ, {
                    "HIC_O16_NUCLEUS_TABLE": str(Path(directory) / "nucleus.hdf5"),
                }):
                    rendered = campaign.render_parameters_dat(base, rows[0], system)
                    params = dict(line.split("=", 1) for line in rendered.splitlines()
                                  if "=" in line and not line.startswith("#"))
                    params = {key.strip(): value.strip() for key, value in params.items()}
                    self.assertEqual(float(params["normalization"]), rows[0][settings["norm_key"]])
                    self.assertEqual(float(params["Initial_strange_quark_fugacity"]), rows[0]["gamma_s_0"])


if __name__ == "__main__":
    unittest.main()
