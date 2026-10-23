"""Exercise campaign triage with raw metadata and cache/reap records together."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'models'))
from workflow_status import annotate_row


def survey_module():
    spec = importlib.util.spec_from_file_location('raw_status_survey', ROOT / 'models/cache/survey_raw_designs.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CampaignStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'raw'
        self.records = self.root / 'records'
        self.records.mkdir()
        self.snapshot = self.root / 'snapshots/survey.json'
        self.ini = self.root / 'campaign.ini'
        self.ini.write_text(f'''[run]
directory={self.raw}
system=AuAu200
nevents=2
[pipeline]
record_dir={self.records}
survey_json={self.snapshot}
cache_cfs_root={self.root / 'published'}
cache_manifest={self.root / 'manifests/cache.manifest'}
reap_manifest={self.root / 'manifests/reap_ready.manifest'}
''')

    def design(self, index, kind='finished'):
        design = self.raw / f'design_{index:03d}/AuAu200'
        design.mkdir(parents=True)
        if kind == 'reaped':
            (design / 'reaped.done').touch()
            return design
        if kind == 'empty':
            return design
        for i in range(2):
            event = design / str(i)
            event.mkdir()
            if kind == 'afterburner':
                (event / 'hydro.done').touch()
            elif kind == 'no_surface':
                (event / 'hydro.no_surface').touch()
            else:
                (event / 'afterburner.done').touch()
                if kind != 'missing_particles':
                    (event / 'particle_lists.oscar').write_text('Only sizes should be read')
            if kind == 'locked':
                (event / 'smash.lock').touch()
        return design

    def record(self, index, stage='stage1', **fields):
        doc = dict(design=f'design_{index:03d}', system='AuAu200', verdict='clean',
                   dryrun_buckets={'would_delete': 2})
        doc.update(fields)
        path = self.records / f'design_{index:03d}_AuAu200_{stage}.json'
        path.write_text(json.dumps(doc))
        return path

    def invoke(self, *args, code=0):
        result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py'),
                                 '--config', str(self.ini), *args], capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_mixed_campaign_reports_actions_and_full_snapshot_despite_display_filter(self):
        self.design(0, 'empty')
        self.design(1, 'afterburner')
        self.design(2)
        self.design(3, 'locked')
        self.design(4, 'reaped')
        self.design(5)
        self.record(5)
        self.design(6, 'missing_particles')
        self.design(7, 'no_surface')
        self.record(7, dryrun_buckets={'no_surface_nothing_to_reap': 2})
        # Published marker alone means verify, rather than permission to reap.
        self.design(8)
        published = self.root / 'published/design_008/AuAu200'
        published.mkdir(parents=True)
        (published / 'cache.verified').touch()
        result = self.invoke('status', '--only', 'ready_to_cache', '--details', '--nprocs', '1')
        rows = {r['design']: r for r in json.loads(self.snapshot.read_text())['rows']}
        self.assertEqual({k: v['status'] for k, v in rows.items()}, {
            'design_000': 'needs_run', 'design_001': 'needs_run', 'design_002': 'ready_to_cache',
            'design_003': 'waiting', 'design_004': 'reaped', 'design_005': 'ready_to_reap',
            'design_006': 'review', 'design_007': 'cached', 'design_008': 'verify_cache'})
        self.assertEqual(rows['design_000']['needs_hydro'], 2)
        self.assertEqual(rows['design_001']['needs_afterburner'], 2)
        self.assertIn('design_002', result.stdout)
        self.assertNotIn('design_000', result.stdout)
        self.invoke('prepare-cache')
        self.assertEqual((self.root / 'manifests/cache.manifest').read_text(),
                         'design_002 AuAu200\ndesign_008 AuAu200\n')

    def test_reaped_marker_avoids_event_traversal_and_empty_untargeted_scan_is_not_ready(self):
        design = self.design(0, 'reaped')
        survey = survey_module()
        with patch.object(survey.os, 'scandir', side_effect=AssertionError('reaped tree should not be scanned')):
            counts = survey.scan_design(str(design), target_events=10000)
        self.assertTrue(counts['reaped_marker'])
        empty = self.design(1, 'empty')
        result = subprocess.run([sys.executable, str(ROOT / 'models/cache/survey_raw_designs.py'),
                                 '--raw-root', str(self.raw), '--nprocs', '1', '--json', str(self.snapshot)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        row = next(r for r in json.loads(self.snapshot.read_text())['rows'] if r['path'] == str(empty))
        self.assertFalse(row['raw_ready_for_cache'])
        self.assertEqual(row['status'], 'review')

    def test_large_summary_is_bounded_and_details_can_show_every_design(self):
        for i in range(35):
            self.design(i, 'empty')
        result = self.invoke('status', '--nprocs', '1')
        self.assertLess(len(result.stdout.splitlines()), 18)
        self.assertIn('(+29 more)', ' '.join(result.stdout.split()))
        result = self.invoke('status', '--only', 'needs_run', '--details', '--max-rows', '0', '--nprocs', '1')
        self.assertIn('design_034/AuAu200', result.stdout)
        self.assertEqual(len(json.loads(self.snapshot.read_text())['rows']), 35)

    def test_missing_system_and_record_only_reaped_design_are_accounted_for(self):
        (self.raw / 'design_000').mkdir(parents=True)
        self.record(1, stage='stage2', verdict='applied_clean', post_apply_buckets={})
        self.invoke('status', '--nprocs', '1')
        rows = {r['design']: r for r in json.loads(self.snapshot.read_text())['rows']}
        self.assertEqual(rows['design_000']['status'], 'needs_run')
        self.assertEqual(rows['design_001']['status'], 'reaped')

    def test_stale_reap_record_returns_to_cache_verification(self):
        self.design(0)
        self.record(0, timestamps={'end': '2026-10-19T00:00:00Z'})
        self.record(0, stage='stage2', verdict='quarantined',
                    timestamps={'end': '2026-10-20T00:00:00Z'},
                    quarantine_reason='current CFS state incomplete != verified_match against stage1 fingerprint')
        self.invoke('prepare-cache')
        self.assertEqual((self.root / 'manifests/cache.manifest').read_text(), 'design_000 AuAu200\n')
        row = json.loads(self.snapshot.read_text())['rows'][0]
        self.assertEqual(row['status'], 'verify_cache')

    def test_record_path_mismatch_is_visible_and_not_selected(self):
        self.design(0)
        self.record(0, raw_design='/different/campaign/design_000/AuAu200')
        self.invoke('prepare-cache')
        row = json.loads(self.snapshot.read_text())['rows'][0]
        self.assertEqual(row['status'], 'review')
        self.assertIn('path differs', row['status_reason'])
        self.assertEqual((self.root / 'manifests/cache.manifest').read_text(), '')

    def test_stale_survey_for_another_raw_root_cannot_replace_manifest(self):
        self.design(0)
        self.invoke('prepare-cache')
        manifest = self.root / 'manifests/cache.manifest'
        original = manifest.read_bytes()
        self.ini.write_text(self.ini.read_text().replace(str(self.raw), str(self.root / 'other-raw')))
        result = self.invoke('manifest', code=1)
        self.assertIn('Survey raw_root differs', result.stderr)
        self.assertEqual(manifest.read_bytes(), original)

    def test_review_uses_configured_system_and_display_filter_does_not_narrow_manifest(self):
        self.record(0)
        self.record(1)
        other = dict(design='design_002', system='OO5360', verdict='clean', dryrun_buckets={'would_delete': 2})
        (self.records / 'design_002_OO5360_stage1.json').write_text(json.dumps(other))
        result = self.invoke('review', '--only', 'review', '--json', str(self.root / 'review.json'))
        self.assertNotIn('design_000', result.stdout)
        self.assertEqual((self.root / 'manifests/reap_ready.manifest').read_text(),
                         'design_000 AuAu200\ndesign_001 AuAu200\n')
        rows = json.loads((self.root / 'review.json').read_text())['rows']
        self.assertEqual(len(rows), 2)

    def test_duplicate_records_do_not_replace_a_previously_reviewed_manifest(self):
        self.design(0)
        first = self.record(0)
        (self.records / 'old').mkdir()
        (self.records / 'old' / first.name).write_bytes(first.read_bytes())
        self.invoke('status', '--nprocs', '1')
        self.assertEqual(json.loads(self.snapshot.read_text())['rows'][0]['status'], 'review')
        manifest = self.root / 'manifests/reap_ready.manifest'
        manifest.parent.mkdir()
        manifest.write_text('previous selection\n')
        self.invoke('review', code=4)
        self.assertEqual(manifest.read_text(), 'previous selection\n')
        self.invoke('manifest', code=1)

    def test_accepted_shortfall_history_does_not_become_a_generation_request(self):
        row = dict(design='design_000', system='AuAu200', path='/raw/design_000/AuAu200',
                   terminal=1, scanned_expected=2, ready_for_cache_reap=False, missing=1)
        record = ({'verdict': 'clean', 'dryrun_buckets': {'would_delete': 1}}, '/record')
        annotate_row(row, record, good_buckets={'would_delete'})
        self.assertEqual(row['status'], 'ready_to_reap')


if __name__ == '__main__':
    unittest.main()
