"""NIC-VDE — datalogger (profile-ref) support tests."""
import csv
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from volkov_core.datalogger import (                              # noqa: E402
    DataloggerBuilder, DataloggerTables, dl_gps, is_datalogger, export_csv,
)
from mla_schema import MlaField                                   # noqa: E402
from nic_mla import MlaCore, MlaPosixHAL                          # noqa: E402


def _make_datalogger_mla(path):
    b = DataloggerBuilder()
    b.log("datetime")
    meteo = b.profile([MlaField("temp", 2, "degC", -2, signed=True),
                       MlaField("hum",  2, "pct",  -1)])
    elec = b.profile([MlaField("power", 2, "W"), MlaField("energy", 4, "kWh")])
    b.station(dl_gps(50.0875, 14.4213), meteo)
    b.station(dl_gps(49.1951, 16.6068), elec)
    blob = b.serialize()
    t = DataloggerTables.parse(blob)
    sz = 64 * 1024
    hal = MlaPosixHAL.create(path, sz)
    with hal:
        m = MlaCore(hal)
        m.format(file_size=sz, schema_table=blob)
        m.append(1700000000, station=1, data=t.encode(1, {"temp": 22.0, "hum": 55.0}))
        m.append(1700000060, station=2, data=t.encode(2, {"power": 900, "energy": 7000}))


class DataloggerSupport(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.gettempdir(), "vde_dl.mla")
        _make_datalogger_mla(self.path)
        self.outdir = tempfile.mkdtemp()

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        shutil.rmtree(self.outdir, ignore_errors=True)

    def test_is_datalogger(self):
        self.assertTrue(is_datalogger(self.path))

    def test_export_per_profile(self):
        paths = export_csv(self.path, self.outdir)
        self.assertEqual(len(paths), 2)
        with open(os.path.join(self.outdir, "profile0.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        self.assertAlmostEqual(float(rows[0]["temp"]), 22.0, places=6)
        with open(os.path.join(self.outdir, "profile1.csv"), newline="") as fh:
            erows = list(csv.DictReader(fh))
        self.assertIn("power", erows[0])
        self.assertNotIn("temp", erows[0])
        self.assertEqual(int(erows[0]["energy"]), 7000)


if __name__ == "__main__":
    unittest.main()
