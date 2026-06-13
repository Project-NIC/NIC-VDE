"""NIC-GLUE-OUT — datalogger (profile-ref) export tests. Run: python3 tests/test_datalogger.py"""
import csv
import os
import shutil
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import nic_glue_out as g                                          # noqa: E402  (sets sys.path)
from nic_glue_out import (                                        # noqa: E402
    DataloggerBuilder, DataloggerTables, dl_gps, is_datalogger, dl_export_csv,
)
from mla_schema import MlaField                                   # noqa: E402
from nic_mla import MlaCore, MlaPosixHAL                          # noqa: E402

ok = 0
tot = 0


def check(name, cond):
    global ok, tot
    tot += 1
    if cond:
        ok += 1
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name}")


print("\n=== NIC-GLUE-OUT datalogger export ===\n")

# Build a mixed-profile datalogger file
b = DataloggerBuilder()
b.log("datetime")
meteo = b.profile([MlaField("temp", 2, "degC", -2, signed=True),
                   MlaField("hum",  2, "pct",  -1)])
elec = b.profile([MlaField("power", 2, "W"), MlaField("energy", 4, "kWh")])
b.station(dl_gps(50.0875, 14.4213), meteo)
b.station(dl_gps(49.1951, 16.6068), elec)
blob = b.serialize()
t = DataloggerTables.parse(blob)

SZ = 64 * 1024
path = os.path.join(tempfile.gettempdir(), "glueout_dl.mla")
hal = MlaPosixHAL.create(path, SZ)
with hal:
    m = MlaCore(hal)
    m.format(file_size=SZ, schema_table=blob)
    m.append(1700000000, station=1, data=t.encode(1, {"temp": 22.0, "hum": 55.0}))
    m.append(1700000060, station=2, data=t.encode(2, {"power": 900, "energy": 7000}))

check("is_datalogger() detects the file", is_datalogger(path) is True)

outdir = tempfile.mkdtemp()
paths = dl_export_csv(path, outdir)
check("glue-out exports one CSV per profile (2)", len(paths) == 2)

with open(os.path.join(outdir, "profile0.csv"), newline="") as fh:
    rows = list(csv.DictReader(fh))
check("meteo CSV row decoded via its profile", abs(float(rows[0]["temp"]) - 22.0) < 1e-9)
with open(os.path.join(outdir, "profile1.csv"), newline="") as fh:
    erows = list(csv.DictReader(fh))
check("electricity CSV has its own columns", "power" in erows[0] and "temp" not in erows[0])
check("electricity value matches", int(erows[0]["energy"]) == 7000)

os.remove(path)
shutil.rmtree(outdir)

print(f"\n{ok}/{tot} passed")
sys.exit(0 if ok == tot else 1)
