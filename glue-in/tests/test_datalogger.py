"""NIC-GLUE-IN — writing a datalogger (profile-ref) .mla via GlueLogger.

glue-in needs no datalogger-specific code: pass the datalogger tables blob as
``schema_table`` and write rows with ``log_raw(station, data)`` — the container
carries the blob opaquely. This proves the write side end-to-end.
"""
import csv
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import nic_glue_in                                                # noqa: E402 (sets sys.path)
from nic_glue_in import GlueLogger                                # noqa: E402
from mla_datalogger import (                                      # noqa: E402  vendored
    DataloggerBuilder, DataloggerTables, dl_gps, export_csv,
)
from mla_schema import MlaField                                   # noqa: E402

ok = tot = 0


def check(name, cond):
    global ok, tot
    tot += 1
    if cond:
        ok += 1
        print(f"  ok  {name}")
    else:
        print(f"  FAIL {name}")


print("\n=== NIC-GLUE-IN datalogger write ===\n")

b = DataloggerBuilder()
b.log("datetime")
meteo = b.profile([MlaField("temp", 2, "degC", -2, signed=True),
                   MlaField("hum",  2, "pct",  -1)])
elec = b.profile([MlaField("power", 2, "W"), MlaField("energy", 4, "kWh")])
b.station(dl_gps(50.0875, 14.4213), meteo)
b.station(dl_gps(49.1951, 16.6068), elec)
blob = b.serialize()
t = DataloggerTables.parse(blob)

path = os.path.join(tempfile.gettempdir(), "gluein_dl.mla")
if os.path.exists(path):
    os.remove(path)

# Write a mixed-profile datalogger file through glue-in (RAW path)
with GlueLogger(path, schema_table=blob, file_size=64 * 1024) as lg:
    lg.log_raw(1700000000, station=1, data=t.encode(1, {"temp": 22.0, "hum": 55.0}))
    lg.log_raw(1700000060, station=2, data=t.encode(2, {"power": 900, "energy": 7000}))

outdir = tempfile.mkdtemp()
paths = export_csv(path, outdir)
check("glue-in wrote a datalogger file (2 profiles exported)", len(paths) == 2)
with open(os.path.join(outdir, "profile0.csv"), newline="") as fh:
    rows = list(csv.DictReader(fh))
check("meteo row written & decoded", abs(float(rows[0]["temp"]) - 22.0) < 1e-9)
with open(os.path.join(outdir, "profile1.csv"), newline="") as fh:
    erows = list(csv.DictReader(fh))
check("electricity row written & decoded", int(erows[0]["energy"]) == 7000)

os.remove(path)
shutil.rmtree(outdir)
print(f"\n{ok}/{tot} passed")
sys.exit(0 if ok == tot else 1)
