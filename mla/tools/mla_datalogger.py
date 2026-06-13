#!/usr/bin/env python3
"""
NIC-MLA — datalogger format (profile-ref) — reference implementation.

Lets ONE .mla file carry MANY station profiles with DIFFERENT column layouts
(a LoRa repeater / datalogger receiving from several station types). The 16-byte
log record is UNCHANGED — it still carries just a 1-byte station index. The
prefix tables gain the structure:

    GLOBAL LOG schema : describes the fixed 16 B log record (datetime, …)
    PROFILES          : N column layouts (each = its own data-field descriptors)
    STATIONS          : per station, an 8 B opaque identity + a 1 B profile ref

    log index → STATION → { identity(8B), profile_ref } → PROFILE → decode payload

This is ADDITIVE and self-contained: it reuses MlaField / the (raw+offset)·10^exp10
machinery from mla_schema.py and does NOT touch the v1.2 single-schema format.

Tables binary layout (after the 34 B prefix header; each section is tagged and
self-sizing, so a reader walks them in order):

    LOG       : DL_LOG_VER(1) n_log(1)   n_log × 14 B
    PROFILES  : DL_PROF_VER(1) n_prof(1)  [ n_data(1) n_data × 14 B ] × n_prof
    STATIONS  : DL_STA_VER(1) n_sta(1)    [ identity(8 B) profile_ref(1 B) ] × n_sta

Python 3.10+   |   MIT   |   ★ Viva La Resistánce ★
"""
from __future__ import annotations

import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mla_schema import (                       # noqa: E402
    MlaField, MLA_FIELD_SIZE, MLA_SCHEMA_PRESETS,
    mla_encode_payload, mla_decode_payload,
)

# ── Format tags (distinct from the v1.2 schema/station tags) ────────────────
DL_LOG_VER   = 0x4C    # 'L'
DL_PROF_VER  = 0x50    # 'P'
DL_STA_VER   = 0x54    # 'T'
DL_IDENT_LEN = 8       # opaque station identity, 4-byte aligned


# ── Identity encoders (the 8 bytes are opaque to MLA; glue gives them meaning) ─
def dl_gps(lat_deg: float, lon_deg: float) -> bytes:
    """Latitude + longitude as 2× i32 (degrees × 1e7, ~1 cm). 8 B."""
    lat = round(lat_deg * 1e7)
    lon = round(lon_deg * 1e7)
    if not (-(1 << 31) <= lat < (1 << 31) and -(1 << 31) <= lon < (1 << 31)):
        raise ValueError("gps out of i32 range")
    return struct.pack("<ii", lat, lon)


def dl_gps_decode(ident: bytes) -> tuple[float, float]:
    lat, lon = struct.unpack("<ii", ident)
    return lat / 1e7, lon / 1e7


def dl_ident(number: int = 0, region: int = 0, kind: int = 0,
             reserved: int = 0) -> bytes:
    """Hierarchical identity: region(2) + number(2) + kind(2) + reserved(2). 8 B."""
    for nm, v in (("region", region), ("number", number),
                  ("kind", kind), ("reserved", reserved)):
        if not 0 <= v <= 0xFFFF:
            raise ValueError(f"{nm} out of u16 range: {v}")
    return struct.pack("<HHHH", region, number, kind, reserved)


def dl_raw(eight: bytes) -> bytes:
    if len(eight) != DL_IDENT_LEN:
        raise ValueError(f"identity must be {DL_IDENT_LEN} B, got {len(eight)}")
    return bytes(eight)


