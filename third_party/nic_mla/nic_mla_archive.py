#!/usr/bin/env python3
"""
nic_mla_archive.py  —  NIC-MLA: file rotation + host-side queries

Contains:
  MlaArchive  — rotation manager for containers (NIC0000.MLA, NIC0001.MLA, …).
                When the current file fills up, it automatically starts the next
                one. Intended for filesystems (FAT/SD/POSIX), where rotation is
                a simpler and crash-safe alternative to "full" (each file is
                independently mountable; self-describing via prefix.file_seq).

  query(...)   — host-side helper for flat record filtering (time / station /
                 region / type). Runs only on a PC/RPi, where the log is swept
                 in RAM — the chip stays lean (write-only).

These things DO NOT belong on the ATmega — they are strictly host-side / for
more capable platforms.

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
import re
from typing import Iterator, Iterable

from nic_mla import (
    MlaCore, MlaPosixHAL, MlaLog,
    MLA_DEFAULT_SIZE, CRC_FULL, ENC_RAW,
)

# Station index helper: the log carries a 1-byte station index into the prefix
# station table. Translation index ↔ real number is the host glue's job.


# ──────────────────────────────────────────────────────────────────────────────
#  MlaArchive — file rotation
# ──────────────────────────────────────────────────────────────────────────────

class MlaArchive:
    """
    A rotating set of containers in one directory: NIC0000.MLA, NIC0001.MLA, …

    Writing:
        arch = MlaArchive("/data", file_size=1<<20)
        arch.append(ts, station, region, data)   # rotates itself when full
        arch.close()

    Reading (across all files in order):
        for rec, payload in MlaArchive("/data"):
            ...
    """

    _NAME_RE = None  # set in __init__ based on base

    def __init__(self, directory: str,
                 file_size:     int = MLA_DEFAULT_SIZE,
                 base:          str = "MLA",
                 digits:        int = 5,
                 crc_mode:      int = CRC_FULL,
                 cluster_shift: int = 12,
                 keyframe_intv: int = 8,
                 schema_table:  bytes = b"",
                 station_table: bytes = b""):
        self.dir       = directory
        self.base      = base
        self.digits    = digits
        self.file_size = file_size
        self._fmt = dict(crc_mode=crc_mode, cluster_shift=cluster_shift,
                         keyframe_intv=keyframe_intv,
                         schema_table=schema_table, station_table=station_table)
        # Aligned with the MCU example (atmega_sd_writeonly.ino): MLA00000.MLA …
        self._NAME_RE = re.compile(rf"{re.escape(base)}(\d{{{digits}}})\.MLA$")

        os.makedirs(directory, exist_ok=True)
        self._seq:  int | None = None
        self._hal:  MlaPosixHAL | None = None
        self._core: MlaCore | None = None

    # ── Paths and file enumeration ──────────────────────────────────────────

    def _path(self, seq: int) -> str:
        return os.path.join(self.dir, f"{self.base}{seq:0{self.digits}d}.MLA")

    def existing_seqs(self) -> list[int]:
        """Return a sorted list of sequence numbers of existing containers."""
        seqs = []
        if os.path.isdir(self.dir):
            for name in os.listdir(self.dir):
                m = self._NAME_RE.match(name)
                if m:
                    seqs.append(int(m.group(1)))
        return sorted(seqs)

    @property
    def file_count(self) -> int:
        return len(self.existing_seqs())

    # ── Writing (with rotation) ─────────────────────────────────────────────

    def _ensure_writer(self) -> None:
        """Open the last file for writing, or create NIC0000.MLA."""
        if self._core is not None:
            return
        seqs = self.existing_seqs()
        if not seqs:
            self._seq = 0
            self._create_and_format(0)
        else:
            self._seq = seqs[-1]
            self._hal = MlaPosixHAL(self._path(self._seq))
            self._hal.__enter__()
            self._core = MlaCore(self._hal)
            self._core.mount()

    def _create_and_format(self, seq: int) -> None:
        MlaPosixHAL.create(self._path(seq), self.file_size)
        self._hal = MlaPosixHAL(self._path(seq))
        self._hal.__enter__()
        self._core = MlaCore(self._hal)
        self._core.format(file_size=self.file_size, file_seq=seq,
                          container_kind=1, **self._fmt)

    def _rotate(self) -> None:
        """Close the current file and start the next one in sequence."""
        self.sync()
        if self._hal is not None:
            self._hal.__exit__()
        self._seq = (self._seq or 0) + 1
        self._core = None
        self._create_and_format(self._seq)

    def append(self, timestamp: int, station: int, data: bytes,
               rec_type: int = ENC_RAW, kf_back: int = 0) -> None:
        """Append a record; rotate to the next file when the current one fills up.

        station — 1-byte index into the prefix station table (see MlaCore.append).
        """
        self._ensure_writer()
        try:
            self._core.append(timestamp, station, data, rec_type, kf_back)
        except RuntimeError:
            # Current file is full → next one in sequence and retry.
            self._rotate()
            self._core.append(timestamp, station, data, rec_type, kf_back)

    def sync(self) -> None:
        if self._core is not None:
            self._core.sync()

    def close(self) -> None:
        self.sync()
        if self._hal is not None:
            self._hal.__exit__()
            self._hal = None
            self._core = None

    def __enter__(self) -> MlaArchive:
        return self

    def __exit__(self, *_) -> None:
        self.close()

    # ── Reading (across all files) ──────────────────────────────────────────

    def __iter__(self) -> Iterator[tuple[MlaLog, bytes]]:
        """
        Iterate records across all containers in file_seq order, from the oldest.
        Read-only and independent of any open writer — each file is opened,
        mounted and swept separately.
        """
        for seq in self.existing_seqs():
            with MlaPosixHAL(self._path(seq)) as hal:
                core = MlaCore(hal)
                core.mount()
                yield from core

    @property
    def total_records(self) -> int:
        """Number of valid data records across all files."""
        return sum(1 for _ in self)


# ──────────────────────────────────────────────────────────────────────────────
#  query — host-side helper for flat filtering
# ──────────────────────────────────────────────────────────────────────────────

def query(source: Iterable[tuple[MlaLog, bytes]], *,
          time_from: int | None = None,
          time_to:   int | None = None,
          station:   int | None = None,
          rec_type:  int | None = None,
          enc:       int | None = None) -> Iterator[tuple[MlaLog, bytes]]:
    """
    Filter records from any source that iterates (MlaLog, data) —
    i.e. both MlaCore and MlaArchive.

    Filters (None = do not apply):
      time_from / time_to — closed interval of timestamps (Unix seconds)
      station             — exact match on the station index
      rec_type            — exact match of the whole rec_type byte
      enc                 — match on encoding only (low nibble of rec_type)

    The search runs on the host over the loaded log — no on-disk tree.
    """
    for rec, data in source:
        if time_from is not None and rec.timestamp < time_from:
            continue
        if time_to is not None and rec.timestamp > time_to:
            continue
        if station is not None and rec.station != station:
            continue
        if rec_type is not None and rec.rec_type != rec_type:
            continue
        if enc is not None and (rec.rec_type & 0x0F) != enc:
            continue
        yield rec, data


# ──────────────────────────────────────────────────────────────────────────────
#  Example / quick smoke test
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="nic_mla_arch_")
    try:
        print("── Rotation: writing 300 records into small 2 KB files ──")
        with MlaArchive(tmp, file_size=2048) as arch:
            for i in range(300):
                arch.append(1_600_000_000 + i, station=1 + (i % 4),
                            data=bytes([i & 0xFF] * 3))
        arch_ro = MlaArchive(tmp, file_size=2048)
        print(f"  Files created:   {arch_ro.file_count}")
        print(f"  Total records:   {arch_ro.total_records}")

        print("\n── Query: station index 2 only ──")
        n = sum(1 for _ in query(arch_ro, station=2))
        print(f"  Records on station 2: {n}")

        print("\n── Query: time window 1_600_000_010 .. 1_600_000_020 ──")
        rows = list(query(arch_ro, time_from=1_600_000_010, time_to=1_600_000_020))
        print(f"  Records in window: {len(rows)}  "
              f"(ts {rows[0][0].timestamp}..{rows[-1][0].timestamp})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nDone ✓  ★ Viva La Resistánce ★")
