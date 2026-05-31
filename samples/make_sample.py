#!/usr/bin/env python3
"""
Generate a sample NIC-MLA file for developing the Volkov Data GUI.

Simulates a small weather-station datalogger: a few stations in a ~5 km LoRa
range, each emitting one **packed measurement row** per cadence — all sensors
(temperature / humidity / pressure / wind) concatenated back-to-back, exactly
the new MLA model. The file is **self-describing**: a schema table is written
into the prefix at format time (see third_party/nic_mla/tools/mla_schema.py),
so any reader decodes the raw integers into real values + units with no prior
knowledge. An occasional text "ping" event exercises the non-schema path.

Usage:  python3 samples/make_sample.py [out.mla]
"""
from __future__ import annotations

import math
import os
import struct
import sys

# import the vendored MLA reference + its host-only schema tool
_MLA = os.path.join(os.path.dirname(__file__), "..", "third_party", "nic_mla")
sys.path.insert(0, _MLA)
sys.path.insert(0, os.path.join(_MLA, "tools"))
from nic_mla import MlaCore, MlaPosixHAL, ENC_RAW, CLASS_MEASURE, CLASS_EVENT  # noqa: E402
from mla_schema import SchemaBuilder  # noqa: E402

# rec_type = high nibble (class) | low nibble (encoding)
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
STATIONS = [1, 2, 3]  # three LoRa nodes

T0 = 1_748_000_000  # base unix time (~2025)
STEP = 900  # 15 min between samples, per the brief's cadence
N_ROUNDS = 60


def build_schema() -> bytes:
    sb = SchemaBuilder()
    sb.log("datetime").log("station").log("region")
    for name, unit, width, exp10, signed, _b, _s in SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    return sb.table()


def physical(name, base, swing, station, t) -> float:
    phase = (t // STEP) / 12.0
    return base + swing * math.sin(phase) * 0.5 + station * 0.7


def pack_row(station: int, t: int) -> bytes:
    """All sensor values for one sample, packed back-to-back as the schema says."""
    out = b""
    for name, _unit, width, exp10, signed, base, swing in SENSORS:
        v = physical(name, base, swing, station, t)
        raw = round(v / (10.0 ** exp10))           # invert (raw+0)*10^exp10
        out += int(raw).to_bytes(width, "little", signed=signed)
    return out


def main() -> None:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "weather.mla"
    )
    table = build_schema()
    hal = MlaPosixHAL.create(out, file_size=256 * 1024)  # 256 KB container
    with hal:
        core = MlaCore(hal)
        core.format(file_size=256 * 1024, index_kb=4, checkpoint_shift=6,
                    schema_table=table)

        t = T0
        for r in range(N_ROUNDS):
            for station in STATIONS:
                core.append(t, station, 0, pack_row(station, t), rec_type=RT_MEASURE)
                # occasional event record (e.g. a status ping)
                if r % 20 == 0:
                    core.append(t, station, 0, b"PING", rec_type=RT_EVENT)
            t += STEP

        core.sync()
        count = core.record_count
    size = os.path.getsize(out)
    print(f"Wrote {out}  ({size} B, {count} records, schema={len(table)} B)")


if __name__ == "__main__":
    main()
