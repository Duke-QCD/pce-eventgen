"""Run workflow entry points in temporary trees; models and Slurm are stand-ins."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def executable(path, body):
    path.write_text(f'#!{sys.executable}\n' + body)
    path.chmod(0o755)


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.startswith(('HIC_', 'BASH_FUNC_')) and key not in ('BASH_ENV', 'ENV')}


class CampaignHelpTests(unittest.TestCase):
    def test_selected_command_help_reaches_backend_without_reading_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for command, option in ((['events'], '--allow-existing'),
                                    (['cache'], '--designs-per-task'),
                                    (['analysis', 'run'], '--submit'),
                                    (['analysis', 'aggregate'], '--n-boot')):
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, str(ROOT / 'models/campaign.py'), '--config',
                         str(root / 'missing.ini')] + command + ['--help'],
                        cwd=root, env=clean_env(), capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn(option, result.stdout)
                    self.assertEqual(list(root.iterdir()), [])

    def test_dispatcher_help_works_without_config_but_execution_requires_it(self):
        for options, expected in ((['--help'], 0), (['cache'], 2),
                                  (['--config', 'missing.ini'], 2)):
            result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py')] + options,
                                    env=clean_env(), capture_output=True, text=True)
            self.assertEqual(result.returncode, expected, result.stdout + result.stderr)


class InitializationTests(unittest.TestCase):
    def test_checkout_helper_and_relative_tables_survive_directory_change(self):
        for in_place in (False, True):
            with self.subTest(in_place=in_place), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run = root / 'design' / 'OO5360'
                run.mkdir(parents=True)
                source = run if in_place else root
                params = source / 'parameters.dat'
                params.write_text('''system = tables/oxygen.h5 tables/oxygen.h5
normalization = 1
nucleon_width = 0.5
gamma_fluctuation = 1
cross_section = 7
grid_step = 0.1
grid_max = 10
''')
                template = source / 'music_input_template'
                template.write_text('Grid_size_in_x {}\nX_grid_size_in_fm {}\n')
                (root / 'tables').mkdir()
                table = root / 'tables/oxygen.h5'
                table.touch()
                binary_dir = root / 'bin'
                binary_dir.mkdir()
                executable(binary_dir / 'trento', '''import json, sys
from pathlib import Path
Path('trento-call.json').write_text(json.dumps(dict(args=sys.argv[1:], cwd=str(Path.cwd()))))
Path('initial.hdf').write_text('stand-in IC output')
''')
                env = clean_env()
                # No python3 on PATH: the helper must use the current interpreter.
                env['PATH'] = str(binary_dir)
                result = subprocess.run([sys.executable, str(ROOT / 'models/initialize.py'),
                                         str(run), str(params), '2', str(template)],
                                        cwd=root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                call = json.loads((run / 'trento-call.json').read_text())
                self.assertEqual(call['args'][-3:], [str(table), str(table), '2'])
                self.assertEqual(call['cwd'], str(run))
                self.assertEqual((run / 'music_input').read_text(),
                                 'Grid_size_in_x 200\nX_grid_size_in_fm 20.0\n')
                self.assertEqual((run / 'parameters.dat').read_bytes(), params.read_bytes())
                # A repeat must refuse before changing any part of the existing bundle.
                (run / 'music_input').write_text('existing input')
                result = subprocess.run([sys.executable, str(ROOT / 'models/initialize.py'),
                                         str(run), str(params), '2', str(template)],
                                        cwd=root, env=env, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('refusing to overwrite', result.stderr)
                self.assertEqual((run / 'music_input').read_text(), 'existing input')


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'campaign/design_000/AuAu200'
        self.run.mkdir(parents=True)
        for name in ('parameters.dat', 'music_input', 'initial.hdf'):
            (self.run / name).write_text('fixture = 1\n')
        (self.root / 'base.dat').write_text('')
        columns = ('norm200 w k gamma_l_0 gamma_s_0 tau_l tau_s eta_low eta_high '
                   'eta_min zeta_max zeta_width zeta_T zeta_skew').split()
        (self.root / 'design.dat').write_text('# Parameter ' + ' '.join(columns) + '\n'
                                             + ' '.join(['1'] * len(columns)) + '\n')
        self.env = clean_env()
        self.env['CAPTURE_SBATCH'] = str(self.root / 'sbatch.jsonl')
        binary_dir = self.root / 'bin'
        binary_dir.mkdir()
        executable(binary_dir / 'sbatch', '''import json, os, sys
with open(os.environ['CAPTURE_SBATCH'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
''')
        self.env['PATH'] = str(binary_dir) + os.pathsep + self.env['PATH']
        self.launcher = self.root / 'launcher'
        self.launcher.write_text('#!/bin/bash\n')

    def invoke(self, *options, code=0):
        result = subprocess.run([sys.executable, str(ROOT / 'models/run_designs.py'),
                                 '--params', 'base.dat', '--design', 'design.dat',
                                 '--directory', 'campaign', '--system', 'AuAu200',
                                 '--nevents', '1', '--launcher', str(self.launcher), *options],
                                cwd=self.root, env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def submissions(self):
        path = self.root / 'sbatch.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_auto_routes_and_explicit_modes_share_submission_options(self):
        for mode, state, real_mode, restore in (
            ('auto', 'missing', 'both', 'false'),
            ('auto', 'hydro', 'afterburner', 'true'),
            ('both', 'missing', 'both', 'false'),
            ('afterburner', 'hydro', 'afterburner', 'false'),
        ):
            with self.subTest(mode=mode, state=state):
                event = self.run / '0'
                if state == 'hydro':
                    event.mkdir(exist_ok=True)
                    (event / 'hydro.done').touch()
                    (event / 'surface.dat').write_text('surface')
                elif event.exists():
                    for path in event.iterdir():
                        path.unlink()
                    event.rmdir()
                before = len(self.submissions())
                self.invoke('--mode', mode, '--archive-root', 'selected-archive',
                            '--archive-base', str(self.root),
                            '--jobs-per-design', '2', '--submit')
                calls = self.submissions()[before:]
                self.assertEqual(len(calls), 2)
                for job_index, call in enumerate(calls, 1):
                    exported = next(arg for arg in call if arg.startswith('--export='))
                    values = exported[len('--export='):].split(',')
                    self.assertIn(f'MODE={real_mode}', values)
                    self.assertIn(f'RESTORE_ARCHIVES={restore}', values)
                    self.assertIn(f'JOB_INDEX={job_index}', values)
                    self.assertIn(f'HIC_SURFACE_ARCHIVE_ROOT={self.root / "selected-archive"}', values)
                    self.assertIn(f'HIC_SURFACE_ARCHIVE_BASE={self.root}', values)
                    self.assertEqual(call[-1], str(self.launcher))

    def test_preview_and_repair_audit_change_nothing_and_submit_nothing(self):
        event = self.run / '0'
        event.mkdir()
        (event / 'afterburner.lock').mkdir()
        config = self.root / 'site.ini'
        config.write_text('[repair]\napply_repairs=true\nfinalize_archive_failed=true\n')
        for action in ([], ['--repair-only']):
            with self.subTest(action=action):
                self.invoke('--config', str(config), '--mode', 'auto', *action)
                self.assertTrue((event / 'afterburner.lock').is_dir())
                self.assertEqual(self.submissions(), [])
                self.assertTrue((self.run / 'campaign_state.json').is_file())
        self.assertIn('PREVIEW', self.invoke('--mode', 'auto').stdout)
        # Applying repairs clears the dead lock but still submits nothing.
        config.write_text('[repair]\napply_repairs=false\n')
        self.invoke('--config', str(config), '--mode', 'auto', '--repair-only', '--apply')
        self.assertFalse((event / 'afterburner.lock').exists())
        self.assertEqual(self.submissions(), [])

    def test_archive_base_precedence_and_repair_forwarding(self):
        config = self.root / 'archive.ini'
        config.write_text(f'[repair]\narchive_base={self.root}\n')
        self.env['HIC_SURFACE_ARCHIVE_BASE'] = '/wrong/environment'
        result = self.invoke('--config', str(config), '--mode', 'both', '--submit')
        self.assertIn('--archive-base ' + str(self.root), result.stdout)
        self.assertIn('HIC_SURFACE_ARCHIVE_BASE=' + str(self.root), str(self.submissions()[-1]))
        # A CLI value wins even over an invalid INI value.
        config.write_text('[repair]\narchive_base=/wrong/config\n')
        result = self.invoke('--config', str(config), '--archive-base', str(self.run.parent.parent),
                             '--mode', 'both', '--submit')
        self.assertIn('--archive-base ' + str(self.run.parent.parent), result.stdout)
        before = len(self.submissions())
        result = self.invoke('--config', str(config), '--mode', 'both', '--submit', code=2)
        self.assertIn('outside HIC_SURFACE_ARCHIVE_BASE', result.stderr)
        self.assertEqual(len(self.submissions()), before)

    def test_existing_designs_are_refused_before_any_action(self):
        self.invoke('--mode', 'both', '--refuse-existing', '--submit', code=1)
        self.assertEqual(self.submissions(), [])
        self.assertIn('--allow-existing', self.invoke('--mode', 'both', '--refuse-existing').stdout)
        self.invoke('--mode', 'both', '--refuse-existing', '--repair-only')
        self.invoke('--mode', 'both', '--refuse-existing', '--allow-existing', '--submit')
        self.assertEqual(len(self.submissions()), 1)

    def test_new_design_initialization_uses_this_checkouts_helper_chain(self):
        for name in ('parameters.dat', 'music_input', 'initial.hdf'):
            (self.run / name).unlink()
        template = self.root / 'campaign/music_input_template'
        template.write_text('Grid_size_in_x {}\nX_grid_size_in_fm {}\n')
        executable(self.root / 'bin/trento', '''from pathlib import Path
Path('initial.hdf').write_text('stand-in IC output')
''')
        result = self.invoke('--initialize', '--mode', 'both')
        self.assertIn('would initialize', result.stdout)
        self.assertFalse((self.run / 'parameters.dat').exists())
        self.invoke('--mode', 'both', '--initialize-only', '--apply')
        self.assertIn('system = Au Au', (self.run / 'parameters.dat').read_text())
        self.assertEqual((self.run / 'music_input').read_text(),
                         'Grid_size_in_x 300\nX_grid_size_in_fm 30.0\n')
        self.assertEqual((self.run / 'initial.hdf').read_text(), 'stand-in IC output')
        self.assertEqual(self.submissions(), [])

    def test_ready_and_reaped_designs_are_not_submitted(self):
        event = self.run / '0'
        event.mkdir()
        (event / 'hydro.no_surface').touch()
        self.invoke('--mode', 'auto', '--submit')
        self.assertEqual(self.submissions(), [])
        state = self.run / 'campaign_state.json'
        state.unlink()
        (self.run / 'reaped.done').touch()
        self.invoke('--mode', 'auto', '--submit')
        self.assertFalse(state.exists())
        self.assertEqual(self.submissions(), [])


class LauncherTests(unittest.TestCase):
    def test_archive_override_survives_site_defaults_and_bad_path_file_stops_job(self):
        sys.path.insert(0, str(ROOT / 'nersc'))
        self.addCleanup(sys.path.remove, str(ROOT / 'nersc'))
        import configure_user
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / 'pce-eventgen'
            launcher.write_text(configure_user.fill('pce-eventgen', {
                'ACCOUNT': 'fixture', 'SLURM_OUTPUT': root / 'out', 'SLURM_ERROR': root / 'err',
                'QOS': 'debug', 'SUBMIT_TIME': '00:10:00',
                'LAUNCHER_TASKS': 1, 'LAUNCHER_CPUS': 1,
                'PATHS_FILE': root / 'unused.sh'}, quote=True))
            site = root / 'site.sh'
            site.write_text('export HIC_SURFACE_ARCHIVE_ROOT=/site/default\n'
                            'export HIC_SURFACE_ARCHIVE_BASE=/site/source\n')
            executable(root / 'srun', '''import json, os, sys
print(json.dumps(dict(archive=os.getenv('HIC_SURFACE_ARCHIVE_ROOT'), base=os.getenv('HIC_SURFACE_ARCHIVE_BASE'), args=sys.argv[1:])))
''')
            env = clean_env()
            env.update(PATH=str(root) + os.pathsep + env['PATH'],
                       RUNDIR=str(root), LOG_ROOT=str(root / 'logs'),
                       NERSC_HOST='fixture', SLURM_JOB_ID='1', SLURM_NTASKS='1', NEVENTS='1',
                       MODE='afterburner', RESTORE_ARCHIVES='true',
                       HIC_PATHS_FILE=str(site), HIC_SURFACE_ARCHIVE_ROOT='/selected/archive',
                       HIC_SURFACE_ARCHIVE_BASE='/original/scratch')
            env['BASH_FUNC_conda%%'] = '() { [[ "$1" == activate ]]; }'
            env['BASH_FUNC_module%%'] = '() { return 0; }'
            result = subprocess.run(['bash', str(launcher)],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            call = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(call['archive'], '/selected/archive')
            self.assertEqual(call['base'], '/original/scratch')
            self.assertEqual(call['args'].count('--restore-archives'), 1)
            env['HIC_PATHS_FILE'] = str(root / 'missing.sh')
            result = subprocess.run(['bash', str(launcher)],
                                    env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('"archive"', result.stdout)


if __name__ == '__main__':
    unittest.main()
