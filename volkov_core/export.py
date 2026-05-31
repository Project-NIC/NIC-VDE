"""
Dumb export library — turn generic tabular rows into CSV / SQLite bytes.

It knows nothing about MLA or any backend: you hand it column names and rows of
values, it hands back bytes. This keeps the storage backends thin — they decide
*what* the rows are; this decides *how* they're serialised. Reusable headless.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from typing import Iterable, Sequence


def _csv_cell(v) -> str:
    if v is None:
        return ""
    return str(v).replace(",", ";").replace("\n", " ").replace("\r", " ")


def to_csv(headers: Sequence[str], rows: Iterable[Sequence]) -> bytes:
    """Serialise rows to CSV (UTF-8). Cells are sanitised to stay one-per-column."""
    out = [",".join(_csv_cell(h) for h in headers)]
    for row in rows:
        out.append(",".join(_csv_cell(c) for c in row))
    return ("\n".join(out) + "\n").encode("utf-8")


def _sql_ident(name: str, used: set) -> str:
    """A safe, unique SQL identifier derived from a free-form column name."""
    ident = "".join(c if c.isalnum() else "_" for c in str(name)) or "col"
    if ident[0].isdigit():
        ident = "c_" + ident
    base, n = ident, 1
    while ident.lower() in used:
        n += 1
        ident = f"{base}_{n}"
    used.add(ident.lower())
    return ident


def to_sqlite(columns: Sequence[tuple[str, str]], rows: Iterable[Sequence],
              table: str = "records") -> bytes:
    """Serialise rows to a single-table SQLite database, returned as bytes.

    columns — sequence of (name, sql_decl) e.g. ("temp", "NUMERIC"). Names are
    sanitised to unique SQL identifiers. SQLite needs a real path, so this builds
    in a temp file and reads the bytes back.
    """
    used: set = set()
    idents = [_sql_ident(name, used) for name, _decl in columns]
    decls = ", ".join(f"{i} {d}" for i, (_n, d) in zip(idents, columns))
    placeholders = ",".join("?" * len(idents))

    fd, tmp = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(tmp)
        try:
            con.execute(f"CREATE TABLE {table} ({decls})")
            con.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                [tuple(r) for r in rows])
            con.commit()
        finally:
            con.close()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
