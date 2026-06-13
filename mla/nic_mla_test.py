#!/usr/bin/env python3
"""
nic_mla_test.py  —  Tests for the NIC-MLA core (format v1.1)

Covers:
  • CRC16 and prefix / log-record serialization
  • format() → mount() on an empty file
  • append() → mount() → read round-trip and ordering
  • Full-file detection
  • Torn lock write  (bad-CRC lock — skipped)
  • Torn data write  (lock OK, data MAGIC missing — zeroed on mount)
  • Abandon-by-zeroing (a zeroed record fails the CRC and is skipped)
  • Emergency recovery (recover())
  • File rotation + host-side mla_query (MlaArchive)
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
    mla_crc16, MLA_CRC_FULL, MLA_CRC_NONE, MLA_PREFIX_SIZE, MLA_LOG_REC_SIZE,
    MLA_DATA_MAGIC, MLA_MAX_PREFIX_SEC,
)
from nic_mla_archive import MlaArchive, mla_query
from mla_schema import (
    MlaSchemaBuilder, MlaStationTable, MlaField,
    mla_read_schema, mla_read_stations, mla_decode_value, mla_decode_payload, mla_split_station,
    mla_encode_value, mla_encode_payload,
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
    check("test vector 0x29B1", mla_crc16(b"123456789") == 0x29B1)
    check("empty data (init=0xFFFF)", mla_crc16(b"") == 0xFFFF)
    check("single zero byte", mla_crc16(b"\x00") == 0xE1F0)
    # A zeroed 14 B log body must NOT collide with a stored CRC of 0x0000,
    # otherwise an abandoned (all-zero) record would look valid.
    check("zeroed record fails CRC", mla_crc16(bytes(14)) != 0x0000)


# ──────────────────────────────────────────────────────────────────────────────
#  2. Prefix
# ──────────────────────────────────────────────────────────────────────────────

def test_prefix():
    section("Prefix serialize / deserialize")
    p = MlaPrefix(file_size=_SZ, cluster_shift=8, flags=MLA_CRC_FULL)
    raw = p.to_bytes()
    check("length 512 B", len(raw) == 512)

    p2 = MlaPrefix.from_bytes(raw)
    check("round-trip magic",       p2.magic == p.magic)
    check("round-trip file_size",   p2.file_size == _SZ)
    check("round-trip flags",       p2.flags == MLA_CRC_FULL)
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
    r = MlaLog(offset=1234, timestamp=1700000000, subsec=512, length=28,
               kf_back=3, compressed=True, station=7)
    raw = r.to_bytes()
    check("length 16 B", len(raw) == 16)
    r2, ok = MlaLog.from_bytes(raw)
    check("CRC ok", ok)
    check("round-trip offset",     r2.offset == 1234)
    check("round-trip timestamp",  r2.timestamp == 1700000000)
    check("round-trip subsec",     r2.subsec == 512)
    # subsec is two opaque bytes: 512 = 0x0200 → low byte 0x00, high byte 0x02.
    check("subsec_lo / subsec_hi split", r2.subsec_lo == 0x00 and r2.subsec_hi == 0x02)
    # The two bytes set independently re-combine into the u16 the wire carries.
    rb = MlaLog(offset=0, timestamp=0); rb.subsec_hi = 0xAB; rb.subsec_lo = 0xCD
    check("subsec two-byte compose", rb.subsec == 0xABCD)
    check("round-trip length",     r2.length == 28)
    check("round-trip kf_back",    r2.kf_back == 3)
    check("round-trip compressed", r2.compressed is True)
    check("round-trip station",    r2.station == 7)
    # kf_back and the compressed bit share one byte without colliding.
    r3, _ = MlaLog.from_bytes(MlaLog(offset=0, timestamp=0, kf_back=127,
                                     compressed=False).to_bytes())
    check("kf_back 127, not compressed", r3.kf_back == 127 and r3.compressed is False)

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
        # The log now ends one prefix-size before EOF — that tail holds the
        # mirror prefix (resilience). bot_ptr starts at that log ceiling.
        check("bot_ptr at log ceiling", mla._bot_ptr == _SZ - 512)
        check("region_end reserves mirror", mla._prefix.region_end == _SZ - 512)


def test_roundtrip():
    section("append() → mount() round-trip")
    payloads = [bytes([i] * (3 + i)) for i in range(5)]
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ, crc_mode=MLA_CRC_FULL)
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
        # Slots grow down from the log ceiling (region_end), not from EOF.
        rs = MLA_LOG_REC_SIZE
        slot_addr = mla._prefix.region_end - 2 * rs
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
        # The torn lock must have been zeroed on mount (slot 1, below the ceiling).
        slot = hal.read(m2._prefix.region_end - 2 * MLA_LOG_REC_SIZE, MLA_LOG_REC_SIZE)
        check("torn lock zeroed", slot == bytes(MLA_LOG_REC_SIZE))


def test_abandon_by_zeroing():
    section("Abandon by zeroing (CRC fails → record skipped)")
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        mla.append(1700000000, station=1, data=b"keep")
        mla.append(1700000001, station=2, data=b"drop")
        mla.append(1700000002, station=3, data=b"keep2")
        # Zero out the middle record's lock (slot 1, counting down from the ceiling).
        rs = MLA_LOG_REC_SIZE
        hal.write(mla._prefix.region_end - 2 * rs, bytes(rs))
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
        mla = MlaCore(hal); mla.format(file_size=_SZ, crc_mode=MLA_CRC_FULL)
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
#  7. Rotation + mla_query
# ──────────────────────────────────────────────────────────────────────────────

def test_rotation_and_query():
    section("Rotation (MlaArchive) + host-side mla_query")
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
        check("mla_query by station index",
              sum(1 for _ in mla_query(ro, station=2)) == 75)
        win = list(mla_query(ro, time_from=1_600_000_010, time_to=1_600_000_020))
        check("mla_query by time window", len(win) == 11)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotation_inherits_tables():
    section("Rotation hardening 2a — reopen inherits prefix tables")
    import tempfile, shutil
    from mla_schema import MlaStationTable
    tmp = tempfile.mkdtemp(prefix="nic_mla_arch_")
    try:
        stab = (MlaStationTable().station(region=7, number=42)
                                 .station(region=7, number=43).table())
        # First session: create with a station table, write a little.
        with MlaArchive(tmp, file_size=2048, station_table=stab) as arch:
            for i in range(3):
                arch.append(1_600_000_000 + i, station=1, data=bytes([i] * 3))
        # Reopen WITHOUT re-supplying the table, then write enough to rotate.
        with MlaArchive(tmp, file_size=2048) as arch:
            for i in range(300):
                arch.append(1_600_000_100 + i, station=2, data=bytes([i & 0xFF] * 3))
        # Every rotated file must still carry the station table (not empty).
        ro = MlaArchive(tmp, file_size=2048)
        check("rotation happened", ro.file_count > 1)
        all_have_tables = True
        for seq in ro.existing_seqs():
            with MlaPosixHAL(ro._path(seq)) as hal:
                core = MlaCore(hal); core.mount()
                if core._prefix.station_table != stab:
                    all_have_tables = False
        check("all rotated files inherit station table", all_have_tables)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rotation_keyframe_signal():
    section("Rotation hardening 2b — rotation event surfaced to the glue")
    import tempfile, shutil
    tmp = tempfile.mkdtemp(prefix="nic_mla_arch_")
    try:
        events: list[tuple[int, int]] = []
        with MlaArchive(tmp, file_size=2048,
                        on_rotate=lambda a, b: events.append((a, b))) as arch:
            rotated_flags = []
            for i in range(300):
                # The glue can predict a rotation BEFORE encoding and force a
                # keyframe so the first record of each file is decodable.
                predicted = arch.will_rotate(3)
                landed_new = arch.append(1_600_000_000 + i, station=1,
                                         data=bytes([i & 0xFF] * 3))
                rotated_flags.append((predicted, landed_new))
        check("on_rotate callback fired", len(events) > 0)
        check("callback gives (prev, new) seq",
              all(b == a + 1 for a, b in events))
        # Whenever append reported a rotation, will_rotate predicted it.
        check("will_rotate predicts the rotation",
              all(pred for pred, landed in rotated_flags if landed))
        check("append returns True only on rotation",
              sum(1 for _, landed in rotated_flags if landed) == len(events))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_prefix_mirror_fallback():
    section("Prefix resilience 2c — mount falls back to the tail mirror")
    payloads = [bytes([i] * (3 + i)) for i in range(4)]
    with fresh_posix() as hal:
        mla = MlaCore(hal); mla.format(file_size=_SZ)
        for i, p in enumerate(payloads):
            mla.append(1700000000 + i, station=1, data=p)
    # Trash the PRIMARY prefix (offset 0) — a single bad sector at the head.
    with MlaPosixHAL(_TMP) as hal:
        hal.write(0, bytes([0x00] * MLA_PREFIX_SIZE))
        hal.sync()
    # Primary read must fail; mount recovers via the mirror at the tail.
    with MlaPosixHAL(_TMP) as hal:
        bad = MlaCore(hal)
        primary_dead = False
        try:
            MlaPrefix.from_bytes(hal.read(0, MLA_PREFIX_SIZE))
        except ValueError:
            primary_dead = True
        check("primary prefix is unreadable", primary_dead)
        bad.mount()
        check("mount recovered via mirror", bad.record_count == len(payloads))
        check("data intact through mirror mount",
              [d for _, d in bad] == payloads)


# ──────────────────────────────────────────────────────────────────────────────
#  8. Self-describing schema (names/units for CSV/SQL)
# ──────────────────────────────────────────────────────────────────────────────

def _example_schema() -> MlaSchemaBuilder:
    sb = MlaSchemaBuilder()
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

    log_f, data_f = mla_read_schema(prefix)
    check("mla_read_schema n_log",  log_f is not None and len(log_f) == 1)
    check("mla_read_schema n_data", data_f is not None and len(data_f) == 3)
    check("8-char names recovered",
          [f.name for f in data_f] == ["temp_in", "humidity", "energy"])
    check("units recovered",
          [f.unit for f in data_f] == ["degC", "pct", "kWh"])

    raw = (250).to_bytes(2, "little", signed=True)        # temp_in raw = 250
    check("mla_decode_value temp_in", abs(mla_decode_value(data_f[0], raw) - 23.5) < 1e-9)

    payload = raw + (550).to_bytes(2, "little") + (1234).to_bytes(4, "little")
    decoded = mla_decode_payload(data_f, payload)
    check("mla_decode_payload shape",
          [(n, u) for n, u, _ in decoded]
          == [("temp_in", "degC"), ("humidity", "pct"), ("energy", "kWh")])
    check("mla_decode_payload values",
          abs(decoded[0][2] - 23.5) < 1e-9 and abs(decoded[1][2] - 55.0) < 1e-9
          and decoded[2][2] == 1234)
    try:
        mla_decode_payload(data_f, payload + b"\x00")
        check("mla_decode_payload rejects bad length", False, "no ValueError")
    except ValueError:
        check("mla_decode_payload rejects bad length", True)

    # No-schema file → (None, None)
    plain = MlaPrefix(file_size=_SZ).to_bytes()
    check("no-schema → (None, None)", mla_read_schema(plain) == (None, None))

    # End-to-end through format()/mount()
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal); m.format(file_size=_SZ, schema_table=table)
        m.append(1700000000, station=1, data=payload)
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        check("mount recovers schema", m2._prefix.schema_table == table)
        _, df = mla_read_schema(m2._prefix.to_bytes())
        rec = list(m2)[0]
        check("end-to-end decode", mla_decode_payload(df, rec[1])[0][2] == 23.5)


def test_schema_encode():
    section("Schema encode — pack physical values (inverse of decode)")
    df = _example_schema().table()
    _, data_f = mla_read_schema(MlaPrefix(file_size=_SZ, schema_table=df).to_bytes())

    # temp_in: width2 exp10=-1 signed offset=-15 → physical 23.5 must pack to raw 250
    raw = mla_encode_value(data_f[0], 23.5)
    check("encode temp_in 23.5 → raw 250 (offset applied)",
          int.from_bytes(raw, "little", signed=True) == 250)
    check("encode∘decode round-trips temp_in",
          abs(mla_decode_value(data_f[0], raw) - 23.5) < 1e-9)

    # Full payload round-trip via dict and via sequence.
    vals = {"temp_in": 23.5, "humidity": 55.0, "energy": 1234}
    pay = mla_encode_payload(data_f, vals)
    check("encode_payload width matches schema", len(pay) == sum(f.width for f in data_f))
    dec = dict((n, v) for n, _u, v in mla_decode_payload(data_f, pay))
    check("payload dict round-trips",
          abs(dec["temp_in"] - 23.5) < 1e-9 and abs(dec["humidity"] - 55.0) < 1e-9
          and dec["energy"] == 1234)
    pay2 = mla_encode_payload(data_f, [23.5, 55.0, 1234])
    check("sequence form == dict form", pay2 == pay)

    # Range checks: an out-of-range value is rejected, not silently wrapped.
    try:
        mla_encode_value(MlaField("x", 1, "raw"), 9999)   # u8 max 255
        check("encode rejects overflow", False, "no ValueError")
    except ValueError:
        check("encode rejects overflow", True)
    try:
        mla_encode_payload(data_f, {"temp_in": 1.0})       # missing fields
        check("encode_payload rejects missing field", False)
    except ValueError:
        check("encode_payload rejects missing field", True)

    # End-to-end: encode → append → mount → decode equals what we put in.
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal); m.format(file_size=_SZ, schema_table=df)
        m.append(1700000000, station=1, data=mla_encode_payload(data_f, vals))
    with MlaPosixHAL(_TMP) as hal:
        m2 = MlaCore(hal); m2.mount()
        _, ddf = mla_read_schema(m2._prefix.to_bytes())
        got = dict((n, v) for n, _u, v in mla_decode_payload(ddf, list(m2)[0][1]))
        check("end-to-end encode→store→decode", abs(got["temp_in"] - 23.5) < 1e-9)


# ──────────────────────────────────────────────────────────────────────────────
#  9. Station table (index → raw record)
# ──────────────────────────────────────────────────────────────────────────────

def test_station_table():
    section("Station table — index → station number")
    st = MlaStationTable()
    st.station(region=55, number=25000)    # index 1
    st.station(region=55, number=25001)    # index 2
    st.station(region=55, number=25777)    # index 3 (gap is fine)
    stab = st.table()

    prefix = MlaPrefix(file_size=_SZ, station_table=stab).to_bytes()
    check("prefix 512 B with stations", len(prefix) == 512)

    recs = mla_read_stations(prefix)
    check("mla_read_stations count", recs is not None and len(recs) == 3)
    check("split index 1 → 55/25000", mla_split_station(recs[0])[:2] == (55, 25000))
    check("split index 3 → 55/25777", mla_split_station(recs[2])[:2] == (55, 25777))

    # No station table → None
    plain = MlaPrefix(file_size=_SZ).to_bytes()
    check("no stations → None", mla_read_stations(plain) is None)

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
        stations = mla_read_stations(pfx)
        rec, _ = list(m2)[0]
        region, number, _ = mla_split_station(stations[rec.station - 1])
        check("log index → real station", (region, number) == (55, 25777))
        check("both tables coexist in prefix",
              mla_read_schema(pfx)[1] is not None and stations is not None)


# ──────────────────────────────────────────────────────────────────────────────
#  10. Extended prefix (tables overflow one sector)
# ──────────────────────────────────────────────────────────────────────────────

def test_extended_prefix():
    section("Extended prefix — tables overflow 512 B, CRC moves")
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    for i in range(60):                     # 60 × 14 B ≈ 840 B schema
        sb.data(f"s{i:02d}", unit="raw", width=1)
    schema = sb.table()
    st = MlaStationTable()
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
        _, df = mla_read_schema(m2._prefix.to_bytes())
        rec = list(m2)[0]
        check("decode wide record", len(mla_decode_payload(df, rec[1])) == 60)

    # The 255-sector ceiling is enforced.
    try:
        huge = MlaSchemaBuilder()
        for i in range(255):
            huge.data(f"d{i:03d}", unit="raw", width=1)
        MlaPrefix(file_size=_SZ, schema_table=huge.table(),
                  station_table=MlaStationTable().station(1, 1).table())
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
    print("NIC-MLA Test Suite (format v1.1)")
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
        test_rotation_inherits_tables()
        test_rotation_keyframe_signal()
        test_prefix_mirror_fallback()
        test_schema()
        test_schema_encode()
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
