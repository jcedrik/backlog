cmake_minimum_required(VERSION 3.16)
project(backlog LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release)
endif()

option(BACKLOG_BUILD_TOOLS "Build the CLI and the C++ test suite" ON)
option(BACKLOG_BUILD_PYTHON "Build the Python extension module" OFF)
if(SKBUILD)
  # pip install: only the Python module
  set(BACKLOG_BUILD_TOOLS OFF)
  set(BACKLOG_BUILD_PYTHON ON)
endif()

add_library(backlog_core STATIC
  src/graph.cpp
  src/decoder.cpp
  src/reference.cpp
)
target_include_directories(backlog_core PUBLIC include)
if(NOT MSVC)
  target_compile_options(backlog_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(BACKLOG_BUILD_TOOLS)
  add_executable(backlog_decode src/main.cpp)
  target_link_libraries(backlog_decode PRIVATE backlog_core)

  add_executable(backlog_tests tests/test_decoder.cpp)
  target_link_libraries(backlog_tests PRIVATE backlog_core)

  enable_testing()
  add_test(NAME decoder_tests COMMAND backlog_tests)
endif()

if(BACKLOG_BUILD_PYTHON)
  find_package(Python 3.9 COMPONENTS Interpreter Development.Module REQUIRED)
  find_package(pybind11 CONFIG REQUIRED)
  pybind11_add_module(_core bindings/module.cpp)
  target_link_libraries(_core PRIVATE backlog_core)
  install(TARGETS _core LIBRARY DESTINATION backlog)
endif()
