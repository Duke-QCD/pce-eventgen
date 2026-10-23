cmake_minimum_required(VERSION 3.16)
project(iS3D LANGUAGES CXX)

# Honor CXX/the loaded compiler stack, including NERSC's compiler wrapper.
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()
set(CMAKE_INSTALL_RPATH "$ORIGIN/../lib")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)
# Use CMake's imported GSL targets so external library paths reach the RPATH.
find_package(GSL REQUIRED)
find_package(OpenMP REQUIRED)
include_directories(src/cpp)

# src/cpp is the maintained CPU implementation.
add_subdirectory(src/cpp)

if(NOT IS_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}/deltaf_coefficients/vh/smash_pce_light_strange")
    message(WARNING "The production hrg_eos=6 tables are not in deltaf_coefficients/vh/smash_pce_light_strange (they are kept outside Git). Copy them there and verify with tools/check_physics_data.py is3d before installing, or set HIC_IS3D_DELTAF_DIR to an external copy at run time.")
endif()

option(IS3D_INSTALL_DATA "Install workflow configuration and physics tables" ON)
if(IS3D_INSTALL_DATA)
    install(DIRECTORY PDG tables deltaf_coefficients DESTINATION share/iS3D
            PATTERN "*~" EXCLUDE PATTERN "*_backup*" EXCLUDE)
    install(FILES iS3D_parameters.dat paths.sh DESTINATION share/iS3D)
endif()
