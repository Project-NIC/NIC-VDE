# SPDX-License-Identifier: MIT
"""
NIC-GLUE-OUT — output / read-side connector between the NIC libraries.

This package is the "glue" for the *read* path — the mirror of NIC-GLUE-IN.
Where GLUE-IN wires a data row into a container, GLUE-OUT walks a finished
container back out into a table you can export:

    NIC-MLA container  ──▶  schema decode  ──▶  rows  ──▶  CSV / SQLite

It opens the container, reads the
self-describing schema/station tables out of the prefix, **decompresses NIC-DMD
blobs** (compressed records — keyframe/delta) by replaying each station's stream
in order, decodes every payload into named values, and serialises the lot to
CSV or SQLite. Encryption (NIC-KSF) is out of scope here. A record's *kind*
("raw"/"keyframe"/"delta") comes from its ``compressed`` bit and ``kf_back``.

The vendored dependencies live under ``third_party/`` (copies, the same way
NIC-VDE / NIC-GLUE-IN vendor them). Importing this package puts them on
``sys.path``.
"""
from __future__ import annotations

import os
import sys

# ── Make the vendored libraries importable ──────────────────────────────────
_TP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "third_party"))
for _p in (os.path.join(_TP, "nic_mla"), os.path.join(_TP, "nic_dmd")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Re-export the bits a caller of the glue actually needs, so they never have to
# reach into third_party/ themselves.
from nic_mla import (  # noqa: E402
    MLA_FLAG_COMPRESSED, MLA_KF_MASK,
    MLA_CRC_NONE, MLA_CRC_DATA, MLA_CRC_FULL,
)
from mla_schema import (  # noqa: E402
    mla_read_schema, mla_read_stations, mla_split_station,
    mla_decode_value, mla_decode_payload, MlaField,
)

from .reader import (  # noqa: E402
    GlueReader, GlueArchiveReader, DecodedRecord, record_kind_name,
)
from .export import to_csv, to_sqlite  # noqa: E402
from .datalogger import (  # noqa: E402  — profile-ref (datalogger) export
    is_datalogger, DataloggerBuilder, DataloggerTables,
    export_csv as dl_export_csv, export_sqlite as dl_export_sqlite,
    dl_gps, dl_ident, dl_raw,
)

__version__ = "1.2"

__all__ = [
    "GlueReader",
    "GlueArchiveReader",
    "DecodedRecord",
    "record_kind_name",
    "to_csv",
    "to_sqlite",
    "mla_read_schema", "mla_read_stations", "mla_split_station",
    "mla_decode_value", "mla_decode_payload", "MlaField",
    "MLA_FLAG_COMPRESSED", "MLA_KF_MASK",
    "MLA_CRC_NONE", "MLA_CRC_DATA", "MLA_CRC_FULL",
    # datalogger (profile-ref)
    "is_datalogger", "DataloggerBuilder", "DataloggerTables",
    "dl_export_csv", "dl_export_sqlite", "dl_gps", "dl_ident", "dl_raw",
]
