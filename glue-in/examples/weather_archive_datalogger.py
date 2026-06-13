#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Example: a ROTATING weather datalogger on NIC-GLUE-IN.

Same idea as ``weather_datalogger.py``, but it writes into a *rotating archive*
(``MLA00000.MLA``, ``MLA00001.MLA``, …) via ``GlueArchiveLogger`` + ``ChannelBank``.
Deliberately small files force several rotations; the glue makes **each rotated
file start every stream on a keyframe**, so any single file decodes on its own.

Open any one file in NIC-VDE, or export the whole set at once with NIC-GLUE-OUT's
``weather_archive_export.py``.

Usage:  python3 examples/weather_archive_datalogger.py [out_dir]
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_glue_in import (  # noqa: E402
    GlueArchiveLogger, ChannelBank, MlaSchemaBuilder, MlaStationTable,
)

# (name, unit, width, exp10, signed, base, swing) — base/swing drive the sim.
SENSORS = [
    ("temp",     "degC", 2, -1, True,   12.0,  8.0),
    ("humidity", "pct",  2, -1, False,  65.0, 20.0),
]
ROW_WIDTH = sum(w for _n, _u, w, *_ in SENSORS)        # 4 B — the DMD pkt_len
STATIONS = [(55, 25000), (55, 25001)]                  # index 1..2 → region/number
T0, STEP, N_ROUNDS = 1_748_000_000, 900, 400


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
    for _n, _u, width, exp10, signed, base, swing in SENSORS:
        phase = (t // STEP) / 12.0
        v = base + swing * math.sin(phase) * 0.5 + station_idx * 0.7
        out += round(v / (10.0 ** exp10)).to_bytes(width, "little", signed=signed)
    return out


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "weather_archive")
    os.makedirs(out_dir, exist_ok=True)

    # Small files (8 KB) → several rotations over N_ROUNDS×stations records.
    with GlueArchiveLogger(out_dir, schema_table=build_schema(),
                           station_table=build_stations(),
                           file_size=8 * 1024) as log:
        bank = ChannelBank(log)            # auto-wires the rotation → keyframe seam
        t = T0
        for _r in range(N_ROUNDS):
            for idx in range(1, len(STATIONS) + 1):
                bank.log(idx, ROW_WIDTH, t, pack_row(idx, t))   # compressed, rotates itself
            t += STEP
        log.sync()
        files = log.file_count

    total = N_ROUNDS * len(STATIONS)
    print(f"[ARCHIVE] {out_dir}  ({files} files, {total} records, "
          f"{len(STATIONS)} streams, row width {ROW_WIDTH} B)")
    print("Each file starts every stream on a keyframe → decodable on its own.")
    print("Export the whole set:  python3 ../../glue-out/examples/weather_archive_export.py "
          f"{out_dir}")


if __name__ == "__main__":
    main()
