cmake_minimum_required(VERSION 3.20)
project(salabimpp VERSION 1.0.0 LANGUAGES CXX)

<<<<<<< HEAD
=======
# salabim++ is a single header: this interface target is all you need.
>>>>>>> claude/latest-commit-check-tl51wu
add_library(salabimpp INTERFACE)
add_library(salabimpp::salabimpp ALIAS salabimpp)
target_include_directories(salabimpp INTERFACE ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_features(salabimpp INTERFACE cxx_std_20)

<<<<<<< HEAD
=======
# keep random streams bit-identical to CPython (no fused multiply-add);
# clang is handled in-source via pragmas, gcc needs the flag
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU")
    target_compile_options(salabimpp INTERFACE -ffp-contract=off)
endif()

>>>>>>> claude/latest-commit-check-tl51wu
option(SALABIMPP_BUILD_EXAMPLES "Build salabim++ examples" ON)
option(SALABIMPP_BUILD_TESTS "Build salabim++ tests" ON)

if(SALABIMPP_BUILD_EXAMPLES)
    file(GLOB example_sources ${CMAKE_CURRENT_SOURCE_DIR}/examples/*.cpp)
    foreach(src ${example_sources})
        get_filename_component(name ${src} NAME_WE)
        add_executable(${name} ${src})
        target_link_libraries(${name} PRIVATE salabimpp)
    endforeach()
endif()

if(SALABIMPP_BUILD_TESTS)
    enable_testing()
    file(GLOB test_sources ${CMAKE_CURRENT_SOURCE_DIR}/tests/*.cpp)
    foreach(src ${test_sources})
        get_filename_component(name ${src} NAME_WE)
        add_executable(${name} ${src})
        target_link_libraries(${name} PRIVATE salabimpp)
        add_test(NAME ${name} COMMAND ${name})
    endforeach()
<<<<<<< HEAD
endif()
=======
endif()
>>>>>>> claude/latest-commit-check-tl51wu
