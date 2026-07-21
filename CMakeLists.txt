cmake_minimum_required(VERSION 3.20)
project(flow_simulator_engine VERSION 0.1.0 LANGUAGES CXX)

# The C++ engine ("flow_sim"): a drop-in alternative to flow_designer/sim_runner.py.
# It honours the same CLI + @@-line + run-folder contract, so the flow designer
# can spawn either engine interchangeably.
#
# NOTE: salabim.hpp uses C++20 coroutines for process functions. GCC <=13 hits
# an internal compiler error on them; build with Clang or MSVC.

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release)
endif()

add_executable(flow_sim
    engine/main.cpp
)
target_include_directories(flow_sim PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/salabim++
    ${CMAKE_CURRENT_SOURCE_DIR}/simulation++
    ${CMAKE_CURRENT_SOURCE_DIR}/engine/third_party
)

# Portable, dependency-free binaries so a user can run the file directly.
# CI fills in the per-platform static flags; these are sane local defaults.
if(NOT MSVC)
    target_compile_options(flow_sim PRIVATE -O2)
endif()
