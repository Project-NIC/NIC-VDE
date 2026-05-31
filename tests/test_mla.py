"""Tests for MlaBackend — browsing records, decoding values, exports, repair."""
import os
import sqlite3
import struct
import tempfile
import unittest

from tests import helpers
from tests.helpers import make_temp_mla, make_temp_mla_schema, SAMPLE_MLA, FIXTURE

from volkov_core.local import LocalBackend
from volkov_core.mla import MlaBackend, rec_type_name
from volkov_core.backend import Unsupported


class RecTypeNameTests(unittest.TestCase):
    def test_known_combinations(self):
        self.assertEqual(rec_type_name(0x00), "measure/raw")
        self.assertEqual(rec_type_name(0x01), "measure/delta")
        self.assertEqual(rec_type_name(0x13), "event/text")
        self.assertEqual(rec_type_name(0xF0), "checkpoint/raw")

    def test_unknown_falls_back_readable(self):
        # unknown class/enc should still produce a string, not crash
        self.assertIsInstance(rec_type_name(0x5A), str)


class MlaBackendFixtureTests(unittest.TestCase):
    """Exact assertions against a known, freshly built fixture."""

    def setUp(self):
        self.path = make_temp_mla()
        self.parent = LocalBackend(os.path.dirname(self.path))
        self.b = MlaBackend(self.path, parent=self.parent)

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def records(self):
        return [e for e in self.b.list() if e.kind == "record"]

    def test_list_updir_plus_records(self):
        items = self.b.list()
        self.assertEqual(items[0].name, "..")
        self.assertEqual(len(self.records()), len(FIXTURE))

    def test_enter_updir_returns_parent(self):
        up = self.b.enter(self.b.list()[0])
        self.assertIs(up, self.parent)

    def test_records_are_leaves(self):
        self.assertIsNone(self.b.enter(self.records()[0]))

    def test_read_returns_payload(self):
        recs = self.records()
        # fixture record 0 is a float32 measurement of 21.5
        self.assertEqual(self.b.read(recs[0]), struct.pack("<f", 21.5))
        # fixture record 2 is text
        self.assertEqual(self.b.read(recs[2]), b'{"msg":"hello"}')

    def test_decode_value_float(self):
        self.assertEqual(self.b.decode_value(self.records()[0]), "21.5000")

    def test_decode_value_text(self):
        self.assertEqual(self.b.decode_value(self.records()[2]), '{"msg":"hello"}')

    def test_info_record_rows(self):
        rows = dict(self.b.info(self.records()[0]))
        self.assertIn("Record (seq)", rows)
        self.assertIn("Station", rows)
        self.assertEqual(rows["Length"], "4 B")

    def test_info_container(self):
        rows = dict(self.b.info(self.b.list()[0]))  # ".." → container info
        self.assertEqual(rows["Records"], str(len(FIXTURE)))

    # ── exports ────────────────────────────────────────────────────────────────
    def test_to_csv_header_and_rowcount(self):
        text = self.b.to_csv().decode("utf-8")
        lines = text.strip().split("\n")
        self.assertTrue(lines[0].startswith("seq,time,unix,station,region,type"))
        self.assertEqual(len(lines), len(FIXTURE) + 1)  # header + rows

    def test_csv_value_commas_are_sanitised(self):
        # text payload has no comma, but the sanitiser must keep one field per col
        text = self.b.to_csv().decode("utf-8")
        for line in text.strip().split("\n")[1:]:
            self.assertEqual(line.count(","), 7)  # 8 columns → 7 separators

    def test_to_sqlite_is_queryable(self):
        blob = self.b.to_sqlite()
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(blob)
            con = sqlite3.connect(tmp)
            try:
                n = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
                self.assertEqual(n, len(FIXTURE))
                val = con.execute(
                    "SELECT value FROM records ORDER BY seq LIMIT 1").fetchone()[0]
                self.assertEqual(val, "21.5000")
            finally:
                con.close()
        finally:
            os.remove(tmp)

    def test_export_names(self):
        self.assertTrue(self.b.csv_name().endswith(".csv"))
        self.assertTrue(self.b.sqlite_name().endswith(".db"))

    # ── repair / health ──────────────────────────────────────────────────────
    def test_repair_clean_file_verdict_ok(self):
        rows = dict(self.b.repair_info())
        self.assertEqual(rows["Valid records"], str(len(FIXTURE)))
        self.assertIn("OK", rows["Verdict"])

    # ── append-only enforcement ────────────────────────────────────────────────
    def test_mutating_ops_unsupported(self):
        rec = self.records()[0]
        for call in (
            lambda: self.b.mkdir("x"),
            lambda: self.b.delete(rec),
            lambda: self.b.rename(rec, "y"),
            lambda: self.b.put_file("x", b""),
        ):
            with self.assertRaises(Unsupported):
                call()


