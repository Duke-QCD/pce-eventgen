"""Exercise the workflow installer and runtime paths without a SMASH simulation."""

import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SmashInstallationTests(unittest.TestCase):
    def test_mismatched_particle_and_decay_tables_block_installation(self):
        with tempfile.TemporaryDirectory(prefix="smash mismatched tables ") as directory:
            root = Path(directory)
            (root / "local").mkdir()
            shutil.copy(ROOT / "local/install", root / "local/install")
            inputs = root / "models/SMASH/input"
            inputs.mkdir(parents=True)
            (inputs.parent / "CMakeLists.txt").write_text("# fixture\n")
            (inputs / "particles.txt").write_text("#σ 0.800 0.400 + 9000221\nπ 0.138 0 - 111 211\n")
            (inputs / "decaymodes.txt").write_text("σ\n1.0 0 π π\n")
            prefix = root / "environment"
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin/python").symlink_to(sys.executable)
            env = os.environ.copy()
            env.update(CONDA_PREFIX=str(prefix),
                       PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"])
            env.pop("BASH_ENV", None)
            result = subprocess.run(["bash", str(root / "local/install"), "SMASH"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("SMASH tables disagree", result.stderr)
            self.assertFalse((prefix / "bin/smash").exists())
            self.assertFalse((inputs.parent / "build-workflow").exists())

    def test_wrong_git_revision_blocks_installation(self):
        if not shutil.which("git"):
            self.skipTest("Git is required")
        with tempfile.TemporaryDirectory(prefix="smash wrong revision ") as directory:
            root = Path(directory)
            (root / "local").mkdir()
            shutil.copy(ROOT / "local/install", root / "local/install")
            source = root / "models/SMASH"
            source.mkdir(parents=True)
            (source / "README").write_text("wrong source revision\n")
            (source / "CMakeLists.txt").write_text("# fixture\n")
            env = os.environ.copy()
            env.update(GIT_AUTHOR_NAME="Fixture", GIT_AUTHOR_EMAIL="fixture@example.invalid",
                       GIT_COMMITTER_NAME="Fixture", GIT_COMMITTER_EMAIL="fixture@example.invalid")
            for command in (["git", "init", str(source)],
                            ["git", "-C", str(source), "add", "README", "CMakeLists.txt"],
                            ["git", "-C", str(source), "commit", "-m", "Fixture"]):
                subprocess.run(command, env=env, check=True, capture_output=True)
            prefix = root / "environment"
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin/python").symlink_to(sys.executable)
            env.update(CONDA_PREFIX=str(prefix),
                       PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"])
            env.pop("BASH_ENV", None)
            result = subprocess.run(["bash", str(root / "local/install"), "SMASH"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("SMASH source revision must be the pinned commit", result.stderr)
            self.assertFalse((prefix / "bin/smash").exists())

    def test_missing_submodule_is_reported_before_building(self):
        with tempfile.TemporaryDirectory(prefix="smash missing ") as directory:
            root = Path(directory)
            (root / "local").mkdir()
            shutil.copy(ROOT / "local/install", root / "local/install")
            (root / "models/SMASH").mkdir(parents=True)  # uninitialized submodule
            prefix = root / "environment"
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin/python").symlink_to(sys.executable)
            env = os.environ.copy()
            env.update(CONDA_PREFIX=str(prefix),
                       PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"])
            env.pop("BASH_ENV", None)
            result = subprocess.run(["bash", str(root / "local/install"), "SMASH"],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("git submodule update --init models/SMASH", result.stderr)
            # With no model names every model is required, so a partial checkout fails early.
            result = subprocess.run(["bash", str(root / "local/install")],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("missing source tree: models/trento/CMakeLists.txt", result.stderr)

    def test_fresh_build_and_install_after_source_move(self):
        if not shutil.which("cmake") or not shutil.which("cc"):
            self.skipTest("CMake and a C compiler are required")
        with tempfile.TemporaryDirectory(prefix="smash installer ") as directory:
            root = Path(directory)
            source = root / "models/SMASH"
            source.mkdir(parents=True)
            (root / "models/__pycache__").mkdir()
            (root / "models/results").mkdir()
            (root / "local").mkdir()
            shutil.copy(ROOT / "local/install", root / "local/install")
            for name in ("run-events", "run-events-wrapper"):
                (root / "models" / name).write_text("#!/bin/sh\nexit 0\n")
            (root / "models/iS3D").mkdir()
            (root / "models/iS3D/iS3D_OSCAR.py").write_text("# fixture converter\n")
            (root / "config").mkdir()
            (root / "config/config.yaml").write_text("General: {Nevents: 10}\n")
            # The copied build cache must remain untouched and unused.
            old_cache = source / "build/CMakeCache.txt"
            old_cache.parent.mkdir()
            old_cache.write_text("CMAKE_HOME_DIRECTORY:INTERNAL=/old/location/SMASH\n")
            (source / "CMakeLists.txt").write_text("""
cmake_minimum_required(VERSION 3.15)
project(installer_fixture LANGUAGES C)
if(NOT WORKFLOW_FIXTURE)
  message(FATAL_ERROR "SMASH CMake options were not forwarded")
endif()
add_executable(smash main.c)
# Model the bundled Pythia shared library: it lives inside the source tree,
# so CMake's automatic install RPATH does not retain its directory.
add_library(pythia8 SHARED dependency.c)
set_target_properties(pythia8 PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/dependency/lib")
set(Pythia_LIBRARY "${CMAKE_CURRENT_SOURCE_DIR}/dependency/lib/libpythia8.so" CACHE FILEPATH "Fixture dependency")
target_link_libraries(smash PRIVATE pythia8)
install(TARGETS smash RUNTIME DESTINATION bin)
install(FILES input.txt DESTINATION share/smash)
""")
            (source / "main.c").write_text(
                '#include <stdio.h>\nextern const char *dependency_version(void);\n'
                'int main(void) { puts(dependency_version()); return 0; }\n'
            )
            (source / "dependency.c").write_text(
                'const char *dependency_version(void) { return "SMASH-install-fixture"; }\n'
            )
            (source / "input.txt").write_text("fixture data\n")
            prefix = root / "environment with spaces"
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(prefix)],
                           check=True, capture_output=True)
            env = os.environ.copy()
            for key in ("CONDA_PREFIX", "BASH_ENV", "HIC_SMASH_BUILD_DIR", "CC", "CXX",
                        "CFLAGS", "CXXFLAGS", "LDFLAGS"):
                env.pop(key, None)
            env.update(PATH=str(prefix / "bin") + os.pathsep + os.environ["PATH"],
                       HIC_BUILD_JOBS="2")
            # A venv created with a conda Python uses a symlink to the same
            # executable. A surviving CONDA_PREFIX must not redirect installs
            # into the outer environment just because `test -ef` matches.
            outer_conda = root / "outer conda environment"
            (outer_conda / "bin").mkdir(parents=True)
            (outer_conda / "bin/python").symlink_to(sys.executable)
            for selection, inherited_conda in ((["SMASH"], False), (["SMASH"], True)):
                with self.subTest(selection=selection, inherited_conda=inherited_conda):
                    selected_env = dict(env)
                    if inherited_conda:
                        selected_env["CONDA_PREFIX"] = str(outer_conda)
                    result = subprocess.run(
                        ["bash", str(root / "local/install"), *selection,
                         "--", "-DWORKFLOW_FIXTURE=ON"],
                        cwd=root, env=selected_env, capture_output=True, text=True, timeout=60,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertFalse((outer_conda / "bin/smash").exists(),
                                     "The active venv must take precedence over its conda parent")
            self.assertEqual(old_cache.read_text(),
                             "CMAKE_HOME_DIRECTORY:INTERNAL=/old/location/SMASH\n")
            self.assertTrue((source / "build-workflow/CMakeCache.txt").is_file())
            self.assertEqual((prefix / "share/smash/input.txt").read_text(), "fixture data\n")
            installed = subprocess.check_output([str(prefix / "bin/smash"), "--version"], text=True)
            self.assertIn("SMASH-install-fixture", installed)
            self.assertTrue((prefix / "bin/run-events-wrapper").is_file())
            self.assertEqual(
                (prefix / "share/pce-eventgen/models/iS3D/iS3D_OSCAR.py").read_text(),
                "# fixture converter\n",
            )

    def test_profile_template_selects_the_installed_executables(self):
        sys.path.insert(0, str(ROOT / "nersc"))
        self.addCleanup(sys.path.remove, str(ROOT / "nersc"))
        import configure_user
        values = {name: f"/fixture/{name.lower()}" for name in (
            "CODE_HOME", "SOFTWARE_ROOT", "RUNTIME_GCC_MODULE", "RAW_ROOT", "OUTPUT_ROOT",
            "ARCHIVE_ROOT", "ARCHIVE_BASE", "CACHE_STAGING_ROOT", "CACHE_PUBLISHED_ROOT", "DATA_ROOT")}
        text = configure_user.fill("nersc_paths.sh", values, quote=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("HIC_") and k != "BASH_ENV"}
        command = 'eval "$1" && printf "%s\\n" "$HIC_MUSIC_EXEC" "$HIC_IS3D_EXEC" "$HIC_SMASH_EXEC"'
        resolved = subprocess.check_output(["bash", "-c", command, "paths", text],
                                           env=env, text=True).splitlines()
        self.assertEqual(resolved, ["/fixture/software_root/bin/MUSIChydro",
                                    "/fixture/software_root/bin/iS3D",
                                    "/fixture/software_root/bin/smash"])

    def test_runner_defaults_follow_shared_install_and_repository_config(self):
        tree = ast.parse((ROOT / "models/run-events").read_text())
        selected = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "env_path"]
        selected += [node for node in tree.body if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name)
                             and target.id in {"SMASH_EXEC", "SMASH_CONFIG", "IS3D_EXEC", "MUSIC_EXEC"}
                             for target in node.targets)]
        scope = dict(os=os, Path=Path, HIC_CODE_HOME=Path("/checkout"),
                     HIC_SOFTWARE_ROOT=Path("/software"))
        env = os.environ.copy()
        try:
            os.environ.pop("HIC_MUSIC_EXEC", None)
            os.environ.pop("HIC_IS3D_EXEC", None)
            os.environ.pop("HIC_SMASH_EXEC", None)
            os.environ.pop("HIC_SMASH_CONFIG", None)
            exec(compile(ast.Module(body=selected, type_ignores=[]), "run-events", "exec"), scope)
        finally:
            os.environ.clear()
            os.environ.update(env)
        self.assertEqual(scope["MUSIC_EXEC"], Path("/software/bin/MUSIChydro"))
        self.assertEqual(scope["IS3D_EXEC"], Path("/software/bin/iS3D"))
        self.assertEqual(scope["SMASH_EXEC"], Path("/software/bin/smash"))
        self.assertEqual(scope["SMASH_CONFIG"], Path("/checkout/config/config.yaml"))

    def test_nersc_routes_including_smash_preserve_loaded_toolchain(self):
        with tempfile.TemporaryDirectory(prefix="smash nersc ") as directory:
            root = Path(directory)
            (root / "nersc").mkdir()
            (root / "local").mkdir()
            shutil.copy(ROOT / "nersc/install", root / "nersc/install")
            # Only the entrypoint routing runs; this stub never builds or installs.
            delegate = root / "local/install"
            delegate.write_text(
                '#!/bin/sh\nprintf "arg=%s\\n" "$@"\n'
                'printf "flags=%s\\ncompiler=%s\\nbash_env=%s\\n" '
                '"$CXXFLAGS" "$CXX" "${BASH_ENV-unset}"\n'
            )
            delegate.chmod(0o755)
            env = os.environ.copy()
            env.update(NERSC_HOST="test", CONDA_PREFIX=str(root / "existing environment"), CXX="chosen-compiler",
                       CXXFLAGS="chosen-flags", BASH_ENV="/unused/profile")
            for arguments in ([], ["--", "-DFIXTURE=with space"],
                              ["SMASH", "--", "-DFIXTURE=with space"],
                              ["trento", "SMASH", "--", "-DFIXTURE=with space"],
                              ["iS3D", "--", "-DFIXTURE=with space"],
                              ["music-hydro", "iS3D", "--", "-DFIXTURE=with space"],
                              ["music-hydro", "--", "-DFIXTURE=with space"],
                              ["trento", "--", "-DFIXTURE=with space"]):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        ["sh", str(root / "nersc/install"), *arguments],
                        env=env, capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if arguments:
                        self.assertIn("arg=-DFIXTURE=with space\n", result.stdout)
                    self.assertIn("flags=chosen-flags\ncompiler=chosen-compiler\nbash_env=unset\n",
                                  result.stdout)

    def test_nersc_accepts_existing_conda_location_and_still_requires_activation(self):
        with tempfile.TemporaryDirectory(prefix="nersc existing conda ") as directory:
            root = Path(directory)
            for name in ("nersc", "local", "existing environment/bin"):
                (root / name).mkdir(parents=True)
            shutil.copy(ROOT / "nersc/install", root / "nersc/install")
            shutil.copy(ROOT / "local/install", root / "local/install")
            prefix = root / "existing environment"
            (prefix / "bin/python").symlink_to(sys.executable)
            env = os.environ.copy()
            for key in ("CONDA_PREFIX", "VIRTUAL_ENV", "BASH_ENV", "ENV"):
                env.pop(key, None)
            env.update(NERSC_HOST="test", PATH=str(prefix / "bin") + os.pathsep + env["PATH"])
            command = ["sh", str(root / "nersc/install"), "SMASH"]
            result = subprocess.run(command, env=dict(env, CONDA_PREFIX=str(prefix)),
                                    capture_output=True, text=True)
            # Passing the environment check reaches the missing-source check, without a build.
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("missing source tree", result.stderr)
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("must install in an active python virtualenv or conda env", result.stdout)


if __name__ == "__main__":
    unittest.main()
