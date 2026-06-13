# NIC-KSF — Makefile
# MIT License — Copyright (c) 2026 NIC — Native Intellect Community

CC      = gcc
CFLAGS  = -std=c99 -Wall -Wextra -Os -Isrc

AVR_CC  = avr-gcc
AVR_MCU = atmega328p
AVR_FLAGS = -std=c99 -mmcu=$(AVR_MCU) -Os -Isrc

.PHONY: all test32 test64 avr32 avr64 clean

all: test32 test64

# --- PC ---
test32: tests/test_ksf.c src/nic_ksf_32.c src/nic_ksf.h
	$(CC) $(CFLAGS) -o test_ksf_32 tests/test_ksf.c src/nic_ksf_32.c
	./test_ksf_32

test64: tests/test_ksf.c src/nic_ksf_64.c src/nic_ksf.h
	$(CC) $(CFLAGS) -o test_ksf_64 tests/test_ksf.c src/nic_ksf_64.c
	./test_ksf_64

# --- AVR ---
avr32: src/nic_ksf_32.c src/nic_ksf.h
	$(AVR_CC) $(AVR_FLAGS) -o nic_ksf_32.elf src/nic_ksf_32.c

avr64: src/nic_ksf_64.c src/nic_ksf.h
	$(AVR_CC) $(AVR_FLAGS) -o nic_ksf_64.elf src/nic_ksf_64.c

clean:
	rm -f test_ksf_32 test_ksf_64 nic_ksf_32.elf nic_ksf_64.elf
