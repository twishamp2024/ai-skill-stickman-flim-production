cmake_minimum_required(VERSION 3.16)
project(kage_engine VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "" FORCE)
endif()

option(KAGE_WITH_GL "Build the OpenGL backend (requires GLFW 3.3+)" ON)
option(KAGE_BUILD_TESTS "Build unit tests" ON)

add_library(kage_core STATIC
  src/json.cpp
  src/geometry.cpp
  src/rig.cpp
  src/scene.cpp
  src/motion.cpp
  src/frame.cpp
  src/raster_soft.cpp
  src/raster_gl.cpp
  src/image.cpp)
target_include_directories(kage_core PUBLIC include)

if(MSVC)
  target_compile_options(kage_core PRIVATE /W4 /fp:precise)
else()
  # -ffp-contract=off: no FMA contraction, keeps software frames reproducible
  # across optimisation levels on the same platform.
  target_compile_options(kage_core PRIVATE -Wall -Wextra -ffp-contract=off)
endif()

set(KAGE_GL_STATUS "disabled")
if(KAGE_WITH_GL)
  find_package(glfw3 3.3 CONFIG QUIET)
  if(NOT glfw3_FOUND)
    find_package(PkgConfig QUIET)
    if(PkgConfig_FOUND)
      pkg_check_modules(GLFW3 QUIET IMPORTED_TARGET glfw3)
    endif()
  endif()
  if(TARGET glfw)
    target_link_libraries(kage_core PUBLIC glfw)
    set(KAGE_GL_STATUS "enabled (glfw3 CMake package)")
  elseif(TARGET PkgConfig::GLFW3)
    target_link_libraries(kage_core PUBLIC PkgConfig::GLFW3)
    set(KAGE_GL_STATUS "enabled (glfw3 via pkg-config)")
  endif()
  if(NOT KAGE_GL_STATUS STREQUAL "disabled")
    target_compile_definitions(kage_core PRIVATE KAGE_HAVE_GLFW)
    if(APPLE)
      target_compile_definitions(kage_core PRIVATE GL_SILENCE_DEPRECATION)
    endif()
  else()
    message(WARNING "GLFW not found: building software rasterizer only")
  endif()
endif()
message(STATUS "Kage OpenGL backend: ${KAGE_GL_STATUS}")

add_executable(kage_render src/main.cpp)
target_link_libraries(kage_render PRIVATE kage_core)

if(KAGE_BUILD_TESTS)
  enable_testing()
  add_executable(kage_tests tests/tests.cpp)
  target_link_libraries(kage_tests PRIVATE kage_core)
  add_test(NAME kage_unit_tests COMMAND kage_tests)
endif()
