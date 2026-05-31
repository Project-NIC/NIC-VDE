"""Tests for the dumb export library — CSV / SQLite serialisation, no MLA."""
import os
import sqlite3
import tempfile
import unittest

from tests import helpers  # noqa: F401 - path bootstrap
from volkov_core import export


class CsvTests(unittest.TestCase):
    def test_header_and_rows(self):
        out = export.to_csv(["a", "b"], [(1, 2), (3, 4)]).decode("utf-8")
        self.assertEqual(out, "a,b\n1,2\n3,4\n")

    def test_none_becomes_blank(self):
        out = export.to_csv(["a", "b"], [(1, None)]).decode("utf-8")
        self.assertEqual(out.strip().split("\n")[1], "1,")

    def test_commas_and_newlines_sanitised(self):
        out = export.to_csv(["x"], [("a,b\nc",)]).decode("utf-8")
        line = out.strip().split("\n")[1]
        self.assertEqual(line, "a;b c")
        self.assertEqual(line.count(","), 0)


class SqliteTests(unittest.TestCase):
    def _query(self, blob, sql):
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(blob)
            con = sqlite3.connect(tmp)
            try:
                return con.execute(sql).fetchall()
            finally:
                con.close()
        finally:
            os.remove(tmp)

    def test_roundtrip_values(self):
        blob = export.to_sqlite([("n", "INTEGER"), ("v", "NUMERIC")],
                                [(1, 23.5), (2, -1.5)])
        self.assertEqual(self._query(blob, "SELECT v FROM records ORDER BY n"),
                         [(23.5,), (-1.5,)])

    def test_messy_names_become_safe_unique_idents(self):
        # two columns that sanitise to the same identifier must stay distinct
        blob = export.to_sqlite([("a b", "TEXT"), ("a/b", "TEXT")], [("x", "y")])
        cols = [r[1] for r in self._query(blob, "PRAGMA table_info(records)")]
        self.assertEqual(len(cols), 2)
        self.assertEqual(len(set(cols)), 2)


if __name__ == "__main__":
    unittest.main()
