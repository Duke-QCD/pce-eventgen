#!/usr/bin/env python3
"""Check local workflow syntax and configured script paths without running jobs."""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
# Inspect workflow code only; model source trees have their own build/test systems.
WORKFLOW_DIRS = (
    ".", "models", "models/music-hydro", "models/analysis", "models/cache",
    "models/maintenance",
    "osg", "config", "config/templates", "local", "nersc", "inference",
)


def workflow_files():
    paths = {
        path
        for folder in WORKFLOW_DIRS
        for path in (ROOT / folder).iterdir()
        if (path.is_file() or path.is_symlink()) and not (
            path.name.endswith("~") or path.name.startswith(("#", "."))
        )
    }
    for folder in ("tools", "tests", "osg/tests", "inference/tests"):
        paths.update((ROOT / folder).rglob("*.py"))
    paths.update(ROOT / path for path in (
        "models/iS3D/iS3D_OSCAR.py", "models/iS3D/scripts/iS3D_observables.py",
    ))
    return sorted(paths)


def main():
    failures = []
    python_count = shell_count = script_count = 0
    env = os.environ.copy()
    env.pop("BASH_ENV", None)
    env.pop("ENV", None)

    checked = set()
    for path in workflow_files():
        if not path.is_file():
            failures.append(f"{path.relative_to(ROOT)}: broken workflow link")
            continue
        canonical = path.resolve()
        if canonical in checked:
            continue
        checked.add(canonical)
        with path.open("rb") as source:
            first_line = source.readline(200)
        shebang = first_line.decode("utf-8", errors="replace").strip()
        is_python = path.suffix == ".py" or (
            shebang.startswith("#!") and "python" in shebang
        )
        if is_python:
            python_count += 1
            try:
                compile(path.read_bytes(), str(path), "exec")
            except (SyntaxError, ValueError) as error:
                failures.append(f"{path.relative_to(ROOT)}: {error}")
        elif path.suffix == ".sh" or re.match(
            r"^#!(?:/\S*/|/usr/bin/env\s+)(?:ba)?sh(?:\s|$)", shebang
        ):
            shell_count += 1
            shell = "bash" if path.suffix == ".sh" or "bash" in shebang else "sh"
            executable = shutil.which(shell)
            if executable is None:
                failures.append(f"{path.relative_to(ROOT)}: {shell} is unavailable")
                continue
            result = subprocess.run(
                [executable, "-n", str(path)], capture_output=True, text=True, env=env,
            )
            if result.returncode:
                failures.append(f"{path.relative_to(ROOT)}: {result.stderr.strip()}")

    # Checkout-relative paths named by the profile templates must exist in this checkout.
    for config in sorted((ROOT / "config/templates").iterdir()):
        for relative in re.findall(r"(?:\$HIC_CODE_HOME|\$\{HIC_CODE_HOME\}|\{\{CODE_HOME\}\})/([\w./-]+)",
                                   config.read_text()):
            script_count += 1
            if not (ROOT / relative).exists():
                failures.append(f"{config.relative_to(ROOT)}: points to missing {relative}")

    print(
        f"Checked {python_count} Python files, {shell_count} shell files, "
        f"and {script_count} checkout paths named by profile files."
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print("Workflow checks passed (syntax and paths only; no jobs executed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
