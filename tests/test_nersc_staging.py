"""Exercise setup copies without NERSC access or production-sized datasets."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('nersc_stage', ROOT / 'nersc/stage.py')
stage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stage)


class NerscStagingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='NERSC staging with spaces ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        for name in ('local/install', 'nersc/configure_user.py', 'models/SMASH/CMakeLists.txt'):
            self.write(self.source / name, 'maintained source\n')
        self.output = io.StringIO()
        self.capture = contextlib.redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def data_fixture(self):
        sampler = self.source / 'iS3D'
        eos = self.source / 'EOS'
        for name in stage.IS3D_FILES:
            self.write(sampler / name, 'approved metadata ' + name)
        self.write(sampler / 'tables/quadrature.dat', 'quadrature\n')
        self.write(sampler / 'deltaf_coefficients/vh/other/F.dat', 'other required tables\n')
        self.write(sampler / 'paths.sh', '# helper\n')
        self.write(sampler / 'build/CMakeCache.txt', 'machine-specific build\n')
        self.write(sampler / 'src/cpp/model.cpp', 'not runtime data\n')
        manifests = {}
        for dataset, directory in (
                ('eos', eos), ('is3d', sampler / 'deltaf_coefficients/vh/smash_pce_light_strange')):
            name = '0.dat' if dataset == 'eos' else 'F.dat'
            path = self.write(directory / name, 'approved ' + dataset + '\n')
            manifests[dataset] = self.write(self.root / (dataset + '.json'), json.dumps({
                'files': [{'name': name, 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}))
        patcher = patch.object(stage, 'MANIFESTS', manifests)
        patcher.start()
        self.addCleanup(patcher.stop)
        return sampler, eos

    @unittest.skipUnless(shutil.which('rsync'), 'rsync is required')
    def test_source_preview_and_copy_preserve_edits_and_submodule_without_clutter(self):
        wanted = (
            'README.md', '.gitignore', '.gitmodules', '.env.example', 'config/config.yaml',
            'models/SMASH/src/custom.cc', 'models/SMASH/input/particles.txt',
            'models/music-hydro/EOS/PCE/eos-strange.py',
            'models/music-hydro/EOS/PCE/README.md',
            'models/iS3D/PDG/pdg_smash_pce.dat',
            'models/iS3D/deltaf_coefficients/vh/smash/F.dat',
        )
        excluded = (
            '.git/objects/85/randomhash', 'models/SMASH/.git', '.recovery/saved.tar',
            '.aws/credentials', '.codex/settings', '.agents/settings',
            '.ssh/id_ed25519', '.env', '.env.production', '.netrc', '.pypirc',
            'personal/notes.txt', 'private/notes.txt', 'scratch/notes.txt',
            'multisystem-calibration-paper/main.tex',
            'multisystem-calibration-paper-source.zip',
            'dist/release.tar.gz', 'physics-data/EOS/PCE/strange_30_10000/0.dat',
            'inference/.venv/bin/python', 'models/__pycache__/cached.pyc',
            'models/SMASH/build-deps/lib.so', 'models/trento/build/CMakeCache.txt',
            'models/iS3D/build-workflow/CMakeCache.txt', 'models/iS3D/iS3D',
            'config/users/someone/activate.sh', 'example-output/raw/event.dat',
            'config/users/someone/OO5360.ini',
            'models/music-hydro/utilities/submit_full_job.pbs',
            'models/music-hydro/EOS/PCE/strange_30_10000/0.dat',
            'models/iS3D/deltaf_coefficients/vh/smash_pce_light_strange/F.dat',
            'inference/plots_walkthrough/plot.pdf', 'inference/scan_example/chain.npz',
        )
        for name in wanted + excluded:
            self.write(self.source / name, 'local edit: ' + name + '\n')
        script = self.source / 'local/install'
        script.chmod(0o750)
        link = self.source / 'models/SMASH/src/source-link.cc'
        link.symlink_to('custom.cc')
        destination = self.root / 'destination'
        # Capture rsync's inherited stdout without changing its real operation.
        run = subprocess.run
        with patch.object(stage.subprocess, 'run', side_effect=lambda *a, **kw: run(*a, **kw, capture_output=True)):
            stage.copy_code(self.source, str(destination))
            self.assertFalse(destination.exists())
            stage.copy_code(self.source, str(destination), apply=True)
            for name in wanted:
                self.assertEqual((destination / name).read_bytes(), (self.source / name).read_bytes())
            for name in excluded:
                self.assertFalse((destination / name).exists(), name)
            self.assertTrue((destination / 'models/SMASH/src/source-link.cc').is_symlink())
            self.assertEqual((destination / 'local/install').stat().st_mode & 0o777, 0o750)
            # Content changes are copied even with the same size and modification time.
            original_stat = script.stat()
            script.write_text('edited local code\n')
            target = destination / 'local/install'
            target.write_text('stale! local code\n')
            os.utime(script, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
            keep = self.write(destination / 'keep-existing.txt', 'must not delete\n')
            stage.copy_code(self.source, str(destination), apply=True)
            self.assertEqual(script.read_bytes(), target.read_bytes())
            self.assertTrue(keep.exists())

    def test_missing_smash_blocks_transfer(self):
        (self.source / 'models/SMASH/CMakeLists.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'initialized SMASH'):
            stage.copy_code(self.source, 'user@nersc:/home/user/source', apply=True)

    def test_nested_source_destination_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'separate trees'):
            stage.copy_code(self.source, str(self.source / 'nested'))

    def test_remote_transfer_uses_arguments_and_never_delete(self):
        with patch.object(stage.shutil, 'which', return_value='/usr/bin/rsync'), \
                patch.object(stage.subprocess, 'run') as run:
            stage.copy_code(self.source, 'user@nersc:/home/user/code with spaces')
        command = run.call_args.args[0]
        self.assertIn('--dry-run', command)
        self.assertIn('--protect-args', command)
        self.assertNotIn('--delete', command)
        self.assertEqual(command[-1], 'user@nersc:/home/user/code with spaces/')
        self.assertNotIn('shell', run.call_args.kwargs)
        with self.assertRaisesRegex(ValueError, 'Remote destination'):
            stage.copy_code(self.source, 'user@nersc:')

    def test_home_relative_remote_destination_is_passed_without_local_expansion(self):
        with patch.object(stage.shutil, 'which', return_value='/usr/bin/rsync'), \
                patch.object(stage.subprocess, 'run') as run:
            stage.copy_code(self.source, 'user@nersc.example:pce-eventgen')
        command = run.call_args.args[0]
        self.assertEqual(command[-1], 'user@nersc.example:pce-eventgen/')
        self.assertIn('--dry-run', command)

    def test_data_preview_verifies_but_creates_nothing(self):
        sampler, eos = self.data_fixture()
        destination = self.root / 'new parent/data'
        stage.copy_data(sampler, eos, destination)
        self.assertFalse(destination.parent.exists())

    def test_copy_data_is_self_contained_and_only_contains_runtime_inputs(self):
        sampler, eos = self.data_fixture()
        external = self.write(self.root / 'external.dat', 'linked quadrature\n')
        (sampler / 'tables/linked.dat').symlink_to(external)
        destination = self.root / 'CFS/data'
        stage.copy_data(sampler, eos, destination, apply=True)
        stage.check_data(destination / 'iS3D', destination / 'EOS/PCE/strange_30_10000')
        self.assertEqual((destination / 'iS3D/tables/linked.dat').read_bytes(), external.read_bytes())
        self.assertFalse((destination / 'iS3D/tables/linked.dat').is_symlink())
        self.assertTrue((destination / 'iS3D/deltaf_coefficients/vh/other/F.dat').exists())
        self.assertFalse((destination / 'iS3D/build').exists())
        self.assertFalse((destination / 'iS3D/src').exists())
        self.assertEqual(list(destination.parent.glob('.pce-data-*')), [])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            stage.copy_data(sampler, eos, destination, apply=True)

    def test_bad_checksum_and_missing_metadata_block_copy(self):
        sampler, eos = self.data_fixture()
        destination = self.root / 'new/data'
        self.write(eos / '0.dat', 'corrupt! eos\n')  # same size; exercise checksum, not only size
        (sampler / 'PDG/baryon_dict.dat').unlink()
        with self.assertRaises(ValueError) as error:
            stage.copy_data(sampler, eos, destination, apply=True)
        self.assertIn('checksum mismatch', str(error.exception))
        self.assertIn('baryon_dict.dat', str(error.exception))
        self.assertFalse(destination.parent.exists())

    def test_corrupt_transfer_never_publishes_data_root(self):
        sampler, eos = self.data_fixture()
        destination = self.root / 'CFS/data'
        copytree = shutil.copytree

        def corrupt_copy(source, target, *args, **kwargs):
            result = copytree(source, target, *args, **kwargs)
            if Path(source) == eos:
                (Path(target) / '0.dat').write_text('corrupted during transfer\n')
            return result

        with patch.object(stage.shutil, 'copytree', side_effect=corrupt_copy):
            with self.assertRaisesRegex(ValueError, 'wrong size'):
                stage.copy_data(sampler, eos, destination, apply=True)
        self.assertFalse(destination.exists())
        self.assertEqual(list(destination.parent.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
