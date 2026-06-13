#!/bin/sh
# Compile-only check for the NIC-MLA C HAL adapters.
#
# The POSIX HAL is exercised by c/nic_mla_test.c, but the FatFs adapter is never
# built in this repo (it needs ChaN FatFs, supplied by the user's project). This
# compiles it against a tiny mock ff.h so CI catches any drift between the
# adapter and the mla_hal_t interface in nic_mla_format.h — without vendoring
# FatFs. Nothing is linked; we stop at the object file.
#
# SdFat (Arduino C++) is intentionally NOT covered here — like the .ino example
# it only builds inside the Arduino/SdFat toolchain, not on a host C compiler.
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"     # c/hal/test
HAL="$DIR/.."                            # c/hal
CC="${CC:-cc}"
FLAGS="-std=c99 -Wall -Wextra"

echo "compile: POSIX HAL"
"$CC" $FLAGS -c "$HAL/nic_mla_hal_posix.c" -o /dev/null

echo "compile: FatFs HAL (against mock ff.h)"
"$CC" $FLAGS -I"$DIR/mock" -c "$HAL/nic_mla_hal_fatfs.c" -o /dev/null

echo "OK: HAL adapters compile cleanly."
