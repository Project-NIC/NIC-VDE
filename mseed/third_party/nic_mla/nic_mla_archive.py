#!/usr/bin/env python3
"""
nic_mla_archive.py  —  NIC-MLA: file rotation + host-side queries

Contains:
  MlaArchive  — rotation manager for containers (NIC0000.MLA, NIC0001.MLA, …).
                When the current file fills up, it automatically starts the next
                one. Intended for filesystems (FAT/SD/POSIX), where rotation is
                a simpler and crash-safe alternative to "full" (each file is
                independently mountable; self-describing via prefix.file_seq).

  mla_query(...)   — host-side helper for flat record filtering (time / station /
                 region / type). Runs only on a PC/RPi, where the log is swept
                 in RAM — the chip stays lean (write-only).

These things DO NOT belong on the ATmega — they are strictly host-side / for
more capable platforms.

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
import re
from typing import Callable, Iterator, Iterable

from nic_mla import (
    MlaCore, MlaPosixHAL, MlaLog,
    MLA_DEFAULT_SIZE, MLA_CRC_FULL,
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
                 crc_mode:      int | None = None,
                 cluster_shift: int | None = None,
                 keyframe_intv: int | None = None,
                 schema_table:  bytes | None = None,
                 station_table: bytes | None = None,
                 on_rotate:     Callable[[int, int], None] | None = None):
        self.dir       = directory
        self.base      = base
        self.digits    = digits
        self.file_size = file_size
        # Format params default to None = "not supplied". On reopen of an
        # existing archive we INHERIT any unsupplied param from the file's own
        # prefix (see _backfill_fmt_from_prefix), so a later rotation produces a
        # file with the SAME tables/params — not empty ones.
        _defaults = dict(crc_mode=MLA_CRC_FULL, cluster_shift=12, keyframe_intv=0,
                         schema_table=b"", station_table=b"")
        supplied = dict(crc_mode=crc_mode, cluster_shift=cluster_shift,
                        keyframe_intv=keyframe_intv,
                        schema_table=schema_table, station_table=station_table)
        self._fmt_supplied = {k for k, v in supplied.items() if v is not None}
        self._fmt = {k: (v if v is not None else _defaults[k])
                     for k, v in supplied.items()}
        # Called as on_rotate(prev_seq, new_seq) right after a new file is
        # created on rotation — the seam the glue uses to force a keyframe so
        # each rotated file stays independently decodable (2b).
        self._on_rotate = on_rotate
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
            self._backfill_fmt_from_prefix()

    def _backfill_fmt_from_prefix(self) -> None:
        """2a: inherit format params/tables the caller did NOT supply from the
        mounted file's prefix, so the next rotated file matches the existing one
        instead of being written with empty tables."""
        pfx = self._core._prefix
        inherited = dict(crc_mode=pfx.flags, cluster_shift=pfx.cluster_shift,
                         keyframe_intv=pfx.keyframe_intv,
                         schema_table=pfx.schema_table,
                         station_table=pfx.station_table)
        for k, v in inherited.items():
            if k not in self._fmt_supplied:
                self._fmt[k] = v

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
        prev = self._seq or 0
        self._seq = prev + 1
        self._core = None
        self._create_and_format(self._seq)
        if self._on_rotate is not None:
            self._on_rotate(prev, self._seq)

    def will_rotate(self, data_len: int) -> bool:
        """Predict whether the next append of `data_len` payload bytes will
        rotate to a new file. The glue calls this BEFORE encoding so it can emit
        a keyframe up front — making the first record of every rotated file a
        keyframe, hence each file independently decodable (2b). RAW: ignore."""
        self._ensure_writer()
        return not self._core.has_room(data_len)

    def append(self, timestamp: int, station: int, data: bytes,
               *, subsec: int = 0, compressed: bool = False, kf_back: int = 0) -> bool:
        """Append a record; rotate to the next file when the current one fills up.

        station — 1-byte index into the prefix station table (see MlaCore.append).

        Returns True if this record landed in a FRESHLY ROTATED file. For a
        compressed (delta/keyframe) stream the glue should make sure that record
        was a keyframe — either by checking will_rotate() up front, or via the
        on_rotate callback — so each rotated file stays self-contained. MLA
        itself stays dumb: it only surfaces the event. For RAW data it is moot.
        """
        self._ensure_writer()
        try:
            self._core.append(timestamp, station, data,
                              subsec=subsec, compressed=compressed, kf_back=kf_back)
            return False
        except RuntimeError:
            # Current file is full → next one in sequence and retry.
            self._rotate()
            self._core.append(timestamp, station, data,
                              subsec=subsec, compressed=compressed, kf_back=kf_back)
            return True

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
#  mla_query — host-side helper for flat filtering
# ──────────────────────────────────────────────────────────────────────────────

def mla_query(source: Iterable[tuple[MlaLog, bytes]], *,
          time_from:  int | None = None,
          time_to:    int | None = None,
          station:    int | None = None,
          compressed: bool | None = None) -> Iterator[tuple[MlaLog, bytes]]:
    """
    Filter records from any source that iterates (MlaLog, data) —
    i.e. both MlaCore and MlaArchive.

    Filters (None = do not apply):
      time_from / time_to — closed interval of timestamps (Unix seconds)
      station             — exact match on the station index
      compressed          — match on the compressed flag (True/False)

    The search runs on the host over the loaded log — no on-disk tree.
    """
    for rec, data in source:
        if time_from is not None and rec.timestamp < time_from:
            continue
        if time_to is not None and rec.timestamp > time_to:
            continue
        if station is not None and rec.station != station:
            continue
        if compressed is not None and rec.compressed != compressed:
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
        n = sum(1 for _ in mla_query(arch_ro, station=2))
        print(f"  Records on station 2: {n}")

        print("\n── Query: time window 1_600_000_010 .. 1_600_000_020 ──")
        rows = list(mla_query(arch_ro, time_from=1_600_000_010, time_to=1_600_000_020))
        print(f"  Records in window: {len(rows)}  "
              f"(ts {rows[0][0].timestamp}..{rows[-1][0].timestamp})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\nDone ✓  ★ Viva La Resistánce ★")
