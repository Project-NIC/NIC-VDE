#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Example: a small weather-station datalogger built on NIC-GLUE-IN.

It does two things, both through the glue:

  1. The classic datalogger — three stations, each measurement row stored RAW
     into one self-describing ``.mla`` container. This is the everyday case:
     take a row, store a row. Open the result in NIC-VDE to browse/export it.

  2. A compression demonstration — the same rows for a *single* station, but
     run through NIC-DMD first and stored compressed in a second container, so
     you can see it actually works (and shrinks). DMD is delta-based, so this
     only makes sense per single stream; multiple stations would each need
     their own channel and the delta would buy nothing across them.

Usage:  python3 examples/weather_datalogger.py [out_dir]
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_glue_in import (  # noqa: E402
    GlueLogger, MlaSchemaBuilder, MlaStationTable,
)

# DATA schema — one entry per sensor value, packed back to back in this order.
# (name, unit, width, exp10, signed, base, swing)  — base/swing drive the sim.
SENSORS = [
    ("temp",     "degC", 2, -1, True,   12.0,  8.0),
    ("humidity", "pct",  2, -1, False,  65.0, 20.0),
    ("pressure", "hPa",  2, -1, False, 1013.0, 12.0),
    ("wind",     "m_s",  2, -1, False,    4.0,  3.0),
]
ROW_WIDTH = sum(width for _n, _u, width, *_ in SENSORS)   # 8 B — the DMD pkt_len

STATIONS = [(55, 25000), (55, 25001), (55, 25777)]        # index 1..3 → region/number

T0 = 1_748_000_000   # base unix time (~2025)
STEP = 900           # 15 min between samples
N_ROUNDS = 60


def build_schema() -> bytes:
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    for name, unit, width, exp10, signed, _b, _s in SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    return sb.table()


def build_stations() -> bytes:
    st = MlaStationTable()
    for region, number in STATIONS:
        st.station(region=region, number=number)
    return st.table()


def pack_row(station_idx: int, t: int) -> bytes:
    out = b""
    for _name, _unit, width, exp10, signed, base, swing in SENSORS:
        phase = (t // STEP) / 12.0
        v = base + swing * math.sin(phase) * 0.5 + station_idx * 0.7
        raw = round(v / (10.0 ** exp10))           # invert (raw)*10^exp10
        out += int(raw).to_bytes(width, "little", signed=signed)
    return out


def write_raw(path: str, schema: bytes, stations: bytes) -> int:
    """The classic datalogger: every station's rows stored RAW."""
    with GlueLogger(path, schema_table=schema, station_table=stations,
                    keyframe_intv=0,                 # pure RAW: no keyframe hint
                    file_size=256 * 1024) as log:
        t = T0
        for r in range(N_ROUNDS):
            for idx in range(1, len(STATIONS) + 1):
                log.log_raw(t, idx, pack_row(idx, t))
                if r % 20 == 0:                      # occasional status ping
                    log.log_event(t, idx, "PING")
            t += STEP
        log.sync()
        return log.record_count


def write_compressed(path: str, schema: bytes, stations: bytes) -> tuple[int, int, int]:
    """Compression demo: one station's rows run through NIC-DMD, stored compressed.

    Returns (records, raw_payload_bytes, stored_payload_bytes) so the caller can
    show the saving on the data payloads themselves.
    """
    station_idx = 1
    raw_bytes = stored_bytes = 0
    # The glue seeds keyframe_intv from DMD's cadence by default (keyframe_intv=None).
    with GlueLogger(path, schema_table=schema, station_table=stations,
                    file_size=256 * 1024) as log:
        ch = log.open_compressed_channel(station_idx, pkt_len=ROW_WIDTH)
        t = T0
        for _r in range(N_ROUNDS):
            row = pack_row(station_idx, t)
            blob = ch.log(t, row)
            raw_bytes += len(row)
            stored_bytes += len(blob)
            t += STEP
        log.sync()
        return log.record_count, raw_bytes, stored_bytes


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__)
    os.makedirs(out_dir, exist_ok=True)
    raw_path = os.path.join(out_dir, "weather_raw.mla")
    dmd_path = os.path.join(out_dir, "weather_dmd.mla")

    schema, stations = build_schema(), build_stations()

    n_raw = write_raw(raw_path, schema, stations)
    print(f"[RAW ] {raw_path}  ({os.path.getsize(raw_path)} B file, "
          f"{n_raw} records, row width {ROW_WIDTH} B)")

    n_dmd, raw_payload, stored_payload = write_compressed(dmd_path, schema, stations)
    saved = raw_payload - stored_payload
    pct = (saved / raw_payload * 100.0) if raw_payload else 0.0
    print(f"[DMD ] {dmd_path}  ({os.path.getsize(dmd_path)} B file, "
          f"{n_dmd} records, 1 station)")
    print(f"       payload {raw_payload} B → {stored_payload} B  "
          f"(saved {saved} B, {pct:.1f}%)  — DMD adds at most 1 B/record, never loses data")
    print("Open either file in NIC-VDE to browse and export it.")


if __name__ == "__main__":
    main()
