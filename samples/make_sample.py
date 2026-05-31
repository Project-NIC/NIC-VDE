#!/usr/bin/env python3
"""
Generate a sample NIC-MLA v1.0 file for developing the Volkov Data GUI.

Simulates a small weather-station datalogger. Each measurement record is one
**packed row** — all sensors (temperature / humidity / pressure / wind) back to
back, exactly the v1.0 model. The file is **self-describing**: a SCHEMA table
(field names/units/scale) and a STATION table (1-byte index → region+number) are
written into the prefix at format time, so any reader decodes raw integers into
real values with no prior knowledge. An occasional text "ping" event exercises
the non-schema path.

Usage:  python3 samples/make_sample.py [out.mla]
"""
from __future__ import annotations

import math
import os
import sys

_MLA = os.path.join(os.path.dirname(__file__), "..", "third_party", "nic_mla")
sys.path.insert(0, _MLA)
sys.path.insert(0, os.path.join(_MLA, "tools"))
from nic_mla import MlaCore, MlaPosixHAL, ENC_RAW, CLASS_MEASURE, CLASS_EVENT  # noqa: E402
from mla_schema import SchemaBuilder, StationTable  # noqa: E402

RT_MEASURE = CLASS_MEASURE | ENC_RAW
RT_EVENT = CLASS_EVENT | ENC_RAW

# DATA schema — one entry per sensor value, packed in this order.
# (name, unit, width, exp10, signed, base, swing)  — base/swing are sim params.
SENSORS = [
    ("temp",     "degC", 2, -1, True,   12.0,  8.0),
    ("humidity", "pct",  2, -1, False,  65.0, 20.0),
    ("pressure", "hPa",  2, -1, False, 1013.0, 12.0),
    ("wind",     "m_s",  2, -1, False,    4.0,  3.0),
]
# Three LoRa nodes — index 1..3 → (region, number). Filled by the host glue.
STATIONS = [(55, 25000), (55, 25001), (55, 25777)]

T0 = 1_748_000_000  # base unix time (~2025)
STEP = 900  # 15 min between samples
N_ROUNDS = 60


def build_schema() -> bytes:
    sb = SchemaBuilder()
    sb.log("datetime")
    for name, unit, width, exp10, signed, _b, _s in SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    return sb.table()


def build_stations() -> bytes:
    st = StationTable()
    for region, number in STATIONS:
        st.station(region=region, number=number)
    return st.table()


def pack_row(station_idx: int, t: int) -> bytes:
    out = b""
    for _name, _unit, width, exp10, signed, base, swing in SENSORS:
        phase = (t // STEP) / 12.0
        v = base + swing * math.sin(phase) * 0.5 + station_idx * 0.7
        raw = round(v / (10.0 ** exp10))           # invert (raw+0)*10^exp10
        out += int(raw).to_bytes(width, "little", signed=signed)
    return out


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "weather.mla")
    schema, stations = build_schema(), build_stations()
    hal = MlaPosixHAL.create(out, file_size=256 * 1024)  # 256 KB container
    with hal:
        core = MlaCore(hal)
        core.format(file_size=256 * 1024, schema_table=schema, station_table=stations)
        t = T0
        for r in range(N_ROUNDS):
            for idx in range(1, len(STATIONS) + 1):   # station index 1..n
                core.append(t, idx, pack_row(idx, t), rec_type=RT_MEASURE)
                if r % 20 == 0:                        # occasional status ping
                    core.append(t, idx, b"PING", rec_type=RT_EVENT)
            t += STEP
        core.sync()
        count = core.record_count
    size = os.path.getsize(out)
    print(f"Wrote {out}  ({size} B, {count} records, "
          f"schema={len(schema)} B, stations={len(stations)} B)")


if __name__ == "__main__":
    main()
