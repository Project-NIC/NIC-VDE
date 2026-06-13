#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Example: read a NIC-MLA weather container back out and export it.

This is the mirror of NIC-GLUE-IN's ``weather_datalogger.py``. Where that script
*wrote* rows into a container, this one *reads* a container and turns it into a
table — printing a short preview and writing both a ``.csv`` and a ``.db``
(SQLite) next to the source file.

Run it with no arguments and it builds a tiny self-describing sample first (the
same shape GLUE-IN's example produces: a datetime log field + a few sensor data
fields, three stations, rows stored raw/uncompressed), so the read/export path
is runnable standalone:

    python3 examples/weather_export.py [path/to/file.mla] [out_dir]

Compressed records (keyframe/delta NIC-DMD blobs) are decompressed and decoded
to the same named values as raw ones; a record only shows blank cells if it has
no schema mapping at all.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_glue_out import GlueReader  # noqa: E402

# ── Sample builder (uses the vendored libraries directly — only to create demo
#    input; the glue itself is read-only). Mirrors GLUE-IN's weather schema. ──
# (name, unit, width, exp10, signed, base, swing)  — base/swing drive the sim.
SENSORS = [
    ("temp",     "degC", 2, -1, True,   12.0,  8.0),
    ("humidity", "pct",  2, -1, False,  65.0, 20.0),
    ("pressure", "hPa",  2, -1, False, 1013.0, 12.0),
    ("wind",     "m_s",  2, -1, False,    4.0,  3.0),
]
ROW_WIDTH = sum(w for _n, _u, w, *_ in SENSORS)        # 8 B
STATIONS = [(55, 25000), (55, 25001), (55, 25777)]      # index 1..3
T0, STEP, N_ROUNDS = 1_748_000_000, 900, 20


def build_sample(path: str) -> None:
    from nic_mla import MlaCore, MlaPosixHAL
    from mla_schema import MlaSchemaBuilder, MlaStationTable

    sb = MlaSchemaBuilder()
    sb.log("datetime")
    for name, unit, width, exp10, signed, _b, _s in SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    st = MlaStationTable()
    for region, number in STATIONS:
        st.station(region=region, number=number)

    def pack(idx: int, t: int) -> bytes:
        out = b""
        for _n, _u, width, exp10, signed, base, swing in SENSORS:
            v = base + swing * math.sin((t // STEP) / 12.0) * 0.5 + idx * 0.7
            out += int(round(v / (10.0 ** exp10))).to_bytes(width, "little", signed=signed)
        return out

    hal = MlaPosixHAL.create(path, file_size=256 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=256 * 1024, keyframe_intv=0,
                    schema_table=sb.table(), station_table=st.table())
        t = T0
        for r in range(N_ROUNDS):
            for idx in range(1, len(STATIONS) + 1):
                core.append(t, idx, pack(idx, t))   # raw / uncompressed
            t += STEP
        core.sync()


def main() -> None:
    args = sys.argv[1:]
    path = args[0] if args else None
    out_dir = args[1] if len(args) > 1 else None

    if path is None:
        out_dir = out_dir or os.path.dirname(__file__)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "weather.mla")
        build_sample(path)
        print(f"[sample] wrote {path}")
    out_dir = out_dir or os.path.dirname(os.path.abspath(path))

    stem = os.path.splitext(os.path.basename(path))[0]
    csv_path = os.path.join(out_dir, stem + ".csv")
    db_path = os.path.join(out_dir, stem + ".db")

    with GlueReader(path) as r:
        fields = ", ".join(f.name for f in r.data_fields) or "(none)"
        print(f"[read ] {path}  ({r.record_count} records, schema: {fields})")

        # short console preview
        print("       idx  time(unix)   station   kind            values")
        for dr in list(r)[:6]:
            if dr.values is not None:
                vals = "  ".join(f"{n}={v:g}" if isinstance(v, float) else f"{n}={v}"
                                 for n, _u, v in dr.values)
            else:
                vals = f"<{dr.kind} — no schema match>"
            print(f"       {dr.index:>3}  {dr.timestamp}  {dr.station_label:<8}  "
                  f"{dr.kind:<14}  {vals}")

        n_csv = r.write_csv(csv_path)
        n_db = r.write_sqlite(db_path)

    print(f"[csv  ] {csv_path}  ({n_csv} B)")
    print(f"[sqlite] {db_path}  ({n_db} B)")
    print("Open the .csv in any spreadsheet, or query the .db with sqlite3.")


if __name__ == "__main__":
    main()
