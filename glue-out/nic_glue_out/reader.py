# SPDX-License-Identifier: MIT
"""
GlueReader — the read-side connector itself.

This is the mirror image of NIC-GLUE-IN's ``GlueLogger``. The logger's job was
to *fill in* the seams between the dumb libraries on the way into a container;
the reader's job is to *read those same seams back out* on the way to a table:

  • NIC-MLA v1.1 carries no type byte: each record only knows whether it is
    ``compressed`` and its ``kf_back``. The reader derives the record *kind*
    ("raw"/"keyframe"/"delta") from those. Uncompressed ("raw") payloads are
    decoded straight from the schema; compressed records (keyframe/delta — a
    NIC-DMD blob) are **decompressed** by replaying each station's stream in
    order (one ``DmdEncoder``-mirroring decoder per station), then decoded from
    the schema exactly like a raw payload. The keyframe/delta distinction is the
    codec's; once decoded the values are the original sensor readings.

  • The self-describing SCHEMA table in the prefix says how the packed data
    bytes split into named, scaled values; ``mla_decode_payload`` does the
    split. The reader pulls the table out of the prefix once on open.

  • The STATION table maps the log's 1-byte station *index* back to its real
    region/number. The reader resolves it so exported rows carry real numbers,
    not opaque indices.

NIC-DMD compression IS decoded here (the reader replays each station's stream);
encryption (NIC-KSF) is out of scope — see the README.

The whole container is read into RAM on open (the documented host model), so
there is no open-file lifecycle to manage beyond ``close()``.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from nic_mla import MlaCore, MlaPosixHAL
from mla_schema import mla_read_schema, mla_read_stations, mla_split_station, mla_decode_value
from nic_dmd import DmdDecoder

from . import export

# Units that carry no human suffix in a CSV/console cell.
_BARE_UNITS = {"raw", "id", "count"}


def record_kind_name(compressed: bool, kf_back: int) -> str:
    """Canonical record *kind* derived from the v1.1 log fields.

    MLA v1.1 no longer stores a type byte; a record's kind comes from whether
    it is ``compressed`` and its ``kf_back`` (these three kind names continue
    the old per-record encoding names and are shared verbatim with NIC-VDE):

        "raw"      → not compressed
        "keyframe" → compressed and kf_back == 0  (this record IS a keyframe)
        "delta"    → compressed and kf_back > 0   (refers back to a keyframe)
    """
    if not compressed:
        return "raw"
    return "keyframe" if kf_back == 0 else "delta"


@dataclass
class DecodedRecord:
    """One record, read back out of the container with its seams resolved."""

    index:      int                # position in the container (0 = oldest)
    timestamp:  int                # Unix seconds, from the log header
    subsec:     int                # two opaque bytes (0..65535); meaning owned by
                                   #   the glue (sub-second time and/or section/rotation)
    station:    int                # 1-byte station index (0 = none)
    compressed: bool               # was stored as a codec (DMD) blob
    kf_back:    int                # records back to the owning keyframe (0 = is one)
    length:     int                # stored data-block length
    block:      bytes              # the bytes as stored (sensor bytes, or a DMD blob)
    payload:    bytes | None       # decoded sensor bytes (raw as-is, or DMD-decompressed;
                                   #   None only if it couldn't be decoded)
    values:     list[tuple[str, str, float | int]] | None  # (name, unit, value) per field
    region:     int | None         # resolved station region (None if no table)
    number:     int | None         # resolved station number

    @property
    def kind(self) -> str:
        """Canonical record kind: "raw" / "keyframe" / "delta"."""
        return record_kind_name(self.compressed, self.kf_back)

    # `subsec` is two opaque bytes MLA assigns no meaning. Expose each byte on its
    # own so a reader can split the glue's convention (e.g. hi = section/rotation,
    # lo = sub-second tick) — same view as the MLA library's MlaLog.
    @property
    def subsec_lo(self) -> int:
        return self.subsec & 0xFF

    @property
    def subsec_hi(self) -> int:
        return (self.subsec >> 8) & 0xFF

    @property
    def is_compressed(self) -> bool:
        """True if the record was stored compressed (a DMD blob) on disk."""
        return self.compressed

    @property
    def undecoded(self) -> bool:
        """True only if no decoded sensor bytes are available (e.g. a compressed
        record in a schemaless file, or a blob that failed to decompress)."""
        return self.payload is None

    @property
    def station_label(self) -> str:
        if self.region is not None:
            return f"{self.region}/{self.number}"
        return f"#{self.station}"


class GlueReader:
    """A thin reader/exporter over a single NIC-MLA container.

    The everyday path is to iterate it (``for rec in reader``) or to export the
    whole thing (``reader.to_csv()`` / ``reader.to_sqlite()``). Open it on a
    path; it mounts the container, reads the schema/station tables, and loads
    every record into RAM.
    """

    def __init__(self, path: str):
        self.path = path
        self._hal = MlaPosixHAL(path)
        self._hal.__enter__()
        self._core = MlaCore(self._hal)
        self._core.mount()

        # Pull the self-describing tables out of the prefix (host-side helpers).
        raw_prefix = self._hal.read(0, self._core._prefix.size)
        try:
            self._log_fields, self._data_fields = mla_read_schema(raw_prefix)
        except ValueError:
            self._log_fields = self._data_fields = None
        self._stations = mla_read_stations(raw_prefix)   # list[bytes] | None

        # Host model: read it all into RAM, then the file handle is free.
        self._records = list(self._core)                 # [(MlaLog, bytes)]
        # Decompress NIC-DMD blobs up front: replay each station's stream in
        # order through its own decoder (keyframes reset it). Result is the
        # decoded sensor bytes per record (parallel to self._records); raw
        # records pass through untouched, undecodable ones become None.
        self._payloads = self._decompress_all()

    # ── introspection ────────────────────────────────────────────────────────
    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def has_schema(self) -> bool:
        return bool(self._data_fields)

    @property
    def data_fields(self):
        return list(self._data_fields or [])

    @property
    def keyframe_intv(self) -> int:
        return self._core._prefix.keyframe_intv

    def station_info(self, index: int) -> tuple[int, int] | None:
        """(region, number) for a station index (1..n), or None if unresolved."""
        if not self._stations or not (1 <= index <= len(self._stations)):
            return None
        region, number, _reserved = mla_split_station(self._stations[index - 1])
        return region, number

    # ── decompression (NIC-DMD) ────────────────────────────────────────────────
    def _decompress_all(self) -> list:
        """Decoded sensor bytes per record (parallel to self._records).

        Compressed records are run through a per-station ``DmdDecoder`` in the
        order they were written; the decoder needs the schema's data width as
        the packet length. Without a schema (no width) a compressed stream can't
        be sized, so those stay None. A blob that fails to decode also stays None
        — the reader never emits guessed values.
        """
        fields = self._data_fields
        pkt_len = sum(f.width for f in fields) if fields else None
        decoders: dict[int, DmdDecoder] = {}
        out: list[bytes | None] = []
        for rec, block in self._records:
            if not rec.compressed:
                out.append(block)
                continue
            if not pkt_len:
                out.append(None)
                continue
            dec = decoders.get(rec.station)
            if dec is None:
                dec = decoders[rec.station] = DmdDecoder(pkt_len)
            try:
                row = dec.decompress(block)
                out.append(row if len(row) == pkt_len else None)
            except Exception:
                out.append(None)
        return out

    # ── decoding ──────────────────────────────────────────────────────────────
    def _decode_values(self, block: bytes):
        """Split a packed payload into (name, unit, value) per schema field.

        Returns None when there is no schema or the block width does not match
        (e.g. a compressed blob in a measurement file).
        """
        fields = self._data_fields
        if not fields or len(block) != sum(f.width for f in fields):
            return None
        out, pos = [], 0
        try:
            for f in fields:
                out.append((f.name, f.unit, mla_decode_value(f, block[pos:pos + f.width])))
                pos += f.width
        except ValueError:
            return None
        return out

    def _decode(self, index: int, rec, block: bytes) -> DecodedRecord:
        # payload = decoded sensor bytes (raw as-is, or DMD-decompressed up front).
        payload = self._payloads[index]
        values = self._decode_values(payload) if payload is not None else None
        rn = self.station_info(rec.station)
        region, number = rn if rn else (None, None)
        return DecodedRecord(
            index=index, timestamp=rec.timestamp, subsec=rec.subsec,
            station=rec.station, compressed=rec.compressed,
            kf_back=rec.kf_back, length=rec.length, block=block,
            payload=payload, values=values,
            region=region, number=number,
        )

    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self):
        for i, (rec, block) in enumerate(self._records):
            yield self._decode(i, rec, block)

    def records(self, *, station: int | None = None,
                time_from: int | None = None, time_to: int | None = None):
        """Filtered iteration (host-side flat scan): time window and/or station."""
        for dr in self:
            if station is not None and dr.station != station:
                continue
            if time_from is not None and dr.timestamp < time_from:
                continue
            if time_to is not None and dr.timestamp > time_to:
                continue
            yield dr

    # ── export (rows assembled here, serialised by the dumb export module) ─────
    _BASE_HEADERS = ("idx", "time", "unix", "sta_idx", "region", "number",
                     "kind", "length")
    _BASE_SQL = (("idx", "INTEGER"), ("time", "TEXT"), ("unix", "INTEGER"),
                 ("sta_idx", "INTEGER"), ("region", "INTEGER"),
                 ("number", "INTEGER"), ("kind", "TEXT"), ("length", "INTEGER"))

    @staticmethod
    def _iso(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def _base_cells(self, dr: DecodedRecord) -> list:
        return [dr.index, self._iso(dr.timestamp), dr.timestamp, dr.station,
                dr.region, dr.number, dr.kind, dr.length]

    # `subsec` is two opaque bytes (meaning owned by the glue), so the table can
    # carry it either way: split=True → two byte columns (subsec_hi, subsec_lo,
    # the default — "by default it splits into two bytes"); split=False → one
    # 16-bit column (subsec) when the glue uses the field as a single value.
    @staticmethod
    def _subsec_headers(split: bool) -> list:
        return ["subsec_hi", "subsec_lo"] if split else ["subsec"]

    @staticmethod
    def _subsec_sql(split: bool) -> list:
        return [(n, "INTEGER") for n in GlueReader._subsec_headers(split)]

    @staticmethod
    def _subsec_cells(dr: DecodedRecord, split: bool) -> list:
        return [dr.subsec_hi, dr.subsec_lo] if split else [dr.subsec]

    def _field_values(self, dr: DecodedRecord, raw: bool):
        """Per-field native values for a RAW payload, or None if it doesn't fit.

        raw=True keeps the on-the-wire integers; raw=False applies the schema's
        scale/offset (the physical value).
        """
        if dr.payload is None or not self._data_fields:
            return None
        if len(dr.payload) != sum(f.width for f in self._data_fields):
            return None
        out, pos = [], 0
        for f in self._data_fields:
            chunk = dr.payload[pos:pos + f.width]
            pos += f.width
            out.append(int.from_bytes(chunk, "little", signed=f.signed) if raw
                       else mla_decode_value(f, chunk))
        return out

    @staticmethod
    def _fmt_num(v) -> str:
        if isinstance(v, float):
            s = f"{v:.6f}".rstrip("0").rstrip(".")
            return s if s else "0"
        return str(v)

    def _value_fallback(self, dr: DecodedRecord) -> str:
        """Best-effort single value for a schemaless file (mirror of NIC-VDE)."""
        if dr.undecoded:
            return f"<{dr.kind} {dr.length}B>"
        if len(dr.block) in (1, 2, 4):
            return str(int.from_bytes(dr.block, "little"))
        return dr.block.hex(" ")

    def _rows(self, raw: bool, stringify: bool, subsec_split: bool):
        for dr in self:
            base = self._base_cells(dr) + self._subsec_cells(dr, subsec_split)
            if self._data_fields:
                vals = self._field_values(dr, raw)
                if vals is None:
                    vals = [None] * len(self._data_fields)
                elif stringify:
                    vals = [self._fmt_num(v) for v in vals]
                yield base + list(vals)
            else:
                yield base + [self._value_fallback(dr)]

    def to_csv(self, *, raw: bool = False, subsec_split: bool = True) -> bytes:
        base = list(self._BASE_HEADERS) + self._subsec_headers(subsec_split)
        if self._data_fields:
            headers = base + [f.name for f in self._data_fields]
        else:
            headers = base + ["value"]
        return export.to_csv(headers, self._rows(raw, True, subsec_split))

    def to_sqlite(self, *, raw: bool = False, subsec_split: bool = True) -> bytes:
        base = list(self._BASE_SQL) + self._subsec_sql(subsec_split)
        if self._data_fields:
            cols = base + [(f.name, "NUMERIC") for f in self._data_fields]
        else:
            cols = base + [("value", "TEXT")]
        return export.to_sqlite(cols, self._rows(raw, False, subsec_split))

    def write_csv(self, out_path: str, *, raw: bool = False, subsec_split: bool = True) -> int:
        data = self.to_csv(raw=raw, subsec_split=subsec_split)
        with open(out_path, "wb") as f:
            f.write(data)
        return len(data)

    def write_sqlite(self, out_path: str, *, raw: bool = False, subsec_split: bool = True) -> int:
        data = self.to_sqlite(raw=raw, subsec_split=subsec_split)
        with open(out_path, "wb") as f:
            f.write(data)
        return len(data)

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def close(self) -> None:
        if self._hal is not None:
            self._hal.__exit__()
            self._hal = None

    def __enter__(self) -> "GlueReader":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class GlueArchiveReader(GlueReader):
    """Reader / exporter over a whole ROTATED archive — a directory of
    ``MLA00000.MLA``, ``MLA00001.MLA``, … written by NIC-GLUE-IN's
    ``GlueArchiveLogger``.

    Each file is independently decodable (its streams start on a keyframe and it
    carries its own schema/station tables), so the archive is read by sweeping the
    files in order and concatenating their records — the *same* iterate /
    ``to_csv()`` / ``to_sqlite()`` API as the single-file :class:`GlueReader`, with
    a global ``idx`` across the whole set.

    Memory: one file is decoded at a time (opened, yielded, closed), so a large
    archive never has to fit in RAM all at once.
    """

    def __init__(self, directory: str, *, base: str = "MLA", digits: int = 5):
        self.path = directory
        self.dir = directory
        self._hal = None                       # nothing held open at this level
        pat = re.compile(rf"{re.escape(base)}(\d{{{digits}}})\.MLA$")
        self._paths = [os.path.join(directory, n)
                       for n in sorted(os.listdir(directory)) if pat.match(n)]
        if not self._paths:
            raise FileNotFoundError(
                f"no {base}{'N' * digits}.MLA files in {directory!r}")
        # Schema / keyframe hint / stations come from the first file — MlaArchive
        # writes the same tables into every file's prefix.
        with GlueReader(self._paths[0]) as r0:
            self._log_fields = r0._log_fields
            self._data_fields = r0._data_fields
            self._stations = r0._stations
            self._kfi = r0.keyframe_intv
        # Total record count, cheaply: mount only, no decompression.
        total = 0
        for p in self._paths:
            with MlaPosixHAL(p) as hal:
                core = MlaCore(hal)
                core.mount()
                total += core.record_count
        self._count = total

    # ── introspection (override the ones that lean on _core / _records) ────────
    @property
    def record_count(self) -> int:
        return self._count

    def __len__(self) -> int:
        return self._count

    @property
    def keyframe_intv(self) -> int:
        return self._kfi

    @property
    def file_count(self) -> int:
        return len(self._paths)

    @property
    def files(self) -> list[str]:
        return list(self._paths)

    # ── iteration across files, with a global record index ─────────────────────
    def __iter__(self):
        gidx = 0
        for p in self._paths:
            with GlueReader(p) as r:
                for dr in r:
                    yield replace(dr, index=gidx)
                    gidx += 1

    def close(self) -> None:
        return None                            # each file is opened/closed per sweep
