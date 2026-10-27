"""Check publication boundaries against real temporary Git histories."""

import contextlib
import importlib.util
import io
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('publication', ROOT / 'tools/check_publication.py')
publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publication)


@unittest.skipUnless(shutil.which('git'), 'Git is required')
class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='publication history ')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Publication Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'commit.gpgsign', 'false')
        self.write('README.md', 'Public source\n')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root, stderr=subprocess.PIPE)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'Fixture revision')

    def audit(self):
        output = io.StringIO()
        with patch.object(publication, 'ROOT', self.root), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            failed = publication.audit('HEAD')
        return failed, output.getvalue()

    def test_deleted_personal_file_still_blocks_publication(self):
        self.write('config/users/researcher/campaign.ini', 'Personal campaign\n')
        self.commit()
        (self.root / 'config/users/researcher/campaign.ini').unlink()
        self.commit()
        failed, output = self.audit()
        self.assertTrue(failed)
        self.assertIn('config/users/researcher/campaign.ini', output)

    def test_local_paper_and_results_are_excluded_by_real_ignore_files(self):
        shutil.copy2(ROOT / '.gitignore', self.root / '.gitignore')
        (self.root / 'inference').mkdir()
        shutil.copy2(ROOT / 'inference/.gitignore', self.root / 'inference/.gitignore')
        private = (
            'multisystem-calibration-paper/main.tex',
            'multisystem-calibration-paper-source.zip', '.env.production',
            'config/users/researcher/campaign.ini', '.aws/credentials',
            'inference/plots_private/chain.npz', 'inference/model/private.npz',
        )
        for name in private:
            self.write(name, 'Keep local\n')
        self.write('.env.example', 'PUBLIC_SETTING=example\n')
        self.write('inference/data/reference.dat', 'Public reference data\n')
        self.commit()
        files = self.git('ls-files').decode().splitlines()
        self.assertFalse(set(private).intersection(files))
        self.assertIn('.env.example', files)
        self.assertIn('inference/data/reference.dat', files)
        self.assertTrue(all((self.root / name).exists() for name in private))
        self.assertFalse(self.audit()[0])

    def test_historical_lfs_pointer_is_detected(self):
        self.write('retired.dat', 'version https://git-lfs.github.com/spec/' + 'v1\n'
                   + 'oid sha256:' + '0' * 64 + '\nsize 1000\n')
        self.commit()
        (self.root / 'retired.dat').unlink()
        self.commit()
        failed, output = self.audit()
        self.assertTrue(failed)
        self.assertIn('Git LFS pointer in history: retired.dat', output)

    def test_credentials_are_reported_without_echoing_the_value(self):
        token = 'gh' + 'p_' + 'x' * 36
        self.write('settings.txt', 'token=' + token + '\n')
        self.commit()
        failed, output = self.audit()
        self.assertTrue(failed)
        self.assertIn('Possible credential in history', output)
        self.assertNotIn(token, output)


if __name__ == '__main__':
    unittest.main()
