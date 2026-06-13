# NIC-GLUE-IN — C build.  MLA / DMD sources referenced, never vendored.
#   make NIC_MLA=/path NIC_DMD=/path   to retarget (central library later)
#   -DDMD_PKT_MAX_BUILD=N (via CFLAGS)  for fixed-size / no-VLA MCU builds
NIC_MLA ?= ../../mla
NIC_DMD ?= ../../dmd

CC      ?= cc
CFLAGS  ?= -std=c99 -Wall -Wextra -O2
INC      = -I. -I$(NIC_MLA)/c -I$(NIC_DMD)/c
DEPS     = $(NIC_MLA)/c/nic_mla.c $(NIC_MLA)/c/nic_mla_write.c \
           $(NIC_MLA)/c/hal/nic_mla_hal_posix.c $(NIC_DMD)/c/nic_dmd.c
ARCHIVE  = $(NIC_MLA)/c/mla_archive.c

.PHONY: test logger-test archive-test clean
test: logger-test archive-test

logger-test:
	$(CC) $(CFLAGS) $(INC) glue_logger_test.c glue_logger.c $(DEPS) -o glue_logger_test
	./glue_logger_test

archive-test:
	$(CC) $(CFLAGS) $(INC) glue_archive_test.c glue_archive.c $(ARCHIVE) $(DEPS) -o glue_archive_test
	./glue_archive_test

clean:
	rm -f glue_logger_test glue_archive_test
