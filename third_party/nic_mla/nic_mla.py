#!/usr/bin/env python3
"""
NIC-MLA  —  Matroshka Logging Archive
Core  —  Python reference implementation  (format v1.0)

File format:
  [PREFIX 512 B][DATA →][   free space 0xFF   ][← LOG][EOF]

The file has a fixed size (1 MB by default), pre-filled with 0xFF.
Data grows from offset 512 upward, the log grows from EOF downward.
Full = top_ptr + next_block > bot_ptr - log_rec_size.

Commit protocol: LOG (lock) first, DATA second.
A valid lock = commit token; a torn write is safely repaired on mount.

Format highlights:
  • LOG record 24 B: timestamp, station, region, offset, length, rec_type, flags.
  • Data block WITHOUT a TYPE byte — just MAGIC + data + CRC (type lives in the log).
  • Checkpoint — a special log record every 2^checkpoint_shift records;
    speeds up mount and provides an anchor point after a power loss.
  • Prefix carries a self-describing schema table (see tools/mla_schema.py),
    container_kind, file_seq, keyframe_intv, enc_caps, data_base, region_end,
    checkpoint_shift.

Python 3.10+   |   MIT   |   ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
import struct
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Iterator

# ──────────────────────────────────────────────────────────────────────────────
#  Constants
# ──────────────────────────────────────────────────────────────────────────────

MLA_MAGIC          = b"MLA\x00"        # 4 B — prefix identifier
MLA_DATA_MAGIC     = bytes([0xAB, 0xCD])  # 2 B — sync word of every data block
MLA_VERSION        = 1
MLA_PREFIX_SIZE    = 512               # B — base prefix block (grows in 512 B steps for a big schema)
MLA_LOG_REC_SIZE   = 24                # B — log record (lock)
MLA_INDEX_REC_SIZE = MLA_LOG_REC_SIZE  # backward-compatible alias
MLA_DEFAULT_SIZE   = 1 << 20           # 1 MB — default file size

# Self-describing schema/decode table — embedded in the prefix free space,
# covered by the prefix CRC. Built/read by tools/mla_schema.py (host-only).
MLA_SCHEMA_OFF     = 34                # = end of the structured prefix header
MLA_SCHEMA_MAX     = 510 - MLA_SCHEMA_OFF  # 476 — fits a single 512 B prefix
MLA_SCHEMA_VER     = 1                 # the one and only schema table version
MLA_SCHEMA_FIELD   = 14                # bytes per field descriptor (6 core + 8 name)
MLA_PREFIX_MAX     = 8 * MLA_PREFIX_SIZE   # 4 KB / 8 sectors — hard ceiling for the prefix
                                       # (fits the format max of 255 data fields)


def _prefix_byte_len(schema_len: int) -> int:
    """Total prefix size for a schema of `schema_len` bytes (CRC included).

    Normally 512 B (schema fits [34..510), CRC at [510]). A schema that
    overflows grows the prefix in whole 512 B blocks, the CRC moving to the
    prefix's last 2 bytes. Mirrors prefix_byte_len() in tools/mla_schema.py.
    """
    need = MLA_SCHEMA_OFF + schema_len + 2          # header + schema + CRC16
    if need <= MLA_PREFIX_SIZE:
        return MLA_PREFIX_SIZE
    return -(-need // MLA_PREFIX_SIZE) * MLA_PREFIX_SIZE   # round up to 512


def _schema_byte_len(raw: bytes) -> int:
    """Length of the schema table embedded at [34..) (0 if none/empty)."""
    if len(raw) < MLA_SCHEMA_OFF + 3:
        return 0
    if raw[MLA_SCHEMA_OFF] != MLA_SCHEMA_VER:        # 0x00 / 0xFF / other → none
        return 0
    n_log, n_data = raw[MLA_SCHEMA_OFF + 1], raw[MLA_SCHEMA_OFF + 2]
    return 3 + MLA_SCHEMA_FIELD * (n_log + n_data)

# Log record state — byte 20 (OUTSIDE the CRC, may be changed after writing)
FLAG_LIVE      = 0xFF  # valid, committed record
FLAG_ABANDONED = 0x00  # abandoned (torn data write); data was not written

# Integrity mode — prefix.flags (bits 0–1)
CRC_NONE = 0  # no data CRC — fast, weak protection (the log CRC is always present)
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
CLASS_CHECKPT = 0xF0  # checkpoint (register)

REC_CHECKPOINT = CLASS_CHECKPT  # rec_type of a checkpoint (raw encoding)

# ──────────────────────────────────────────────────────────────────────────────
#  Index region — host-side time/station skip-table (optional)
#
#  An optional fixed region reserved between the prefix and the data, at
#  [MLA_PREFIX_SIZE, data_base). It holds a flat, append-only array of small
#  "anchors". One anchor is written at each checkpoint cadence; on a time/station
#  query the host reads the whole (tiny) region into RAM, finds the nearest
#  anchor and jumps straight to that log slot — instead of scanning from the
#  start. It is a pure speed-up: if absent (data_base == prefix size) or
#  incomplete, the host simply scans (see DESIGN §1).
#
#  Anchor layout (12 B, little-endian):
#    [0]  timestamp 4 B  uint32  — measurement time at the anchor's record
#    [4]  slot      4 B  uint32  — log slot index to jump to (enables O(1) seek)
#    [8]  station   2 B  uint16  — station at the anchor's record (hint)
#    [10] status    1 B  uint8   — IDX_UNUSED / IDX_LIVE / IDX_DEAD
#    [11] reserved  1 B  uint8   — 0xFF (future: region / flags)
#
#  status lives outside any CRC so it can be flipped on NOR (0xFF→0xA5 when
#  written, 0xA5→0x00 to invalidate — both are 1→0 only).
# ──────────────────────────────────────────────────────────────────────────────

MLA_IDX_REC_SIZE = 12        # B — one index anchor
IDX_UNUSED = 0xFF            # status: slot never written (fresh 0xFF)
IDX_LIVE   = 0xA5            # status: valid anchor
IDX_DEAD   = 0x00            # status: invalidated (zeroed — NOR-friendly)

_IDX_FMT = "<IIHBB"          # timestamp, slot, station, status, reserved

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
#  Prefix (512 B)
#
#  Layout:
#    [0]   magic[4]         b"MLA\0"
#    [4]   version          1 B   = 1
#    [5]   cluster_shift    1 B   8=256B raw NOR; 12=4KB FAT/SD; … 15=32KB
#    [6]   log_rec_size     1 B   24 (default) or 32
#    [7]   flags            1 B   CRC mode (b0-1) + alignment (b2-3)
#    [8]   file_size        4 B   uint32 LE
#    [12]  phys_addr        8 B   uint64 LE  (base on the medium; 0 for FAT/POSIX)
#    [20]  container_kind   1 B   0=single 1=rotation 2=circular
#    [21]  file_seq         2 B   uint16 LE  file index within a rotation
#    [23]  keyframe_intv    1 B   keyframe interval for compression (8; 0=N/A)
#    [24]  enc_caps         1 B   bitmask of encodings present in the file
#    [25]  data_base        4 B   uint32 LE  = 512
#    [29]  region_end       4 B   uint32 LE  = file_size
#    [33]  checkpoint_shift 1 B   checkpoint interval = 2^value (0=off)
#    [34]  padding          0x00 up to byte 509
#    [510] crc16            2 B   LE  —  over bytes 0..509
# ──────────────────────────────────────────────────────────────────────────────

_PFX_FMT1 = "<4sBBBBIQ"   # bytes [0..19]   (20 B)
_PFX_FMT2 = "<BHBBIIB"    # bytes [20..33]  (14 B)

@dataclass
class MlaPrefix:
    magic:            bytes = MLA_MAGIC
    version:          int   = MLA_VERSION
    cluster_shift:    int   = 12
    log_rec_size:     int   = MLA_LOG_REC_SIZE
    flags:            int   = CRC_FULL
    file_size:        int   = MLA_DEFAULT_SIZE
    phys_addr:        int   = 0
    container_kind:   int   = 0
    file_seq:         int   = 0
    keyframe_intv:    int   = 8
    enc_caps:         int   = 0
    data_base:        int   = 0   # 0 → computed as the prefix size (>= 512)
    region_end:       int   = 0   # 0 → computed as file_size
    checkpoint_shift: int   = 8   # 2^8 = 256
    schema_table:     bytes = b""  # self-describing decode table (see tools/mla_schema.py)

    def __post_init__(self):
        if self.size > MLA_PREFIX_MAX:
            raise ValueError(
                f"schema needs a {self.size} B prefix, exceeds "
                f"MLA_PREFIX_MAX={MLA_PREFIX_MAX} ({MLA_PREFIX_MAX // MLA_PREFIX_SIZE} sectors)"
            )
        if self.data_base == 0:
            self.data_base = self.size
        if self.region_end == 0:
            self.region_end = self.file_size

    @property
    def size(self) -> int:
        """Prefix size in bytes — 512, or a larger 512 B multiple if the schema
        table does not fit (the CRC then lives in the prefix's last 2 bytes)."""
        return _prefix_byte_len(len(self.schema_table))

    @staticmethod
    def parse_size(raw: bytes) -> int:
        """Prefix size implied by the first block (>= 37 B) — for a two-step read."""
        return _prefix_byte_len(_schema_byte_len(raw))

    def to_bytes(self) -> bytes:
        """Serialize → `size` bytes (header + schema + padding + trailing CRC16)."""
        size = self.size
        buf = bytearray(size - 2)
        struct.pack_into(_PFX_FMT1, buf, 0,
                         self.magic, self.version, self.cluster_shift,
                         self.log_rec_size, self.flags,
                         self.file_size, self.phys_addr)
        struct.pack_into(_PFX_FMT2, buf, 20,
                         self.container_kind, self.file_seq, self.keyframe_intv,
                         self.enc_caps, self.data_base, self.region_end,
                         self.checkpoint_shift)
        if self.schema_table:                       # embed at [34 .. ), under the CRC
            buf[MLA_SCHEMA_OFF:MLA_SCHEMA_OFF + len(self.schema_table)] = self.schema_table
        return bytes(buf) + struct.pack("<H", crc16(bytes(buf)))

    @classmethod
    def from_bytes(cls, raw: bytes) -> MlaPrefix:
        """Deserialize. Raises ValueError on a CRC error or a bad magic.

        `raw` must hold the whole prefix; for an extended (>512 B) prefix that
        means more than one block — mount()/recover() read parse_size() bytes.
        """
        if len(raw) < MLA_PREFIX_SIZE:
            raise ValueError("Prefix: too short")
        size = cls.parse_size(raw)
        if len(raw) < size:
            raise ValueError(f"Prefix: need {size} B, got {len(raw)}")
        body, crc_at = size - 2, size - 2
        crc_stored = struct.unpack_from("<H", raw, crc_at)[0]
        if crc16(raw[:body]) != crc_stored:
            raise ValueError(
                f"Prefix: bad CRC (stored {crc_stored:#06x}, "
                f"computed {crc16(raw[:body]):#06x})"
            )
        f1 = struct.unpack_from(_PFX_FMT1, raw, 0)
        if f1[0] != MLA_MAGIC:
            raise ValueError(f"Prefix: bad magic {f1[0]!r}")
        f2 = struct.unpack_from(_PFX_FMT2, raw, 20)
        return cls(magic=f1[0], version=f1[1], cluster_shift=f1[2],
                   log_rec_size=f1[3], flags=f1[4],
                   file_size=f1[5], phys_addr=f1[6],
                   container_kind=f2[0], file_seq=f2[1], keyframe_intv=f2[2],
                   enc_caps=f2[3], data_base=f2[4], region_end=f2[5],
                   checkpoint_shift=f2[6], schema_table=cls._extract_schema(raw))

    @staticmethod
    def _extract_schema(raw: bytes) -> bytes:
        """Slice the embedded schema table out of the prefix (b"" if none).

        The table is self-sizing: tbl_ver(1) + n_log(1) + n_data(1) + per-field
        descriptors (14 B each). A tbl_ver byte of 0x00 (zero padding) or 0xFF
        (fresh medium) means no schema. Parsing/validation is left to
        tools/mla_schema.read_schema; here we only recover the bytes.
        """
        return bytes(raw[MLA_SCHEMA_OFF:MLA_SCHEMA_OFF + _schema_byte_len(raw)])

# ──────────────────────────────────────────────────────────────────────────────
#  LOG record / Lock (24 B)
#
#  Layout (little-endian):
#    [0]  timestamp   4 B  uint32  — Unix seconds; supplied by the caller (RTC/GPS)
#    [4]  offset      4 B  uint32  — logical offset of the data block
#    [8]  station     2 B  uint16
#    [10] region     2 B  uint16
#    [12] seq         2 B  uint16  — monotonic order within the file
#    [14] rec_type    1 B  uint8   — data type (encoding + class)
#    [15] length      2 B  uint16  — data length 1..65535 (0 for a checkpoint)
#    [17] kf_back     2 B  uint16  — distance (in seq) to the owning keyframe
#    [19] reserved    1 B  uint8   — 0x00, reserved (INSIDE the CRC)
#    [20] flags       1 B  uint8   — OUTSIDE the CRC (FLAG_LIVE=0xFF / FLAG_ABANDONED=0x00)
#    [21] pad         1 B  uint8   — 0xFF, OUTSIDE the CRC
#    [22] crc16       2 B  uint16  — over bytes 0..19
#
#  flags is intentionally outside the CRC: on NOR flash it can be flipped
#  0xFF→0x00 without breaking the checksum (abandoning a record after a torn
#  data write).
# ──────────────────────────────────────────────────────────────────────────────

_LOG_FMT     = "<IIHHHBHHB"  # bytes 0..19 (20 B)
_LOG_CRC_LEN = 20            # CRC over the first 20 bytes (timestamp..reserved)

@dataclass
class MlaLog:
    timestamp: int
    offset:    int
    station:   int
    region:   int
    seq:       int = 0
    rec_type:  int = ENC_RAW
    length:    int = 0
    kf_back:   int = 0
    reserved:  int = 0
    flags:     int = FLAG_LIVE

    def to_bytes(self) -> bytes:
        """Serialize → exactly 24 B."""
        body = struct.pack(_LOG_FMT,
                           self.timestamp, self.offset,
                           self.station, self.region,
                           self.seq, self.rec_type, self.length,
                           self.kf_back, self.reserved)
        return (body
                + bytes([self.flags, 0xFF])          # flags + pad (OUTSIDE the CRC)
                + struct.pack("<H", crc16(body)))

    @classmethod
    def from_bytes(cls, raw: bytes) -> tuple[MlaLog, bool]:
        """Deserialize. Returns (record, crc_ok)."""
        f = struct.unpack_from(_LOG_FMT, raw)
        flags      = raw[20]
        crc_stored = struct.unpack_from("<H", raw, 22)[0]
        crc_ok = crc16(raw[:_LOG_CRC_LEN]) == crc_stored
        return cls(timestamp=f[0], offset=f[1], station=f[2], region=f[3],
                   seq=f[4], rec_type=f[5], length=f[6], kf_back=f[7],
                   reserved=f[8], flags=flags), crc_ok

    @property
    def is_live(self) -> bool:
        return self.flags == FLAG_LIVE

    @property
    def is_abandoned(self) -> bool:
        return self.flags == FLAG_ABANDONED

    @property
    def is_checkpoint(self) -> bool:
        """True = checkpoint (register), not a data record."""
        return (self.rec_type & 0xF0) == CLASS_CHECKPT

    @property
    def block_end(self) -> int:
        """Address past the end of the data block: offset + MAGIC(2) + data + CRC16(2)."""
        return self.offset + 2 + self.length + 2


# Backward-compatible alias.
MlaIndex = MlaLog

# ──────────────────────────────────────────────────────────────────────────────
#  HAL — hardware abstraction layer
#  4 functions; all offsets are logical (0 .. file_size-1).
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

# ──────────────────────────────────────────────────────────────────────────────
#  POSIX HAL — a regular file (PC / Linux / Windows / macOS)
# ──────────────────────────────────────────────────────────────────────────────

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
        """
        Create a new file pre-filled with 0xFF — emulates fresh NOR flash
        after an erase. Must be called before format().
        """
        chunk = b"\xff" * 4096
        with open(path, "wb") as f:
            remaining = file_size
            while remaining > 0:
                n = min(len(chunk), remaining)
                f.write(chunk[:n])
                remaining -= n
        return MlaPosixHAL(path)

# ──────────────────────────────────────────────────────────────────────────────
#  MlaCore — the core
# ──────────────────────────────────────────────────────────────────────────────

class MlaCore:
    """
    Manages an MLA log file. Platform-independent — works solely through a HAL.

    Typical usage:
        hal = MlaPosixHAL.create("log.mla")
        with hal:
            mla = MlaCore(hal)
            mla.format()                          # first run
            mla.append(ts, station, region, data)
            for rec, payload in mla:
                process(rec, payload)

        # On a later run:
        with MlaPosixHAL("log.mla") as hal:
            mla = MlaCore(hal)
            mla.mount()                           # restores top_ptr / bot_ptr
    """

    def __init__(self, hal: MlaHAL):
        self._hal        = hal
        self._prefix:    MlaPrefix | None = None
        self._top_ptr  = 0  # end of data — where to write the next data block
        self._bot_ptr  = 0  # start of log — the next lock goes to bot_ptr - log_rec_size
        self._n_slots  = 0  # number of physical log slots (incl. abandoned/torn/checkpoint)
        self._count    = 0  # number of valid (live) DATA records (excluding checkpoints)
        self._seq      = 0  # next seq
        self._idx_n    = 0  # number of index anchors written so far
        self._last_sta = 0  # station of the most recent data record (anchor hint)

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def _rs(self) -> int:
        """Log record size (from the prefix, falling back to the constant)."""
        return self._prefix.log_rec_size if self._prefix else MLA_LOG_REC_SIZE

    @property
    def _pfx_size(self) -> int:
        """Actual prefix size (512, or larger if an oversized schema grew it)."""
        return self._prefix.size if self._prefix else MLA_PREFIX_SIZE

    @property
    def _data_base(self) -> int:
        """First byte of the data region (after the prefix + optional index region)."""
        return self._prefix.data_base if self._prefix else MLA_PREFIX_SIZE

    @property
    def _idx_capacity(self) -> int:
        """How many index anchors fit in the reserved region (0 = index disabled)."""
        return (self._data_base - self._pfx_size) // MLA_IDX_REC_SIZE

    @property
    def record_count(self) -> int:
        """Number of valid data records (excluding checkpoints)."""
        return self._count

    @property
    def free_bytes(self) -> int:
        """Approximate number of free bytes (excluding the next record)."""
        return max(0, self._bot_ptr - self._top_ptr - self._rs)

    @property
    def is_full(self) -> bool:
        """True = not even the smallest possible record fits (1 B data = 5 B block)."""
        return self.free_bytes < 5

    # ── Formatting ─────────────────────────────────────────────────────────────

    def format(self,
               file_size:        int = MLA_DEFAULT_SIZE,
               cluster_shift:    int = 12,
               crc_mode:         int = CRC_FULL,
               phys_addr:        int = 0,
               log_rec_size:     int = MLA_LOG_REC_SIZE,
               checkpoint_shift: int = 8,
               keyframe_intv:    int = 8,
               container_kind:   int = 0,
               file_seq:         int = 0,
               index_kb:         int = 0,
               schema_table:     bytes = b"") -> None:
        """
        Initialize a new file — writes the prefix, the rest stays 0xFF (ensured by create()).
        Call only once on a fresh medium.

        index_kb — KB reserved between the prefix and the data for the host-side
        time/station skip-table (0 = disabled, format byte-identical to before).
        Capable platforms (STM32/ESP/PC) typically pass index_kb=4; the write-only
        ATmega path leaves it 0 and just appends.

        schema_table — optional self-describing decode table (see tools/mla_schema.py)
        embedded in the prefix free space, covered by the prefix CRC. Lets the host
        export records with no prior knowledge; matches what the C library writes
        via mla_w_format_ex(). Empty = no schema (byte-identical to before).
        """
        if log_rec_size != MLA_LOG_REC_SIZE:
            raise NotImplementedError(
                "The reference implementation currently supports only log_rec_size=24. "
                "The layout of the 32 B variant is still being finalized (see spec §10.1)."
            )
        data_base = _prefix_byte_len(len(schema_table)) + index_kb * 1024
        self._prefix = MlaPrefix(
            file_size=file_size, cluster_shift=cluster_shift,
            log_rec_size=log_rec_size, flags=crc_mode, phys_addr=phys_addr,
            checkpoint_shift=checkpoint_shift, keyframe_intv=keyframe_intv,
            container_kind=container_kind, file_seq=file_seq,
            data_base=data_base, schema_table=schema_table,
        )
        self._hal.write(0, self._prefix.to_bytes())
        self._hal.sync()
        self._top_ptr = data_base
        self._bot_ptr = file_size
        self._n_slots = 0
        self._count   = 0
        self._seq     = 0
        self._idx_n   = 0

    # ── Mount ──────────────────────────────────────────────────────────────────

    def _read_prefix(self) -> MlaPrefix:
        """Read and verify the prefix, fetching extra blocks if it is extended."""
        first = self._hal.read(0, MLA_PREFIX_SIZE)
        size  = MlaPrefix.parse_size(first)
        raw   = first if size <= MLA_PREFIX_SIZE else \
            first + self._hal.read(MLA_PREFIX_SIZE, size - MLA_PREFIX_SIZE)
        return MlaPrefix.from_bytes(raw)

    def mount(self) -> None:
        """
        Load an existing file and restore top_ptr / bot_ptr in RAM.

        Algorithm:
          1. Read and verify the prefix.
          2. Binary search (O(log n)) for the number of written slots in the
             log region (the 0xFF ↔ data boundary).
          3. Find the newest valid checkpoint (register) and read count + top_ptr
             from it — then only the tail (slots after it) needs to be scanned.
          4. Check the newest slot:
             - Bad CRC  → torn lock write (data write never started); skip the slot.
             - ABANDONED → torn data write (lock OK, data missing); do not count.
             - LIVE      → verify the MAGIC; if missing, abandon it and return top_ptr.
        """
        self._prefix = self._read_prefix()
        fs = self._prefix.file_size
        rs = self._prefix.log_rec_size
        db = self._prefix.data_base
        self._idx_n = self._scan_index_count()

        # ── Binary search: number of used slots ──
        # Slot j (0 = oldest) sits at address fs - (j+1)*rs.
        # Empty slots (0xFF) are above bot_ptr; used ones are below it.
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

        if lo == 0:
            self._top_ptr = db
            self._count   = 0
            self._seq     = 0
            return

        # ── Find the newest valid checkpoint (from the newest slot backward) ──
        start_slot = 0
        count      = 0
        top_ptr    = db
        last_seq   = -1
        for slot in range(lo - 1, -1, -1):
            rec, ok = MlaLog.from_bytes(self._hal.read(fs - (slot + 1) * rs, rs))
            if ok and rec.is_live and rec.is_checkpoint:
                count    = (rec.station << 16) | rec.region
                top_ptr  = rec.offset
                last_seq = rec.seq
                start_slot = slot + 1
                break

        # ── Forward scan of the tail (slots start_slot .. lo-1; no checkpoints) ──
        for slot in range(start_slot, lo):
            addr = fs - (slot + 1) * rs
            rec, ok = MlaLog.from_bytes(self._hal.read(addr, rs))
            is_newest = (slot == lo - 1)

            if not ok:
                # Torn lock write — the lock was only partially written. The data
                # region is clean; we merely burned a slot. Keep top_ptr.
                continue

            last_seq = rec.seq

            if rec.is_checkpoint:
                # (There should be no checkpoint after the newest one, but just
                #  in case we honor it as an anchor point.)
                count   = (rec.station << 16) | rec.region
                top_ptr = rec.offset
                continue

            if rec.is_abandoned:
                if is_newest:
                    top_ptr = rec.offset   # abandoned record — the space is clean
                continue

            # LIVE data record
            if is_newest:
                # Verify the data was written. If power dropped between the lock
                # and the data, the MAGIC is missing (0xFF) → abandon the lock
                # (0xFF→0x00) and don't count it.
                magic = self._hal.read(rec.offset, 2)
                if magic != MLA_DATA_MAGIC:
                    self._hal.write(addr + 20, bytes([FLAG_ABANDONED]))
                    self._hal.sync()
                    top_ptr = rec.offset
                    continue
            top_ptr = rec.block_end
            count  += 1

        self._top_ptr = top_ptr
        self._count   = count
        self._seq     = (last_seq + 1) & 0xFFFF if last_seq >= 0 else 0

    # ── Write ──────────────────────────────────────────────────────────────────

    def append(self, timestamp: int, station: int, region: int,
               data: bytes, rec_type: int = ENC_RAW, kf_back: int = 0) -> None:
        """
        Append a data record. Commit protocol: LOCK first, DATA second.

        Steps:
          1. Write the lock (log record) at bot_ptr - log_rec_size  [flags = FLAG_LIVE]
          2. Write the data block at top_ptr  (MAGIC + data + CRC)
          3. Update the RAM pointers
          4. Every 2^checkpoint_shift records, write a checkpoint

        A torn write between steps 1 and 2 → on the next mount the lock is
        detected as ABANDONED (data missing) and safely skipped.

        rec_type — data type (ENC_RAW / ENC_DELTA / ENC_KEYFRAME / …); the
        container only carries it, it does not perform compression. kf_back —
        distance (in seq) to the owning keyframe (compression only, else 0).
        """
        if not self._prefix:
            raise RuntimeError("Call format() or mount() first")

        n = len(data)
        if not (1 <= n <= 65535):
            raise ValueError(f"Data: length must be 1–65535 B, not {n}")

        rs       = self._rs
        block_sz = 2 + n + 2  # MAGIC + DATA + CRC16
        if self._top_ptr + block_sz > self._bot_ptr - rs:
            raise RuntimeError("MLA file is full")

        new_bot = self._bot_ptr - rs

        # Step 1 — lock
        lock = MlaLog(timestamp=timestamp, offset=self._top_ptr,
                      station=station, region=region,
                      seq=self._seq & 0xFFFF, rec_type=rec_type,
                      length=n, kf_back=kf_back)
        self._hal.write(new_bot, lock.to_bytes())

        # Step 2 — data
        self._hal.write(self._top_ptr, self._build_block(data))

        # Step 3 — RAM
        self._top_ptr += block_sz
        self._bot_ptr  = new_bot
        self._n_slots += 1
        self._count   += 1
        self._seq      = (self._seq + 1) & 0xFFFF
        self._last_sta = station & 0xFFFF

        # Step 4 — checkpoint (+ index anchor)
        cs = self._prefix.checkpoint_shift
        if cs and (self._count % (1 << cs) == 0):
            self._write_checkpoint(timestamp)

    def _write_checkpoint(self, timestamp: int) -> None:
        """
        Write a checkpoint (register) — a special log record carrying the current
        fill state (top_ptr, count, seq). Takes no space in the data region.
        If there is no room, it is silently skipped (it is not mandatory, only
        a speed-up).
        """
        rs      = self._rs
        new_bot = self._bot_ptr - rs
        if new_bot <= self._top_ptr:
            return  # no room — the checkpoint is optional
        cp = MlaLog(timestamp=timestamp, offset=self._top_ptr,
                    station=(self._count >> 16) & 0xFFFF,
                    region=self._count & 0xFFFF,
                    seq=self._seq & 0xFFFF, rec_type=REC_CHECKPOINT,
                    length=0, kf_back=0)
        self._hal.write(new_bot, cp.to_bytes())
        self._bot_ptr  = new_bot
        self._n_slots += 1
        # The checkpoint occupies slot index (n_slots-1); an anchor lets the host
        # jump straight there when filtering by time.
        self._write_anchor(timestamp, self._n_slots - 1, self._last_sta)

    # ── Index region (host-side skip-table) ────────────────────────────────────

    def _scan_index_count(self) -> int:
        """Count anchors already written (linear scan until the first 0xFF status)."""
        cap = self._idx_capacity
        n = 0
        for i in range(cap):
            status = self._hal.read(self._pfx_size + i * MLA_IDX_REC_SIZE + 10, 1)[0]
            if status == IDX_UNUSED:
                break
            n += 1
        return n

    def _write_anchor(self, timestamp: int, slot: int, station: int) -> None:
        """
        Append one 12 B anchor (timestamp, slot, station) to the index region.
        Pure speed-up — silently skipped if the index is disabled or full.
        """
        if self._idx_n >= self._idx_capacity:
            return  # disabled (capacity 0) or region full
        addr = self._pfx_size + self._idx_n * MLA_IDX_REC_SIZE
        rec = struct.pack(_IDX_FMT, timestamp & 0xFFFFFFFF, slot & 0xFFFFFFFF,
                          station & 0xFFFF, IDX_LIVE, 0xFF)
        self._hal.write(addr, rec)
        self._idx_n += 1

    def read_index(self) -> list[tuple[int, int, int]]:
        """
        Read the skip-table: list of (timestamp, slot, station) for live anchors,
        in write order (ascending slot). Empty if the index is disabled/unused.
        """
        out: list[tuple[int, int, int]] = []
        for i in range(self._idx_n):
            raw = self._hal.read(self._pfx_size + i * MLA_IDX_REC_SIZE, MLA_IDX_REC_SIZE)
            ts, slot, sta, status, _ = struct.unpack(_IDX_FMT, raw)
            if status == IDX_LIVE:
                out.append((ts, slot, sta))
        return out

    def _start_slot_for_time(self, time_from: int | None) -> int:
        """
        Use the index to pick a safe starting log slot for records at >= time_from.
        Returns the slot of the newest anchor whose timestamp <= time_from (so the
        forward scan can't miss earlier-but-close records); 0 if none/!indexed.
        """
        if time_from is None:
            return 0
        # Use a strict '<': an anchor's slot is the CHECKPOINT slot, which sits
        # just after its triggering record. Starting one bucket back guarantees
        # we never skip a record whose timestamp equals time_from.
        # Anchors carry no CRC (kept at 12 B / NOR-flippable), so a torn last
        # anchor could hold a bogus slot. Ignore any slot past the live tail:
        # a wrong-but-too-low start only costs speed, never correctness, but a
        # too-high start could skip records — so we clamp against n_slots.
        start = 0
        for ts, slot, _sta in self.read_index():
            if slot >= self._n_slots:
                continue
            if ts < time_from:
                start = slot
            else:
                break
        return start

    def _build_block(self, data: bytes) -> bytes:
        """Assemble a data block: MAGIC + data + CRC16 (or 0xFFFF when no CRC)."""
        crc = crc16(data) if self._prefix.flags & 0x3 >= CRC_DATA else 0xFFFF
        return MLA_DATA_MAGIC + data + struct.pack("<H", crc)

    # ── Read ───────────────────────────────────────────────────────────────────

    def read_record(self, index: int) -> tuple[MlaLog, bytes]:
        """
        Read a record by order (0 = oldest, live data records only).
        Returns (MlaLog, data). Abandoned/torn/checkpoint records are skipped.
        """
        if not (0 <= index < self._count):
            raise IndexError(f"Index {index} out of range [0, {self._count})")
        fs = self._prefix.file_size
        rs = self._rs
        live = 0
        for slot in range(self._n_slots):
            raw = self._hal.read(fs - (slot + 1) * rs, rs)
            rec, crc_ok = MlaLog.from_bytes(raw)
            if not crc_ok or not rec.is_live or rec.is_checkpoint:
                continue
            if live == index:
                return rec, self._read_data(rec)
            live += 1
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
        """Iterate over all valid data records from the oldest."""
        yield from self.iter_slots(0)

    def iter_slots(self, start_slot: int = 0) -> Iterator[tuple[MlaLog, bytes]]:
        """
        Iterate valid data records starting at physical slot `start_slot`.
        Used by the index-accelerated scan to skip straight into the log.
        """
        fs = self._prefix.file_size
        rs = self._rs
        for slot in range(max(0, start_slot), self._n_slots):
            raw = self._hal.read(fs - (slot + 1) * rs, rs)
            rec, crc_ok = MlaLog.from_bytes(raw)
            if not crc_ok or not rec.is_live or rec.is_checkpoint:
                continue
            try:
                yield rec, self._read_data(rec)
            except ValueError:
                continue  # corrupted data block — skip

    def scan(self, *, time_from: int | None = None, time_to: int | None = None,
             station: int | None = None, region: int | None = None
             ) -> Iterator[tuple[MlaLog, bytes]]:
        """
        Index-accelerated query. Uses the skip-table to jump near `time_from`,
        then scans forward applying the filters. Falls back to a full scan when
        the index is absent. Same result as filtering every record — only faster.
        """
        start = self._start_slot_for_time(time_from)
        for rec, data in self.iter_slots(start):
            if time_from is not None and rec.timestamp < time_from:
                continue
            if time_to is not None and rec.timestamp > time_to:
                continue
            if station is not None and rec.station != station:
                continue
            if region is not None and rec.region != region:
                continue
            yield rec, data

    def sync(self) -> None:
        self._hal.sync()

    # ── Emergency recovery ─────────────────────────────────────────────────────

    def recover(self) -> int:
        """
        Emergency log recovery by scanning the data region.
        Use only when the entire log region is damaged or erased.

        Algorithm:
          Look for MAGIC 0xAB 0xCD, then try lengths 1..65535 until the data CRC
          matches. For each valid block, create a log record (timestamp=0,
          rec_type=raw).

        NOTE: Requires CRC_DATA or CRC_FULL. Without a CRC the length cannot be
        verified. Slow — emergency use only. Returns the number of recovered records.
        """
        self._prefix = self._read_prefix()
        fs = self._prefix.file_size
        rs = self._prefix.log_rec_size

        if self._prefix.flags & 0x3 < CRC_DATA:
            raise RuntimeError("Recovery requires CRC_DATA or CRC_FULL")

        db = self._prefix.data_base
        recovered: list[MlaLog] = []
        pos      = db
        data_end = db

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
                    recovered.append(
                        MlaLog(timestamp=0, offset=pos, station=0, region=0,
                               seq=len(recovered) & 0xFFFF, rec_type=ENC_RAW,
                               length=length)
                    )
                    data_end = end
                    pos = end
                    found = True
                    break
            if not found:
                pos += 1

        # Rewrite the log with the recovered content
        self._top_ptr = data_end
        self._bot_ptr = fs
        self._n_slots = 0
        self._count   = 0
        self._seq     = 0
        # The old index anchors reference pre-recovery slot numbers and are now
        # meaningless — drop them so scan() falls back to a full (correct) scan.
        self._idx_n   = 0

        for rec in recovered:
            if self._top_ptr + 4 <= self._bot_ptr - rs:
                new_bot = self._bot_ptr - rs
                self._hal.write(new_bot, rec.to_bytes())
                self._bot_ptr  = new_bot
                self._n_slots += 1
                self._count   += 1
                self._seq      = (self._seq + 1) & 0xFFFF

        self._hal.sync()
        return self._count


# ──────────────────────────────────────────────────────────────────────────────
#  Example / quick smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    PATH = "/tmp/_nic_mla_test.bin"
    SIZE = 64 * 1024  # 64 KB — enough for the test

    print("── Format and write ──")
    hal = MlaPosixHAL.create(PATH, SIZE)
    with hal:
        mla = MlaCore(hal)
        mla.format(file_size=SIZE, crc_mode=CRC_FULL)
        print(f"  Formatted | free: {mla.free_bytes} B")

        for i in range(6):
            payload = bytes([i * 10, i * 20, 0xAB, 0xCD, i])  # 5 B
            mla.append(timestamp=int(time.time()) + i,
                       station=42, region=i, data=payload)
        mla.sync()
        print(f"  Wrote 6 records | free: {mla.free_bytes} B")

    print("\n── Mount and read ──")
    with MlaPosixHAL(PATH) as hal2:
        mla2 = MlaCore(hal2)
        mla2.mount()
        print(f"  After mount: {mla2.record_count} records")

        for rec, data in mla2:
            print(f"  slot ts={rec.timestamp}  sta={rec.station:3d}"
                  f"  ch={rec.region}  seq={rec.seq}  len={rec.length}"
                  f"  data={data.hex()}")

    print("\n── Round-trip verification ──")
    with MlaPosixHAL(PATH) as hal3:
        mla3 = MlaCore(hal3)
        mla3.mount()
        for i in range(6):
            rec, data = mla3.read_record(i)
            expected = bytes([i * 10, i * 20, 0xAB, 0xCD, i])
            assert data == expected, f"MISMATCH record {i}: {data.hex()} != {expected.hex()}"
        print("  All records OK ✓")

    os.remove(PATH)
    print("\nTest complete ✓  ★ Viva La Resistánce ★")
