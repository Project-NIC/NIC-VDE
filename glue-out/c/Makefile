# NIC-GLUE-OUT — C build.  MLA / DMD sources referenced, never vendored.
#   make NIC_MLA=/path NIC_DMD=/path   to retarget (central library later)
#   make SQLITE=1   to compile the real .db path (needs -lsqlite3 or the amalgamation)
NIC_MLA ?= ../../mla
NIC_DMD ?= ../../dmd

CC      ?= cc
CFLAGS  ?= -std=c99 -Wall -Wextra -O2
INC      = -I. -I$(NIC_MLA)/c -I$(NIC_DMD)/c
DEPS     = $(NIC_MLA)/c/nic_mla.c $(NIC_MLA)/c/nic_mla_write.c \
           $(NIC_MLA)/c/hal/nic_mla_hal_posix.c $(NIC_DMD)/c/nic_dmd.c

ifdef SQLITE
CFLAGS  += -DGLUE_WITH_SQLITE
LDLIBS  += -lsqlite3
endif

.PHONY: test export-test reader-test out-test clean
test: export-test reader-test out-test

# dumb exporter — dependency-free (CSV + SQL dump; .db only with SQLITE=1)
export-test:
	$(CC) $(CFLAGS) glue_export_test.c glue_export.c $(LDLIBS) -o glue_export_test
	./glue_export_test

# host load-all reader (.mla -> named CSV/SQL/.db)
reader-test:
	$(CC) $(CFLAGS) $(INC) glue_reader_test.c glue_reader.c glue_export.c $(DEPS) $(LDLIBS) -o glue_reader_test
	./glue_reader_test

# streaming embedded reader
out-test:
	$(CC) $(CFLAGS) $(INC) glue_out_test.c glue_out.c $(DEPS) $(LDLIBS) -o glue_out_test
	./glue_out_test

clean:
	rm -f glue_export_test glue_reader_test glue_out_test
