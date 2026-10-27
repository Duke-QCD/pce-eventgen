"""Data releases install completely, verify automatically and preserve old data."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fetch = load('download_physics_data')
builder = load('package_physics_data')


class PhysicsDataDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='public data setup ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.sampler = self.root / 'source/iS3D'
        self.eos = self.root / 'source/EOS'
        self.destination = self.root / 'new/data'
        self.archive = self.root / 'release.tar.gz'
        self.capture = contextlib.redirect_stdout(io.StringIO())
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)
        manifests = {}
        for dataset, directory in (
                ('eos', self.eos),
                ('is3d', self.sampler / 'deltaf_coefficients/vh/smash_pce_light_strange')):
            path = self.write(directory / ('0.dat' if dataset == 'eos' else 'F.dat'), 'production ' + dataset)
            manifests[dataset] = self.write(self.root / (dataset + '.json'), json.dumps({
                'files': [{'name': path.name, 'bytes': path.stat().st_size,
                           'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]}))
        for name in ('iS3D_parameters.dat', 'paths.sh', 'PDG/pdg_smash_pce.dat',
                     'PDG/chosen_particles.dat', 'PDG/charge_dict.dat', 'PDG/baryon_dict.dat',
                     'tables/quadrature.dat', 'tables/readme.txt',
                     'deltaf_coefficients/vh/other/F.dat'):
            self.write(self.sampler / name, 'data ' + name)
        self.write(self.sampler / 'build/compiled-executable', 'excluded')
        for name in ('LICENSE', 'models/iS3D/LICENSE', 'models/music-hydro/LICENSE',
                     'data/ARCHIVE_README.md', 'data/ATTRIBUTION.md'):
            self.write(self.root / name, 'preserved notice ' + name)
        for name, value in (('ROOT', self.root), ('MANIFESTS', manifests)):
            patcher = patch.object(builder, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def package(self):
        return builder.package(self.sampler, self.eos, self.archive)

    def test_complete_offline_install_and_repeat_without_network(self):
        manifest = self.package()
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('must stay offline')):
            fetch.download(self.destination, manifest, self.archive)
            fetch.download(self.destination, manifest)
        self.assertEqual(fetch.validate(self.destination, manifest), [])
        self.assertTrue((self.destination / 'iS3D/deltaf_coefficients/vh/other/F.dat').is_file())
        self.assertFalse((self.destination / 'iS3D/build').exists())
        for name in ('ATTRIBUTION.md', 'licenses/iS3D-MIT.txt', 'licenses/MUSIC-GPL-2.0.txt',
                     'licenses/pce-eventgen-MIT.txt', 'manifests/music-eos.json'):
            self.assertTrue((self.destination / name).is_file(), name)
        self.assertEqual(list(self.destination.parent.glob('.pce-download-*')), [])

    def test_url_download_uses_checked_in_release_and_verifies_all_files(self):
        manifest = self.package()
        with self.archive.open('rb') as response:
            with patch.object(fetch.urllib.request, 'urlopen', return_value=response) as request:
                fetch.download(self.destination, manifest)
        self.assertEqual(request.call_args.args[0].full_url, manifest['url'])
        self.assertEqual(fetch.validate(self.destination, manifest), [])
        self.assertEqual(list(self.destination.parent.glob('.pce-download-*')), [])

    def test_corrupt_archive_is_not_installed(self):
        manifest = self.package()
        original = self.archive.read_bytes()
        self.archive.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        with self.assertRaises(tarfile.TarError):
            fetch.download(self.destination, manifest, self.archive)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_older_packaging_and_documentation_are_accepted_and_reused(self):
        manifest = self.package()
        self.write(self.root / 'data/ARCHIVE_README.md', 'older documentation of a different length')
        self.write(self.sampler / 'tables/readme.txt', 'older table documentation')
        older = self.root / 'older-named-release.tar.gz'
        older_manifest = builder.package(self.sampler, self.eos, older)
        self.assertNotEqual(manifest['sha256'], older_manifest['sha256'])
        fetch.download(self.destination, manifest, older)
        self.assertEqual((self.destination / 'README.md').read_text(),
                         'older documentation of a different length')
        # A later check must not reject the installed copy because of its README.
        with patch.object(fetch.urllib.request, 'urlopen', side_effect=AssertionError('must reuse')):
            fetch.download(self.destination, manifest)

    def test_mismatched_existing_data_are_not_replaced(self):
        manifest = self.package()
        fetch.download(self.destination, manifest, self.archive)
        existing = self.destination / 'iS3D/PDG/baryon_dict.dat'
        existing.write_text('important existing data')
        with self.assertRaisesRegex(ValueError, 'existing files were not changed'):
            fetch.download(self.destination, manifest, self.archive)
        self.assertEqual(existing.read_text(), 'important existing data')

    def test_extracted_file_checksums_are_enforced(self):
        manifest = self.package()
        next(entry for entry in manifest['files']
             if entry['name'] == 'EOS/PCE/strange_30_10000/0.dat')['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Data verification failed'):
            fetch.download(self.destination, manifest, self.archive)
        self.assertFalse(self.destination.exists())

    def test_interrupted_download_cleans_partial_files(self):
        manifest = self.package()

        class Interrupted(io.BytesIO):
            def read(self, size=-1):
                raise OSError('connection interrupted')

        with patch.object(fetch.urllib.request, 'urlopen', return_value=Interrupted(b'partial')):
            with self.assertRaisesRegex(OSError, 'connection interrupted'):
                fetch.download(self.destination, manifest)
        self.assertFalse(self.destination.exists())
        self.assertEqual(list(self.destination.parent.iterdir()), [])

    def test_unpublished_release_has_an_actionable_error(self):
        manifest = self.package()
        release = self.write(self.root / 'release.json', json.dumps(manifest))
        error = urllib.error.HTTPError(manifest['url'], 404, 'Not Found', {}, None)
        stderr = io.StringIO()
        with patch.object(fetch, 'RELEASE', release), \
                patch.object(sys, 'argv', ['download_physics_data.py', '--directory', str(self.destination)]), \
                patch.object(fetch.urllib.request, 'urlopen', side_effect=error), \
                contextlib.redirect_stderr(stderr):
            self.assertEqual(fetch.main(), 1)
        self.assertIn('maintainer must upload', stderr.getvalue())
        self.assertIn('--archive PATH', stderr.getvalue())
        self.assertNotIn('Traceback', stderr.getvalue())
        self.assertFalse(self.destination.exists())

    def test_archive_members_cannot_escape_destination_or_create_links(self):
        for name, kind in (('../outside', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                           ('link', tarfile.SYMTYPE), ('hardlink', tarfile.LNKTYPE)):
            with self.subTest(name=name):
                archive = self.root / 'unsafe.tar.gz'
                with tarfile.open(archive, 'w:gz') as stream:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.linkname = '../outside'
                    stream.addfile(entry)
                with self.assertRaisesRegex(ValueError, 'Unexpected archive entry'):
                    fetch.unpack(archive, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertFalse((self.root / 'new/outside').exists())

    def test_archive_duplicates_and_missing_members_are_rejected(self):
        manifest = self.package()
        with tarfile.open(self.archive, 'w:gz') as stream:
            for _ in range(2):
                stream.addfile(tarfile.TarInfo('duplicate'))
        with self.assertRaisesRegex(ValueError, 'Unexpected archive entry'):
            fetch.unpack(self.archive, self.destination)
        # Use another destination because the direct extraction above left a fixture file.
        destination = self.root / 'empty-archive-data'
        with tarfile.open(self.archive, 'w:gz'):
            pass
        with self.assertRaisesRegex(ValueError, 'Data verification failed'):
            fetch.download(destination, manifest, self.archive)
        self.assertFalse(destination.exists())

    def test_packaging_is_reproducible_and_checks_production_inputs(self):
        manifest = self.package()
        # Filesystem metadata cannot change the release bytes.
        for path in self.sampler.rglob('*'):
            os.utime(path, (123456789, 123456789))
        other = self.root / 'second-release.tar.gz'
        repeated = builder.package(self.sampler, self.eos, other)
        self.assertEqual(manifest['sha256'], repeated['sha256'])
        self.assertEqual(manifest['files'], repeated['files'])
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.package()
        self.write(self.eos / '0.dat', 'invalid eos')
        with self.assertRaisesRegex(ValueError, 'wrong size|checksum mismatch'):
            builder.package(self.sampler, self.eos, self.root / 'bad-release.tar.gz')
        self.assertFalse((self.root / 'bad-release.tar.gz').exists())


if __name__ == '__main__':
    unittest.main()
