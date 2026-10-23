cmake_minimum_required(VERSION 3.4)
project(MUSIChydro VERSION 2.0 LANGUAGES CXX)

set(libname "music")
set(exename "MUSIChydro")

# require C++11
set(CMAKE_CXX_STANDARD 11)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(CMAKE_MODULE_PATH ${CMAKE_MODULE_PATH} "${CMAKE_HOME_DIRECTORY}")

set(CMAKE_INSTALL_RPATH "$ORIGIN/../lib")
set(CMAKE_INSTALL_RPATH_USE_LINK_PATH TRUE)

# Require GSL for this project:
find_package(GSL REQUIRED)
include_directories(${GSL_INCLUDE_DIR})

if ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "GNU")
    if (CMAKE_CXX_COMPILER_VERSION VERSION_LESS 6.0)
        message(FATAL_ERROR "Insufficient gcc version")
    endif()
endif()

if ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "AppleClang")
    message("Using Clang compiler without OpenMP parallelization... ")
else ()
    find_package(OpenMP)
    if (OpenMP_FOUND)
        set(CMAKE_CXX_COMPILE_FLAGS ${CMAKE_CXX_COMPILE_FLAGS} ${OpenMP_CXX_FLAGS})
        set(CMAKE_CXX_LINK_FLAGS ${CMAKE_CXX_LINK_FLAGS} ${OpenMP_EXE_LINKER_FLAGS})
    endif ()
endif()

# default build type: Release
if (NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release")
endif()

if(CMAKE_INSTALL_PREFIX_INITIALIZED_TO_DEFAULT)
    set(CMAKE_INSTALL_PREFIX "${CMAKE_HOME_DIRECTORY}/"
        CACHE PATH "Install path prefix, prepended onto install directories."
        FORCE)
endif()

message(STATUS "Build type: ${CMAKE_BUILD_TYPE}")
message(STATUS "Install prefix: ${CMAKE_INSTALL_PREFIX}")

option (KNL "Build executable on KNL" OFF)
option (unittest "Build Unit tests" OFF)

if ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "Intel")
    if (KNL)
        set(CMAKE_CXX_FLAGS "-g -vecabi=cmdtarget -dynamic -debug inline-debug-info -fimf-use-svml -xmic-avx512 -qopt-report=5 -qopt-report-phase:vec -Ofast ${OpenMP_CXX_FLAGS} -std=c++11 -fma -align -finline-functions -DDOCTEST_CONFIG_DISABLE -DNDEBUG")
    else ()
        set(CMAKE_CXX_FLAGS "-g -ipo -qopt-report=5 -qopt-report-phase:vec -Ofast ${OpenMP_CXX_FLAGS} -std=c++11 -fimf-use-svml -fma -align -finline-functions -DDOCTEST_CONFIG_DISABLE -DNDEBUG")
    endif ()
elseif ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "GNU")
    set(CMAKE_CXX_FLAGS "-g -Ofast ${OpenMP_CXX_FLAGS} -std=c++11 -DDOCTEST_CONFIG_DISABLE -DNDEBUG")
elseif ("${CMAKE_CXX_COMPILER_ID}" STREQUAL "AppleClang")
    set(CMAKE_CXX_FLAGS "-g -O3 -std=c++11  -stdlib=libc++ -DDOCTEST_CONFIG_DISABLE -DNDEBUG")
endif()

if (unittest)
    set(CMAKE_CXX_FLAGS "-g ${OpenMP_CXX_FLAGS} -std=c++11 -DDOCTEST_CONFIG_IMPLEMENT_WITH_MAIN")
endif()

string(APPEND CMAKE_CXX_FLAGS " -Wall")

add_subdirectory (src)

if(DEFINED ENV{HIC_EOS_PCE_DIR} AND NOT "$ENV{HIC_EOS_PCE_DIR}" STREQUAL "")
    set(_music_pce_default "$ENV{HIC_EOS_PCE_DIR}")
else()
    set(_music_pce_default "${CMAKE_CURRENT_SOURCE_DIR}/EOS/PCE/strange_30_10000")
endif()
set(MUSIC_PCE_TABLE_DIR "${_music_pce_default}" CACHE PATH "Production PCE directory containing 0.dat through 29.dat")
option(MUSIC_INSTALL_PCE_DATA "Install the selected production PCE dataset" ON)
install(FILES data/pce-strange-30-10000.json DESTINATION share/music-hydro/data)
if(MUSIC_INSTALL_PCE_DATA)
    set(_music_pce_files)
    set(_music_pce_complete TRUE)
    foreach(_index RANGE 0 29)
        set(_table "${MUSIC_PCE_TABLE_DIR}/${_index}.dat")
        if(NOT EXISTS "${_table}")
            set(_music_pce_complete FALSE)
        endif()
        list(APPEND _music_pce_files "${_table}")
    endforeach()
    if(_music_pce_complete)
        install(FILES ${_music_pce_files}
                DESTINATION share/music-hydro/EOS/PCE/strange_30_10000)
    else()
        message(WARNING "Production PCE tables are unavailable in ${MUSIC_PCE_TABLE_DIR}. Building code without installing EOS binaries; provide HIC_EOS_PCE_DIR at runtime or configure MUSIC_PCE_TABLE_DIR.")
    endif()
endif()
