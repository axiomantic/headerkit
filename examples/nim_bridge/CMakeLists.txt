cmake_minimum_required(VERSION 3.18)
project(fastmath_pkg LANGUAGES C)

find_program(NIM_EXECUTABLE nim REQUIRED)

set(NIM_SRC "${CMAKE_CURRENT_SOURCE_DIR}/src/fastmath.nim")
set(NIM_LIB "${CMAKE_CURRENT_BINARY_DIR}/libfastmath${CMAKE_SHARED_LIBRARY_SUFFIX}")

add_custom_command(
    OUTPUT "${NIM_LIB}"
    COMMAND "${NIM_EXECUTABLE}" c --app:lib --mm:orc --threads:on -d:release --out:"${NIM_LIB}" "${NIM_SRC}"
    DEPENDS "${NIM_SRC}"
    COMMENT "Compiling Nim module with ORC memory management"
)

add_custom_target(nim_fastmath ALL DEPENDS "${NIM_LIB}")

# Install shared library into the Python package directory
install(
    FILES "${NIM_LIB}"
    DESTINATION "fastmath"
)
