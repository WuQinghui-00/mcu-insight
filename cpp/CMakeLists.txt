cmake_minimum_required(VERSION 3.10)
project(fwres CXX)

# C++17：只要标准，不要编译器私有的扩展（-std=c++17 而不是 gnu++17）
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "build type" FORCE)
endif()

add_compile_options(-Wall -Wextra -Wpedantic)

# 核心逻辑编成静态库，后面加 GoogleTest 时直接链接它，不用重新编译一遍解析器
add_library(fwres_core STATIC src/elf_file.cpp src/map_file.cpp src/reporter.cpp src/telemetry.cpp)
target_include_directories(fwres_core PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/include)

add_executable(fwres src/main.cpp)
target_link_libraries(fwres PRIVATE fwres_core)
# ---------- 测试 ----------
option(BUILD_TESTING "构建单元测试" ON)
if(BUILD_TESTING)
  enable_testing()
  find_package(GTest REQUIRED)
  add_executable(fwres_tests tests/test_fwres.cpp)
  target_link_libraries(fwres_tests PRIVATE fwres_core GTest::GTest GTest::Main)
  add_test(NAME fwres_tests COMMAND fwres_tests)
endif()