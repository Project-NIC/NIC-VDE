#!/usr/bin/env python3
"""Tests for the NIC-MLA datalogger format (profile-ref). Run: python3 tools/mla_datalogger_test.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mla_schema import MlaField                                   # noqa: E402
from mla_datalogger import (                                      # noqa: E402
    DataloggerBuilder, DataloggerTables, dl_gps, dl_gps_decode, dl_ident, dl_raw,
)

passed = total = 0


def check(name, ok):
    global passed, total
    total += 1
    if ok:
        passed += 1
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name}")


print("\n=== NIC-MLA datalogger format ===\n")

# Build: 2 profiles, 3 stations (two meteo SHARE one profile, one electricity)
b = DataloggerBuilder()
b.log("datetime")
meteo = b.profile([
    MlaField("temp", 2, "degC", -2, signed=True),   # 0.01 °C
    MlaField("hum",  2, "pct",  -1),                 # 0.1 %
])
elec = b.profile([
    MlaField("power",  2, "W"),
    MlaField("energy", 4, "kWh"),
])
s1 = b.station(dl_gps(50.0875, 14.4213), meteo)      # Prague meteo
s2 = b.station(dl_gps(49.1951, 16.6068), meteo)      # Brno meteo (SAME profile)
s3 = b.station(dl_gps(50.0875, 14.4213), elec)       # electricity, same GPS as s1
blob = b.serialize()

check("station indices are 1-based", (s1, s2, s3) == (1, 2, 3))

# Round-trip the tables
t = DataloggerTables.parse(blob)
check("2 profiles parsed", len(t.profiles) == 2)
check("3 stations parsed", len(t.stations) == 3)
check("1 global log field", len(t.log_fields) == 1)
check("serialize→parse is stable", DataloggerTables.parse(t_blob := b.serialize()).profiles == t.profiles)

# Two meteo stations share ONE profile (no layout duplication)
check("station 1 and 2 share the same profile", t.stations[0][1] == t.stations[1][1] == meteo)
check("station 3 uses a different profile", t.stations[2][1] == elec)

# Mixed records in one file decode by their OWN profile
pay1 = t.encode(1, {"temp": 25.45, "hum": 60.0})
ident1, dec1 = t.decode(1, pay1)
d1 = dict((n, v) for n, _u, v in dec1)
check("meteo (s1) temp round-trips", abs(d1["temp"] - 25.45) < 1e-9)
check("meteo (s1) hum round-trips", abs(d1["hum"] - 60.0) < 1e-9)

pay3 = t.encode(3, {"power": 1500, "energy": 12345})
ident3, dec3 = t.decode(3, pay3)
d3 = dict((n, v) for n, _u, v in dec3)
check("electricity (s3) power round-trips", d3["power"] == 1500)
check("electricity (s3) energy round-trips", d3["energy"] == 12345)

# A meteo payload decoded as meteo from a different station (s2, same profile)
pay2 = t.encode(2, {"temp": -3.20, "hum": 91.0})
_, dec2 = t.decode(2, pay2)
d2 = dict((n, v) for n, _u, v in dec2)
check("meteo (s2) negative temp round-trips", abs(d2["temp"] + 3.20) < 1e-9)

# Payload widths differ per profile (proof the layouts are really independent)
check("meteo payload is 4 B", len(pay1) == 4)        # 2 + 2
check("electricity payload is 6 B", len(pay3) == 6)  # 2 + 4

# Identity (8 B, opaque) round-trips
lat, lon = dl_gps_decode(t.identity_for(1))
check("GPS identity round-trips (~1 cm)", abs(lat - 50.0875) < 1e-6 and abs(lon - 14.4213) < 1e-6)
check("identity is exactly 8 B", len(t.identity_for(1)) == 8)
check("two stations can share GPS, differ by profile",
      t.identity_for(1) == t.identity_for(3) and t.stations[0][1] != t.stations[2][1])

# Hierarchical identity encoder also fits 8 B
check("dl_ident is 8 B", len(dl_ident(number=25000, region=55, kind=7)) == 8)
check("dl_raw enforces 8 B", len(dl_raw(b"\x00" * 8)) == 8)

# Guards
try:
    b.station(dl_gps(0, 0), 99)
    check("rejects bad profile_ref", False)
except ValueError:
    check("rejects bad profile_ref", True)
try:
    t.decode(9, b"")
    check("rejects out-of-range station index", False)
except ValueError:
    check("rejects out-of-range station index", True)

# ── End-to-end: write a REAL .mla with mixed profiles, read it back ──────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tempfile                                                   # noqa: E402
from nic_mla import MlaCore, MlaPosixHAL                          # noqa: E402

_SZ = 64 * 1024
_path = os.path.join(tempfile.gettempdir(), "mla_dl_e2e.mla")
hal = MlaPosixHAL.create(_path, _SZ)
with hal:
    m = MlaCore(hal)
    m.format(file_size=_SZ, schema_table=blob)
    m.append(1700000000, station=1, data=t.encode(1, {"temp": 21.50, "hum": 48.0}))
    m.append(1700000060, station=3, data=t.encode(3, {"power": 800, "energy": 5000}))
    m.append(1700000120, station=2, data=t.encode(2, {"temp": -1.25, "hum": 95.0}))

with MlaPosixHAL(_path) as hal:
    m2 = MlaCore(hal); m2.mount()
    check("real .mla: prefix carries datalogger tables", m2._prefix.schema_table == blob)
    rt = DataloggerTables.parse(m2._prefix.schema_table)
    by_sta = {}
    for rec, payload in m2:
        _ident, decoded = rt.decode(rec.station, payload)
        by_sta[rec.station] = dict((n, v) for n, _u, v in decoded)
    check("real .mla: 3 records read back", len(by_sta) == 3)
    check("real .mla: meteo s1 temp", abs(by_sta[1]["temp"] - 21.50) < 1e-9)
    check("real .mla: electricity s3 power", by_sta[3]["power"] == 800)
    check("real .mla: meteo s2 negative temp", abs(by_sta[2]["temp"] + 1.25) < 1e-9)
    check("real .mla: mixed profiles decoded by own layout",
          "temp" in by_sta[1] and "power" in by_sta[3])

# Export: one CSV + one SQL table per profile
import csv as _csv                                                # noqa: E402
import sqlite3 as _sqlite3                                        # noqa: E402
from mla_datalogger import export_csv, export_sqlite             # noqa: E402

_outdir = tempfile.mkdtemp()
paths = export_csv(_path, _outdir)
check("CSV export: one file per profile (2)", len(paths) == 2)
_meteo_csv = os.path.join(_outdir, "profile0.csv")
with open(_meteo_csv, newline="") as fh:
    rows = list(_csv.DictReader(fh))
check("CSV meteo: 2 rows (s1, s2 share profile 0)", len(rows) == 2)
check("CSV meteo: has its own columns", "temp" in rows[0] and "hum" in rows[0]
      and "power" not in rows[0])
check("CSV meteo: value matches", abs(float(rows[0]["temp"]) - 21.50) < 1e-9)

_db = os.path.join(_outdir, "out.db")
names = export_sqlite(_path, _db)
check("SQLite export: one table per profile (2)", len(names) == 2)
_con = _sqlite3.connect(_db)
_p1 = _con.execute('SELECT power, energy FROM profile1').fetchall()
check("SQLite electricity table queryable", _p1 == [(800, 5000)])
_con.close()

os.remove(_path)
import shutil as _shutil; _shutil.rmtree(_outdir)

print(f"\n{'='*50}\nTOTAL: {total} tests, {total - passed} failures\n"
      f"RESULT: {'✓ ALL OK' if passed == total else '✗ FAILURES'}\n{'='*50}\n")
sys.exit(0 if passed == total else 1)
