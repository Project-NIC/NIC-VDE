"""Shared test helpers — path bootstrap and tiny on-disk MLA builders.

All tests import this first so that the repo root is importable (``volkov_core``,
``volkov_i18n``) regardless of how the test runner sets ``sys.path``.
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile

# ── make the repo root importable ────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# vendored MLA reference + its host-only schema tool
_MLA_DIR = os.path.join(_ROOT, "third_party", "nic_mla")
for _p in (_MLA_DIR, os.path.join(_MLA_DIR, "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from nic_mla import (  # noqa: E402
    MlaCore, MlaPosixHAL,
    ENC_RAW, ENC_TEXT, CLASS_MEASURE, CLASS_EVENT,
)
from mla_schema import SchemaBuilder, StationTable  # noqa: E402

REPO_ROOT = _ROOT
SAMPLE_MLA = os.path.join(_ROOT, "samples", "weather.mla")

RT_MEASURE = CLASS_MEASURE | ENC_RAW
RT_TEXT = CLASS_EVENT | ENC_TEXT

# A known schemaless fixture: (station_index, rec_type, payload). Two float
# measurements and one text event — exercises the length-guess fallback path.
FIXTURE = [
    (1, RT_MEASURE, struct.pack("<f", 21.5)),
    (2, RT_MEASURE, struct.pack("<f", 1013.0)),
    (1, RT_TEXT, b'{"msg":"hello"}'),
]


def make_temp_mla(records=FIXTURE, t0=1_748_000_000, step=900) -> str:
    """Write a small schemaless MLA with the given records; return its path."""
    fd, path = tempfile.mkstemp(suffix=".mla")
    os.close(fd)
    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024)
        t = t0
        for station, rt, payload in records:
            core.append(t, station, payload, rec_type=rt)
            t += step
        core.sync()
    return path


# A self-describing fixture: two int16 sensors + a one-station table at index 1.
SCHEMA_SENSORS = [("temp", "degC", 2, -1, True), ("humidity", "pct", 2, -1, False)]
SCHEMA_STATION = (7, 100)  # index 1 → region 7, number 100


def make_temp_mla_schema(t0=1_748_000_000, step=900) -> str:
    """Write a v1.0 schema-carrying MLA: 3 packed rows + 1 text event.

    Decoded: row0 temp=23.5/humidity=60.0, row1 temp=-1.5/hum=61.2,
    row2 temp=0.0/humidity=99.9. Station index 1 → region 7, number 100.
    """
    sb = SchemaBuilder()
    sb.log("datetime")
    for name, unit, width, exp10, signed in SCHEMA_SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    st = StationTable()
    st.station(region=SCHEMA_STATION[0], number=SCHEMA_STATION[1])

    rows = [(235, 600), (-15, 612), (0, 999)]  # raw int16 pairs (temp, humidity)
    fd, path = tempfile.mkstemp(suffix=".mla")
    os.close(fd)
    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024,
                    schema_table=sb.table(), station_table=st.table())
        t = t0
        for temp, hum in rows:
            payload = struct.pack("<h", temp) + struct.pack("<H", hum)
            core.append(t, 1, payload, rec_type=RT_MEASURE)
            t += step
        core.append(t, 1, b"PING", rec_type=RT_TEXT)  # non-matching width
        core.sync()
    return path
