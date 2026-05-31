#!/usr/bin/env python3
"""
NIC-MLA  —  Matroshka Logging Archive
Core  —  Python reference implementation  (format v1.0)

File layout:
  [PREFIX 1..255 sectors][DATA →][   free space 0xFF   ][← LOG][EOF]

The file has a fixed size (1 MB by default), pre-filled with 0xFF.
Data grows from the end of the prefix upward, the log grows from EOF downward.
Full = top_ptr + next_block > bot_ptr - log_rec_size.

Commit protocol: LOG (lock) first, DATA second.
A lock with a matching CRC = a committed record. A torn write leaves either a
bad-CRC lock (ignored) or a lock whose data block has no MAGIC (zeroed on mount).

Design — a deliberately DUMB container:
  • LOG record 16 B: offset, timestamp, length, rec_type, kf_back, station,
    reserved, crc16 — the WHOLE record is covered by the CRC.
  • Abandon = overwrite the 16 B record with zeros; its CRC no longer matches,
    so readers skip it. No "flags outside the CRC" trick.
  • station is a 1-byte INDEX (1..255, 0 = none) into the station table in the
    prefix. The real station/region numbers live in that table; the library
    never interprets them — translation is the host glue's job.
  • Prefix carries two self-describing tables (see tools/mla_schema.py): the
    SCHEMA table (names/units of the fields, for CSV/SQL export) and the
    STATION table (index → 6 raw bytes per station). The prefix grows in whole
    512 B sectors (up to 255) to fit them; the CRC sits in its last 2 bytes.
  • No checkpoints, no on-disk index/search tree. Mount finds the log boundary
    by binary search; recovery is "last CRC bad → zero it and carry on".

Python 3.10+   |   MIT   |   ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

MLA_MAGIC          = b"MLA\x00"        # 4 B — prefix identifier
MLA_DATA_MAGIC     = bytes([0xAB, 0xCD])  # 2 B — sync word of every data block
MLA_VERSION        = 1
MLA_PREFIX_SIZE    = 512               # B — base prefix sector
MLA_MAX_PREFIX_SEC = 255               # hard limit: 255 sectors (~127 KB) — theoretical
MLA_REC_PREFIX_SEC = 16                # recommended ceiling: 16 sectors (8 KB); with the
                                       # auto-sized tables you never get near 255 anyway
MLA_LOG_REC_SIZE   = 16                # B — log record (lock)
MLA_DEFAULT_SIZE   = 1 << 20           # 1 MB — default file size

# Integrity mode — prefix.flags (bits 0–1)
CRC_NONE = 0  # no data CRC — the log CRC is always present
CRC_DATA = 1  # CRC on data only
CRC_FULL = 2  # CRC on both log and data — recommended

# rec_type — low nibble = encoding, high nibble = class
ENC_RAW       = 0x0   # uncompressed
ENC_DELTA     = 0x1   # delta (compressed)
ENC_KEYFRAME  = 0x2   # keyframe (compressed)
ENC_TEXT      = 0x3   # text / JSON

CLASS_MEASURE = 0x00  # measurement
CLASS_EVENT   = 0x10  # event
CLASS_CONFIG  = 0x20  # configuration

# Self-describing tables embedded in the prefix free space, covered by the
# prefix CRC. Built/read by tools/mla_schema.py (host-only).
MLA_SCHEMA_OFF     = 34                # = end of the structured prefix header
MLA_SCHEMA_VER     = 1                 # schema table version
MLA_SCHEMA_FIELD   = 14                # bytes per field descriptor (6 core + 8 name)
MLA_STATION_VER    = 0x53              # station table tag (distinct from schema ver)
MLA_STATION_REC    = 6                 # raw bytes per station (meaning = glue's)


def _schema_byte_len(raw: bytes, off: int = MLA_SCHEMA_OFF) -> int:
    """Length of the schema table at `off` (0 if none/empty)."""
    if len(raw) < off + 3:
        return 0
    if raw[off] != MLA_SCHEMA_VER:                  # 0x00 / 0xFF / other → none
        return 0
    n_log, n_data = raw[off + 1], raw[off + 2]
    return 3 + MLA_SCHEMA_FIELD * (n_log + n_data)


def _station_byte_len(raw: bytes, off: int) -> int:
    """Length of the station table at `off` (0 if none/empty)."""
    if len(raw) < off + 2:
        return 0
    if raw[off] != MLA_STATION_VER:                 # 0x00 / 0xFF / other → none
        return 0
    return 2 + MLA_STATION_REC * raw[off + 1]


def _prefix_byte_len(schema_len: int, station_len: int) -> int:
    """Total prefix size (CRC included) for the given table sizes.

    Normally 512 B; if the tables overflow, the prefix grows in whole 512 B
    sectors (CRC in the last 2 bytes), up to MLA_MAX_PREFIX_SEC sectors.
    """
    need = MLA_SCHEMA_OFF + schema_len + station_len + 2   # header + tables + CRC16
    if need <= MLA_PREFIX_SIZE:
        return MLA_PREFIX_SIZE
    sectors = (need + MLA_PREFIX_SIZE - 1) // MLA_PREFIX_SIZE
    if sectors > MLA_MAX_PREFIX_SEC:
        raise ValueError(
            f"prefix needs {sectors} sectors, exceeds "
            f"MLA_MAX_PREFIX_SEC={MLA_MAX_PREFIX_SEC}"
        )
    return sectors * MLA_PREFIX_SIZE


# ──────────────────────────────────────────────────────────────────────────────
#  CRC-16 / CCITT-FALSE
#  poly=0x1021  init=0xFFFF  refin=False  refout=False  xorout=0x0000
#  Test vector: crc16(b"123456789") == 0x29B1
# ──────────────────────────────────────────────────────────────────────────────

def crc16(data: bytes, init: int = 0xFFFF) -> int:
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc

assert crc16(b"123456789") == 0x29B1, "CRC16 self-test FAILED — check the implementation!"

# ──────────────────────────────────────────────────────────────────────────────
#  Prefix
#
#  Header (34 B, little-endian):
#    [0]   magic[4]         b"MLA\0"
#    [4]   version          1 B   = 1
#    [5]   cluster_shift    1 B   12=4KB FAT/SD; 8=256B; … 15=32KB
#    [6]   log_rec_size     1 B   = 16
#    [7]   flags            1 B   CRC mode (bits 0-1)
#    [8]   file_size        4 B   uint32 LE
#    [12]  reserved8        8 B   reserved (0)
#    [20]  container_kind   1 B   0=single 1=rotation
#    [21]  file_seq         2 B   uint16 LE  file index within a rotation
#    [23]  keyframe_intv    1 B   keyframe interval for compression (8; 0=N/A)
#    [24]  enc_caps         1 B   bitmask of encodings present in the file
#    [25]  data_base        4 B   uint32 LE  = prefix size (first data byte)
#    [29]  region_end       4 B   uint32 LE  = file_size
#    [33]  reserved1        1 B   reserved (0)
#    [34]  SCHEMA table     …     (see tools/mla_schema.py)
#    [..]  STATION table    …
#    [end-2] crc16          2 B   LE  — over everything before it
# ──────────────────────────────────────────────────────────────────────────────

_PFX_FMT1 = "<4sBBBBIQ"   # bytes [0..19]   (20 B)
_PFX_FMT2 = "<BHBBIIB"    # bytes [20..33]  (14 B)

@dataclass
class MlaPrefix:
    magic:          bytes = MLA_MAGIC
    version:        int   = MLA_VERSION
    cluster_shift:  int   = 12
    log_rec_size:   int   = MLA_LOG_REC_SIZE
    flags:          int   = CRC_FULL
    file_size:      int   = MLA_DEFAULT_SIZE
    reserved8:      int   = 0
    container_kind: int   = 0
    file_seq:       int   = 0
    keyframe_intv:  int   = 8
    enc_caps:       int   = 0
    data_base:      int   = 0   # 0 → computed as the prefix size
    region_end:     int   = 0   # 0 → computed as file_size
    reserved1:      int   = 0
    schema_table:   bytes = b""  # field names/units (CSV/SQL export)
    station_table:  bytes = b""  # index → 6 raw bytes per station

    def __post_init__(self):
        if self.data_base == 0:
            self.data_base = self.size
        if self.region_end == 0:
            self.region_end = self.file_size

    @property
    def size(self) -> int:
        """Prefix size in bytes (a 512 B multiple; >512 only for big tables)."""
        return _prefix_byte_len(len(self.schema_table), len(self.station_table))

    @staticmethod
    def parse_size(raw: bytes) -> int:
        """Prefix size implied by the embedded table headers."""
        slen  = _schema_byte_len(raw)
        stlen = _station_byte_len(raw, MLA_SCHEMA_OFF + slen)
        return _prefix_byte_len(slen, stlen)

    def to_bytes(self) -> bytes:
        """Serialize → `size` bytes (header + tables + padding + trailing CRC16)."""
        size = self.size
        buf = bytearray(b"\x00" * (size - 2))
        struct.pack_into(_PFX_FMT1, buf, 0,
                         self.magic, self.version, self.cluster_shift,
                         self.log_rec_size, self.flags,
                         self.file_size, self.reserved8)
        struct.pack_into(_PFX_FMT2, buf, 20,
                         self.container_kind, self.file_seq, self.keyframe_intv,
                         self.enc_caps, self.data_base, self.region_end,
                         self.reserved1)
        off = MLA_SCHEMA_OFF
        if self.schema_table:
            buf[off:off + len(self.schema_table)] = self.schema_table
            off += len(self.schema_table)
        if self.station_table:
            buf[off:off + len(self.station_table)] = self.station_table
        return bytes(buf) + struct.pack("<H", crc16(bytes(buf)))

    @classmethod
    def from_bytes(cls, raw: bytes) -> MlaPrefix:
        """Deserialize. Raises ValueError on a CRC error or a bad magic.

        `raw` must hold the whole prefix; for an extended (>512 B) prefix that
        means more than one sector — _read_prefix() fetches the extra ones.
        """
        if len(raw) < MLA_PREFIX_SIZE:
            raise ValueError("Prefix: too short")
        size = cls.parse_size(raw)
        if len(raw) < size:
            raise ValueError(f"Prefix: need {size} B, got {len(raw)}")
        body = size - 2
        crc_stored = struct.unpack_from("<H", raw, body)[0]
        if crc16(raw[:body]) != crc_stored:
            raise ValueError(
                f"Prefix: bad CRC (stored {crc_stored:#06x}, "
                f"computed {crc16(raw[:body]):#06x})"
            )
        f1 = struct.unpack_from(_PFX_FMT1, raw, 0)
        if f1[0] != MLA_MAGIC:
            raise ValueError(f"Prefix: bad magic {f1[0]!r}")
        f2 = struct.unpack_from(_PFX_FMT2, raw, 20)
        slen  = _schema_byte_len(raw)
        stoff = MLA_SCHEMA_OFF + slen
        stlen = _station_byte_len(raw, stoff)
        return cls(magic=f1[0], version=f1[1], cluster_shift=f1[2],
                   log_rec_size=f1[3], flags=f1[4],
                   file_size=f1[5], reserved8=f1[6],
                   container_kind=f2[0], file_seq=f2[1], keyframe_intv=f2[2],
                   enc_caps=f2[3], data_base=f2[4], region_end=f2[5],
                   reserved1=f2[6],
                   schema_table=bytes(raw[MLA_SCHEMA_OFF:MLA_SCHEMA_OFF + slen]),
                   station_table=bytes(raw[stoff:stoff + stlen]))


# ──────────────────────────────────────────────────────────────────────────────
#  LOG record / Lock (16 B) — the WHOLE record is covered by the CRC
#
#  Layout (little-endian):
#    [0]  offset     4 B  uint32 — logical offset of the data block
#    [4]  timestamp  4 B  uint32 — Unix seconds; supplied by the caller (RTC/GPS)
#    [8]  length     2 B  uint16 — data length 1..65535
#    [10] rec_type   1 B  uint8  — data type (encoding + class)
#    [11] kf_back    1 B  uint8  — records back to the owning keyframe (0 = is one)
#    [12] station    1 B  uint8  — index 1..255 into the station table (0 = none)
#    [13] reserved   1 B  uint8  — 0x00
#    [14] crc16      2 B  uint16 — over bytes 0..13
#
#  A record is VALID iff its stored CRC matches. An empty slot (all 0xFF) and an
#  abandoned slot (all 0x00) both fail the CRC, so both are skipped.
# ──────────────────────────────────────────────────────────────────────────────

_LOG_FMT     = "<IIHBBBB"  # bytes 0..13 (14 B)
_LOG_CRC_LEN = 14

@dataclass
class MlaLog:
    offset:    int
    timestamp: int
    length:    int = 0
    rec_type:  int = ENC_RAW
    kf_back:   int = 0
    station:   int = 0
    reserved:  int = 0

    def to_bytes(self) -> bytes:
        """Serialize → exactly 16 B (14 B body + CRC16)."""
        body = struct.pack(_LOG_FMT,
                           self.offset, self.timestamp, self.length,
                           self.rec_type, self.kf_back, self.station,
                           self.reserved)
        return body + struct.pack("<H", crc16(body))

    @classmethod
    def from_bytes(cls, raw: bytes) -> tuple[MlaLog, bool]:
        """Deserialize. Returns (record, crc_ok)."""
        f = struct.unpack_from(_LOG_FMT, raw)
        crc_stored = struct.unpack_from("<H", raw, _LOG_CRC_LEN)[0]
        crc_ok = crc16(raw[:_LOG_CRC_LEN]) == crc_stored
        return cls(offset=f[0], timestamp=f[1], length=f[2], rec_type=f[3],
                   kf_back=f[4], station=f[5], reserved=f[6]), crc_ok

    @property
    def block_end(self) -> int:
        """Address past the end of the data block: offset + MAGIC(2) + data + CRC16(2)."""
        return self.offset + 2 + self.length + 2


# ──────────────────────────────────────────────────────────────────────────────
#  HAL — hardware abstraction layer (4 functions; logical offsets 0..file_size-1)
# ──────────────────────────────────────────────────────────────────────────────

class MlaHAL(ABC):

    @abstractmethod
    def read(self, off: int, n: int) -> bytes: ...

    @abstractmethod
    def write(self, off: int, data: bytes) -> None: ...

    @abstractmethod
    def sync(self) -> None: ...

    @abstractmethod
    def size(self) -> int: ...


class MlaPosixHAL(MlaHAL):
    """HAL for a regular file. Primarily for development and testing."""

    def __init__(self, path: str):
        self._path = path
        self._f = None

    def __enter__(self) -> MlaPosixHAL:
        self._f = open(self._path, "r+b")
        return self

    def __exit__(self, *_) -> None:
        if self._f:
            self._f.close()
            self._f = None

    def read(self, off: int, n: int) -> bytes:
        self._f.seek(off)
        return self._f.read(n)

    def write(self, off: int, data: bytes) -> None:
        self._f.seek(off)
        self._f.write(data)

    def sync(self) -> None:
        self._f.flush()

    def size(self) -> int:
        return os.path.getsize(self._path)

    @staticmethod
    def create(path: str, file_size: int = MLA_DEFAULT_SIZE) -> MlaPosixHAL:
        """Create a new file pre-filled with 0xFF (a fresh, erased medium)."""
        chunk = b"\xff" * 4096
        with open(path, "wb") as f:
            written = 0
            while written < file_size:
                n = min(len(chunk), file_size - written)
                f.write(chunk[:n])
                written += n
        return MlaPosixHAL(path)


# ──────────────────────────────────────────────────────────────────────────────
#  MlaCore — the container engine (platform-independent, works through a HAL)
# ──────────────────────────────────────────────────────────────────────────────

class MlaCore:
    """
    Manages an MLA log file.

    Typical usage:
        hal = MlaPosixHAL.create("log.mla")
        with hal:
            mla = MlaCore(hal)
            mla.format()                           # first run
            mla.append(timestamp, station=1, data=b"...")
            for rec, payload in mla:
                process(rec, payload)

        with MlaPosixHAL("log.mla") as hal:
            mla = MlaCore(hal); mla.mount()        # restores top_ptr / bot_ptr
    """

    def __init__(self, hal: MlaHAL):
        self._hal       = hal
        self._prefix:   MlaPrefix | None = None
        self._top_ptr = 0  # end of data — where the next data block goes
        self._bot_ptr = 0  # start of log — the next lock goes to bot_ptr - rs
        self._n_slots = 0  # number of physical log slots written (incl. dead ones)
        self._count   = 0  # number of valid data records

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def _rs(self) -> int:
        return self._prefix.log_rec_size if self._prefix else MLA_LOG_REC_SIZE

    @property
    def _pfx_size(self) -> int:
        return self._prefix.size if self._prefix else MLA_PREFIX_SIZE

    @property
    def _data_base(self) -> int:
        return self._prefix.data_base if self._prefix else MLA_PREFIX_SIZE

    @property
    def record_count(self) -> int:
        return self._count

    @property
    def free_bytes(self) -> int:
        return max(0, self._bot_ptr - self._top_ptr - self._rs)

    @property
    def is_full(self) -> bool:
        return self.free_bytes < 5   # smallest block = MAGIC(2)+1B+CRC(2)

    # ── Formatting ────────────────────────────────────────────────────────────

    def format(self,
               file_size:      int = MLA_DEFAULT_SIZE,
               cluster_shift:  int = 12,
               crc_mode:       int = CRC_FULL,
               keyframe_intv:  int = 8,
               container_kind: int = 0,
               file_seq:       int = 0,
               schema_table:   bytes = b"",
               station_table:  bytes = b"") -> None:
        """
        Initialize a fresh file — writes the prefix; the rest stays 0xFF.

        schema_table / station_table — optional self-describing tables (see
        tools/mla_schema.py) embedded in the prefix, covered by its CRC. The
        prefix grows in 512 B sectors to fit them. Empty = byte-identical to a
        minimal prefix (a single 512 B sector).
        """
        self._prefix = MlaPrefix(
            file_size=file_size, cluster_shift=cluster_shift,
            flags=crc_mode, keyframe_intv=keyframe_intv,
            container_kind=container_kind, file_seq=file_seq,
            schema_table=schema_table, station_table=station_table,
        )
        self._hal.write(0, self._prefix.to_bytes())
        self._hal.sync()
        self._top_ptr = self._prefix.data_base
        self._bot_ptr = file_size
        self._n_slots = 0
        self._count   = 0

    # ── Mount ──────────────────────────────────────────────────────────────────

    def _read_prefix(self) -> MlaPrefix:
        """Read and verify the prefix, fetching extra sectors if it is extended."""
        raw  = self._hal.read(0, MLA_PREFIX_SIZE)
        size = MlaPrefix.parse_size(raw)
        while len(raw) < size:                       # extended prefix → more sectors
            raw += self._hal.read(len(raw), size - len(raw))
            size = MlaPrefix.parse_size(raw)          # re-derive (station hdr now present)
        return MlaPrefix.from_bytes(raw)

    def mount(self) -> None:
        """
        Load an existing file and restore top_ptr / bot_ptr in RAM.

          1. Read and verify the prefix.
          2. Binary-search the log boundary (the 0xFF ↔ written transition).
          3. Forward-scan the slots; the end of the newest valid record's data
             is top_ptr. If the newest lock's data block has no MAGIC (torn data
             write), zero that lock and reclaim its space.
        """
        self._prefix = self._read_prefix()
        fs = self._prefix.file_size
        rs = self._prefix.log_rec_size
        db = self._prefix.data_base

        # Binary search: slot j (0 = oldest) sits at fs - (j+1)*rs.
        max_slots = (fs - db) // rs
        lo, hi = 0, max_slots
        while lo < hi:
            mid = (lo + hi) // 2
            if self._hal.read(fs - (mid + 1) * rs, rs) == b"\xff" * rs:
                hi = mid       # slot mid is free
            else:
                lo = mid + 1   # slot mid is used
        self._n_slots = lo
        self._bot_ptr = fs - lo * rs

        top_ptr = db
        count   = 0
        for slot in range(lo):
            addr = fs - (slot + 1) * rs
            rec, ok = MlaLog.from_bytes(self._hal.read(addr, rs))
            if not ok:
                continue                              # burned slot (torn lock / dead)
            if slot == lo - 1:                        # newest — check the data block
                if self._hal.read(rec.offset, 2) != MLA_DATA_MAGIC:
                    self._hal.write(addr, b"\x00" * rs)   # torn data → abandon
                    self._hal.sync()
                    top_ptr = rec.offset
                    continue
            top_ptr = rec.block_end
            count  += 1
        self._top_ptr = top_ptr
        self._count   = count

    # ── Write ──────────────────────────────────────────────────────────────────

    def append(self, timestamp: int, station: int, data: bytes,
               rec_type: int = ENC_RAW, kf_back: int = 0) -> None:
        """
        Append a data record. Commit protocol: LOCK first, DATA second.

        station — 1-byte index (1..255) into the prefix station table; the
        library does not interpret it. rec_type — data class/encoding (the
        container only carries it). kf_back — records back to the owning
        keyframe (compression only, else 0).
        """
        if not self._prefix:
            raise RuntimeError("Call format() or mount() first")
        n = len(data)
        if not (1 <= n <= 65535):
            raise ValueError(f"Data: length must be 1–65535 B, not {n}")

        rs       = self._rs
        block_sz = 2 + n + 2  # MAGIC + DATA + CRC16
        new_bot  = self._bot_ptr - rs
        if self._top_ptr + block_sz > new_bot:
            raise RuntimeError("MLA file is full")

        lock = MlaLog(offset=self._top_ptr, timestamp=timestamp, length=n,
                      rec_type=rec_type, kf_back=kf_back, station=station & 0xFF)
        self._hal.write(new_bot, lock.to_bytes())            # Step 1 — lock
        self._hal.write(self._top_ptr, self._build_block(data))  # Step 2 — data

        self._top_ptr += block_sz                            # Step 3 — RAM
        self._bot_ptr  = new_bot
        self._n_slots += 1
        self._count   += 1

    def _build_block(self, data: bytes) -> bytes:
        """Assemble a data block: MAGIC + data + CRC16 (or 0xFFFF when no CRC)."""
        crc = crc16(data) if self._prefix.flags & 0x3 >= CRC_DATA else 0xFFFF
        return MLA_DATA_MAGIC + data + struct.pack("<H", crc)

    # ── Read ───────────────────────────────────────────────────────────────────

    def read_record(self, index: int) -> tuple[MlaLog, bytes]:
        """Read the index-th valid record (0 = oldest)."""
        if not (0 <= index < self._count):
            raise IndexError(f"Index {index} out of range [0, {self._count})")
        for i, (rec, data) in enumerate(self):
            if i == index:
                return rec, data
        raise IndexError(f"Record {index} not found — inconsistent state")

    def _read_data(self, rec: MlaLog) -> bytes:
        """Read and verify a data block using the info from the log record."""
        raw = self._hal.read(rec.offset, rec.length + 4)
        if raw[:2] != MLA_DATA_MAGIC:
            raise ValueError(f"Bad MAGIC at offset {rec.offset:#010x}")
        data = raw[2:2 + rec.length]
        if self._prefix.flags & 0x3 >= CRC_DATA:
            crc_s = struct.unpack_from("<H", raw, 2 + rec.length)[0]
            if crc16(data) != crc_s:
                raise ValueError(f"Bad data CRC at offset {rec.offset:#010x}")
        return data

    def __len__(self) -> int:
        return self._count

    def __iter__(self) -> Iterator[tuple[MlaLog, bytes]]:
        """Iterate over all valid data records, oldest first."""
        fs = self._prefix.file_size
        rs = self._rs
        for slot in range(self._n_slots):
            rec, ok = MlaLog.from_bytes(self._hal.read(fs - (slot + 1) * rs, rs))
            if not ok:
                continue
            try:
                yield rec, self._read_data(rec)
            except ValueError:
                continue  # corrupted data block — skip

    def scan(self, *, time_from: int | None = None, time_to: int | None = None,
             station: int | None = None) -> Iterator[tuple[MlaLog, bytes]]:
        """Linear filtered scan (host-side). Same result as filtering every record."""
        for rec, data in self:
            if time_from is not None and rec.timestamp < time_from:
                continue
            if time_to is not None and rec.timestamp > time_to:
                continue
            if station is not None and rec.station != station:
                continue
            yield rec, data

    def sync(self) -> None:
        self._hal.sync()

    # ── Emergency recovery ─────────────────────────────────────────────────────

    def recover(self) -> int:
        """
        Emergency log recovery by scanning the data region for MAGIC + a valid
        data CRC. Rebuilds the log (timestamp=0, station=0). Requires CRC_DATA or
        CRC_FULL. Slow — emergency use only. Returns the number of records.
        """
        self._prefix = self._read_prefix()
        fs = self._prefix.file_size
        rs = self._prefix.log_rec_size
        if self._prefix.flags & 0x3 < CRC_DATA:
            raise RuntimeError("Recovery requires CRC_DATA or CRC_FULL")

        db = self._prefix.data_base
        recovered: list[MlaLog] = []
        pos = data_end = db
        while pos < fs - 4:
            if self._hal.read(pos, 2) != MLA_DATA_MAGIC:
                pos += 1
                continue
            found = False
            for length in range(1, 65536):
                end = pos + 2 + length + 2
                if end > fs:
                    break
                block = self._hal.read(pos, 2 + length + 2)
                data  = block[2:2 + length]
                crc_s = struct.unpack_from("<H", block, 2 + length)[0]
                if crc16(data) == crc_s:
                    recovered.append(MlaLog(offset=pos, timestamp=0, length=length))
                    data_end = end
                    pos = end
                    found = True
                    break
            if not found:
                pos += 1

        self._top_ptr = data_end
        self._bot_ptr = fs
        self._n_slots = 0
        self._count   = 0
        for rec in recovered:
            new_bot = self._bot_ptr - rs
            if new_bot <= self._top_ptr:
                break
            self._hal.write(new_bot, rec.to_bytes())
            self._bot_ptr  = new_bot
            self._n_slots += 1
            self._count   += 1
        self._hal.sync()
        return self._count


# ──────────────────────────────────────────────────────────────────────────────
#  Example / quick smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    PATH = "/tmp/_nic_mla_test.bin"
    SIZE = 64 * 1024

    print("── Format and write ──")
    hal = MlaPosixHAL.create(PATH, SIZE)
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=SIZE)
        now = int(time.time())
        for i in range(5):
            mla.append(now + i, station=1, data=bytes([i] * (4 + i)))
        print(f"  records written: {mla.record_count}")

    print("── Mount and read ──")
    with MlaPosixHAL(PATH) as hal:
        mla = MlaCore(hal); mla.mount()
        print(f"  records found:   {mla.record_count}")
        for rec, data in mla:
            print(f"   ts={rec.timestamp} station={rec.station} len={rec.length} data={data.hex()}")

    os.remove(PATH)
    print("\nDone ✓  ★ Viva La Resistánce ★")
