"""Build small native fixtures while changing real dependency and compiler paths."""

import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cmake') and shutil.which('cc') and shutil.which('c++'),
                     'CMake and native compilers are required')
class InstallerCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='installer-cache-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'code with spaces'
        self.write('local/install', (ROOT / 'local/install').read_text()).chmod(0o755)
        for name in ('models/run-events', 'models/run-events-wrapper'):
            self.write(name, '#!/bin/sh\nexit 0\n')
        self.write('models/iS3D/iS3D_OSCAR.py', '# fixture converter\n')
        self.write('config/config.yaml', 'General: {Nevents: 10}\n')
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith(('HIC_', 'BASH_FUNC_'))
                    and key not in ('CONDA_PREFIX', 'VIRTUAL_ENV', 'BASH_ENV', 'ENV',
                                    'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'CPPFLAGS', 'LDFLAGS',
                                    'CMAKE_PREFIX_PATH', 'CMAKE_INCLUDE_PATH', 'CMAKE_LIBRARY_PATH',
                                    'HDF5_ROOT', 'GSL_ROOT_DIR', 'GSL_ROOT', 'PYTHIA8',
                                    'PYTHIA_ROOT_DIR', 'PYTHIA_CONFIG')}
        self.env['HIC_BUILD_JOBS'] = '2'

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def environment(self, name):
        prefix = self.root / name
        (prefix / 'bin').mkdir(parents=True)
        (prefix / 'bin/python').symlink_to(sys.executable)
        return dict(self.env, CONDA_PREFIX=str(prefix),
                    PATH=str(prefix / 'bin') + os.pathsep + self.env['PATH'])

    def dependency(self, name, pythia=False):
        prefix = self.root / name
        (prefix / 'include').mkdir(parents=True)
        (prefix / 'lib').mkdir()
        header = prefix / ('include/Pythia8/Pythia.h' if pythia else 'include/fixture.h')
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text('#define HEADER_LABEL "' + name + '"\n'
                          '#define PYTHIA_VERSION_INTEGER 8309\n'
                          'extern "C" const char *fixture_version(void);\n')
        source = prefix / 'library.c'
        source.write_text('const char *fixture_version(void) { return "' + name + '"; }\n')
        library = prefix / ('lib/libpythia8.so' if pythia else 'lib/libgsl.so')
        subprocess.run(['cc', '-shared', '-fPIC', str(source), '-o', str(library)],
                       env=self.env, check=True, capture_output=True)
        if pythia:
            (prefix / 'share/Pythia8/xmldoc').mkdir(parents=True)
            (prefix / 'share/Pythia8/xmldoc/Version.xml').write_text('fixture XML data\n')
            (prefix / 'bin').mkdir()
            config = prefix / 'bin/pythia8-config'
            config.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(str(prefix)) + '\n')
            config.chmod(0o755)
        return prefix

    def model(self, name, executable, pythia=False):
        source = self.repo / 'models' / name
        finder = '''
find_path(GSL_INCLUDE_DIR fixture.h HINTS "$ENV{GSL_ROOT_DIR}/include" NO_DEFAULT_PATH)
find_library(GSL_LIBRARY gsl HINTS "$ENV{GSL_ROOT_DIR}/lib" NO_DEFAULT_PATH)
set(DEPENDENCY_INCLUDE "${GSL_INCLUDE_DIR}")
set(DEPENDENCY_LIBRARY "${GSL_LIBRARY}")
'''
        if pythia:
            self.write('models/SMASH/cmake/FindPythia.cmake',
                       (ROOT / 'models/SMASH/cmake/FindPythia.cmake').read_text())
            finder = '''
list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
find_package(Pythia 8.309 EXACT REQUIRED)
set(DEPENDENCY_INCLUDE "${Pythia_INCLUDE_DIR}/Pythia8")
set(DEPENDENCY_LIBRARY "${Pythia_LIBRARY}")
'''
        self.write('models/' + name + '/CMakeLists.txt', '''
cmake_minimum_required(VERSION 3.16)
project(installer_cache_fixture LANGUAGES CXX)
''' + finder + '''
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH ON)
add_executable(''' + executable + ''' main.cpp)
target_include_directories(''' + executable + ''' PRIVATE "${DEPENDENCY_INCLUDE}")
target_link_libraries(''' + executable + ''' PRIVATE "${DEPENDENCY_LIBRARY}")
install(TARGETS ''' + executable + ''' DESTINATION bin)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/settings.txt"
     "${MUSIC_PCE_TABLE_DIR}|${MUSIC_INSTALL_PCE_DATA}|${IS3D_INSTALL_DATA}")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/settings.txt" DESTINATION share/''' + name + ''')
''')
        self.write('models/' + name + '/main.cpp', '''
#include <''' + ('Pythia.h' if pythia else 'fixture.h') + '''>
#include <iostream>
#ifndef BUILD_NUMBER
#define BUILD_NUMBER 0
#endif
int main() { std::cout << HEADER_LABEL << "|" << fixture_version() << "|" << BUILD_NUMBER << "\\n"; }
''')
        return source

    def install(self, model, env, *options):
        command = ['bash', str(self.repo / 'local/install'), model]
        if options:
            command += ['--', *options]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def output(self, env, executable):
        return subprocess.check_output([str(Path(env['CONDA_PREFIX']) / 'bin' / executable)],
                                       env=env, text=True).strip()

    def test_music_and_is3d_refresh_dependencies_compiler_prefix_and_moved_source(self):
        first = self.dependency('first GSL')
        second = self.dependency('second GSL')
        first_env = dict(self.environment('first environment'), GSL_ROOT_DIR=str(first),
                         CXXFLAGS='-DBUILD_NUMBER=1')
        wrapper = self.root / 'chosen-cxx'
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(shutil.which('c++')) + ' "$@"\n')
        wrapper.chmod(0o755)
        second_env = dict(self.environment('second environment'), GSL_ROOT_DIR=str(second),
                          CXX=str(wrapper), CXXFLAGS='-DBUILD_NUMBER=2')
        models = (('music-hydro', 'MUSIChydro'), ('iS3D', 'iS3D'))
        for model, executable in models:
            source = self.model(model, executable)
            build = source / 'build-workflow'
            (build / 'CMakeFiles').mkdir(parents=True)
            (build / 'CMakeCache.txt').write_text('CMAKE_HOME_DIRECTORY:INTERNAL=/old/checkout\n')
            marker = build / 'CMakeFiles/old-object'
            marker.write_text('old generated state')
            kept = build / 'keep-install.log'
            kept.write_text('preserved build log')
            with self.subTest(model=model):
                self.install(model, first_env)
                self.assertFalse(marker.exists())
                self.assertEqual(kept.read_text(), 'preserved build log')
                self.assertEqual(self.output(first_env, executable), 'first GSL|first GSL|1')
                obj = next((build / 'CMakeFiles').rglob('main.cpp.o'))
                old_mtime = obj.stat().st_mtime_ns
                self.install(model, first_env)
                self.assertEqual(obj.stat().st_mtime_ns, old_mtime, 'unchanged builds must stay incremental')
                self.install(model, second_env)
                self.assertEqual(self.output(second_env, executable), 'second GSL|second GSL|2')
                cache = (build / 'CMakeCache.txt').read_text()
                self.assertIn(str(wrapper), cache)
                self.assertNotIn(str(first / 'lib/libgsl.so'), cache)
                # Installing in the new environment does not replace the old executable.
                self.assertEqual(self.output(first_env, executable), 'first GSL|first GSL|1')
        moved = self.root / 'moved source'
        shutil.move(self.repo, moved)
        self.repo = moved
        for model, executable in models:
            self.install(model, second_env)
            self.assertEqual(self.output(second_env, executable), 'second GSL|second GSL|2')
            state = json.loads((self.repo / 'models' / model / 'build-workflow/.pce-install-context.json').read_text())
            self.assertEqual(state['source'], str(self.repo / 'models' / model))

    def test_smash_switches_pythia_config_root_and_environment_over_bundled_copy(self):
        first = self.dependency('first Pythia', pythia=True)
        second = self.dependency('second Pythia', pythia=True)
        source = self.model('SMASH', 'smash', pythia=True)
        bundled = source / 'build-deps/pythia8309'
        shutil.copytree(second, bundled)
        env = self.environment('software')
        self.install('SMASH', env, '-DPythia_CONFIG_EXECUTABLE=' + str(first / 'bin/pythia8-config'))
        self.assertEqual(self.output(env, 'smash'), 'first Pythia|first Pythia|0')
        self.install('SMASH', env, '-DPYTHIA_ROOT_DIR=' + str(second))
        self.assertEqual(self.output(env, 'smash'), 'second Pythia|second Pythia|0')
        cache = (source / 'build-workflow/CMakeCache.txt').read_text()
        self.assertNotIn(str(first / 'lib'), cache)
        self.assertIn('Pythia_XMLDOC_DIR:PATH=' + str(second / 'share/Pythia8/xmldoc'), cache)
        self.install('SMASH', dict(env, PYTHIA_CONFIG=str(first / 'bin/pythia8-config')))
        self.assertEqual(self.output(env, 'smash'), 'first Pythia|first Pythia|0')

    def test_smash_supplies_fixed_width_types_and_retains_environment_flags(self):
        pythia = self.dependency('existing Pythia', pythia=True)
        source = self.model('SMASH', 'smash', pythia=True)
        main = source / 'main.cpp'
        # Use the type before any source includes: it must come from the build,
        # even on compilers whose other standard headers include cstdint today.
        main.write_text('constexpr std::uint32_t code = 211;\n' + main.read_text())
        env = dict(self.environment('software'), PYTHIA_CONFIG=str(pythia / 'bin/pythia8-config'),
                   CXXFLAGS='-DBUILD_NUMBER=7')
        self.install('SMASH', env)
        self.assertEqual(self.output(env, 'smash'), 'existing Pythia|existing Pythia|7')
        cache = (source / 'build-workflow/CMakeCache.txt').read_text()
        self.assertIn('-include cstdint', cache)
        self.assertIn('-DBUILD_NUMBER=7', cache)

    def test_music_data_defaults_reset_and_is3d_data_installation_is_enabled(self):
        dependency = self.dependency('GSL')
        env = dict(self.environment('software'), GSL_ROOT_DIR=str(dependency))
        music = self.model('music-hydro', 'MUSIChydro')
        is3d = self.model('iS3D', 'iS3D')
        selected = dict(env, HIC_EOS_PCE_DIR=str(self.root / 'external EOS'),
                        HIC_MUSIC_INSTALL_PCE_DATA='OFF')
        self.install('music-hydro', selected)
        settings = music / 'build-workflow/settings.txt'
        self.assertEqual(settings.read_text(), str(self.root / 'external EOS') + '|OFF|')
        self.install('music-hydro', env)
        self.assertEqual(settings.read_text(), str(music / 'EOS/PCE/strange_30_10000') + '|ON|')
        stale = is3d / 'build-workflow/CMakeCache.txt'
        stale.parent.mkdir(parents=True)
        stale.write_text('IS3D_INSTALL_DATA:BOOL=OFF\n')
        self.install('iS3D', env)
        self.assertEqual((is3d / 'build-workflow/settings.txt').read_text(), '||ON')


if __name__ == '__main__':
    unittest.main()
