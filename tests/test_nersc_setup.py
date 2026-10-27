"""Generated user profiles work with the existing dispatcher and batch launcher."""

import configparser
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class NerscSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="new NERSC user's setup ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = self.root / 'profile'
        self.software = self.root / 'software'
        self.data = self.root / 'static-data'
        self.output = self.root / 'durable-output'
        self.raw = self.root / 'scratch/smoke'
        self.command = [sys.executable, str(ROOT / 'nersc/configure_user.py'),
                        '--project', 'new-project', '--software-root', str(self.software),
                        '--data-root', str(self.data), '--output-root', str(self.output),
                        '--raw-root', str(self.raw), '--out-dir', str(self.profile), '--nevents', '2']
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('HIC_', 'BASH_FUNC_'))
                    and key not in ('BASH_ENV', 'ENV', 'PSCRATCH', 'SCRATCH')}

    def generate(self, command=None, code=0):
        result = subprocess.run(command or self.command, env=self.env, cwd=self.root,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result

    def test_generation_is_configuration_only_and_refuses_overwrite(self):
        self.generate()
        self.assertEqual({p.name for p in self.profile.iterdir()},
                         {'nersc_paths.sh', 'calibration.ini', 'activate.sh', 'pce-eventgen'})
        for path in (self.software, self.data, self.output, self.raw):
            self.assertFalse(path.exists())
        originals = {p.name: p.read_bytes() for p in self.profile.iterdir()}
        result = self.generate(code=2)
        self.assertIn('Profile already exists:', result.stderr)
        self.assertIn('No files were changed.', result.stderr)
        self.assertEqual(originals, {p.name: p.read_bytes() for p in self.profile.iterdir()})

    def test_runtime_module_selection_is_saved_in_personal_paths(self):
        for name, inherited, options, expected in (
                ('default', None, [], 'gcc-native/14'),
                ('inherited', 'gcc-native/13', [], 'gcc-native/13'),
                ('explicit', 'gcc-native/13', ['--runtime-gcc-module', 'gcc-native/12'], 'gcc-native/12'),
                ('disabled', 'gcc-native/13', ['--runtime-gcc-module', ''], ''),
                ('inherited-disabled', '', [], '')):
            with self.subTest(selection=name):
                profile = self.root / name
                command = list(self.command)
                command[command.index('--out-dir') + 1] = str(profile)
                env = dict(self.env)
                if inherited is not None:
                    env['HIC_RUNTIME_GCC_MODULE'] = inherited
                result = subprocess.run(command + options, env=env, cwd=self.root,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'GCC runtime module: {expected or "disabled"}', result.stdout)
                # Later login-shell settings cannot replace the saved selection.
                result = subprocess.run(['bash', '-c', '''source "$1" || exit 1
printf '%s' "$HIC_RUNTIME_GCC_MODULE"
''', '--', str(profile / 'nersc_paths.sh')],
                                        env=dict(self.env, HIC_RUNTIME_GCC_MODULE='other-runtime'),
                                        cwd=self.root, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_generated_ini_dispatches_without_original_users_paths(self):
        self.generate()
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.profile / 'calibration.ini')
        self.assertEqual(config['run']['directory'], str(self.raw))
        self.assertEqual(config['files']['launcher'], str(self.profile / 'pce-eventgen'))
        self.assertEqual(config['files']['params'], str(ROOT / 'config/initialization/base_parameters.dat'))
        self.assertEqual(config['pipeline']['tmp_dir'], str(self.raw / '_tmp'))
        self.assertEqual(config['cache']['account'], 'new-project')
        self.assertEqual(config['run']['row_end'], '0')
        self.assertEqual(config['run']['nevents'], '2')
        for section in ('submit', 'cache'):
            self.assertEqual(config[section]['qos'], 'debug')
            self.assertEqual(config[section]['time'], '00:30:00')
        launcher = (self.profile / 'pce-eventgen').read_text()
        self.assertIn('#SBATCH --qos=debug\n', launcher)
        self.assertIn('#SBATCH --time=00:30:00\n', launcher)
        self.assertEqual(config['submit']['ntasks_per_node'], '2')
        self.assertEqual(config['submit']['cpus_per_task'], '32')
        self.assertIn('#SBATCH --ntasks-per-node=2\n', launcher)
        self.assertIn('#SBATCH --cpus-per-task=32\n', launcher)
        self.assertEqual(config['repair']['archive_base'], str(self.raw.parent))
        self.assertEqual(config['pipeline']['cache_manifest'], str(self.output / 'records/smoke/cache.manifest'))
        self.assertEqual(config['pipeline']['reap_manifest'], str(self.output / 'records/smoke/reap_ready.manifest'))
        result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py'), '--config',
                                 str(self.profile / 'calibration.ini'), 'events', '--print-config'],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(str(self.profile / 'pce-eventgen'), result.stdout)
        for text in [p.read_text() for p in self.profile.iterdir()]:
            self.assertNotIn('{{', text)
        self.assertFalse(self.raw.exists())

    def test_production_keeps_its_generation_and_cache_resource_defaults(self):
        command = list(self.command)
        command[command.index('--nevents') + 1] = '10000'
        self.generate(command)
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.profile / 'calibration.ini')
        self.assertEqual(config['submit']['qos'], 'regular')
        self.assertEqual(config['submit']['time'], '00:10:00')
        self.assertEqual(config['cache']['qos'], 'regular')
        self.assertEqual(config['cache']['time'], '04:00:00')
        self.assertEqual(config['submit']['ntasks_per_node'], '16')
        self.assertEqual(config['submit']['cpus_per_task'], '16')
        launcher = (self.profile / 'pce-eventgen').read_text()
        self.assertIn('#SBATCH --qos=regular\n', launcher)
        self.assertIn('#SBATCH --time=00:10:00\n', launcher)

    def test_personal_paths_use_the_canonical_data_layout(self):
        self.generate()
        capture = 'printf "%s\\n" "$HIC_CHARGE_DICT" "$HIC_BARYON_DICT" "$HIC_PDG_MASS" "$HIC_EOS_PCE_DIR"'
        result = subprocess.run(['bash', '-c', 'source "$1" || exit 1\n' + capture,
                                 '--', str(self.profile / 'nersc_paths.sh')],
                                env=self.env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [
            str(self.data / 'iS3D/PDG/charge_dict.dat'), str(self.data / 'iS3D/PDG/baryon_dict.dat'),
            str(self.data / 'iS3D/PDG/pdg_smash_pce.dat'), str(self.data / 'EOS/PCE/strange_30_10000')])

    def test_archive_base_is_saved_once_and_survives_a_new_users_environment(self):
        original_scratch = self.root / 'original-user/scratch'
        raw = original_scratch / 'projectA/campaign'
        self.env.update(PSCRATCH=str(original_scratch), SCRATCH=str(original_scratch))
        command = list(self.command)
        command[command.index('--raw-root') + 1] = str(raw)
        self.generate(command)
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.profile / 'calibration.ini')
        self.assertEqual(config['repair']['archive_base'], str(original_scratch))
        for scratch in ('', str(self.root / 'recipient/scratch')):
            env = dict(self.env, PSCRATCH=scratch, SCRATCH=scratch,
                       HIC_SURFACE_ARCHIVE_BASE='/stale/environment')
            result = subprocess.run(['bash', '-c', '''source "$1" || exit 1
exec "$2" -c 'import sys; sys.path.insert(0, sys.argv[1]); from surface_archive import archive_relative_path; print(archive_relative_path(sys.argv[2]))' "$3" "$4"
''', '--', str(self.profile / 'nersc_paths.sh'), sys.executable, str(ROOT / 'models'),
                                     str(raw / 'design_000/AuAu200/0000')],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(result.stdout.strip(), 'projectA/campaign/design_000/AuAu200/0000')

    def test_existing_campaign_can_select_original_archive_base_explicitly(self):
        self.env.update(PSCRATCH=str(self.root / 'recipient'), SCRATCH='')
        self.generate(self.command + ['--archive-base', str(self.root)])
        config = configparser.ConfigParser(interpolation=None)
        config.read(self.profile / 'calibration.ini')
        self.assertEqual(config['repair']['archive_base'], str(self.root))
        self.assertIn('HIC_SURFACE_ARCHIVE_BASE=' + shlex.quote(str(self.root)),
                      (self.profile / 'nersc_paths.sh').read_text())

    def test_archive_base_outside_campaign_is_rejected_before_profile_creation(self):
        result = self.generate(self.command + ['--archive-base', str(self.root / 'other')], code=2)
        self.assertIn('outside archive base', result.stderr)
        self.assertFalse(self.profile.exists())

    def test_oxygen_initialization_uses_personal_data_or_explicit_table(self):
        self.generate()
        ini = self.profile / 'calibration.ini'
        config = configparser.ConfigParser(interpolation=None)
        config.read(ini)
        config['run']['system'] = 'OO5360'
        config['files']['music_input_template'] = str(self.root / 'music_input_template')
        (self.root / 'music_input_template').write_text('Grid_size_in_x {}\nX_grid_size_in_fm {}\n')
        trento = self.root / 'test-bin/trento'
        trento.parent.mkdir()
        trento.write_text(f'#!{sys.executable}\n' + '''import json, sys
from pathlib import Path
Path('trento.json').write_text(json.dumps(sys.argv[1:]))
Path('initial.hdf').touch()
''')
        trento.chmod(0o755)
        for name, table in (('default', self.data / 'nuclear_tables/o16_nleft_o13.hdf5'),
                            ('override', self.root / 'approved elsewhere/oxygen.h5')):
            with self.subTest(selection=name):
                table.parent.mkdir(parents=True)
                table.touch()
                run_root = self.raw.parent / name
                config['run']['directory'] = str(run_root)
                with ini.open('w') as stream:
                    config.write(stream)
                env = dict(self.env, PATH=str(trento.parent) + os.pathsep + self.env['PATH'])
                if name == 'override':
                    env['HIC_O16_NUCLEUS_TABLE'] = str(table)
                result = subprocess.run(['bash', '-c', '''source "$1" || exit 1
exec "$2" "$3" --config "$4" initialize --apply
''', '--', str(self.profile / 'nersc_paths.sh'), sys.executable,
                                         str(ROOT / 'models/campaign.py'), str(ini)],
                                        cwd=self.root, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                run = run_root / 'design_000/OO5360'
                self.assertEqual(json.loads((run / 'trento.json').read_text())[-3:],
                                 [str(table), str(table), '2'])
                system = next(line.split('=', 1)[1].strip() for line in
                              (run / 'parameters.dat').read_text().splitlines()
                              if line.startswith('system ='))
                self.assertEqual(shlex.split(system), [str(table), str(table)])

    def test_personal_paths_and_direct_batch_launch_select_own_environment(self):
        self.generate(self.command + ['--runtime-gcc-module', 'gcc-native/13'])
        pdg = self.data / 'iS3D/PDG'
        capture = self.root / 'batch.json'
        tools = self.root / 'test-bin'
        tools.mkdir()
        srun = tools / 'srun'
        srun.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
keys = ['CONDA_PREFIX', 'HIC_CODE_HOME', 'HIC_SOFTWARE_ROOT', 'HIC_CFS_ROOT',
        'HIC_SURFACE_ARCHIVE_ROOT', 'HIC_PDG_MASS', 'HIC_RAW_ROOT', 'PYTHONNOUSERSITE',
        'HIC_RUNTIME_GCC_MODULE', 'OMP_NUM_THREADS', 'OMP_PLACES', 'OMP_PROC_BIND',
        'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS']
Path(os.environ['CAPTURE_SETUP']).write_text(json.dumps(dict(args=sys.argv[1:], env={k: os.getenv(k) for k in keys})))
''')
        srun.chmod(0o755)
        env = {**self.env, 'PATH': str(tools) + os.pathsep + self.env['PATH'],
               'RUNDIR': str(self.raw / 'design_000/AuAu200'), 'NERSC_HOST': 'fixture',
               'SLURM_JOB_ID': '123', 'SLURM_NTASKS': '2', 'SLURM_CPUS_PER_TASK': '32',
               'NEVENTS': '2',
               'CAPTURE_SETUP': str(capture),
               'LOG_ROOT': str(self.root / 'event-logs'),
               # This system has CPU/Conda modules, but no GSL module.
               'BASH_FUNC_module%%': '() { [[ "$*" == "load conda" || "$*" == "load cpu" ]]; }',
               'BASH_FUNC_conda%%': '() { [[ $1 == activate ]] && export CONDA_PREFIX="$2"; }'}
        for key in ('OMP_NUM_THREADS', 'OMP_PLACES', 'OMP_PROC_BIND'):
            env.pop(key, None)
        result = subprocess.run(['bash', str(self.profile / 'pce-eventgen')], env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = json.loads(capture.read_text())
        self.assertEqual(actual['env']['CONDA_PREFIX'], str(self.software))
        self.assertEqual(actual['env']['HIC_SOFTWARE_ROOT'], str(self.software))
        self.assertEqual(actual['env']['HIC_CFS_ROOT'], str(self.data))
        self.assertEqual(actual['env']['HIC_PDG_MASS'], str(pdg / 'pdg_smash_pce.dat'))
        self.assertEqual(actual['env']['HIC_SURFACE_ARCHIVE_ROOT'], str(self.output / 'surface_archive'))
        self.assertEqual(actual['env']['HIC_RAW_ROOT'], str(self.raw))
        self.assertEqual(actual['env']['PYTHONNOUSERSITE'], '1')
        self.assertEqual(actual['env']['HIC_RUNTIME_GCC_MODULE'], 'gcc-native/13')
        self.assertEqual(actual['env']['OMP_NUM_THREADS'], '16')
        self.assertEqual(actual['env']['OMP_PLACES'], 'cores')
        self.assertEqual(actual['env']['OMP_PROC_BIND'], 'spread')
        self.assertEqual(actual['env']['OPENBLAS_NUM_THREADS'], '1')
        self.assertEqual(actual['env']['MKL_NUM_THREADS'], '1')
        self.assertIn('--cpu-bind=cores', actual['args'])
        self.assertIn('run-events-wrapper', actual['args'])

        # Allocation sizes control threads even if the submitter's shell is stale.
        for cpus, selected, expected in (('32', '2', '16'), ('2', '64', '1'), ('1', '', '1')):
            override = dict(env, SLURM_CPUS_PER_TASK=cpus, OMP_NUM_THREADS=selected)
            result = subprocess.run(['bash', str(self.profile / 'pce-eventgen')], env=override,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            actual = json.loads(capture.read_text())
            self.assertEqual(actual['env']['OMP_NUM_THREADS'], expected)

    def test_generated_shells_parse_and_unresolved_paths_fail_before_writes(self):
        self.generate()
        for name in ('nersc_paths.sh', 'activate.sh', 'pce-eventgen'):
            result = subprocess.run(['bash', '-n', str(self.profile / name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        command = list(self.command)
        command[command.index('--raw-root') + 1] = '${UNSET_SETUP_SCRATCH}/campaign'
        command[command.index('--out-dir') + 1] = str(self.root / 'bad-profile')
        result = self.generate(command, code=2)
        self.assertIn('Unresolved variable', result.stderr)
        self.assertFalse((self.root / 'bad-profile').exists())

    def test_fill_uses_personal_profile_even_with_another_environment_active(self):
        self.generate(self.command + ['--runtime-gcc-module', ''])
        rundir = self.raw / 'design_000/AuAu200'
        rundir.mkdir(parents=True)
        plan = self.root / 'fill.plan'
        plan.write_text('design_000 1 afterburner false 1 AuAu200\n')
        capture = self.root / 'fill.json'
        tools = self.root / 'test-bin'
        tools.mkdir()
        srun = tools / 'srun'
        srun.write_text(f'#!{sys.executable}\n' + '''import os, sys
args = sys.argv[sys.argv.index('run-events-wrapper'):]
os.execvp(args[0], args)
''')
        srun.chmod(0o755)
        personal_bin = self.software / 'bin'
        personal_bin.mkdir(parents=True)
        wrapper = personal_bin / 'run-events-wrapper'
        wrapper.write_text(f'#!{sys.executable}\n' + '''import json, os, sys
from pathlib import Path
keys = ['CONDA_PREFIX', 'XDG_DATA_HOME', 'HIC_SOFTWARE_ROOT', 'HIC_CFS_ROOT',
        'HIC_RAW_ROOT', 'HIC_SURFACE_ARCHIVE_ROOT', 'PYTHONNOUSERSITE', 'HIC_RUNTIME_GCC_MODULE',
        'OMP_NUM_THREADS', 'OMP_PLACES', 'OMP_PROC_BIND', 'OPENBLAS_NUM_THREADS',
        'REPO', 'CFG', 'PROJECT']
Path(os.environ['CAPTURE_SETUP']).write_text(json.dumps(dict(
    runner=sys.argv[0], args=sys.argv[1:], env={k: os.getenv(k) for k in keys})))
''')
        wrapper.chmod(0o755)
        other = self.root / 'other-environment'
        (other / 'bin').mkdir(parents=True)
        wrong_wrapper = other / 'bin/run-events-wrapper'
        wrong_wrapper.write_text('#!/bin/bash\nexit 9\n')
        wrong_wrapper.chmod(0o755)
        env = {**self.env, 'PATH': str(tools) + os.pathsep + self.env['PATH'],
               'SLURM_JOB_ID': '123', 'CAPTURE_SETUP': str(capture),
               'REPO': '/old-checkout', 'CFG': '/old-profile.ini', 'PROJECT': 'old-account',
               'OMP_NUM_THREADS': '64', 'OPENBLAS_NUM_THREADS': '32',
               'BASH_FUNC_module%%': '() { [[ "$*" == "load conda" || "$*" == "load cpu" ]]; }',
               'BASH_FUNC_conda%%': '''() {
    [[ $1 == activate ]] || return 1
    export CONDA_PREFIX="$2"
    export PATH="$2/bin:$PATH"
}'''}
        result = subprocess.run(['bash', '-c', '''source "$1" || exit 1
export CONDA_PREFIX="$2"
export PATH="$2/bin:$PATH"
exec "$3" "$4" --config "$HIC_CAMPAIGN_CONFIG" fill "$5"
''', '--', str(self.profile / 'activate.sh'), str(other), sys.executable,
                                 str(ROOT / 'models/campaign.py'), str(plan)],
                                env=env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = json.loads(capture.read_text())
        self.assertEqual(actual['runner'], str(wrapper))
        self.assertEqual(actual['env']['CONDA_PREFIX'], str(self.software))
        self.assertEqual(actual['env']['XDG_DATA_HOME'], str(self.software / 'share'))
        self.assertEqual(actual['env']['HIC_SOFTWARE_ROOT'], str(self.software))
        self.assertEqual(actual['env']['HIC_CFS_ROOT'], str(self.data))
        self.assertEqual(actual['env']['HIC_RAW_ROOT'], str(self.raw))
        self.assertEqual(actual['env']['HIC_SURFACE_ARCHIVE_ROOT'], str(self.output / 'surface_archive'))
        self.assertEqual(actual['env']['PYTHONNOUSERSITE'], '1')
        self.assertEqual(actual['env']['HIC_RUNTIME_GCC_MODULE'], '')
        self.assertEqual(actual['env']['OMP_NUM_THREADS'], '16')
        self.assertEqual(actual['env']['OMP_PLACES'], 'cores')
        self.assertEqual(actual['env']['OMP_PROC_BIND'], 'spread')
        self.assertEqual(actual['env']['OPENBLAS_NUM_THREADS'], '1')
        self.assertEqual(actual['env']['REPO'], str(ROOT))
        self.assertEqual(actual['env']['CFG'], str(self.profile / 'calibration.ini'))
        self.assertEqual(actual['env']['PROJECT'], 'new-project')
        self.assertEqual(actual['args'][-1], str(rundir))
        self.assertEqual(actual['args'][actual['args'].index('--nevents') + 1], '2')

        # A failed activation must stop before any Slurm step can use the old env.
        capture.unlink()
        env.update(HIC_PATHS_FILE=str(self.profile / 'nersc_paths.sh'),
                   CONDA_PREFIX=str(other))
        env['BASH_FUNC_conda%%'] = '() { return 7; }'
        result = subprocess.run([sys.executable, str(ROOT / 'models/campaign.py'),
                                 '--config', str(self.profile / 'calibration.ini'), 'fill', str(plan)],
                                env=env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertFalse(capture.exists())

    def test_defaults_use_new_user_project_and_either_scratch_variable(self):
        for variable in ('PSCRATCH', 'SCRATCH'):
            with self.subTest(variable=variable):
                profile = self.root / variable
                env = dict(self.env, LOGNAME='new-user', USER='new-user')
                env.pop('PSCRATCH', None)
                env.pop('SCRATCH', None)
                env[variable] = str(self.root / 'user-scratch')
                result = subprocess.run([sys.executable, str(ROOT / 'nersc/configure_user.py'),
                                         '--project', 'new-project', '--data-root', str(self.data),
                                         '--out-dir', str(profile)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read(profile / 'calibration.ini')
                self.assertEqual(cfg['run']['directory'], str(self.root / 'user-scratch/pce-eventgen'))
                self.assertEqual(cfg['repair']['archive_base'], str(self.root / 'user-scratch'))
                self.assertTrue(cfg['pipeline']['record_dir'].startswith(
                    '/global/cfs/cdirs/new-project/new-user/pce-eventgen/'))
                self.assertIn('/global/common/software/new-project/new-user/pce-eventgen',
                              (profile / 'nersc_paths.sh').read_text())


if __name__ == '__main__':
    unittest.main()
