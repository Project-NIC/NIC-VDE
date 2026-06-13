#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Example: export a whole ROTATED archive with NIC-GLUE-OUT.

The mirror of NIC-GLUE-IN's ``weather_archive_datalogger.py``. It reads a whole
directory of ``MLA0000N.MLA`` files as ONE dataset via ``GlueArchiveReader`` and
writes a single combined ``.csv`` + ``.db`` (SQLite), with a global ``idx`` across
every file. Each file is independently decodable, so the reader just sweeps them
in order.

With no archive dir it builds a tiny rotated sample first, so it runs standalone:

    python3 examples/weather_archive_export.py [archive_dir] [out_dir]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_glue_out import GlueArchiveReader  # noqa: E402


def build_sample_archive(directory: str) -> None:
    """Tiny rotated sample: raw temp/humidity rows, one station, small files so it
    rotates. (Uses the vendored libraries directly — only to create demo input.)"""
    from nic_mla_archive import MlaArchive
    from mla_schema import MlaSchemaBuilder, MlaStationTable

    sb = MlaSchemaBuilder(); sb.log("datetime")
    sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
    sb.data("humidity", unit="pct", width=2, exp10=-1)
    st = MlaStationTable(); st.station(region=55, number=25000)

    with MlaArchive(directory, file_size=2048, schema_table=sb.table(),
                    station_table=st.table()) as arch:
        t = 1_748_000_000
        for i in range(200):
            payload = (200 + i).to_bytes(2, "little", signed=True) + \
                      (500 + i).to_bytes(2, "little")
            arch.append(t, 1, payload)
            t += 900


def main() -> None:
    args = sys.argv[1:]
    archive_dir = args[0] if args else os.path.join(os.path.dirname(__file__),
                                                     "weather_archive_sample")
    out_dir = args[1] if len(args) > 1 else archive_dir

    if not args:
        os.makedirs(archive_dir, exist_ok=True)
        if not any(n.endswith(".MLA") for n in os.listdir(archive_dir)):
            build_sample_archive(archive_dir)

    ar = GlueArchiveReader(archive_dir)
    print(f"[ARCHIVE] {archive_dir}  ({ar.file_count} files, {ar.record_count} records)")
    for dr in list(ar)[:3]:
        vals = ", ".join(f"{n}={v}" for n, _u, v in (dr.values or []))
        print(f"  idx {dr.index:>4}  {dr.station_label:>8}  {dr.kind:<8}  {vals}")

    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.join(out_dir, "weather_archive.csv")
    db_path = os.path.join(out_dir, "weather_archive.db")
    n_csv = ar.write_csv(csv_path)
    n_db = ar.write_sqlite(db_path)
    print(f"[CSV ] {csv_path}  ({n_csv} B)")
    print(f"[DB  ] {db_path}  ({n_db} B)")


if __name__ == "__main__":
    main()