@unittest.skipUnless(os.path.exists(SAMPLE_MLA), "committed sample weather.mla absent")
class CommittedSampleTests(unittest.TestCase):
    """Smoke test against the real committed sample (~549 records)."""

    def setUp(self):
        self.b = MlaBackend(SAMPLE_MLA, parent=LocalBackend(os.path.dirname(SAMPLE_MLA)))

    def test_loads_records(self):
        recs = [e for e in self.b.list() if e.kind == "record"]
        self.assertGreater(len(recs), 100)

    def test_every_record_reads(self):
        for e in self.b.list():
            if e.kind == "record":
                self.assertEqual(len(self.b.read(e)), e.size)

    def test_csv_rowcount_matches_records(self):
        recs = [e for e in self.b.list() if e.kind == "record"]
        lines = self.b.to_csv().decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), len(recs) + 1)


class MlaSchemaTests(unittest.TestCase):
    """The self-describing path: a file whose prefix carries a schema table."""

    def setUp(self):
        self.path = make_temp_mla_schema()
        self.b = MlaBackend(self.path, parent=LocalBackend(os.path.dirname(self.path)))

    def tearDown(self):
        try:
            os.remove(self.path)
        except OSError:
            pass

    def records(self):
        return [e for e in self.b.list() if e.kind == "record"]

    def test_schema_detected(self):
        self.assertTrue(self.b.has_schema)
        self.assertEqual([f.name for f in self.b._data_fields], ["temp", "humidity"])

    def test_decode_value_multifield(self):
        # row0 raw (235, 600) → temp 23.5 degC, humidity 60.0 pct
        v = self.b.decode_value(self.records()[0])
        self.assertIn("temp=23.5 degC", v)
        self.assertIn("humidity=60 pct", v)

    def test_decode_value_signed_negative(self):
        # row1 raw temp -15 → -1.5 degC (signed int16 honoured)
        self.assertIn("temp=-1.5 degC", self.b.decode_value(self.records()[1]))

    def test_text_event_falls_through(self):
        # the trailing PING doesn't match the schema width → plain text
        self.assertEqual(self.b.decode_value(self.records()[3]), "PING")

    def test_info_shows_decoded_columns(self):
        rows = dict(self.b.info(self.records()[0]))
        self.assertEqual(rows["temp"], "23.5 degC")
        self.assertEqual(rows["humidity"], "60 pct")

    def test_csv_has_field_columns(self):
        lines = self.b.to_csv().decode("utf-8").strip().split("\n")
        self.assertEqual(lines[0], "seq,time,unix,station,region,type,length,temp,humidity")
        # first data row: decoded values in the field columns
        self.assertTrue(lines[1].endswith(",23.5,60"))
        # the text event row leaves the data columns blank
        self.assertTrue(lines[-1].endswith(",,"))

    def test_csv_raw_keeps_integers(self):
        lines = self.b.to_csv(raw=True).decode("utf-8").strip().split("\n")
        self.assertTrue(lines[1].endswith(",235,600"))

    def test_sqlite_has_field_columns_and_values(self):
        blob = self.b.to_sqlite()
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            with open(tmp, "wb") as f:
                f.write(blob)
            con = sqlite3.connect(tmp)
            try:
                cols = [r[1] for r in con.execute("PRAGMA table_info(records)")]
                self.assertIn("temp", cols)
                self.assertIn("humidity", cols)
                temp = con.execute(
                    "SELECT temp FROM records ORDER BY seq LIMIT 1").fetchone()[0]
                self.assertEqual(temp, 23.5)
            finally:
                con.close()
        finally:
            os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