# ── Builder ─────────────────────────────────────────────────────────────────
class DataloggerBuilder:
    """Build the datalogger prefix tables: global log + profiles + stations."""

    def __init__(self) -> None:
        self.log_fields: list[MlaField] = []
        self.profiles:   list[list[MlaField]] = []
        self.stations:   list[tuple[bytes, int]] = []

    # global LOG schema (describes the fixed 16 B record)
    def log(self, field) -> "DataloggerBuilder":
        if isinstance(field, str):
            if field not in MLA_SCHEMA_PRESETS:
                raise ValueError(f"'{field}' is not a preset MlaField")
            field = MLA_SCHEMA_PRESETS[field]
        self.log_fields.append(field)
        return self

    # one column layout; returns its profile id (0-based)
    def profile(self, fields: list[MlaField]) -> int:
        if not fields:
            raise ValueError("a profile needs at least one data field")
        if len(self.profiles) >= 255:
            raise ValueError("at most 255 profiles")
        if len(fields) > 255:
            raise ValueError("at most 255 data fields per profile")
        self.profiles.append(list(fields))
        return len(self.profiles) - 1

    # one station = 8 B identity + which profile it uses; returns 1-based index
    def station(self, identity: bytes, profile_ref: int) -> int:
        if len(identity) != DL_IDENT_LEN:
            raise ValueError(f"identity must be {DL_IDENT_LEN} B")
        if not 0 <= profile_ref < len(self.profiles):
            raise ValueError(f"profile_ref {profile_ref} out of range")
        if len(self.stations) >= 255:
            raise ValueError("at most 255 stations (1-byte index)")
        self.stations.append((bytes(identity), profile_ref))
        return len(self.stations)

    def serialize(self) -> bytes:
        out = bytearray()
        out += bytes([DL_LOG_VER, len(self.log_fields)])
        for f in self.log_fields:
            out += f.descriptor()
        out += bytes([DL_PROF_VER, len(self.profiles)])
        for prof in self.profiles:
            out += bytes([len(prof)])
            for f in prof:
                out += f.descriptor()
        out += bytes([DL_STA_VER, len(self.stations)])
        for ident, ref in self.stations:
            out += ident + bytes([ref])
        return bytes(out)


# ── Reader ──────────────────────────────────────────────────────────────────
class DataloggerTables:
    """Parsed datalogger tables + the record decoder."""

    def __init__(self, log_fields, profiles, stations):
        self.log_fields = log_fields            # list[MlaField]
        self.profiles   = profiles              # list[list[MlaField]]
        self.stations   = stations              # list[(identity8, profile_ref)]

    @classmethod
    def parse(cls, blob: bytes) -> "DataloggerTables":
        pos = 0

        def take_fields(n: int) -> list[MlaField]:
            nonlocal pos
            fields = []
            for i in range(n):
                fields.append(MlaField.from_descriptor(
                    blob[pos:pos + MLA_FIELD_SIZE], name=f"f{i}"))
                pos += MLA_FIELD_SIZE
            return fields

        if blob[pos] != DL_LOG_VER:
            raise ValueError("datalogger: missing LOG section")
        n_log = blob[pos + 1]; pos += 2
        log_fields = take_fields(n_log)

        if blob[pos] != DL_PROF_VER:
            raise ValueError("datalogger: missing PROFILES section")
        n_prof = blob[pos + 1]; pos += 2
        profiles = []
        for _ in range(n_prof):
            n_data = blob[pos]; pos += 1
            profiles.append(take_fields(n_data))

        if blob[pos] != DL_STA_VER:
            raise ValueError("datalogger: missing STATIONS section")
        n_sta = blob[pos + 1]; pos += 2
        stations = []
        for _ in range(n_sta):
            ident = bytes(blob[pos:pos + DL_IDENT_LEN]); pos += DL_IDENT_LEN
            ref = blob[pos]; pos += 1
            if ref >= len(profiles):
                raise ValueError(f"station references profile {ref} (have {len(profiles)})")
            stations.append((ident, ref))
        return cls(log_fields, profiles, stations)

    # profile (column layout) for a 1-based log station index
    def profile_for(self, station_index: int) -> list[MlaField]:
        if not 1 <= station_index <= len(self.stations):
            raise ValueError(f"station index {station_index} out of range")
        return self.profiles[self.stations[station_index - 1][1]]

    def identity_for(self, station_index: int) -> bytes:
        if not 1 <= station_index <= len(self.stations):
            raise ValueError(f"station index {station_index} out of range")
        return self.stations[station_index - 1][0]

    def encode(self, station_index: int, values) -> bytes:
        """Pack a data payload for the station's own profile."""
        return mla_encode_payload(self.profile_for(station_index), values)

    def decode(self, station_index: int, payload: bytes):
        """Decode a record's payload by the station's own profile.

        Returns (identity, [(name, unit, value), …]).
        """
        ident = self.identity_for(station_index)
        return ident, mla_decode_payload(self.profile_for(station_index), payload)


