#!/usr/bin/env python3
"""
cross_check.py  —  byte-exact C↔Python interop check for NIC-MLA (format v1.1)

The C test suite (nic_mla_test.c) writes a container with the WRITE-ONLY C
library and embeds the SCHEMA + STATION tables. This script mounts that file
with the Python reference and verifies that every record and every table
decodes to exactly the values the C side wrote — proving the two
implementations share one byte-identical on-disk format.

Run (from the repo root):
    cc -std=c99 -O2 c/nic_mla_test.c c/nic_mla.c c/nic_mla_write.c \\
       c/hal/nic_mla_hal_posix.c -o /tmp/mlatest
    /tmp/mlatest /tmp/mla_c_out.bin
    python3 c/cross_check.py /tmp/mla_c_out.bin

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "tools"))

from nic_mla import MlaCore, MlaPosixHAL
from mla_schema import mla_read_schema, mla_read_stations, mla_decode_payload, mla_split_station

_passed = _failed = 0


def check(name: str, cond: bool) -> None:
    global _passed, _failed
    if cond:
        _passed += 1; print(f"  PASS  {name}")
    else:
        _failed += 1; print(f"  FAIL  {name}")


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/mla_c_out.bin"
    if not os.path.exists(path):
        print(f"cross_check: file not found: {path}\n"
              f"(run the C test first — it writes the cross-compat file)")
        return 2

    print("NIC-MLA C→Python cross-compat check (v1.1)")
    print("=" * 44)
    with MlaPosixHAL(path) as hal:
        m = MlaCore(hal); m.mount()
        recs = list(m)

        # The C test writes 50 records (write-only), then 1 more after a mount.
        check("record count == 51", m.record_count == 51)

        # Records 0..49: ts=1700000000+i, subsec=i, station=1+i%2,
        # payload = u16(200+i) + u16(500+i)  (temp, hum).
        ok_fields = ok_payload = True
        for i in range(50):
            rec, data = recs[i]
            if rec.timestamp != 1700000000 + i: ok_fields = False
            if rec.subsec != i:                 ok_fields = False
            if rec.station != 1 + i % 2:        ok_fields = False
            if rec.compressed:                  ok_fields = False
            exp = (200 + i).to_bytes(2, "little") + (500 + i).to_bytes(2, "little")
            if data != exp:                     ok_payload = False
        check("record fields (ts/subsec/station/flags) match C", ok_fields)
        check("payloads match C byte-exact", ok_payload)

        # The 51st record (written after the mount): ts=1700000099, station=2.
        last, last_data = recs[50]
        check("post-mount record ts/station match",
              last.timestamp == 1700000099 and last.station == 2)
        check("post-mount payload byte-exact",
              last_data == bytes([0x10, 0x20, 0x30, 0x40]))

        # Self-describing tables embedded by the C side.
        pfx = m._prefix.to_bytes()
        log_f, data_f = mla_read_schema(pfx)
        check("schema decodes (1 log, 2 data)",
              log_f is not None and len(log_f) == 1 and data_f is not None and len(data_f) == 2)
        check("data field names match C",
              data_f is not None and [f.name for f in data_f] == ["temp", "hum"])

        stations = mla_read_stations(pfx)
        check("station table decodes (2 stations)", stations is not None and len(stations) == 2)
        if stations:
            check("station 1 → region 55 / number 25000",
                  mla_split_station(stations[0])[:2] == (55, 25000))

        # End-to-end: decode record 0's payload through the C-written schema.
        if data_f is not None:
            dec = mla_decode_payload(data_f, recs[0][1])
            check("decode_payload via C schema → temp=20.0, hum=50.0",
                  abs(dec[0][2] - 20.0) < 1e-9 and abs(dec[1][2] - 50.0) < 1e-9)

    print("=" * 44)
    total = _passed + _failed
    print(f"Result: {_passed}/{total} PASS  |  {_failed} FAIL")
    if _failed == 0:
        print("C↔Python byte-exact ✓  ★ Viva La Resistánce ★")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
