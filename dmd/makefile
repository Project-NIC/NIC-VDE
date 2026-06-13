CC = gcc
CFLAGS = -Wall -Wextra -O3 -std=c99

CDIR = c
PYDIR = python

TARGET = dmd_test
OBJS = nic_dmd.o nic_dmd_test.o

.PHONY: all clean

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CC) $(CFLAGS) -o $@ $^

nic_dmd.o: $(CDIR)/nic_dmd.c $(CDIR)/nic_dmd.h
	$(CC) $(CFLAGS) -c $< -o $@

nic_dmd_test.o: $(CDIR)/nic_dmd_test.c $(CDIR)/nic_dmd.h
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET) libnic_dmd.a libnic_dmd.so nic_dmd.avr.o

libnic_dmd.a: nic_dmd.o
	ar rcs $@ $^

libnic_dmd.so: $(CDIR)/nic_dmd.c $(CDIR)/nic_dmd.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(CDIR)/nic_dmd.c

test: $(TARGET)
	./$(TARGET)
	python3 $(PYDIR)/nic_dmd_test.py

avr:
	avr-gcc -mmcu=atmega328p -Os -DF_CPU=16000000UL -c $(CDIR)/nic_dmd.c -o nic_dmd.avr.o

install: libnic_dmd.a $(CDIR)/nic_dmd.h
	install -d $(DESTDIR)/usr/local/lib $(DESTDIR)/usr/local/include
	install -m644 libnic_dmd.a $(DESTDIR)/usr/local/lib/
	install -m644 $(CDIR)/nic_dmd.h $(DESTDIR)/usr/local/include/

.PHONY: all clean test avr install