# ── Export (datalogger .mla → per-profile CSV / SQLite) ──────────────────────
#  Mixed profiles in one file → one CSV / one SQL table PER profile (each with
#  its own columns). Heterogeneous data stays clean instead of a sparse union.

def read_mla(path: str):
    """Mount a datalogger .mla → (DataloggerTables, [(MlaLog, payload), …])."""
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from nic_mla import MlaCore, MlaPosixHAL                      # noqa: E402
    with MlaPosixHAL(path) as hal:
        m = MlaCore(hal); m.mount()
        tables = DataloggerTables.parse(m._prefix.schema_table)
        recs = list(m)
    return tables, recs


_EXPORT_BASE = ["timestamp", "subsec", "station", "identity"]


def _rows_by_profile(tables: "DataloggerTables", recs):
    """Group decoded records by profile id → {prof_id: (fields, [row_dict])}."""
    groups: dict = {}
    for rec, payload in recs:
        ref = tables.stations[rec.station - 1][1]
        fields = tables.profiles[ref]
        row = {"timestamp": rec.timestamp, "subsec": rec.subsec,
               "station": rec.station, "identity": tables.identity_for(rec.station).hex()}
        for name, _unit, val in mla_decode_payload(fields, payload):
            row[name] = val
        groups.setdefault(ref, (fields, []))[1].append(row)
    return groups


def export_csv(mla_path: str, out_dir: str) -> list[str]:
    """Write one wide CSV per profile (profile<id>.csv). Returns written paths."""
    import csv
    tables, recs = read_mla(mla_path)
    written = []
    for ref, (fields, rows) in _rows_by_profile(tables, recs).items():
        cols = _EXPORT_BASE + [f.name for f in fields]
        path = os.path.join(out_dir, f"profile{ref}.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        written.append(path)
    return written


def export_sqlite(mla_path: str, db_path: str) -> list[str]:
    """Write one SQL table per profile into a SQLite db. Returns table names."""
    import sqlite3
    tables, recs = read_mla(mla_path)
    if os.path.exists(db_path):
        os.remove(db_path)
    con = sqlite3.connect(db_path)
    names = []
    for ref, (fields, rows) in _rows_by_profile(tables, recs).items():
        cols = _EXPORT_BASE + [f.name for f in fields]
        tname = f"profile{ref}"
        con.execute(f'CREATE TABLE "{tname}" (' + ", ".join(f'"{c}"' for c in cols) + ")")
        con.executemany(f'INSERT INTO "{tname}" VALUES (' + ", ".join("?" * len(cols)) + ")",
                        [[r.get(c) for c in cols] for r in rows])
        names.append(tname)
    con.commit(); con.close()
    return names


# ── Demo ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    b = DataloggerBuilder()
    b.log("datetime")
    meteo = b.profile([
        MlaField("temp", 2, "degC", -2, signed=True),
        MlaField("hum",  2, "pct",  -1),
    ])
    elec = b.profile([
        MlaField("power",  2, "W"),
        MlaField("energy", 4, "kWh"),
    ])
    b.station(dl_gps(50.0875, 14.4213), meteo)   # 1: Prague meteo
    b.station(dl_gps(49.1951, 16.6068), meteo)   # 2: Brno meteo (same profile)
    b.station(dl_gps(50.0875, 14.4213), elec)    # 3: electricity, same GPS as #1

    blob = b.serialize()
    print(f"datalogger tables = {len(blob)} B  ({len(b.profiles)} profiles, "
          f"{len(b.stations)} stations)")
    t = DataloggerTables.parse(blob)
    pay = t.encode(1, {"temp": 25.45, "hum": 60.0})
    lat, lon = dl_gps_decode(t.identity_for(1))
    print(f"station 1 @ {lat:.4f},{lon:.4f}: {t.decode(1, pay)[1]}")
