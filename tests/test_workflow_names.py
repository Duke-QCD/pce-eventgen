"""Exercise canonical configuration names and the reap refusal boundaries."""

import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'models'))
import workflow_config
from workflow_status import annotate_row


class WorkflowNameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='campaign names ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith('HIC_') and k not in ('BASH_ENV', 'ENV', 'SLURM_JOB_ID')}
        self.env.update(PSCRATCH=str(self.root), SCRATCH=str(self.root))

    def test_manifest_keys_resolve_and_removed_aliases_are_rejected(self):
        ini = self.root / 'campaign.ini'
        ini.write_text('[pipeline]\ncache_manifest=cache.manifest\nreap_manifest=reap_ready.manifest\n')
        config = workflow_config.read_config(ini)
        self.assertEqual(workflow_config.defaults(config, 'cache', 'reap')['manifest'],
                         'reap_ready.manifest')
        self.assertEqual(workflow_config.defaults(config, 'cache', 'cache')['manifest'],
                         'cache.manifest')
        for text in ('[pipeline]\nstage2_manifest=old\n', '[raw_analysis]\nrun_root=raw\n'):
            with self.subTest(text=text):
                ini.write_text(text)
                with self.assertRaises(ValueError):
                    workflow_config.read_config(ini)

    def test_reap_manifest_guard_preserves_refusals(self):
        scope = self.root / 'scope.json'
        scope.write_text(json.dumps({key: 1 for key in workflow_config.SCOPE_ENV}))
        ini = self.root / 'campaign.ini'
        ini.write_text(f'[run]\ndirectory={self.root}\nsystem=AuAu200\nnevents=1\n'
                       f'[pipeline]\ncache_cfs_root={self.root / "published"}\n'
                       f'cache_local_root={self.root / "staging"}\n'
                       f'expected_fingerprint={scope}\nrecord_dir={self.root / "records"}\n')
        for name in ('cache.manifest', 'reap_ready.manifest'):
            (self.root / name).write_text('')

        def invoke(stage, manifest, *extra):
            return subprocess.run([sys.executable, str(ROOT / 'models/submit_cache_reap_campaign.py'),
                                   '--config', str(ini), '--stage', stage, '--manifest',
                                   str(self.root / manifest), *extra], cwd=self.root,
                                  env=self.env, capture_output=True, text=True)

        result = invoke('reap', 'reap_ready.manifest')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = invoke('reap', 'cache.manifest')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing reap', result.stderr)
        result = invoke('reap', 'cache.manifest', '--force-reap-manifest')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = invoke('reap', 'reap_ready.manifest', '--submit')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('outside a Slurm allocation', result.stderr)
        result = invoke('reap', 'reap_ready.manifest', '--submit', '--allow-reap-outside-slurm')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = invoke('cache', 'cache.manifest')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for removed in ('stage1', 'stage2'):
            self.assertNotEqual(invoke(removed, 'cache.manifest').returncode, 0)
        self.assertFalse((self.root / 'records').exists())  # No work or reaper executed.

    def test_canonical_readiness_does_not_gain_reap_authority_from_legacy_field(self):
        base = dict(design='design_000', system='AuAu200', path='/raw/design_000/AuAu200',
                    terminal=1, scanned_expected=1, ready_for_cache_reap=True)
        row = annotate_row(dict(base, raw_ready_for_cache=False, needs_hydro=1))
        self.assertEqual(row['status'], 'needs_run')
        self.assertEqual(annotate_row(dict(base))['status'], 'ready_to_cache')
        self.assertEqual(annotate_row(dict(base, raw_ready_for_cache=True))['status'], 'ready_to_cache')

    def test_observable_modules_retain_cli_and_schema(self):
        sys.path.insert(0, str(ROOT / 'models/analysis'))
        self.addCleanup(sys.path.remove, str(ROOT / 'models/analysis'))
        canonical = importlib.import_module('extract_observables')
        self.assertEqual(canonical.OBSERVABLE_SCHEMA, 'stage_a/v1')
        for name in ('extract_observables.py', 'reduce_observables.py'):
            result = subprocess.run([sys.executable, str(ROOT / 'models/analysis' / name),
                                     '--help'], cwd=self.root, env=self.env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
