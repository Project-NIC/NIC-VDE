"""Shared test helpers — path bootstrap and a tiny on-disk MLA builder.

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

# vendored MLA reference
_MLA_DIR = os.path.join(_ROOT, "third_party", "nic_mla")
if _MLA_DIR not in sys.path:
    sys.path.insert(0, _MLA_DIR)

_MLA_TOOLS = os.path.join(_MLA_DIR, "tools")
if _MLA_TOOLS not in sys.path:
    sys.path.insert(0, _MLA_TOOLS)

from nic_mla import (  # noqa: E402
    MlaCore, MlaPosixHAL,
    ENC_RAW, ENC_TEXT, CLASS_MEASURE, CLASS_EVENT,
)
from mla_schema import SchemaBuilder  # noqa: E402

REPO_ROOT = _ROOT
SAMPLE_MLA = os.path.join(_ROOT, "samples", "weather.mla")

RT_MEASURE = CLASS_MEASURE | ENC_RAW
RT_TEXT = CLASS_EVENT | ENC_TEXT

# A known schemaless fixture: (station, region, rec_type, payload). Two float
# measurements and one text event — exercises the length-guess fallback path.
FIXTURE = [
    (1, 1, RT_MEASURE, struct.pack("<f", 21.5)),
    (2, 3, RT_MEASURE, struct.pack("<f", 1013.0)),
    (1, 0, RT_TEXT, b'{"msg":"hello"}'),
]


def make_temp_mla(records=FIXTURE, t0=1_748_000_000, step=900) -> str:
    """Write a small schemaless MLA with the given records and return its path.

    Caller owns the file and should remove it (tests use a tmp dir / cleanup).
    """
    fd, path = tempfile.mkstemp(suffix=".mla")
    os.close(fd)
    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024, index_kb=4, checkpoint_shift=6)
        t = t0
        for station, region, rt, payload in records:
            core.append(t, station, region, payload, rec_type=rt)
            t += step
        core.sync()
    return path


# A self-describing fixture: two int16 sensors (temp ×0.1 signed, humidity ×0.1).
# Each measurement record carries a packed 4 B row; one text event is non-matching.
SCHEMA_SENSORS = [("temp", "degC", 2, -1, True), ("humidity", "pct", 2, -1, False)]


def make_temp_mla_schema(t0=1_748_000_000, step=900) -> str:
    """Write a schema-carrying MLA: 3 packed measurement rows + 1 text event.

    Decoded expectations: row0 temp=23.5/humidity=60.0, row1 temp=-1.5/hum=61.2,
    row2 temp=0.0/humidity=99.9. Returns the path (caller removes it).
    """
    sb = SchemaBuilder()
    sb.log("datetime").log("station").log("region")
    for name, unit, width, exp10, signed in SCHEMA_SENSORS:
        sb.data(name, unit=unit, width=width, exp10=exp10, signed=signed)
    table = sb.table()

    rows = [(235, 600), (-15, 612), (0, 999)]  # raw int16 pairs (temp, humidity)
    fd, path = tempfile.mkstemp(suffix=".mla")
    os.close(fd)
    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024, index_kb=4, checkpoint_shift=6,
                    schema_table=table)
        t = t0
        for temp, hum in rows:
            payload = (struct.pack("<h", temp) + struct.pack("<H", hum))
            core.append(t, 1, 0, payload, rec_type=RT_MEASURE)
            t += step
        core.append(t, 1, 0, b"PING", rec_type=RT_TEXT)  # non-matching width
        core.sync()
    return path
