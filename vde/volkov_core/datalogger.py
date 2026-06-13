"""NIC-VDE — datalogger (profile-ref) support (GUI-free, in volkov_core).

Thin layer over the vendored ``mla_datalogger`` module: detect a datalogger
``.mla`` and export it to CSV / SQLite (one table per station profile). Puts the
vendored libraries on ``sys.path`` the same way the rest of volkov_core does.

A ``.mla`` is either v1.2-schema (use VdeMlaBackend) or datalogger (use this);
tell them apart with ``is_datalogger()``.
"""
import os
import sys

_TP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "third_party"))
_MLA = os.path.join(_TP, "nic_mla")
for _p in (_MLA, os.path.join(_MLA, "tools"), os.path.join(_TP, "nic_dmd")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mla_datalogger import (              # noqa: E402  vendored
    export_csv, export_sqlite, read_mla, DataloggerTables,
    DataloggerBuilder, dl_gps, dl_ident, dl_raw,
)
from nic_mla import MlaCore, MlaPosixHAL  # noqa: E402

DL_TABLES_TAG = 0x4C   # datalogger tables tag at prefix offset 34


def is_datalogger(mla_path: str) -> bool:
    """True if the ``.mla`` carries datalogger (profile-ref) tables."""
    with MlaPosixHAL(mla_path) as hal:
        m = MlaCore(hal)
        m.mount()
        blob = m._prefix.schema_table
    return bool(blob) and blob[0] == DL_TABLES_TAG


__all__ = [
    "is_datalogger", "export_csv", "export_sqlite", "read_mla",
    "DataloggerTables", "DataloggerBuilder", "dl_gps", "dl_ident", "dl_raw",
]
