"""
MlaBackend — browse the records inside an NIC-MLA container as if they were files.

Pressing Enter on an .mla file "steps inside" it: each logged record shows up as
a panel item. This backend is a **thin adapter** over the dumb libraries — it
reads records via ``nic_mla``, gives the opaque 1-byte station index a meaning
via ``stations`` (the host glue), decodes packed payloads via the schema reader
in ``mla_schema``, and delegates CSV/SQL serialisation to ``export``. It owns no
format or serialisation logic of its own; it only adapts those libraries to the
file-manager panel.

The whole container is read into RAM on open (the documented host model), so the
file handle is closed immediately — there is no open-file lifecycle to manage.
"""

from __future__ import annotations

import os
import struct
import sys
from dataclasses import replace
from datetime import datetime

from . import export
from .backend import Backend, BackendError, Entry, Unsupported
from .stations import StationMap

# Make the vendored MLA reference (and its host-only schema tool) importable.
_MLA_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "third_party", "nic_mla"))
for _p in (_MLA_DIR, os.path.join(_MLA_DIR, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from nic_mla import MlaCore, MlaPosixHAL, MlaLog, MlaPrefix  # noqa: E402
    from mla_schema import (  # noqa: E402
        read_schema, decode_value as _decode_field,
        read_stations, split_station, SchemaBuilder, StationTable,
    )
except Exception as exc:  # pragma: no cover - only if vendoring is broken
    raise BackendError(f"NIC-MLA library not available: {exc}") from exc

# rec_type decoding (high nibble = class, low nibble = encoding)
_ENC = {0x0: "raw", 0x1: "delta", 0x2: "keyframe", 0x3: "text"}
_CLS = {0x00: "measure", 0x10: "event", 0x20: "config", 0xF0: "checkpoint"}

# units that carry no human suffix (dimensionless / identifier)
_BARE_UNITS = {"raw", "id", "count"}


def rec_type_name(rt: int) -> str:
    cls = _CLS.get(rt & 0xF0, f"cls{rt >> 4:X}")
    enc = _ENC.get(rt & 0x0F, f"enc{rt & 0xF:X}")
    return f"{cls}/{enc}"


def _is_text(rec) -> bool:
    """A text/JSON record (encoding nibble 0x3) — never decode it as numbers."""
    return (rec.rec_type & 0x0F) == 0x3


def _fmt_num(v) -> str:
    """Compact human form: trim trailing zeros on floats, plain ints as-is."""
    if isinstance(v, float):
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s if s else "0"
    return str(v)


class MlaBackend(Backend):
    """Read-only-ish view of an .mla container's records."""

    def __init__(self, path: str, parent: Backend):
        self.path = os.path.abspath(path)
        self._parent = parent
        self._records: list[tuple] = []   # [(MlaLog, bytes)]
        self._stations = StationMap(None)  # index → (region, number) glue
        self._log_fields = None            # schema LOG-header fields (or None)
        self._data_fields = None           # schema DATA-payload fields (or None)
        self._summary: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with MlaPosixHAL(self.path) as hal:
                core = MlaCore(hal)
                core.mount()
                self._records = list(core)  # host model: read it all into RAM
                self._read_tables(hal, core)
                self._summary = self._summarize(self._records)
                self._summary.update(self._scan_health(core))
        except Exception as exc:
            raise BackendError(f"Cannot open MLA: {exc}") from exc

    def _read_tables(self, hal, core) -> None:
        """Pull the self-describing schema + station tables out of the prefix."""
        try:
            raw_prefix = hal.read(0, core._prefix.size)
            self._log_fields, self._data_fields = read_schema(raw_prefix)
            self._stations = StationMap.from_prefix(raw_prefix)
        except Exception:
            # an unreadable / unsupported table must not block browsing
            self._log_fields = self._data_fields = None
            self._stations = StationMap(None)

    @property
    def has_schema(self) -> bool:
        return bool(self._data_fields)

    @staticmethod
    def _scan_health(core) -> dict:
        """Walk every physical slot and classify it (for F2 Repair).

        v1.0 model: a slot whose lock CRC matches is committed; one that fails is
        a burned slot (torn lock, or a record abandoned by zeroing it). A
        committed lock whose data block won't read back is real damage.
        """
        ok = dead = bad_data = 0
        try:
            fs = core._prefix.file_size
            rs = core._rs
            for slot in range(core._n_slots):
                rec, crc_ok = MlaLog.from_bytes(core._hal.read(fs - (slot + 1) * rs, rs))
                if not crc_ok:
                    dead += 1
                    continue
                try:
                    core._read_data(rec)
                    ok += 1
                except Exception:
                    bad_data += 1
        except Exception:
            pass
        return {"h_ok": ok, "h_dead": dead, "h_bad_data": bad_data}

    @staticmethod
    def _summarize(records) -> dict:
        stations = sorted({r.station for r, _ in records})
        times = [r.timestamp for r, _ in records]
        return {
            "count": len(records),
            "stations": stations,
            "time_from": min(times) if times else None,
            "time_to": max(times) if times else None,
        }

    @property
    def location(self) -> str:
        return self.path

    @property
    def label(self) -> str:
        return os.path.basename(self.path)

    # ── browsing ────────────────────────────────────────────────────────────
    def list(self) -> list[Entry]:
        out = [Entry("..", True, 0, None, "updir")]
        for i, (rec, _data) in enumerate(self._records):
            when = datetime.fromtimestamp(rec.timestamp).strftime("%d.%m.%y %H:%M:%S")
            name = "%05d  %s  %-11s %s" % (
                i, when, self._stations.label(rec.station),
                rec_type_name(rec.rec_type),
            )
            stamp = datetime.fromtimestamp(rec.timestamp).strftime("%Y%m%d_%H%M%S")
            export_name = "rec%05d_%s_st%d.bin" % (i, stamp, rec.station)
            out.append(Entry(
                name=name, is_container=False, size=rec.length,
                mtime=rec.timestamp, kind="record",
                meta={"idx": i, "export_name": export_name},
            ))
        return out

    def enter(self, entry: Entry) -> "Backend | None":
        if entry.name == "..":
            return self._parent  # back out to the directory holding the .mla
        return None  # records are leaves

    # ── reading ─────────────────────────────────────────────────────────────
    def read(self, entry: Entry) -> bytes:
        idx = entry.meta.get("idx")
        if idx is None or not (0 <= idx < len(self._records)):
            raise BackendError("No such record")
        return self._records[idx][1]

    def info(self, entry: Entry) -> list[tuple[str, str]]:
        if entry.name == "..":
            return self._container_info()
        idx = entry.meta.get("idx")
        rec, data = self._records[idx]
        ts = datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        rows = [
            ("Record (index)", str(idx)),
            ("Time", f"{ts}  (unix {rec.timestamp})"),
            ("Station", self._station_detail(rec.station)),
            ("Type", f"0x{rec.rec_type:02X}  {rec_type_name(rec.rec_type)}"),
            ("Length", f"{rec.length} B"),
        ]
        if rec.kf_back:
            rows.append(("Keyframe back", str(rec.kf_back)))
        decoded = None if _is_text(rec) else self._decode_row(data)
        if decoded is not None:
            for name, unit, value in decoded:
                suffix = "" if unit in _BARE_UNITS else f" {unit}"
                rows.append((name, f"{_fmt_num(value)}{suffix}"))
        elif len(data) == 4:  # convenience decodes for tiny payloads
            rows.append(("As float32", f"{struct.unpack('<f', data)[0]:.4f}"))
            rows.append(("As int32", str(struct.unpack('<i', data)[0])))
        return rows

    def _station_detail(self, index: int) -> str:
        rn = self._stations.resolve(index)
        if rn is None:
            return f"index {index}"
        return f"index {index}  →  region {rn[0]}, number {rn[1]}"

    def _container_info(self) -> list[tuple[str, str]]:
        s = self._summary
        rows = [
            ("MLA file", self.path),
            ("Size", f"{os.path.getsize(self.path)} B"),
            ("Records", str(s.get("count", 0))),
        ]
        idxs = s.get("stations") or []
        if idxs:
            rows.append(("Stations", ", ".join(self._stations.label(i) for i in idxs)))
        if s.get("time_from") is not None:
            fr = datetime.fromtimestamp(s["time_from"]).strftime("%Y-%m-%d %H:%M")
            to = datetime.fromtimestamp(s["time_to"]).strftime("%Y-%m-%d %H:%M")
            rows.append(("Time range", f"{fr} … {to}"))
        if self._data_fields:
            names = ", ".join(f.name for f in self._data_fields)
            rows.append(("Schema", f"{len(self._data_fields)} data fields: {names}"))
        return rows

    # ── value decoding (F4 View-with-values) ─────────────────────────────────
    def _decode_row(self, data: bytes):
        """Decode a packed payload via the schema → [(name, unit, value), …].

        Returns None when there is no schema or the payload width doesn't match
        (e.g. a text/event record in a measurement-schema file).
        """
        fields = self._data_fields
        if not fields or len(data) != sum(f.width for f in fields):
            return None
        out, pos = [], 0
        try:
            for f in fields:
                chunk = data[pos:pos + f.width]
                pos += f.width
                out.append((f.name, f.unit, _decode_field(f, chunk)))
        except Exception:
            return None
        return out

    def decode_value(self, entry: Entry) -> str:
        """Best-effort human value of a record's payload.

        With a schema, a measurement payload decodes into all of its named sensor
        columns. Without one, fall back to the historical guess.
        """
        idx = entry.meta.get("idx")
        rec, data = self._records[idx]
        if _is_text(rec):
            return data.decode("utf-8", "replace")
        decoded = self._decode_row(data)
        if decoded is not None:
            parts = []
            for name, unit, value in decoded:
                suffix = "" if unit in _BARE_UNITS else f" {unit}"
                parts.append(f"{name}={_fmt_num(value)}{suffix}")
            return "  ".join(parts)
        if len(data) == 4:
            try:
                return f"{struct.unpack('<f', data)[0]:.4f}"
            except struct.error:
                pass
        if len(data) in (1, 2, 4):
            return str(int.from_bytes(data, "little"))
        return data.hex(" ")

    # ── exports (rows assembled here, serialised by the dumb export lib) ──────
    _BASE_HEADERS = ("idx", "time", "unix", "sta_idx", "region", "number",
                     "type", "length")
    _BASE_SQL = (("idx", "INTEGER"), ("time", "TEXT"), ("unix", "INTEGER"),
                 ("sta_idx", "INTEGER"), ("region", "INTEGER"),
                 ("number", "INTEGER"), ("type", "TEXT"), ("length", "INTEGER"))

    def _base_cells(self, idx: int, rec) -> list:
        ts = datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d %H:%M:%S")
        rn = self._stations.resolve(rec.station)
        region, number = (rn if rn else (None, None))
        return [idx, ts, rec.timestamp, rec.station, region, number,
                rec_type_name(rec.rec_type), rec.length]

    def _data_values(self, rec, data: bytes, raw: bool):
        """Per-field native values for a packed payload, or None if it doesn't fit."""
        fields = self._data_fields
        if _is_text(rec) or not fields or len(data) != sum(f.width for f in fields):
            return None
        out, pos = [], 0
        for f in fields:
            chunk = data[pos:pos + f.width]
            pos += f.width
            out.append(int.from_bytes(chunk, "little", signed=f.signed) if raw
                       else _decode_field(f, chunk))
        return out

    def _rows(self, raw: bool, stringify: bool):
        """Yield export rows. stringify=True formats numbers for CSV cells."""
        for idx, (rec, data) in enumerate(self._records):
            base = self._base_cells(idx, rec)
            if self._data_fields:
                vals = self._data_values(rec, data, raw)
                if vals is None:
                    vals = [None] * len(self._data_fields)
                elif stringify:
                    vals = [_fmt_num(v) for v in vals]
                yield base + list(vals)
            else:
                val = self.decode_value(Entry("", meta={"idx": idx}))
                yield base + [val]

    def to_csv(self, raw: bool = False) -> bytes:
        if self._data_fields:
            headers = list(self._BASE_HEADERS) + [f.name for f in self._data_fields]
        else:
            headers = list(self._BASE_HEADERS) + ["value"]
        return export.to_csv(headers, self._rows(raw, stringify=True))

    def to_sqlite(self, raw: bool = False) -> bytes:
        if self._data_fields:
            cols = list(self._BASE_SQL) + [(f.name, "NUMERIC") for f in self._data_fields]
        else:
            cols = list(self._BASE_SQL) + [("value", "TEXT")]
        return export.to_sqlite(cols, self._rows(raw, stringify=False))

    def csv_name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0] + ".csv"

    def sqlite_name(self) -> str:
        return os.path.splitext(os.path.basename(self.path))[0] + ".db"

    # ── F2 Repair — check the file and report ────────────────────────────────
    def repair_info(self) -> list[tuple[str, str]]:
        s = self._summary
        rows = [
            ("File", os.path.basename(self.path)),
            ("Valid records", str(s.get("h_ok", 0))),
            ("Dead slots (torn / abandoned)", str(s.get("h_dead", 0))),
            ("Unreadable data block", str(s.get("h_bad_data", 0))),
        ]
        damaged = s.get("h_bad_data", 0)
        verdict = "OK — no damage found" if damaged == 0 else \
                  f"{damaged} committed record(s) with an unreadable data block"
        rows.append(("Verdict", verdict))
        return rows

    # ── schema / station table editing (F4 editor — values only) ──────────────
    # Editing the prefix tables is allowed (it is NOT appending records): we only
    # ever change the CONTENT of existing descriptors, never their count, so the
    # table byte-length and the prefix size stay fixed and the file layout (the
    # data_base, the records) is never disturbed. The prefix is written back with
    # a fresh CRC. Adding/removing fields is intentionally out of scope.

    @property
    def editable(self) -> bool:
        return bool(self._data_fields) or self._stations.present

    def schema_view(self) -> list[dict]:
        """Editable view of the DATA schema fields."""
        return [{"name": f.name, "unit": f.unit, "width": f.width,
                 "exp10": f.exp10, "signed": f.signed, "offset": f.offset}
                for f in (self._data_fields or [])]

    def station_view(self) -> list[dict]:
        """Editable view of the station table (index 1..n → region/number)."""
        out = []
        for i, rec in enumerate(self._stations.records, start=1):
            region, number, _res = split_station(rec)
            out.append({"index": i, "region": region, "number": number})
        return out

    def edit_schema_field(self, i: int, **changes) -> None:
        """Change one DATA field's content (name/unit/exp10/signed/offset)."""
        fields = list(self._data_fields or [])
        if not (0 <= i < len(fields)):
            raise BackendError("No such schema field")
        bad = set(changes) - {"name", "unit", "exp10", "signed", "offset"}
        if bad:
            raise BackendError(f"Cannot edit: {', '.join(sorted(bad))}")
        try:
            edited = replace(fields[i], **changes)
            edited.descriptor()  # validate ranges (width/unit/exp10/offset/name)
        except (ValueError, TypeError) as exc:
            raise BackendError(f"Invalid value: {exc}") from exc
        fields[i] = edited
        self._rewrite_tables(data_fields=fields)

    def edit_station(self, i: int, *, region: int, number: int) -> None:
        """Change one station's region/number (0-based i); reserved preserved."""
        recs = self._stations.records
        if not (0 <= i < len(recs)):
            raise BackendError("No such station")
        _r, _n, reserved = split_station(recs[i])
        try:
            st = StationTable()
            for j, rec in enumerate(recs):
                if j == i:
                    st.station(region=region, number=number, reserved=reserved)
                else:
                    st.raw(rec)
            new_station = st.table()
        except (ValueError, TypeError) as exc:
            raise BackendError(f"Invalid value: {exc}") from exc
        self._rewrite_tables(station_table=new_station)

    def _rewrite_tables(self, data_fields=None, station_table=None) -> None:
        """Write edited schema/station tables back into the prefix (values only).

        Rebuilds the prefix with new table bytes of the SAME length, recomputes
        its CRC, writes it at offset 0, and reloads. Refuses any change that would
        resize the prefix (which would move data_base and corrupt the file).
        """
        try:
            with MlaPosixHAL(self.path) as hal:
                raw = hal.read(0, MlaPrefix.parse_size(hal.read(0, 512)))
                pfx = MlaPrefix.from_bytes(raw)
                if data_fields is not None:
                    sb = SchemaBuilder()
                    sb.log_fields = list(self._log_fields or [])
                    sb.data_fields = list(data_fields)
                    pfx.schema_table = sb.table()
                if station_table is not None:
                    pfx.station_table = station_table
                new = pfx.to_bytes()
                if len(new) != len(raw):
                    raise BackendError("edit would resize the prefix — refused")
                hal.write(0, new)
                hal.sync()
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Cannot write tables: {exc}") from exc
        self._load()  # re-read everything from disk

    # ── mutating — intentionally limited inside MLA ───────────────────────────
    # By design the MLA container is append-only and crash-safe: the GUI does not
    # edit records in place. Copy a record OUT (F5) to work on it.
    _RO = "MLA is append-only by design — copy a record out (F5) to work on it."

    def mkdir(self, name: str) -> None:
        raise Unsupported(self._RO)

    def delete(self, entry: Entry) -> None:
        raise Unsupported(self._RO)

    def rename(self, entry: Entry, new_name: str) -> None:
        raise Unsupported(self._RO)

    def put_file(self, name: str, data: bytes) -> None:
        raise Unsupported(self._RO)
