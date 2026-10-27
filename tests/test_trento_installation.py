"""TRENTo installation refreshes HDF5 paths without reusing legacy build caches."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('cmake') and shutil.which('cc') and shutil.which('c++'),
                     'CMake and C/C++ compilers are required')
class TrentoInstallationTests(unittest.TestCase):
    def test_legacy_cache_is_ignored_and_hdf5_paths_refresh_between_installs(self):
        with tempfile.TemporaryDirectory(prefix='trento HDF5 installation ') as temporary:
            root = Path(temporary)
            source = root / 'models/trento'
            source.mkdir(parents=True)
            (root / 'local').mkdir()
            shutil.copy(ROOT / 'local/install', root / 'local/install')
            for name in ('run-events', 'run-events-wrapper'):
                (root / 'models' / name).write_text('#!/bin/sh\nexit 0\n')
            (root / 'models/iS3D').mkdir()
            (root / 'models/iS3D/iS3D_OSCAR.py').write_text('# fixture converter\n')
            (root / 'config').mkdir()
            (root / 'config/config.yaml').write_text('General: {Nevents: 10}\n')
            legacy = source / 'build/CMakeCache.txt'
            legacy.parent.mkdir()
            legacy.write_text('CMAKE_HOME_DIRECTORY:INTERNAL=/old/checkout/trento\n')
            (source / 'CMakeLists.txt').write_text('''
cmake_minimum_required(VERSION 3.16)
project(trento_fixture LANGUAGES CXX)
find_path(HDF5_INCLUDE_DIRS H5Cpp.h HINTS "${HDF5_ROOT}/include" NO_DEFAULT_PATH)
find_library(HDF5_CXX_LIB hdf5_cpp HINTS "${HDF5_ROOT}/lib" NO_DEFAULT_PATH)
find_library(HDF5_C_LIB hdf5 HINTS "${HDF5_ROOT}/lib" NO_DEFAULT_PATH)
if(NOT HDF5_INCLUDE_DIRS OR NOT HDF5_CXX_LIB OR NOT HDF5_C_LIB)
    message(FATAL_ERROR "HDF5 fixture not found")
endif()
add_executable(trento main.cpp)
target_include_directories(trento PRIVATE "${HDF5_INCLUDE_DIRS}")
target_link_libraries(trento PRIVATE "${HDF5_CXX_LIB}" "${HDF5_C_LIB}")
# Model a compiler wrapper that treats the HDF5 library directory as implicit.
list(APPEND CMAKE_PLATFORM_IMPLICIT_LINK_DIRECTORIES "${HDF5_ROOT}/lib")
install(TARGETS trento DESTINATION bin)
''')
            (source / 'main.cpp').write_text('''
#include <H5Cpp.h>
#include <iostream>
int main() { std::cout << HEADER_LABEL << "|" << fixture_hdf5_version() << "\\n"; }
''')
            prefix = root / 'existing environment'
            (prefix / 'bin').mkdir(parents=True)
            (prefix / 'bin/python').symlink_to(sys.executable)
            env = os.environ.copy()
            for key in ('BASH_ENV', 'ENV', 'CC', 'CXX', 'CFLAGS', 'CXXFLAGS', 'LDFLAGS',
                        'CMAKE_PREFIX_PATH', 'HIC_TRENTO_BUILD_DIR', 'LD_LIBRARY_PATH'):
                env.pop(key, None)
            env.update(CONDA_PREFIX=str(prefix), HIC_BUILD_JOBS='2',
                       PATH=str(prefix / 'bin') + os.pathsep + env['PATH'])
            for label in ('first-HDF5', 'second-HDF5'):
                hdf5 = root / label
                (hdf5 / 'include').mkdir(parents=True)
                (hdf5 / 'lib').mkdir()
                (hdf5 / 'include/H5Cpp.h').write_text(
                    '#define HEADER_LABEL "' + label + '"\n'
                    'extern "C" const char *fixture_hdf5_version(void);\n')
                c_dependency = hdf5 / 'c_library.c'
                c_dependency.write_text('const char *fixture_c_version(void) { return "' + label + '"; }\n')
                subprocess.run(['cc', '-shared', '-fPIC', '-Wl,-soname,libhdf5.so.320',
                                str(c_dependency), '-o', str(hdf5 / 'lib/libhdf5.so.320')],
                               env=env, check=True, capture_output=True)
                (hdf5 / 'lib/libhdf5.so').symlink_to('libhdf5.so.320')
                dependency = hdf5 / 'cpp_library.c'
                dependency.write_text('extern const char *fixture_c_version(void);\n'
                                      'const char *fixture_hdf5_version(void) { return fixture_c_version(); }\n')
                subprocess.run(['cc', '-shared', '-fPIC', '-Wl,-soname,libhdf5_cpp.so.320',
                                str(dependency), '-L' + str(hdf5 / 'lib'), '-lhdf5',
                                '-o', str(hdf5 / 'lib/libhdf5_cpp.so.320')],
                               env=env, check=True, capture_output=True)
                (hdf5 / 'lib/libhdf5_cpp.so').symlink_to('libhdf5_cpp.so.320')
                result = subprocess.run(['bash', str(root / 'local/install'), 'trento'],
                                        env=dict(env, HDF5_ROOT=str(hdf5)), cwd=root,
                                        capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                runtime_env = dict(env)
                runtime_env.pop('HDF5_ROOT', None)
                installed = subprocess.check_output([str(prefix / 'bin/trento')],
                                                    env=runtime_env, cwd=root, text=True)
                self.assertEqual(installed.strip(), label + '|' + label)
                cache = (source / 'build-workflow/CMakeCache.txt').read_text()
                self.assertIn('CMAKE_INSTALL_RPATH:UNINITIALIZED=$ORIGIN/../lib;' +
                              str(hdf5 / 'lib'), cache)
                for variable, path in (('HDF5_INCLUDE_DIRS:PATH', hdf5 / 'include'),
                                       ('HDF5_CXX_LIB:FILEPATH', hdf5 / 'lib/libhdf5_cpp.so'),
                                       ('HDF5_C_LIB:FILEPATH', hdf5 / 'lib/libhdf5.so')):
                    self.assertIn(variable + '=' + str(path), cache)
            self.assertEqual(legacy.read_text(), 'CMAKE_HOME_DIRECTORY:INTERNAL=/old/checkout/trento\n')
            self.assertTrue((prefix / 'bin/run-events-wrapper').is_file())


if __name__ == '__main__':
    unittest.main()
