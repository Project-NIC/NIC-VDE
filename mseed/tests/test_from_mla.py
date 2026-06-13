# SPDX-License-Identifier: MIT
"""
End-to-end: build a .mla (raw AND NIC-DMD-compressed channels), convert to
miniSEED, read it back, and confirm the integer counts survive — proving the
NIC-MLA + NIC-DMD + Steim bridge round-trips losslessly.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nic_mseed  # noqa: E402  — puts third_party (nic_mla, nic_dmd) on sys.path
from nic_mseed import MseedExporter
from nic_mseed.mseed import read_stream
from nic_mla import MlaCore, MlaPosixHAL
from mla_schema import MlaSchemaBuilder, MlaStationTable
from nic_dmd import DmdEncoder

_p = _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else:    _f += 1; print(f"  FAIL  {name}")

WIDTH = 4   # two int16 channels: "z", "n"


def _schema_stations():
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    sb.data("z", unit="raw", width=2, signed=True)
    sb.data("n", unit="raw", width=2, signed=True)
    st = MlaStationTable()
    st.station(region=55, number=25000)    # index 1 — raw
    st.station(region=55, number=25001)    # index 2 — compressed
    return sb.table(), st.table()


def _pack(z, n):
    return z.to_bytes(2, "little", signed=True) + n.to_bytes(2, "little", signed=True)


def main():
    print("NIC-MLA → miniSEED converter (end-to-end)")
    schema, stations = _schema_stations()
    N = 120
    # ground-truth integer series per channel
    z_raw = [int(50 * __import__("math").sin(i / 5.0)) for i in range(N)]
    n_raw = [i - 60 for i in range(N)]
    z_cmp = [int(30 * __import__("math").sin(i / 7.0)) + 5 for i in range(N)]
    n_cmp = [(-1) ** i * (i % 11) for i in range(N)]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "quake.mla")
        out = os.path.join(d, "quake.mseed")
        hal = MlaPosixHAL.create(path, file_size=256 * 1024)
        with hal:
            core = MlaCore(hal)
            core.format(file_size=256 * 1024, schema_table=schema, station_table=stations)
            base = 1_748_000_000
            # station 1 — RAW
            for i in range(N):
                core.append(base + i, station=1, data=_pack(z_raw[i], n_raw[i]))
            # station 2 — NIC-DMD compressed (mirror CompressedChannel's logic)
            enc = DmdEncoder(WIDTH)
            since_kf = 0
            for i in range(N):
                blob = enc.compress(_pack(z_cmp[i], n_cmp[i]))
                since_kf = 0 if (blob[0] & 0x07) == 0 else since_kf + 1
                core.append(base + i, station=2, data=blob,
                            compressed=True, kf_back=since_kf)

        stats = MseedExporter(sample_rate_hz=100.0, network="NQ").export(path, out)
        check("4 channels exported (2 stations × 2 fields)", stats["channels"] == 4)

        recs = read_stream(open(out, "rb").read())
        # collect per (station-ish via sta code, channel) — group by (sta, cha)
        bychan: dict[tuple, list[int]] = {}
        for r in recs:
            bychan.setdefault((r["sta"], r["cha"]), []).extend(r["samples"])

        check("station 1 / Z raw round-trips", bychan.get(("25000", "Z")) == z_raw)
        check("station 1 / N raw round-trips", bychan.get(("25000", "N")) == n_raw)
        check("station 2 / Z compressed round-trips (DMD→Steim)", bychan.get(("25001", "Z")) == z_cmp)
        check("station 2 / N compressed round-trips (DMD→Steim)", bychan.get(("25001", "N")) == n_cmp)

    print(f"\nResult: {_p}/{_p+_f} PASS | {_f} FAIL")
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
