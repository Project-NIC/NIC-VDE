"""
MlaBackend — browse the records inside an NIC-MLA container as if they were files.

This is the Matroshka / Volkov Commander idea: pressing Enter on an .mla file
"steps inside" it, and each logged record shows up as an item in the panel. The
log carries the metadata (time, station, channel, type); the data block carries
only the payload — so the panel is built from the log alone, and the payload is
read lazily on view.

The whole container is read into RAM on open (the documented host model: "load
the log into RAM, then filter"), so the file handle is closed immediately and
there is no open-file lifecycle to manage.
"""

from __future__ import annotations

import os
import struct
import sys
from datetime import datetime

from .backend import Backend, BackendError, Entry, Unsupported

# Make the vendored MLA reference importable.
_MLA_DIR = os.path.join(os.path.dirname(__file__), "..", "third_party", "nic_mla")
if _MLA_DIR not in sys.path:
    sys.path.insert(0, os.path.abspath(_MLA_DIR))

try:
    from nic_mla import MlaCore, MlaPosixHAL, MlaLog  # noqa: E402
except Exception as exc:  # pragma: no cover - only if vendoring is broken
    raise BackendError(f"NIC-MLA library not available: {exc}") from exc

# rec_type decoding (high nibble = class, low nibble = encoding)
_ENC = {0x0: "raw", 0x1: "delta", 0x2: "keyframe", 0x3: "text"}
_CLS = {0x00: "measure", 0x10: "event", 0x20: "config", 0xF0: "checkpoint"}


def rec_type_name(rt: int) -> str:
    cls = _CLS.get(rt & 0xF0, f"cls{rt >> 4:X}")
    enc = _ENC.get(rt & 0x0F, f"enc{rt & 0xF:X}")
    return f"{cls}/{enc}"


