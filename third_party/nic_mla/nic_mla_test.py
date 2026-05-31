#!/usr/bin/env python3
"""
nic_mla_test.py  —  Tests for the NIC-MLA core (format v1.0)

Covers:
  • CRC16 and prefix / log-record serialization
  • format() → mount() on an empty file
  • append() → mount() → read round-trip and ordering
  • Full-file detection
  • Torn lock write  (bad-CRC lock — skipped)
  • Torn data write  (lock OK, data MAGIC missing — zeroed on mount)
  • Abandon-by-zeroing (a zeroed record fails the CRC and is skipped)
  • Emergency recovery (recover())
  • File rotation + host-side query (MlaArchive)
  • Self-describing SCHEMA table (names/units → CSV/SQL) round-trip
  • STATION table (index → raw record) round-trip
  • Extended prefix (tables overflow one 512 B sector; CRC moves)

Run:  python3 nic_mla_test.py

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""

import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

from nic_mla import (
    MlaCore, MlaPosixHAL, MlaPrefix, MlaLog,
    crc16, CRC_FULL, CRC_NONE, MLA_PREFIX_SIZE, MLA_LOG_REC_SIZE,
    MLA_DATA_MAGIC, MLA_MAX_PREFIX_SEC,
)
from nic_mla_archive import MlaArchive, query
from mla_schema import (
    SchemaBuilder, StationTable,
    read_schema, read_stations, decode_value, decode_payload, split_station,
)

# ──────────────────────────────────────────────────────────────────────────────
#  Test infrastructure
# ──────────────────────────────────────────────────────────────────────────────

_passed = 0
_failed = 0

def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  PASS  {name}")
    else:
        _failed += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))

def section(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 56 - len(title))}")

_TMP = "/tmp/_nic_mla_test_suite.bin"
_SZ  = 32 * 1024

def fresh_posix() -> MlaPosixHAL:
    return MlaPosixHAL.create(_TMP, _SZ)


# ──────────────────────────────────────────────────────────────────────────────
#  1. CRC16
# ──────────────────────────────────────────────────────────────────────────────

def test_crc():
    section("CRC-16 / CCITT-FALSE")
    check("test vector 0x29B1", crc16(b"123456789") == 0x29B1)
    check("empty data (init=0xFFFF)", crc16(b"") == 0xFFFF)
    check("single zero byte", crc16(b"\x00") == 0xE1F0)
    # A zeroed 14 B log body must NOT collide with a stored CRC of 0x0000,
    # otherwise an abandoned (all-zero) record would look valid.
    check("zeroed record fails CRC", crc16(bytes(14)) != 0x0000)


# ──────────────────────────────────────────────────────────────────────────────
#  2. Prefix
# ──────────────────────────────────────────────────────────────────────────────

def test_prefix():
    section("Prefix serialize / deserialize")
    p = MlaPrefix(file_size=_SZ, cluster_shift=8, flags=CRC_FULL)
    raw = p.to_bytes()
    check("length 512 B", len(raw) == 512)

    p2 = MlaPrefix.from_bytes(raw)
    check("round-trip magic",       p2.magic == p.magic)
    check("round-trip file_size",   p2.file_size == _SZ)
    check("round-trip flags",       p2.flags == CRC_FULL)
    check("round-trip cluster_sh",  p2.cluster_shift == 8)
    check("data_base == prefix",    p2.data_base == 512)

    bad = bytearray(raw)
    bad[42] ^= 0x01
    try:
        MlaPrefix.from_bytes(bytes(bad))
        check("bad CRC detected", False, "no ValueError")
    except ValueError:
        check("bad CRC detected", True)


# ──────────────────────────────────────────────────────────────────────────────
#  3. Log record
# ──────────────────────────────────────────────────────────────────────────────

def test_log_record():
    section("Log record serialize / deserialize")
    r = MlaLog(offset=1234, timestamp=1700000000, length=28,
               rec_type=0x10, kf_back=3, station=7)
    raw = r.to_bytes()
    check("length 16 B", len(raw) == 16)
    r2, ok = MlaLog.from_bytes(raw)
    check("CRC ok", ok)
    check("round-trip offset",    r2.offset == 1234)
    check("round-trip timestamp", r2.timestamp == 1700000000)
    check("round-trip length",    r2.length == 28)
    check("round-trip rec_type",  r2.rec_type == 0x10)
    check("round-trip kf_back",   r2.kf_back == 3)
    check("round-trip station",   r2.station == 7)

    # Zeroed (abandoned) and fresh (0xFF) slots both fail the CRC.
    _, ok_zero = MlaLog.from_bytes(bytes(16))
    _, ok_ff   = MlaLog.from_bytes(bytes([0xFF] * 16))
    check("zeroed slot invalid", not ok_zero)
    check("0xFF slot invalid",   not ok_ff)


# ──────────────────────────────────────────────────────────────────────────────
#  4. format() / mount()
# ──────────────────────────────────────────────────────────────────────────────

def test_format_mount_empty():
    section("format() then mount() on an empty file")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        check("0 records after format", mla.record_count == 0)
    with MlaPosixHAL(_TMP) as hal:
        mla = MlaCore(hal); mla.mount()
        check("0 records after mount", mla.record_count == 0)
        check("top_ptr at data_base",  mla._top_ptr == 512)
        check("bot_ptr at EOF",        mla._bot_ptr == _SZ)


def test_roundtrip():
    section("append() → mount() round-trip")
    payloads = [bytes([i] * (3 + i)) for i in range(5)]
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ, crc_mode=CRC_FULL)
        for i, p in enumerate(payloads):
            mla.append(1700000000 + i, station=1 + i, data=p)
        check("5 records written", mla.record_count == 5)
    with MlaPosixHAL(_TMP) as hal:
        mla = MlaCore(hal); mla.mount()
        check("5 records after mount", mla.record_count == 5)
        got = list(mla)
        check("data matches",
              all(got[i][1] == payloads[i] for i in range(5)))
        check("order + station preserved",
              [r.station for r, _ in got] == [1, 2, 3, 4, 5])
        check("timestamps preserved",
              [r.timestamp for r, _ in got] == [1700000000 + i for i in range(5)])


def test_multiple_records():
    section("Many records, read by index")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        for i in range(20):
            mla.append(1700000000 + i, station=1, data=bytes([i & 0xFF] * 4))
        rec, data = mla.read_record(10)
        check("read_record(10) ts", rec.timestamp == 1700000010)
        check("read_record(10) data", data == bytes([10] * 4))
        check("len() == 20", len(mla) == 20)


def test_full():
    section("Full-file detection")
    SZ = 2048
    hal = MlaPosixHAL.create(_TMP, SZ)
    with hal:
        mla = MlaCore(hal); mla.format(file_size=SZ)
        n = 0
        try:
            for i in range(10000):
                mla.append(1700000000 + i, station=1, data=bytes(40))
                n += 1
        except RuntimeError:
            pass
        check("stops when full (RuntimeError)", n > 0 and n < 10000)
        # Re-mount and verify all written records survived.
        cnt = mla.record_count
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("mount() recovers all written", m2.record_count == cnt)


# ──────────────────────────────────────────────────────────────────────────────
#  5. Crash-safety
# ──────────────────────────────────────────────────────────────────────────────

def test_torn_lock():
    section("Torn lock write (bad-CRC lock is skipped)")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        mla.append(1700000000, station=1, data=b"\x01\x02\x03")
        # Corrupt the next (unused) slot with a half-written, bad-CRC lock.
        rs = MLA_LOG_REC_SIZE
        slot_addr = _SZ - 2 * rs
        hal.write(slot_addr, bytes([0x11] * rs))   # garbage, CRC won't match
        hal.sync()
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("torn lock ignored, 1 good record", m2.record_count == 1)
        check("good record intact", list(m2)[0][1] == b"\x01\x02\x03")


def test_torn_data():
    section("Torn data write (lock OK, data MAGIC missing → zeroed)")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        mla.append(1700000000, station=1, data=b"\xaa\xbb")
        # Manually write a valid lock whose data block was never written
        # (the data area is still 0xFF — no MAGIC).
        rs = MLA_LOG_REC_SIZE
        top = mla._top_ptr
        slot_addr = mla._bot_ptr - rs
        lock = MlaLog(offset=top, timestamp=1700000001, length=4, station=2)
        hal.write(slot_addr, lock.to_bytes())
        hal.sync()
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("torn data abandoned, 1 good record", m2.record_count == 1)
        # The torn lock must have been zeroed on mount.
        slot = hal.read(_SZ - 2 * MLA_LOG_REC_SIZE, MLA_LOG_REC_SIZE)
        check("torn lock zeroed", slot == bytes(MLA_LOG_REC_SIZE))


def test_abandon_by_zeroing():
    section("Abandon by zeroing (CRC fails → record skipped)")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        mla.append(1700000000, station=1, data=b"keep")
        mla.append(1700000001, station=2, data=b"drop")
        mla.append(1700000002, station=3, data=b"keep2")
        # Zero out the middle record's lock (slot 1).
        rs = MLA_LOG_REC_SIZE
        hal.write(_SZ - 2 * rs, bytes(rs))
        hal.sync()
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        datas = [d for _, d in m2]
        check("zeroed record skipped", b"drop" not in datas)
        check("surviving records intact", datas == [b"keep", b"keep2"])


# ──────────────────────────────────────────────────────────────────────────────
#  6. Recovery
# ──────────────────────────────────────────────────────────────────────────────

def test_recovery():
    section("Emergency recovery — recover()")
    payloads = [bytes([i * 13 % 256] * (8 + i)) for i in range(5)]
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ, crc_mode=CRC_FULL)
        for i, p in enumerate(payloads):
            mla.append(1700000000 + i, station=1, data=p)
        bot = mla._bot_ptr
        hal.write(bot, b"\x00" * (_SZ - bot))   # wipe the entire log
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal)
        n = m2.recover()
        check("recover() found 5", n == 5)
        check("recovered data matches",
              [d for _, d in m2] == payloads)


# ──────────────────────────────────────────────────────────────────────────────
#  7. Rotation + query
# ──────────────────────────────────────────────────────────────────────────────

def test_rotation_and_query():
    section("Rotation (MlaArchive) + host-side query")
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="nic_mla_arch_")
    try:
        with MlaArchive(tmp, file_size=2048) as arch:
            for i in range(300):
                arch.append(1_600_000_000 + i, station=1 + (i % 4),
                            data=bytes([i & 0xFF] * 3))
        ro = MlaArchive(tmp, file_size=2048)
        check("multiple files created", ro.file_count > 1)
        check("all 300 records read", ro.total_records == 300)
        check("query by station index",
              sum(1 for _ in query(ro, station=2)) == 75)
        win = list(query(ro, time_from=1_600_000_010, time_to=1_600_000_020))
        check("query by time window", len(win) == 11)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
#  8. Self-describing schema (names/units for CSV/SQL)
# ──────────────────────────────────────────────────────────────────────────────

def _example_schema() -> SchemaBuilder:
    sb = SchemaBuilder()
    sb.log("datetime")
    sb.data("temp_in",  unit="degC", width=2, exp10=-1, signed=True, offset=-15)
    sb.data("humidity", unit="pct",  width=2, exp10=-1)
    sb.data("energy",   unit="kWh",  width=4)
    return sb


def test_schema():
    section("Schema table — build → embed → read back → decode")
    sb = _example_schema()
    table = sb.table()
    prefix = MlaPrefix(file_size=_SZ, schema_table=table).to_bytes()
    check("prefix still 512 B", len(prefix) == 512)

    log_f, data_f = read_schema(prefix)
    check("read_schema n_log",  log_f is not None and len(log_f) == 1)
    check("read_schema n_data", data_f is not None and len(data_f) == 3)
    check("8-char names recovered",
          [f.name for f in data_f] == ["temp_in", "humidity", "energy"])
    check("units recovered",
          [f.unit for f in data_f] == ["degC", "pct", "kWh"])

    raw = (250).to_bytes(2, "little", signed=True)        # temp_in raw = 250
    check("decode_value temp_in", abs(decode_value(data_f[0], raw) - 23.5) < 1e-9)

    payload = raw + (550).to_bytes(2, "little") + (1234).to_bytes(4, "little")
    decoded = decode_payload(data_f, payload)
    check("decode_payload shape",
          [(n, u) for n, u, _ in decoded]
          == [("temp_in", "degC"), ("humidity", "pct"), ("energy", "kWh")])
    check("decode_payload values",
          abs(decoded[0][2] - 23.5) < 1e-9 and abs(decoded[1][2] - 55.0) < 1e-9
          and decoded[2][2] == 1234)
    try:
        decode_payload(data_f, payload + b"\x00")
        check("decode_payload rejects bad length", False, "no ValueError")
    except ValueError:
        check("decode_payload rejects bad length", True)

    # No-schema file → (None, None)
    plain = MlaPrefix(file_size=_SZ).to_bytes()
    check("no-schema → (None, None)", read_schema(plain) == (None, None))

    # End-to-end through format()/mount()
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal); m.format(file_size=_SZ, schema_table=table)
        m.append(1700000000, station=1, data=payload)
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("mount recovers schema", m2._prefix.schema_table == table)
        _, df = read_schema(m2._prefix.to_bytes())
        rec = list(m2)[0]
        check("end-to-end decode", decode_payload(df, rec[1])[0][2] == 23.5)


# ──────────────────────────────────────────────────────────────────────────────
#  9. Station table (index → raw record)
# ──────────────────────────────────────────────────────────────────────────────

def test_station_table():
    section("Station table — index → station number")
    st = StationTable()
    st.station(region=55, number=25000)    # index 1
    st.station(region=55, number=25001)    # index 2
    st.station(region=55, number=25777)    # index 3 (gap is fine)
    stab = st.table()

    prefix = MlaPrefix(file_size=_SZ, station_table=stab).to_bytes()
    check("prefix 512 B with stations", len(prefix) == 512)

    recs = read_stations(prefix)
    check("read_stations count", recs is not None and len(recs) == 3)
    check("split index 1 → 55/25000", split_station(recs[0])[:2] == (55, 25000))
    check("split index 3 → 55/25777", split_station(recs[2])[:2] == (55, 25777))

    # No station table → None
    plain = MlaPrefix(file_size=_SZ).to_bytes()
    check("no stations → None", read_stations(plain) is None)

    # End-to-end: write with both tables, mount, translate index → number
    schema = _example_schema().table()
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal)
        m.format(file_size=_SZ, schema_table=schema, station_table=stab)
        pay = ((250).to_bytes(2, "little", signed=True)
               + (550).to_bytes(2, "little") + (1).to_bytes(4, "little"))
        m.append(1700000000, station=3, data=pay)
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        pfx = m2._prefix.to_bytes()
        stations = read_stations(pfx)
        rec, _ = list(m2)[0]
        region, number, _ = split_station(stations[rec.station - 1])
        check("log index → real station", (region, number) == (55, 25777))
        check("both tables coexist in prefix",
              read_schema(pfx)[1] is not None and stations is not None)


# ──────────────────────────────────────────────────────────────────────────────
#  10. Extended prefix (tables overflow one sector)
# ──────────────────────────────────────────────────────────────────────────────

def test_extended_prefix():
    section("Extended prefix — tables overflow 512 B, CRC moves")
    sb = SchemaBuilder()
    sb.log("datetime")
    for i in range(60):                     # 60 × 14 B ≈ 840 B schema
        sb.data(f"s{i:02d}", unit="raw", width=1)
    schema = sb.table()
    st = StationTable()
    for i in range(40):
        st.station(region=10, number=1000 + i)
    stab = st.table()

    prefix = MlaPrefix(file_size=_SZ, schema_table=schema, station_table=stab)
    psize = prefix.size
    raw = prefix.to_bytes()
    check("prefix grew past 512 B", psize > 512 and psize % 512 == 0)
    check("serialized to psize", len(raw) == psize)
    check("parse_size from full prefix", MlaPrefix.parse_size(raw) == psize)
    p2 = MlaPrefix.from_bytes(raw)
    check("CRC at new end verifies", p2.schema_table == schema)
    check("station table recovered", p2.station_table == stab)

    # End-to-end format()/mount() with the oversized prefix.
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal)
        m.format(file_size=_SZ, schema_table=schema, station_table=stab)
        check("data starts after extended prefix", m._top_ptr == psize)
        m.append(1700000000, station=5, data=bytes(60))
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("mount of extended-prefix file", m2._prefix.schema_table == schema)
        _, df = read_schema(m2._prefix.to_bytes())
        rec = list(m2)[0]
        check("decode wide record", len(decode_payload(df, rec[1])) == 60)

    # The 255-sector ceiling is enforced.
    try:
        huge = SchemaBuilder()
        for i in range(255):
            huge.data(f"d{i:03d}", unit="raw", width=1)
        MlaPrefix(file_size=_SZ, schema_table=huge.table(),
                  station_table=StationTable().station(1, 1).table())
        # 255 × 14 ≈ 3.6 KB → 8 sectors, fine; force the ceiling explicitly:
        prefix_byte_len_check = MLA_MAX_PREFIX_SEC * 512 + 1
        from nic_mla import _prefix_byte_len
        _prefix_byte_len(prefix_byte_len_check, 0)
        check("ceiling enforced", False, "no ValueError")
    except ValueError:
        check("ceiling enforced", True)


# ──────────────────────────────────────────────────────────────────────────────
#  Run all
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NIC-MLA Test Suite (format v1.0)")
    print("=" * 60)
    try:
        test_crc()
        test_prefix()
        test_log_record()
        test_format_mount_empty()
        test_roundtrip()
        test_multiple_records()
        test_full()
        test_torn_lock()
        test_torn_data()
        test_abandon_by_zeroing()
        test_recovery()
        test_rotation_and_query()
        test_schema()
        test_station_table()
        test_extended_prefix()
    finally:
        if os.path.exists(_TMP):
            os.remove(_TMP)

    print("\n" + "=" * 60)
    total = _passed + _failed
    print(f"Result: {_passed}/{total} PASS  |  {_failed} FAIL")
    if _failed == 0:
        print("All OK ✓  ★ Viva La Resistánce ★")
    else:
        print("⚠ Some tests failed — check above")
    sys.exit(0 if _failed == 0 else 1)
