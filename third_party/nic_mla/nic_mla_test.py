#!/usr/bin/env python3
"""
nic_mla_test.py  —  Tests for the NIC-MLA core

Tests:
  • CRC16 and serialization of the prefix / index record
  • format() → mount() on an empty file
  • append() → mount() → read_record() round-trip
  • Record iteration in the correct order
  • Full-file detection
  • Torn lock write  (lock written wrong — mount skips it)
  • Torn data write  (lock OK, data missing — mount abandons it)
  • NOR sim: splitting writes at page boundaries
  • NOR sim: enforcing AND writes (0→1 raises an error)
  • NOR sim: sector erase
  • MlaCore with the NOR sim HAL (end-to-end on a simulated chip)
  • Emergency index recovery (recover())

Run:
    python3 nic_mla_test.py

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""

import os
import sys
import struct
import time

# Allow importing from the same folder without installation
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# The experimental NOR simulator (frozen) lives in experimental/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "experimental"))
# The schema builder/reader (host-only) lives in tools/
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))

from nic_mla import (
    MlaCore, MlaPosixHAL, MlaPrefix, MlaLog, MlaIndex,
    crc16, CRC_FULL, CRC_NONE, MLA_PREFIX_SIZE, MLA_LOG_REC_SIZE,
    MLA_SCHEMA_MAX, MLA_PREFIX_MAX, MLA_DATA_MAGIC,
    FLAG_LIVE, FLAG_ABANDONED, REC_CHECKPOINT,
)
from mla_schema import SchemaBuilder, read_schema, decode_value, decode_payload
from nic_mla_hal_nor import MlaNorSimHAL
from nic_mla_archive import MlaArchive, query

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
    print(f"\n── {title} {'─' * (60 - len(title))}")

# Temporary file for POSIX tests
_TMP = "/tmp/_nic_mla_test_suite.bin"
_SZ  = 32 * 1024   # 32 KB — small for a fast test


def fresh_posix() -> MlaPosixHAL:
    """Create a new empty MLA file (POSIX HAL)."""
    return MlaPosixHAL.create(_TMP, _SZ)


def fresh_nor() -> MlaNorSimHAL:
    """Create a new NOR sim HAL (32 KB, all 0xFF)."""
    return MlaNorSimHAL(size=_SZ)


# ──────────────────────────────────────────────────────────────────────────────
#  1. CRC16
# ──────────────────────────────────────────────────────────────────────────────

def test_crc():
    section("CRC-16 / CCITT-FALSE")
    check("test vector 0x29B1",
          crc16(b"123456789") == 0x29B1)
    check("empty data (init=0xFFFF)",
          crc16(b"") == 0xFFFF)
    check("single zero byte",
          crc16(b"\x00") == 0xE1F0)
    check("all 0xFF (16 B)",
          crc16(bytes([0xFF]*16)) == 0x6A4B)


# ──────────────────────────────────────────────────────────────────────────────
#  2. Prefix
# ──────────────────────────────────────────────────────────────────────────────

def test_prefix():
    section("Prefix serialize / deserialize")
    p = MlaPrefix(file_size=_SZ, cluster_shift=8, flags=CRC_FULL)
    raw = p.to_bytes()
    check("length 512 B", len(raw) == 512)

    p2 = MlaPrefix.from_bytes(raw)
    check("round-trip magic",      p2.magic      == p.magic)
    check("round-trip file_size",  p2.file_size  == _SZ)
    check("round-trip flags",      p2.flags      == CRC_FULL)
    check("round-trip cluster_sh", p2.cluster_shift == 8)

    # Corrupted CRC
    bad = bytearray(raw)
    bad[42] ^= 0x01
    try:
        MlaPrefix.from_bytes(bytes(bad))
        check("bad CRC detected", False, "ValueError was not raised")
    except ValueError:
        check("bad CRC detected", True)


# ──────────────────────────────────────────────────────────────────────────────
#  3. Index record
# ──────────────────────────────────────────────────────────────────────────────

def test_index():
    section("LOG record (lock) — 24 B")
    rec = MlaLog(timestamp=0xDEADBEEF, offset=0x200,
                 station=7, region=3, seq=99,
                 rec_type=0x12, length=300, kf_back=5)
    raw = rec.to_bytes()
    check("length 24 B", len(raw) == MLA_LOG_REC_SIZE)

    r2, ok = MlaLog.from_bytes(raw)
    check("CRC OK",          ok)
    check("round-trip ts",   r2.timestamp == 0xDEADBEEF)
    check("round-trip off",  r2.offset    == 0x200)
    check("round-trip sta",  r2.station   == 7)
    check("round-trip seq",  r2.seq       == 99)
    check("round-trip type", r2.rec_type  == 0x12)
    check("round-trip len",  r2.length    == 300)   # >255 → verifies the 2 B length
    check("round-trip kf",   r2.kf_back   == 5)
    check("flags LIVE",      r2.flags     == FLAG_LIVE)

    # flags is OUTSIDE the CRC — changing flags must not break the checksum
    raw_mod = bytearray(raw)
    raw_mod[20] = FLAG_ABANDONED   # byte 20 = flags
    _, ok_mod = MlaLog.from_bytes(bytes(raw_mod))
    check("flags outside CRC (abandoned still CRC OK)", ok_mod)

    # Corrupting the timestamp field — the CRC must fail
    raw_bad = bytearray(raw)
    raw_bad[0] ^= 0xFF
    _, ok_bad = MlaLog.from_bytes(bytes(raw_bad))
    check("bad record CRC detected", not ok_bad)

    # A checkpoint is recognized by its rec_type
    cp = MlaLog(timestamp=1, offset=0x500, station=0, region=0,
                rec_type=REC_CHECKPOINT)
    check("is_checkpoint for REC_CHECKPOINT", cp.is_checkpoint)
    check("a regular record is not a checkpoint", not rec.is_checkpoint)


# ──────────────────────────────────────────────────────────────────────────────
#  4. format() + mount() — empty file
# ──────────────────────────────────────────────────────────────────────────────

def test_format_mount_empty():
    section("format() + mount() — empty file")
    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        check("after format: count=0",          mla.record_count == 0)
        check("after format: top=PREFIX_SIZE",  mla._top_ptr == MLA_PREFIX_SIZE)
        check("after format: bot=file_size",    mla._bot_ptr == _SZ)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check("after mount: count=0",           mla2.record_count == 0)
        check("after mount: top=PREFIX_SIZE",   mla2._top_ptr == MLA_PREFIX_SIZE)
        check("after mount: bot=file_size",     mla2._bot_ptr == _SZ)


# ──────────────────────────────────────────────────────────────────────────────
#  5. append() → mount() → read_record() round-trip
# ──────────────────────────────────────────────────────────────────────────────

def test_roundtrip():
    section("append() → mount() → read_record() round-trip")
    ts     = 1_700_000_000
    sta, ch = 5, 2
    payload = bytes(range(32))  # 32 B

    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        mla.append(ts, sta, ch, payload)
        check("after append: count=1",  mla.record_count == 1)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check("after mount: count=1",  mla2.record_count == 1)
        rec, data = mla2.read_record(0)
        check("timestamp",  rec.timestamp == ts)
        check("station",    rec.station   == sta)
        check("region",    rec.region   == ch)
        check("length",     rec.length    == len(payload))
        check("data",       data          == payload)


# ──────────────────────────────────────────────────────────────────────────────
#  6. Multiple records — order and content
# ──────────────────────────────────────────────────────────────────────────────

def test_multiple_records():
    section("Multiple records — order and content")
    N = 10

    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        for i in range(N):
            mla.append(1_000_000 + i, station=1, region=i,
                       data=bytes([i] * (4 + i)))  # varying lengths

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check(f"count = {N}", mla2.record_count == N)

        # Iteration — oldest first
        records = list(mla2)
        check("iteration returns N records", len(records) == N)
        check("order — oldest first",
              all(records[i][0].region == i for i in range(N)))
        check("order — data matches",
              all(records[i][1] == bytes([i] * (4 + i)) for i in range(N)))

        # read_record by index
        for i in range(N):
            r, d = mla2.read_record(i)
            check(f"read_record({i}) data OK", d == bytes([i] * (4 + i)))


# ──────────────────────────────────────────────────────────────────────────────
#  7. Full file
# ──────────────────────────────────────────────────────────────────────────────

def test_full():
    section("Full-file detection")
    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        # Fill with the smallest possible records (1 B data = 5 B block)
        # so is_full detects the boundary correctly
        count = 0
        while not mla.is_full:
            mla.append(0, 1, 0, b"\x00")
            count += 1
        check("at least 1 record fit",   count > 0)
        check("is_full once filled",     mla.is_full)
        try:
            mla.append(0, 1, 0, b"\x00")
            check("append on full raises RuntimeError", False, "did not raise")
        except RuntimeError:
            check("append on full raises RuntimeError", True)


# ──────────────────────────────────────────────────────────────────────────────
#  8. Torn lock write (bad CRC in the index slot)
# ──────────────────────────────────────────────────────────────────────────────

def test_torn_lock():
    section("Torn lock write — mount() skips it")
    ts, payload = 9_999_999, b"\xAA\xBB\xCC\xDD"

    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        mla.append(ts, 1, 0, payload)  # valid record

        # Simulate a torn lock: write 24 B of garbage at the next slot position
        next_slot = mla._bot_ptr - MLA_LOG_REC_SIZE
        hal.write(next_slot, bytes([0xDE, 0xAD] * 12))  # bad CRC
        # (NB: we do NOT adjust top_ptr/bot_ptr in RAM — simulating a crash after the write)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        # Mount must find 2 slots, but the second has a bad CRC → only 1 live
        check("count = 1 (torn lock skipped)", mla2.record_count == 1)
        r, d = mla2.read_record(0)
        check("original record readable", d == payload and r.timestamp == ts)


# ──────────────────────────────────────────────────────────────────────────────
#  9. Torn data write (lock OK, MAGIC missing → mount abandons the record)
# ──────────────────────────────────────────────────────────────────────────────

def test_torn_data():
    section("Torn data write — mount() abandons the lock, top_ptr rewinds")
    good_payload = b"\x11\x22\x33\x44"

    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        mla.append(0, 1, 0, good_payload)  # valid record
        top_after_good = mla._top_ptr

        # Torn: write only the lock (no data) — the data region stays 0xFF
        torn_offset = mla._top_ptr         # where the data should have gone
        torn_len    = 20
        torn_lock = MlaLog(timestamp=1, offset=torn_offset,
                           station=2, region=9, length=torn_len)
        hal.write(mla._bot_ptr - MLA_LOG_REC_SIZE, torn_lock.to_bytes())
        # We do NOT write the data → the region stays 0xFF (torn between lock and data)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check("count = 1 (torn lock abandoned)",    mla2.record_count == 1)
        check("top_ptr rewound to torn_offset",     mla2._top_ptr == torn_offset)
        r, d = mla2.read_record(0)
        check("good record still readable",         d == good_payload)


# ──────────────────────────────────────────────────────────────────────────────
#  10. NOR sim — splitting writes at page boundaries
# ──────────────────────────────────────────────────────────────────────────────

def test_nor_page_split():
    section("NOR sim — splitting writes at page boundaries")
    hal = MlaNorSimHAL(size=_SZ)
    # Write a 200 B block starting 100 B before a page boundary
    # → it must split into 2 Page Program commands
    off  = 156     # page [0,256) → 100 B left until the 256 boundary
    data = bytes(range(200))

    hal.write(off, data)
    check("data across the page boundary read back correctly",
          hal.read(off, 200) == data)
    check("2 Page Program commands were issued",
          hal.stat_page_programs == 2)

    # Verify the byte right after the page boundary matches
    check("byte at 256 matches", hal.read(256, 1) == bytes([data[256 - off]]))


# ──────────────────────────────────────────────────────────────────────────────
#  11. NOR sim — enforcing AND writes (0→1 fails)
# ──────────────────────────────────────────────────────────────────────────────

def test_nor_and_write():
    section("NOR sim — enforcing AND writes (0→1 raises an error)")
    hal = MlaNorSimHAL(size=_SZ)
    hal.write(0, b"\xAA")          # write 0xAA (0b10101010)
    check("write 0xFF→0xAA OK",
          hal.read(0, 1) == b"\xAA")

    # Allowed: 0xAA & 0x88 = 0x88 (only 1→0)
    hal.write(0, b"\x88")
    check("write 0xAA→0x88 (1→0) OK",
          hal.read(0, 1) == b"\x88")

    # Forbidden: 0x88 & 0xFF → bit 0 would go 0→1
    try:
        hal.write(0, b"\xFF")
        check("0→1 attempt raises RuntimeError", False, "did not raise")
    except RuntimeError:
        check("0→1 attempt raises RuntimeError", True)


# ──────────────────────────────────────────────────────────────────────────────
#  12. NOR sim — sector erase
# ──────────────────────────────────────────────────────────────────────────────

def test_nor_sector_erase():
    section("NOR sim — sector erase")
    hal = MlaNorSimHAL(size=_SZ)
    hal.write(4096, bytes([0x00] * 256))  # write 0x00 into the second sector
    check("after writing 0x00 it is 0x00",
          hal.read(4096, 4) == b"\x00\x00\x00\x00")

    hal.sector_erase(4096)
    check("after erase it is 0xFF",
          hal.read(4096, 4) == b"\xff\xff\xff\xff")
    check("erase did not touch the first sector",
          hal.read(0, 4) == b"\xff\xff\xff\xff")


# ──────────────────────────────────────────────────────────────────────────────
#  13. MlaCore with the NOR sim HAL — end-to-end
# ──────────────────────────────────────────────────────────────────────────────

def test_nor_end_to_end():
    section("MlaCore with the NOR sim HAL — end-to-end")
    hal = fresh_nor()
    mla = MlaCore(hal)
    mla.format(file_size=_SZ, cluster_shift=8)  # cluster_shift=8 = 256 B (raw NOR)

    records = [
        (1_100_000 + i, 3, i, bytes([i * 11 % 256] * (10 + i * 7)))
        for i in range(8)
    ]
    for ts, sta, ch, data in records:
        mla.append(ts, sta, ch, data)

    check(f"wrote {len(records)} records", mla.record_count == len(records))
    check("NOR: at least 1 page program occurred",
          hal.stat_page_programs > 0)

    # Simulate a fresh start — new MlaCore, same HAL (in RAM)
    mla2 = MlaCore(hal)
    mla2.mount()
    check("after mount: count OK", mla2.record_count == len(records))

    all_ok = True
    for i, (rec, data) in enumerate(mla2):
        exp_ts, exp_sta, exp_ch, exp_data = records[i]
        if rec.timestamp != exp_ts or rec.region != exp_ch or data != exp_data:
            all_ok = False
    check("all data matches after mount", all_ok)

    # NOR: verify nothing was written over non-erased bytes
    # (MlaNorSimHAL would have caught it as a RuntimeError — reaching here proves OK)
    check("NOR: no 0→1 attempt (otherwise the test would have failed earlier)", True)


# ──────────────────────────────────────────────────────────────────────────────
#  13b. Checkpoint — write + faster mount
# ──────────────────────────────────────────────────────────────────────────────

def test_checkpoint():
    section("Checkpoint — writing across the interval + mount")
    # Small interval (shift=4 → 64) so checkpoints land even for a smaller N
    N = 200
    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ, checkpoint_shift=4)  # 2^4 = 64
        for i in range(N):
            mla.append(1_500_000 + i, station=2, region=i % 7,
                       data=bytes([i & 0xFF] * 3))
        check(f"after writing: count={N}", mla.record_count == N)
        # There must be more physical slots than N (data + checkpoints)
        check("checkpoints took extra slots", mla._n_slots > N)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check(f"after mount: count={N} (checkpoints not counted)",
              mla2.record_count == N)
        # Spot-check several records via read_record
        ok_all = True
        for i in (0, 63, 64, 99, N - 1):
            r, d = mla2.read_record(i)
            if d != bytes([i & 0xFF] * 3) or r.timestamp != 1_500_000 + i:
                ok_all = False
        check("data matches after mount (incl. around checkpoints)", ok_all)
        # Iteration must return exactly N data records (no checkpoints)
        check("iteration returns exactly N data records",
              len(list(mla2)) == N)


# ──────────────────────────────────────────────────────────────────────────────
#  13c. File rotation — MlaArchive
# ──────────────────────────────────────────────────────────────────────────────

def test_rotation():
    section("File rotation — MlaArchive")
    import tempfile, shutil
    d = tempfile.mkdtemp(prefix="_nic_mla_arch_")
    try:
        N = 250
        with MlaArchive(d, file_size=2048, checkpoint_shift=4) as arch:
            for i in range(N):
                arch.append(1_600_000_000 + i, station=1, region=i % 4,
                            data=bytes([i & 0xFF] * 3))

        ro = MlaArchive(d, file_size=2048)
        check("more than one file created (rotation happened)", ro.file_count > 1)
        check(f"{N} records total across files", ro.total_records == N)

        # Order and content across file boundaries
        rows = list(ro)
        ok_order = all(rows[i][0].timestamp == 1_600_000_000 + i for i in range(N))
        ok_data  = all(rows[i][1] == bytes([i & 0xFF] * 3) for i in range(N))
        check("record order across file boundaries matches", ok_order)
        check("data across file boundaries matches",         ok_data)

        # file_seq in the prefix is self-describing (0, 1, 2, …)
        check("file_seq values run 0..n", ro.existing_seqs() == list(range(ro.file_count)))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ──────────────────────────────────────────────────────────────────────────────
#  13d. Host-side query — query()
# ──────────────────────────────────────────────────────────────────────────────

def test_query():
    section("Host-side query — query()")
    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ)
        # 30 records: stations 1/2, channels 0..2, times 1000..1029
        for i in range(30):
            mla.append(1000 + i, station=1 + (i % 2), region=i % 3,
                       data=bytes([i]))

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()

        by_station = list(query(mla2, station=2))
        check("filter station=2", all(r.station == 2 for r, _ in by_station)
              and len(by_station) == 15)

        by_channel = list(query(mla2, region=0))
        check("filter region=0", all(r.region == 0 for r, _ in by_channel)
              and len(by_channel) == 10)

        window = list(query(mla2, time_from=1010, time_to=1019))
        check("filter time window [1010,1019]",
              len(window) == 10 and window[0][0].timestamp == 1010)

        combo = list(query(mla2, station=1, region=0))
        check("combined filter station=1 & region=0",
              all(r.station == 1 and r.region == 0 for r, _ in combo))


# ──────────────────────────────────────────────────────────────────────────────
#  13e. Index region — host-side time/station skip-table
# ──────────────────────────────────────────────────────────────────────────────

def test_index_skiptable():
    section("Index region — accelerated scan()")
    SZ = 256 * 1024
    hal = MlaPosixHAL.create(_TMP, SZ)
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=SZ, index_kb=4, checkpoint_shift=4)  # anchor every 16
        check("data_base shifted past index region", mla._data_base == 512 + 4096)
        for i in range(500):
            mla.append(1000 + i, station=1 + (i % 3), region=0, data=bytes([i & 0xFF]))
        check("anchors were written", mla._idx_n > 0)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        check("mount recovered anchor count", mla2._idx_n > 0)
        check("mount recovered record count", mla2.record_count == 500)

        # accelerated scan() must equal brute-force filtering, every window
        allrecs = [(r.timestamp, r.station) for r, _ in mla2]
        worst = None
        for tf in range(1000, 1500, 23):
            for tt in (tf + 7, tf + 88):
                for sta in (None, 2):
                    got = [(r.timestamp, r.station)
                           for r, _ in mla2.scan(time_from=tf, time_to=tt, station=sta)]
                    exp = [(ts, s) for (ts, s) in allrecs
                           if tf <= ts <= tt and (sta is None or s == sta)]
                    if got != exp:
                        worst = (tf, tt, sta)
        check("scan() == brute force across all windows", worst is None,
              f"first mismatch {worst}")
        check("scan() with no time filter returns everything",
              [(r.timestamp, r.station) for r, _ in mla2.scan()] == allrecs)

        # the index must actually skip ahead (start slot > 0 for a late window)
        check("index seeks past slot 0 for a late window",
              mla2._start_slot_for_time(1400) > 0)

    # index disabled (index_kb=0) must still work as a plain scan
    hal3 = MlaPosixHAL.create(_TMP, _SZ)
    with hal3:
        m = MlaCore(hal3)
        m.format(file_size=_SZ)  # default index_kb=0
        check("index disabled → data_base == prefix size", m._data_base == MLA_PREFIX_SIZE)
        for i in range(20):
            m.append(2000 + i, station=1, region=0, data=bytes([i]))
        got = [r.timestamp for r, _ in m.scan(time_from=2005, time_to=2010)]
        check("scan() works without an index", got == list(range(2005, 2011)))


# ──────────────────────────────────────────────────────────────────────────────
#  14. Emergency recovery — recover()
# ──────────────────────────────────────────────────────────────────────────────

def test_recovery():
    section("Emergency recovery — recover()")
    n_records = 5
    payloads  = [bytes([i * 13 % 256] * (8 + i)) for i in range(n_records)]

    hal = fresh_posix()
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=_SZ, crc_mode=2)  # CRC_FULL
        for i, p in enumerate(payloads):
            mla.append(0, 1, i, p)

        # Simulate index loss: overwrite the entire index region with zeros
        bot = mla._bot_ptr
        zero_len = _SZ - bot
        hal.write(bot, b"\x00" * zero_len)

    with MlaPosixHAL(_TMP) as hal2:
        mla2 = MlaCore(hal2)
        n_recovered = mla2.recover()
        check(f"recover() found {n_records} records", n_recovered == n_records)

        recovered_data = [d for _, d in mla2]
        check("recovered data matches",
              all(recovered_data[i] == payloads[i] for i in range(n_records)))

    # recover() on a file that HAD an index region: stale anchors must be dropped
    # so the post-recovery scan() stays correct (regression guard).
    SZ = 256 * 1024
    hal3 = MlaPosixHAL.create(_TMP, SZ)
    with hal3:
        m = MlaCore(hal3)
        m.format(file_size=SZ, index_kb=4, checkpoint_shift=4, crc_mode=2)
        for i in range(120):
            m.append(5000 + i, station=1, region=0, data=bytes([i & 0xFF] * 4))
        bot = m._bot_ptr
        hal3.write(bot, b"\x00" * (SZ - bot))  # wipe the log only
    with MlaPosixHAL(_TMP) as hal4:
        m2 = MlaCore(hal4)
        n = m2.recover()
        check("recover() with old index region finds all", n == 120)
        check("recover() drops stale anchors (idx_n=0)", m2._idx_n == 0)
        # With anchors dropped, scan() falls back to a full scan and stays correct.
        # (recover() can't restore timestamps — they lived in the wiped log — so
        # it returns ts=0; what matters is data integrity and anchor invalidation.)
        recs = list(m2.scan())
        check("scan() returns all recovered records after recovery", len(recs) == 120)
        check("recovered payloads intact after recovery",
              all(recs[i][1] == bytes([i & 0xFF] * 4) for i in range(120)))


def test_schema():
    section("Self-describing schema — build → embed → read back")

    sb = SchemaBuilder()
    sb.log("datetime").log("station").log("region")
    sb.data("temp_in",  unit="degC", width=2, exp10=-1, signed=True, offset=-15)
    sb.data("humidity", unit="pct",  width=2, exp10=-1)
    sb.data("energy",   unit="kWh",  width=4)
    table = sb.table()

    # A5: the Python prefix carries the table (matches what C writes).
    prefix = MlaPrefix(file_size=_SZ, schema_table=table).to_bytes()
    check("prefix still 512 B (schema fits)", len(prefix) == 512)

    # A1+A2: read it back — v2 carries 8 B field names on the wire now.
    log_fields, data_fields = read_schema(prefix)
    check("read_schema n_log",  log_fields  is not None and len(log_fields)  == 3)
    check("read_schema n_data", data_fields is not None and len(data_fields) == 3)
    orig = sb.log_fields + sb.data_fields
    back = (log_fields or []) + (data_fields or [])
    check("round-trip descriptors equal",
          [f.descriptor() for f in orig] == [f.descriptor() for f in back])
    check("round-trip names recovered",
          [f.name for f in back] == ["datetime", "station", "region",
                                     "temp_in", "humidity", "energy"])
    check("round-trip units decoded",
          [f.unit for f in back] == ["unix_s", "id", "id", "degC", "pct", "kWh"])

    # A3: decode_value honours width / signedness / offset / exp10.
    raw = (250).to_bytes(2, "little", signed=True)        # temp_in raw = 250
    check("decode_value temp_in", abs(decode_value(data_fields[0], raw) - 23.5) < 1e-9)

    # A4: decode_payload splits a packed payload (2 + 2 + 4 = 8 B) and decodes each.
    payload = (raw
               + (550).to_bytes(2, "little")              # humidity raw = 550 → 55.0
               + (1234).to_bytes(4, "little"))            # energy   raw = 1234
    decoded = decode_payload(data_fields, payload)
    check("decode_payload field count", len(decoded) == 3)
    check("decode_payload names/units",
          [(n, u) for n, u, _ in decoded]
          == [("temp_in", "degC"), ("humidity", "pct"), ("energy", "kWh")])
    check("decode_payload values",
          abs(decoded[0][2] - 23.5) < 1e-9 and abs(decoded[1][2] - 55.0) < 1e-9
          and decoded[2][2] == 1234)
    try:
        decode_payload(data_fields, payload + b"\x00")
        check("decode_payload rejects wrong length", False, "no ValueError")
    except ValueError:
        check("decode_payload rejects wrong length", True)

    # format() embeds the schema; mount() recovers the exact bytes.
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal)
        m.format(file_size=_SZ, schema_table=table)
        m.append(1700000000, station=1, region=0, data=payload)
    with MlaPosixHAL(_TMP) as hal2:
        m2 = MlaCore(hal2); m2.mount()
        check("mount() recovers schema bytes", m2._prefix.schema_table == table)
        recs = list(m2)
        lf, df = read_schema(m2._prefix.to_bytes())
        check("end-to-end decode of stored record",
              len(recs) == 1 and decode_payload(df, recs[0][1])[0][2] == 23.5)

    # A file with no schema → (None, None); format stays byte-identical.
    plain = MlaPrefix(file_size=_SZ).to_bytes()
    check("no-schema prefix → (None, None)", read_schema(plain) == (None, None))


def test_schema_extended_prefix():
    section("Schema overflow — prefix grows past 512 B, CRC moves")

    # Build a wide station: enough data fields to overflow the 476 B budget.
    # Each v2 descriptor is 14 B → ~34 fields fill one block; use 60 to force growth.
    sb = SchemaBuilder()
    sb.log("datetime").log("station")
    for i in range(60):
        sb.data(f"s{i:02d}", unit="raw", width=1)
    table = sb.table()
    psize = sb.prefix_size()
    check("table overflows one prefix", len(table) > MLA_SCHEMA_MAX)
    check("prefix grew to a 512 multiple", psize > 512 and psize % 512 == 0)

    prefix = MlaPrefix(file_size=_SZ, schema_table=table).to_bytes()
    check("extended prefix serialized to psize", len(prefix) == psize)
    check("CRC sits at the new end", MlaPrefix.from_bytes(prefix).schema_table == table)
    check("parse_size from first block only",
          MlaPrefix.parse_size(prefix[:512]) == psize)

    # End-to-end: format() with the oversized schema, then mount() reads it back
    # and the index region starts after the (extended) prefix, not at 512.
    hal = MlaPosixHAL.create(_TMP, _SZ)
    with hal:
        m = MlaCore(hal)
        m.format(file_size=_SZ, schema_table=table, index_kb=4)
        check("data_base accounts for extended prefix",
              m._prefix.data_base == psize + 4 * 1024)
        m.append(1700000000, station=1, region=0, data=bytes(range(60)))
    with MlaPosixHAL(_TMP) as hal2:
        m2 = MlaCore(hal2); m2.mount()
        check("mount() of extended-prefix file", m2._prefix.schema_table == table)
        recs = list(m2)
        _, df = read_schema(m2._prefix.to_bytes())
        check("decode wide record",
              len(recs) == 1 and len(decode_payload(df, recs[0][1])) == 60)

    # Hard ceiling: a schema needing more than MLA_PREFIX_MAX (8 sectors / 4 KB)
    # is rejected at build time, not silently grown.
    big = SchemaBuilder()
    for i in range(40):
        big.log(f"l{i:02d}", unit="id", width=2)
    for i in range(255):                      # 255 × 1 B = MLA_DATA_MAX payload
        big.data(f"d{i:03d}", unit="raw", width=1)
    try:
        big.table()
        check("over-4KB schema rejected (builder)", False, "no ValueError")
    except ValueError:
        check("over-4KB schema rejected (builder)", True)
    try:
        MlaPrefix(file_size=_SZ, schema_table=bytes(MLA_PREFIX_MAX))  # absurdly large
        check("over-4KB prefix rejected (MlaPrefix)", False, "no ValueError")
    except ValueError:
        check("over-4KB prefix rejected (MlaPrefix)", True)


# ──────────────────────────────────────────────────────────────────────────────
#  Run all tests
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("NIC-MLA Test Suite")
    print("=" * 65)

    try:
        test_crc()
        test_prefix()
        test_index()
        test_format_mount_empty()
        test_roundtrip()
        test_multiple_records()
        test_full()
        test_torn_lock()
        test_torn_data()
        test_nor_page_split()
        test_nor_and_write()
        test_nor_sector_erase()
        test_nor_end_to_end()
        test_checkpoint()
        test_rotation()
        test_query()
        test_index_skiptable()
        test_recovery()
        test_schema()
        test_schema_extended_prefix()
    finally:
        if os.path.exists(_TMP):
            os.remove(_TMP)

    print("\n" + "=" * 65)
    total = _passed + _failed
    print(f"Result: {_passed}/{total} PASS  |  {_failed} FAIL")
    if _failed == 0:
        print("All OK ✓  ★ Viva La Resistánce ★")
    else:
        print("⚠ Some tests failed — check above")
    sys.exit(0 if _failed == 0 else 1)
