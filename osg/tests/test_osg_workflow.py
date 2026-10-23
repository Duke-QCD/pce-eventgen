"""Offline integration checks. Remote commands run only in temporary local trees."""

from contextlib import redirect_stdout
import importlib.util
import io
import os
import re
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


OSG = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, OSG / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = load('stage_osg_design')
drain = load('drain_osg_events_batched')
sweep = load('sweep_osg_designs')


def shell_result(command, input_text=None):
    result = subprocess.run(['bash', '-c', command], input=input_text, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


class TemporaryWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='OSG workflow ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = self.root / 'nersc/design_498/AuAu200'
        self.raw.mkdir(parents=True)
        self.ap = self.root / 'ap/stage'
        self.manifest = self.root / 'separate-submit/manifest_498.txt'
        self.manifest.parent.mkdir()
        self.manifest.write_text('stale selection\n')

    def stage_args(self, *extra):
        return ['stage_osg_design.py', '--design', '498', '--nersc-ic-dir', str(self.raw),
                '--pscratch-root', str(self.raw.parents[1]), '--ap-stage-root', str(self.ap),
                '--campaign', 'osg_campaign', '--total-nevents', '2',
                '--manifest', str(self.manifest), *extra]

    def local_stage_ssh(self, host, command, opts, timeout):
        return shell_result(command)

    def local_drain_ssh(self, cfg, command, timeout=600, input_text=None):
        return shell_result(command, input_text)

    def run_stage(self, *extra):
        with patch.object(sys, 'argv', self.stage_args(*extra)), \
                patch.object(stage, 'ssh_nersc', side_effect=self.local_stage_ssh), \
                redirect_stdout(io.StringIO()):
            stage.main()

    def test_completed_or_reaped_design_replaces_stale_manifest_without_pull(self):
        for evidence in ('drained', 'reaped'):
            with self.subTest(evidence=evidence):
                for event in ('0', '1'):
                    (self.raw / event).mkdir(exist_ok=True)
                    (self.raw / event / 'drained.done').touch()
                if evidence == 'reaped':
                    for event in ('0', '1'):
                        shutil.rmtree(self.raw / event)
                    (self.raw / 'reaped.done').touch()
                self.manifest.write_text('stale selection\n')
                with patch.object(stage, 'pull_file') as pull:
                    self.run_stage()
                pull.assert_not_called()
                self.assertEqual(self.manifest.read_text(), '')
                self.assertFalse(self.ap.exists())

    def test_stage_dry_run_preserves_manifest_and_inputs(self):
        (self.raw / 'reaped.done').touch()
        self.run_stage('--dry-run')
        self.assertEqual(self.manifest.read_text(), 'stale selection\n')
        self.assertFalse(self.ap.exists())

    def test_stage_splits_to_explicit_manifest_and_preserves_event_identity(self):
        try:
            import h5py
        except (ImportError, AttributeError) as error:
            self.skipTest(f'Compatible h5py/NumPy required: {error}; try PYTHONNOUSERSITE=1')
        import numpy as np
        with h5py.File(self.raw / 'initial.hdf', 'w') as src:
            src.create_dataset('event_z', data=np.full((2, 2), 9))
            src.create_dataset('event_a', data=np.full((2, 2), 3))
        for name in ('parameters.dat', 'music_input'):
            (self.raw / name).write_text(name)

        def pull(host, remote_path, dest_dir, ssh_opts, timeout=3600):
            dest_dir.mkdir(parents=True, exist_ok=True)
            return Path(shutil.copy(remote_path, dest_dir))

        with patch.object(stage, 'pull_file', side_effect=pull):
            self.run_stage()
        self.assertEqual(self.manifest.read_text().splitlines(), [
            'osg_campaign/design_498/AuAu200/0', 'osg_campaign/design_498/AuAu200/1'])
        for i, value in enumerate((3, 9)):
            with h5py.File(self.ap / f'osg_campaign/design_498/AuAu200/{i}/initial.hdf', 'r') as f:
                self.assertTrue(np.all(f[next(iter(f))][:] == value))
        self.assertTrue((self.raw / 'initial.hdf').is_file())
        self.assertFalse((self.ap.parent / '_monoliths/design_498/initial.hdf').exists())

    def test_sweep_passes_explicit_manifest_and_skips_empty_submission(self):
        options = SimpleNamespace(stage_script='stage.py', split_script='split.py', nersc_host='unused',
                                  nersc_ic_dir=str(self.raw), pscratch_root=str(self.raw.parents[1]),
                                  ap_stage_root=str(self.ap), system='AuAu200', campaign='osg_campaign',
                                  total_nevents=2, ssh_opt=['-o', 'BatchMode=yes'], submit_dir=str(self.manifest.parent))
        with patch.object(sweep, 'run', return_value=(0, '', '')) as run, redirect_stdout(io.StringIO()):
            sweep.stage_design(498, options)
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--manifest') + 1], str(self.manifest))
        self.assertIn('--ssh-opt=-o', argv)
        self.manifest.write_text('')
        argv = ['sweep_osg_designs.py', '--start', '498', '--floor', '498',
                '--nersc-ic-dir', str(self.raw), '--pscratch-root', str(self.raw.parents[1]),
                '--ap-stage-root', str(self.ap), '--campaign', 'osg_campaign',
                '--submit-dir', str(self.manifest.parent)]
        with patch.object(sys, 'argv', argv), patch.object(sweep, 'stage_design', return_value=0), \
                patch.object(sweep, 'submit_design') as submit, patch.object(sweep.time, 'sleep'), \
                redirect_stdout(io.StringIO()):
            sweep.main()
        submit.assert_not_called()

    def test_sweep_keeps_nersc_design_padding_through_staging_and_teardown(self):
        def stage_fixture(design, options):
            self.assertIn(design, ('001', '000'))
            path = self.ap / options.campaign / f'design_{design}/AuAu200/0'
            path.mkdir(parents=True)
            (path / 'initial.hdf').touch()
            (self.manifest.parent / f'manifest_{design}.txt').write_text(
                f'{options.campaign}/design_{design}/AuAu200/0\n')
            return 0

        argv = ['sweep_osg_designs.py', '--start', '1', '--floor', '0',
                '--design-width', '3', '--nersc-ic-dir', str(self.raw),
                '--pscratch-root', str(self.raw.parents[1]), '--ap-stage-root', str(self.ap),
                '--campaign', 'osg_campaign', '--submit-dir', str(self.manifest.parent),
                '--max-running', '1']
        with patch.object(sys, 'argv', argv), \
                patch.object(sweep, 'stage_design', side_effect=stage_fixture), \
                patch.object(sweep, 'submit_design', side_effect=['1', '2']) as submit, \
                patch.object(sweep, 'condor_cluster_empty', return_value=True), \
                patch.object(sweep.time, 'sleep'), redirect_stdout(io.StringIO()):
            sweep.main()
        self.assertEqual([call.args[0] for call in submit.call_args_list], ['001', '000'])
        for design in ('001', '000'):
            self.assertFalse((self.ap / f'osg_campaign/design_{design}/AuAu200').exists())
            self.assertFalse((self.manifest.parent / f'manifest_{design}.txt').exists())

    def cfg(self, dry_run=False):
        return drain.Cfg('unused', str(self.ap / 'osg_campaign'), '498', 'AuAu200',
                         str(self.raw), False, dry_run, [])

    def test_dry_run_does_not_create_remote_root_and_empty_marker_query_succeeds(self):
        cfg = self.cfg(dry_run=True)
        cfg.pscratch_design_root = str(self.root / 'nonexistent')
        with patch.object(drain, 'ssh_nersc', side_effect=self.local_drain_ssh):
            self.assertEqual(drain.remote_drained_set(cfg), set())
            self.assertFalse(Path(cfg.pscratch_design_root).exists())
            cfg.pscratch_design_root = str(self.raw)
            (self.raw / '0').mkdir()
            self.assertEqual(drain.remote_drained_set(cfg), set())

    def test_failed_query_stops_transfer_and_cleanup(self):
        with patch.object(drain, 'list_completed_events', return_value={'0': ('particles', 7)}), \
                patch.object(drain, 'remote_drained_set', return_value=None), \
                patch.object(drain, 'rsync_push') as push, patch.object(drain, 'local_delete') as delete, \
                redirect_stdout(io.StringIO()):
            tally, count = drain.one_pass(self.cfg(), 0)
        self.assertEqual((count, tally['FAILED-marker-check']), (1, 1))
        push.assert_not_called()
        delete.assert_not_called()

    def test_drain_refuses_a_reaped_destination(self):
        (self.raw / 'reaped.done').touch()
        with patch.object(drain, 'ssh_nersc', side_effect=self.local_drain_ssh):
            self.assertIsNone(drain.remote_drained_set(self.cfg()))
        self.assertEqual(sorted(p.name for p in self.raw.iterdir()), ['reaped.done'])

    @unittest.skipUnless(shutil.which('rsync'), 'rsync required for local transfer round trip')
    def test_drain_transfers_only_outbox_and_commits_before_slot_cleanup(self):
        cfg = self.cfg()
        for event, kind in (('0', 'particles'), ('1', 'nosurface')):
            slot = cfg.ap_design_root / event
            (slot / 'outbox').mkdir(parents=True)
            (slot / 'initial.hdf').write_text('input must stay off NERSC output tree')
            (slot / 'job.1.0.out').write_text('log must stay off NERSC output tree')
            if kind == 'particles':
                (slot / 'outbox/particle_lists.oscar').write_bytes(b'payload')
                (slot / 'outbox/afterburner.done').touch()
            else:
                (slot / 'outbox/hydro.no_surface').touch()

        def local_transfer(command, timeout=3600, input_text=None):
            command = list(command)
            if command[0] == 'rsync':
                command[-1] = command[-1].replace('unused:', '', 1)
            self.assertIn(command[0], ('rsync', 'rm'))
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
            return result.returncode, result.stdout, result.stderr

        with patch.object(drain, 'ssh_nersc', side_effect=self.local_drain_ssh), \
                patch.object(drain, 'run', side_effect=local_transfer), redirect_stdout(io.StringIO()):
            tally, count = drain.one_pass(cfg, 0)
        self.assertEqual((count, tally['drained']), (2, 2))
        for event in ('0', '1'):
            self.assertTrue((self.raw / event / 'drained.done').is_file())
            self.assertFalse((self.raw / event / 'initial.hdf').exists())
            self.assertFalse((self.raw / event / 'job.1.0.out').exists())
            self.assertFalse((cfg.ap_design_root / event).exists())

    def test_delete_failure_is_reported_as_pending_cleanup(self):
        with patch.object(drain, 'list_completed_events', return_value={'0': ('particles', 7)}), \
                patch.object(drain, 'remote_drained_set', return_value=set()), \
                patch.object(drain, 'rsync_push', return_value=0), \
                patch.object(drain, 'flatten_verify_mark', return_value={'0'}), \
                patch.object(drain, 'local_delete', return_value=1), redirect_stdout(io.StringIO()):
            tally, _ = drain.one_pass(self.cfg(), 0)
        self.assertEqual(tally['drained'], 0)
        self.assertEqual(tally['drained-delete-pending'], 1)

    def test_single_pass_cli_returns_failure_when_transfer_fails(self):
        argv = ['drain_osg_events_batched.py', '--ap-root', str(self.ap), '--design', '498',
                '--pscratch-root', str(self.raw.parents[1])]
        with patch.object(sys, 'argv', argv), patch.object(drain, 'ssh_nersc', return_value=(0, 'ok\n', '')), \
                patch.object(drain, 'drain_design_set', return_value=(1, True)), redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                drain.main()
        self.assertEqual(error.exception.code, 1)


class WorkerTerminalTests(unittest.TestCase):
    def test_deployed_helper_help_succeeds_without_campaign_files(self):
        for name in ('stage_osg_design', 'split_design', 'sweep_osg_designs',
                     'drain_osg_events_batched'):
            with self.subTest(helper=name), tempfile.TemporaryDirectory() as directory:
                helper = Path(directory) / f'{name}.py'
                shutil.copy2(OSG / helper.name, helper)
                result = subprocess.run([sys.executable, str(helper), '--help'],
                                        cwd=directory, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('usage:', result.stdout)
                self.assertEqual(list(Path(directory).iterdir()), [helper])

    def test_container_paths_use_canonical_layout(self):
        paths = (OSG / 'container_paths.sh').read_text()
        result = subprocess.run(['sh', '-c', paths + '\nprintf "%s\\n" '
                                 '"$HIC_CHARGE_DICT" "$HIC_IS3D_DELTAF_DIR" "$HIC_EOS_PCE_DIR"'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            '/opt/data/iS3D/PDG/charge_dict.dat', '/opt/data/iS3D/deltaf_coefficients/vh',
            '/opt/data/EOS/PCE/strange_30_10000'])

    def test_worker_accepts_only_unambiguous_terminal_artifacts(self):
        cases = [({'particle_lists.oscar': b'payload', 'afterburner.done': b''}, True),
                 ({'particle_lists.oscar.unfinished': b'payload', 'afterburner.done': b''}, True),
                 ({'hydro.no_surface': b''}, True),
                 ({'particle_lists.oscar': b'payload'}, False),
                 ({'particle_lists.oscar': b'', 'afterburner.done': b''}, False),
                 ({'hydro.no_surface': b'', 'hydro.done': b''}, False),
                 ({'particle_lists.oscar': b'payload', 'afterburner.done': b'', 'hydro.no_surface': b''}, False),
                 ({'particle_lists.oscar': b'payload', 'particle_lists.oscar.unfinished': b'payload',
                   'afterburner.done': b''}, False)]
        for files, accepted in cases:
            with self.subTest(files=list(files)), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = root / 'fixture'
                fixture.mkdir()
                for name, contents in files.items():
                    (fixture / name).write_bytes(contents)
                wrapper = root / 'runner'
                wrapper.write_text('#!/bin/bash\n[[ "$HIC_SURFACE_ARCHIVE_BASE" == "$PWD" ]] || exit 9\n'
                                   'mkdir -p 0\ncp -a "$JOB_FIXTURE/." 0/\n')
                wrapper.chmod(0o755)
                paths = root / 'paths.sh'
                paths.write_text('true\n')
                script = (OSG / 'job.sh').read_text().replace('/opt/hic/container_paths.sh', shlex.quote(str(paths)))
                script = script.replace('/opt/hic/bin/run-events-wrapper', shlex.quote(str(wrapper)))
                result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True,
                                        env={**os.environ, 'JOB_FIXTURE': str(fixture),
                                             'HIC_SURFACE_ARCHIVE_BASE': '/original/nersc/scratch'})
                self.assertEqual(result.returncode == 0, accepted, result.stdout + result.stderr)

    def test_failed_afterburner_still_returns_its_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrapper = root / 'runner'
            wrapper.write_text('#!/bin/bash\nmkdir -p 0\ntouch 0/hydro.done 0/afterburner.failed\n')
            wrapper.chmod(0o755)
            smash = root / 'smash'
            smash.write_text('#!/bin/bash\necho "SMASH failed on purpose"\nexit 3\n')
            smash.chmod(0o755)
            paths = root / 'paths.sh'
            paths.write_text(f'export HIC_SMASH_EXEC={shlex.quote(str(smash))} HIC_SMASH_CONFIG=unused\n')
            script = (OSG / 'job.sh').read_text().replace('/opt/hic/container_paths.sh', shlex.quote(str(paths)))
            script = script.replace('/opt/hic/bin/run-events-wrapper', shlex.quote(str(wrapper)))
            result = subprocess.run(['bash', '-c', script], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)  # no particle output: a real failure
            self.assertIn('SMASH failed on purpose', result.stderr)
            self.assertIn('SMASH diagnostic exit status: 3', result.stderr)
            self.assertTrue((root / 'outbox/afterburner.failed').is_file())


class SubmitPolicyTests(unittest.TestCase):
    def test_retry_removal_applies_to_held_jobs_without_killing_running_attempts(self):
        # Evaluate this submit file's simple Boolean/integer removal expression.
        # This checks policy examples, not HTCondor syntax or scheduler behavior.
        source = (OSG / 'events_fanout.submit').read_text().replace('\\\n', '')
        expression = re.search(r'^periodic_remove\s*=\s*(.+)$', source, re.MULTILINE).group(1)
        expression = expression.replace('&&', ' and ').replace('||', ' or ').replace('time()', 'now')
        for status, starts, reason, age, removed in [
                (2, 5, 12, 60, False), (2, 2, 26, 60, False),
                (1, 5, 13, 60, False), (5, 4, 12, 120, False),
                (5, 5, 12, 120, True), (5, 1, 14, 120, True),
                (5, 1, 26, 120, True), (5, 1, 1, 3601, True)]:
            with self.subTest(status=status, starts=starts, reason=reason, age=age):
                values = dict(JobStatus=status, NumJobStarts=starts, HoldReasonCode=reason,
                              now=10000, EnteredCurrentStatus=10000 - age)
                self.assertEqual(eval(expression, {'__builtins__': {}}, values), removed)


if __name__ == '__main__':
    unittest.main()
