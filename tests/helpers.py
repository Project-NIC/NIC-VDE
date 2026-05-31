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

from nic_mla import (  # noqa: E402
    MlaCore, MlaPosixHAL,
    ENC_RAW, ENC_TEXT, CLASS_MEASURE, CLASS_EVENT,
)

REPO_ROOT = _ROOT
SAMPLE_MLA = os.path.join(_ROOT, "samples", "weather.mla")

RT_MEASURE = CLASS_MEASURE | ENC_RAW
RT_TEXT = CLASS_EVENT | ENC_TEXT

# A known fixture: (station, channel, rec_type, payload). Two float measurements
# and one text event — enough to exercise every decode_value branch.
FIXTURE = [
    (1, 1, RT_MEASURE, struct.pack("<f", 21.5)),
    (2, 3, RT_MEASURE, struct.pack("<f", 1013.0)),
    (1, 0, RT_TEXT, b'{"msg":"hello"}'),
]


def make_temp_mla(records=FIXTURE, t0=1_748_000_000, step=900) -> str:
    """Write a small MLA with the given records and return its path.

    Caller owns the file and should remove it (tests use a tmp dir / cleanup).
    """
    fd, path = tempfile.mkstemp(suffix=".mla")
    os.close(fd)
    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024, index_kb=4, checkpoint_shift=6)
        t = t0
        for station, channel, rt, payload in records:
            core.append(t, station, channel, payload, rec_type=rt)
            t += step
        core.sync()
    return path