class MlaBackend(Backend):
    """Read-only-ish view of an .mla container's records."""

    def __init__(self, path: str, parent: Backend):
        self.path = os.path.abspath(path)
        self._parent = parent
        self._records: list[tuple] = []  # [(MlaLog, bytes)]
        self._health: list[bool] = []    # parallel: True = record looks OK
        self._summary: dict = {}
        self._load()

    def _load(self) -> None:
        try:
            with MlaPosixHAL(self.path) as hal:
                core = MlaCore(hal)
                core.mount()
                self._records = list(core)  # host model: read it all into RAM
                self._health = [True] * len(self._records)
                self._summary = self._summarize(core, self._records)
                self._summary.update(self._scan_health(core))
        except Exception as exc:
            raise BackendError(f"Cannot open MLA: {exc}") from exc

    @staticmethod
    def _scan_health(core) -> dict:
        """Walk every physical slot and classify it (for F2 Repair)."""
        ok = bad_crc = abandoned = checkpoint = bad_data = 0
        try:
            fs = core._prefix.file_size
            rs = core._rs
            for slot in range(core._n_slots):
                raw = core._hal.read(fs - (slot + 1) * rs, rs)
                rec, crc_ok = MlaLog.from_bytes(raw)
                if not crc_ok:
                    bad_crc += 1
                    continue
                if (rec.rec_type & 0xF0) == 0xF0:
                    checkpoint += 1
                    continue
                if rec.flags != 0xFF:  # not LIVE → abandoned (torn write, cleaned up)
                    abandoned += 1
                    continue
                try:
                    core._read_data(rec)
                    ok += 1
                except Exception:
                    bad_data += 1
        except Exception:
            pass
        return {"h_ok": ok, "h_bad_crc": bad_crc, "h_abandoned": abandoned,
                "h_checkpoint": checkpoint, "h_bad_data": bad_data}

    @staticmethod
    def _summarize(core, records) -> dict:
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
        # e.g. ".../weather.mla/" — the trailing marker hints we're "inside"
        return self.path

    @property
    def label(self) -> str:
        return os.path.basename(self.path)

    # ── browsing ────────────────────────────────────────────────────────────
    def list(self) -> list[Entry]:
        out = [Entry("..", True, 0, None, "updir")]
        for i, (rec, _data) in enumerate(self._records):
            healthy = self._health[i] if i < len(self._health) else True
            star = " " if healthy else "*"   # '*' flags a record that failed repair scan
            when = datetime.fromtimestamp(rec.timestamp).strftime("%d.%m.%y %H:%M:%S")
            name = "%s%05d  %s  st%-3d ch%-3d %s" % (
                star, rec.seq, when, rec.station, rec.channel,
                rec_type_name(rec.rec_type),
            )
            stamp = datetime.fromtimestamp(rec.timestamp).strftime("%Y%m%d_%H%M%S")
            export = "rec%05d_%s_st%d_ch%d.bin" % (
                rec.seq, stamp, rec.station, rec.channel,
            )
            out.append(Entry(
                name=name, is_container=False, size=rec.length,
                mtime=rec.timestamp, kind="record",
                meta={"idx": i, "export_name": export, "healthy": healthy},
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
            ("Record (seq)", str(rec.seq)),
            ("Time", f"{ts}  (unix {rec.timestamp})"),
            ("Station", str(rec.station)),
            ("Channel", str(rec.channel)),
            ("Type", f"0x{rec.rec_type:02X}  {rec_type_name(rec.rec_type)}"),
            ("Length", f"{rec.length} B"),
        ]
        if rec.kf_back:
            rows.append(("Keyframe back", str(rec.kf_back)))
        # convenience decodes for tiny payloads
        if len(data) == 4:
            rows.append(("As float32", f"{struct.unpack('<f', data)[0]:.4f}"))
            rows.append(("As int32", str(struct.unpack('<i', data)[0])))
        return rows

    def _container_info(self) -> list[tuple[str, str]]:
        s = self._summary
        rows = [
            ("MLA file", self.path),
            ("Size", f"{os.path.getsize(self.path)} B"),
            ("Records", str(s.get("count", 0))),
        ]
        stations = s.get("stations") or []
        if stations:
            rows.append(("Stations", ", ".join(map(str, stations))))
        if s.get("time_from") is not None:
            fr = datetime.fromtimestamp(s["time_from"]).strftime("%Y-%m-%d %H:%M")
            to = datetime.fromtimestamp(s["time_to"]).strftime("%Y-%m-%d %H:%M")
            rows.append(("Time range", f"{fr} … {to}"))
        return rows

    # ── value decoding (F4 View-with-values) ─────────────────────────────────
    def decode_value(self, entry: Entry) -> str:
        """Best-effort human value of a record's payload (the 'conversion table').

        Today the payload convention is a 4-byte little-endian float for
        measurements; events carry short text. As real per-channel conversion
        tables arrive, this is where they plug in.
        """
        idx = entry.meta.get("idx")
        rec, data = self._records[idx]
        enc = rec.rec_type & 0x0F
        if enc == 0x3:  # text/JSON
            return data.decode("utf-8", "replace")
        if len(data) == 4:
            try:
                return f"{struct.unpack('<f', data)[0]:.4f}"
            except struct.error:
                pass
        if len(data) in (1, 2, 4):
            return str(int.from_bytes(data, "little"))
        return data.hex(" ")

    def to_csv(self) -> bytes:
        """Export the whole container as CSV (seq,time,unix,station,channel,type,value)."""
        rows = ["seq,time,unix,station,channel,type,length,value"]
        for i, (rec, _data) in enumerate(self._records):
            ts = datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d %H:%M:%S")
            entry = Entry("", meta={"idx": i})
            val = self.decode_value(entry).replace(",", ";").replace("\n", " ")
            rows.append("%d,%s,%d,%d,%d,%s,%d,%s" % (
                rec.seq, ts, rec.timestamp, rec.station, rec.channel,
                rec_type_name(rec.rec_type), rec.length, val,
            ))
        return ("\n".join(rows) + "\n").encode("utf-8")

    def csv_name(self) -> str:
        base = os.path.splitext(os.path.basename(self.path))[0]
        return base + ".csv"

    def sqlite_name(self) -> str:
        base = os.path.splitext(os.path.basename(self.path))[0]
        return base + ".db"

    def to_sqlite(self) -> bytes:
        """Export the whole container as a SQLite database (one 'records' table).

        SQLite is the simplest self-contained SQL target: the result is a single
        .db file you can open in any SQL tool. This is intentionally a thin view
        of the log — one flat table — not a normalised schema; the .mla stays the
        source of truth and this is just a queryable mirror of it.
        """
        import sqlite3
        import tempfile

        # sqlite3 needs a real path; build in a temp file, then read the bytes back.
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            con = sqlite3.connect(tmp)
            try:
                con.execute(
                    "CREATE TABLE records ("
                    "seq INTEGER, time TEXT, unix INTEGER, station INTEGER, "
                    "channel INTEGER, type TEXT, length INTEGER, value TEXT)"
                )
                rows = []
                for i, (rec, _data) in enumerate(self._records):
                    ts = datetime.fromtimestamp(rec.timestamp).strftime("%Y-%m-%d %H:%M:%S")
                    val = self.decode_value(Entry("", meta={"idx": i}))
                    rows.append((rec.seq, ts, rec.timestamp, rec.station,
                                 rec.channel, rec_type_name(rec.rec_type),
                                 rec.length, val))
                con.executemany(
                    "INSERT INTO records VALUES (?,?,?,?,?,?,?,?)", rows)
                con.commit()
            finally:
                con.close()
            with open(tmp, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass

    # ── F2 Repair — check the file and report ────────────────────────────────
    def repair_info(self) -> list[tuple[str, str]]:
        s = self._summary
        rows = [
            ("File", os.path.basename(self.path)),
            ("Valid records", str(s.get("h_ok", 0))),
            ("Checkpoints", str(s.get("h_checkpoint", 0))),
            ("Abandoned (torn, cleaned)", str(s.get("h_abandoned", 0))),
            ("Bad CRC (skipped)", str(s.get("h_bad_crc", 0))),
            ("Unreadable data block", str(s.get("h_bad_data", 0))),
        ]
        damaged = s.get("h_bad_crc", 0) + s.get("h_bad_data", 0)
        verdict = "OK — no damage found" if damaged == 0 else \
                  f"{damaged} damaged slot(s) — flagged with '*' in the list"
        rows.append(("Verdict", verdict))
        return rows

    # ── mutating — intentionally limited inside MLA ───────────────────────────
    # By design the MLA container is append-only and crash-safe: the GUI does not
    # edit records in place (that would break CRCs / the two-pointer layout). You
    # can always copy a record OUT (F5) and work on the copy. See design notes.
    _RO = "MLA is append-only by design — copy a record out (F5) to work on it."

    def mkdir(self, name: str) -> None:
        raise Unsupported(self._RO)

    def delete(self, entry: Entry) -> None:
        raise Unsupported(self._RO)

    def rename(self, entry: Entry, new_name: str) -> None:
        raise Unsupported(self._RO)

    def put_file(self, name: str, data: bytes) -> None:
        raise Unsupported(self._RO)
