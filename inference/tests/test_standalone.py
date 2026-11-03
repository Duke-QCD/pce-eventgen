"""Keep public inference files usable without the surrounding repository."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def public_files_only(directory, names):
    """Exclude local environments, study inputs and generated results from a copy."""
    directory = Path(directory)
    ignored = {name for name in names if name in (
        ".venv", ".matplotlib", ".pytest_cache", "__pycache__", "studies",
    ) or name.startswith(("plots", "scan_")) or name.endswith((".log", ".pyc"))}
    if directory == ROOT / "model":
        ignored.update(set(names) - {"README.md", "design_params_combined500.dat"})
    if directory == ROOT / "data":
        ignored.add("OO5360")  # Private study inputs; schema is the public OO5360.md.
    return ignored


class StandaloneTests(unittest.TestCase):
    def test_proxy_review_uses_fonts_supplied_by_the_python_environment(self):
        import numpy as np
        from PIL import Image
        sys.path.insert(0, str(ROOT))
        import build_oo_yield_proxies as proxies
        with tempfile.TemporaryDirectory(prefix="proxy review ") as directory:
            root = Path(directory)
            Image.new("RGB", (2362, 3012), "white").save(root / "source.png")
            evidence = {"source_image": "source.png", "rows": {"kaon": [], "proton": []}}
            rows = [{"multiplicity": 100. + 50*i,
                     **{key: {"value": 1. + i, "error": .1*(1+i)}
                        for key in ("pion", "kaon", "proton")}} for i in range(8)]
            proxies.make_review(root, evidence, {"rows": rows}, {"covariance": np.eye(32)})
            outputs = list((root / "review").glob("*.png"))
            self.assertEqual(len(outputs), 3)
            self.assertTrue(all(path.stat().st_size > 0 for path in outputs))

    def test_documentation_links_stay_inside_the_inference_folder(self):
        documents = [*ROOT.glob("*.md"), *ROOT.glob("data/*.md"), *ROOT.glob("model/*.md")]
        for document in documents:
            for link in re.findall(r"\[[^\]]+\]\(([^)]+)\)", document.read_text()):
                url = urlsplit(link)
                if url.scheme or not url.path:
                    continue
                target = (document.parent / unquote(url.path)).resolve()
                with self.subTest(document=document.name, link=link):
                    self.assertTrue(target.is_relative_to(ROOT), f"External local dependency: {target}")
                    self.assertTrue(target.exists(), f"Missing documentation target: {target}")

    def test_generated_cli_reference_is_current(self):
        result = subprocess.run([sys.executable, str(ROOT / "build_docs.py"), "--check"],
                                cwd=ROOT.parent, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_copied_folder_runs_design_tutorial_preflight_and_fit(self):
        with tempfile.TemporaryDirectory(prefix="standalone inference ") as directory:
            work = Path(directory)
            copied = work / "inference"
            shutil.copytree(ROOT, copied, ignore=public_files_only)
            env = dict(os.environ, PYTHONPATH="", PYTHONNOUSERSITE="1",
                       MPLCONFIGDIR=str(work / "matplotlib"), OPENBLAS_NUM_THREADS="1",
                       OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")

            def run(script, *args):
                result = subprocess.run([sys.executable, str(copied / script), *map(str, args)],
                                        cwd=work, env=env, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            # Use a sibling working directory and an empty PYTHONPATH so imports
            # and bundled inputs cannot fall back to the original source tree.
            run("build_docs.py", "--check")
            run("make_design.py", "--out", work / "new.dat", "--ndesign", 16, "--candidates", 2)
            run("design_subset.py", "--design", work / "new.dat", "--n-select", 4,
                "--out", work / "subset.dat", "--remainder-out", work / "remaining.dat")
            run("inspect_design.py", "--design", work / "new.dat", "--rows", "0-2")
            run("make_demo_inputs.py", "--out-dir", work / "demo")
            matrix, design = work / "demo/model_matrix_demo.npz", work / "demo/design.dat"
            run("check_inputs.py", "--matrix", matrix, "--design", design)
            run("inference.py", "--matrix", matrix, "--design", design, "--out-dir", work / "fit",
                "--nburn", 2, "--nsteps", 8, "--nwalkers", 4, "--nsplits", 2, "--no-plots")
            self.assertTrue((work / "fit/auau_posterior.npz").is_file())
            # The shipped joint configuration must locate its shared table here,
            # even when the external matrices needed for an actual fit are absent.
            code = (
                "import sys; from pathlib import Path; sys.path.insert(0, sys.argv[1]); "
                "import inference as I, joint_inference as J; "
                "root = Path(sys.argv[1]); "
                "assert Path(I.DESIGN_DAT).is_relative_to(root); "
                "assert Path(I.DESIGN_DAT).is_file(); "
                "assert J.load_study(J.DEFAULT_CONFIG).design == Path(I.DESIGN_DAT)"
            )
            result = subprocess.run([sys.executable, "-c", code, str(copied)], cwd=work, env=env,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
