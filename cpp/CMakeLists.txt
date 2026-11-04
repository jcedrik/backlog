cmake_minimum_required(VERSION 3.16)
project(backlog_decoder LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

add_library(backlog_uf src/union_find_decoder.cpp)
target_include_directories(backlog_uf PUBLIC include)
target_compile_options(backlog_uf PRIVATE -Wall -Wextra -Wpedantic)

add_executable(backlog_decode src/main.cpp)
target_link_libraries(backlog_decode PRIVATE backlog_uf)
target_compile_options(backlog_decode PRIVATE -Wall -Wextra -Wpedantic)
