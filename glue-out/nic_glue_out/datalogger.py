"""NIC-GLUE-OUT — datalogger (profile-ref) export.

Thin wrapper over the vendored ``mla_datalogger`` module: detect a datalogger
``.mla`` and export it to CSV / SQLite (one table per station profile). Importing
the ``nic_glue_out`` package puts ``third_party/`` on ``sys.path``, so the
vendored module is importable from here.

A single ``.mla`` is either v1.2-schema (use the normal GlueReader) or a
datalogger file (use this). Tell them apart with ``is_datalogger()``.
"""
from mla_datalogger import (              # vendored: third_party/nic_mla/mla_datalogger.py
    export_csv, export_sqlite, read_mla, DataloggerTables,
    DataloggerBuilder, dl_gps, dl_ident, dl_raw,
)
from nic_mla import MlaCore, MlaPosixHAL

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
