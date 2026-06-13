cmake_minimum_required(VERSION 3.16)
project(nic-mseed-c C)

#
# NIC-MSEED — C codec core (Steim-1/2 + miniSEED 2.x writer/reader).
#
# Portable C11, no deps; a faithful, byte-exact port of the Python codec core
# (nic_mseed/steim.py + mseed.py). The tests check internal round-trips AND
# compare against reference vectors generated from the Python (test/vectors.h),
# which is itself validated against ObsPy.
#
# Host build + tests:
#   cmake -S c -B c/build && cmake --build c/build
#   ctest --test-dir c/build --output-on-failure
#

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
add_compile_options(-Wall -Wextra -Wpedantic)

add_library(nic-mseed STATIC src/nic_steim.c src/nic_mseed.c)
target_include_directories(nic-mseed PUBLIC include)
# write_stream / btime use libm and time.h — link m where needed
find_library(M_LIB m)
if(M_LIB)
    target_link_libraries(nic-mseed PUBLIC ${M_LIB})
endif()

enable_testing()

add_executable(test_steim test/test_steim.c)
target_link_libraries(test_steim PRIVATE nic-mseed)
target_include_directories(test_steim PRIVATE test)
add_test(NAME test_steim COMMAND test_steim)

add_executable(test_mseed test/test_mseed.c)
target_link_libraries(test_mseed PRIVATE nic-mseed)
target_include_directories(test_mseed PRIVATE test)
add_test(NAME test_mseed COMMAND test_mseed)
