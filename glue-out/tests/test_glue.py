# SPDX-License-Identifier: MIT
"""
Tests for the NIC-GLUE-OUT read path.

Verifies the things the reader is responsible for:
  • the classic raw path reads back and decodes packed payloads via the schema
    into named, scaled values;
  • compressed (NIC-DMD) records are decompressed by replaying the station's
    stream and then decoded to the same named values as raw ones;
  • the station index is resolved back to its real region/number;
  • the table exports to CSV and SQLite with field + station columns.

Runnable directly (``python3 tests/test_glue.py``) or under pytest.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nic_glue_out  # noqa: E402  — also puts third_party on sys.path
from nic_glue_out import GlueReader, GlueArchiveReader  # noqa: E402
from nic_mla import MlaCore, MlaPosixHAL  # noqa: E402
from nic_mla_archive import MlaArchive  # noqa: E402
from mla_schema import MlaSchemaBuilder, MlaStationTable  # noqa: E402
from nic_dmd import DmdEncoder  # noqa: E402


def _pack(temp: int, hum: int) -> bytes:
    return temp.to_bytes(2, "little", signed=True) + hum.to_bytes(2, "little")


def _build(path: str) -> None:
    """A small self-describing container: temp+humidity, one station, mixed recs."""
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
    sb.data("humidity", unit="pct", width=2, exp10=-1)
    st = MlaStationTable()
    st.station(region=7, number=100)

    hal = MlaPosixHAL.create(path, file_size=64 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=64 * 1024, keyframe_intv=0,
                    schema_table=sb.table(), station_table=st.table())
        # two measurements: (temp raw 235 → 23.5 degC, humidity 600 → 60 pct), then a negative temp
        core.append(1000, 1, _pack(235, 600))
        core.append(1001, 1, _pack(-15, 550))
        # an uncompressed record (kind "raw") whose width does not match the
        # schema (3 B vs 4 B) — decodes to no named values, blank data cells
        core.append(1002, 1, b"PNG")
        # a REAL NIC-DMD-compressed record (first packet → keyframe, kf_back=0):
        # temp 240 → 24.0 degC, humidity 610 → 61 pct. The reader decompresses it.
        enc = DmdEncoder(4)
        blob = enc.compress(_pack(240, 610))
        core.append(1003, 1, blob, compressed=True, kf_back=0)
        core.sync()


def test_raw_roundtrip_and_decode():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mla")
        _build(path)
        with GlueReader(path) as r:
            assert r.record_count == 4
            assert r.has_schema
            recs = list(r)

            # record 0 decodes to the named, scaled values
            v0 = dict((n, val) for n, _u, val in recs[0].values)
            assert abs(v0["temp"] - 23.5) < 1e-9, v0
            assert abs(v0["humidity"] - 60.0) < 1e-9, v0
            assert recs[0].station_label == "7/100"
            assert (recs[0].region, recs[0].number) == (7, 100)

            # record 1: signed negative temperature
            v1 = dict((n, val) for n, _u, val in recs[1].values)
            assert abs(v1["temp"] - (-1.5)) < 1e-9, v1

            # record 2: an uncompressed "raw" record that doesn't fit the schema
            assert recs[2].kind == "raw"
            assert not recs[2].compressed
            assert recs[2].values is None

            # record 3: a compressed keyframe — now DECOMPRESSED and decoded
            assert recs[3].compressed and recs[3].kf_back == 0
            assert recs[3].kind == "keyframe"
            assert not recs[3].undecoded
            v3 = dict((n, val) for n, _u, val in recs[3].values)
            assert abs(v3["temp"] - 24.0) < 1e-9, v3
            assert abs(v3["humidity"] - 61.0) < 1e-9, v3


def test_compressed_stream_roundtrip():
    """A full NIC-DMD stream (keyframe + deltas) decompresses and decodes back to
    the original sensor values — the GLUE-IN → GLUE-OUT round-trip through DMD."""
    import math
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.mla")
        sb = MlaSchemaBuilder()
        sb.log("datetime")
        sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
        sb.data("humidity", unit="pct", width=2, exp10=-1)
        st = MlaStationTable(); st.station(region=7, number=100)
        truth = [(int(200 + 50 * math.sin(i / 4.0)), int(500 + i)) for i in range(40)]

        hal = MlaPosixHAL.create(path, file_size=64 * 1024)
        with hal:
            core = MlaCore(hal)
            core.format(file_size=64 * 1024, schema_table=sb.table(), station_table=st.table())
            enc = DmdEncoder(4)
            since_kf = 0
            for t, h in truth:
                blob = enc.compress(_pack(t, h))
                since_kf = 0 if (blob[0] & 0x07) == 0 else since_kf + 1
                core.append(1000, 1, blob, compressed=True, kf_back=since_kf)
            core.sync()

        with GlueReader(path) as r:
            got = []
            for dr in r:
                assert dr.compressed and not dr.undecoded, dr.kind
                v = dict((n, val) for n, _u, val in dr.values)
                got.append((round(v["temp"] * 10), round(v["humidity"] * 10)))
        assert got == truth, (got[:3], truth[:3])


def test_filter_by_station_and_time():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mla")
        _build(path)
        with GlueReader(path) as r:
            window = list(r.records(time_from=1001, time_to=1002))
            assert [dr.timestamp for dr in window] == [1001, 1002]


def test_csv_has_field_and_station_columns():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mla")
        _build(path)
        with GlueReader(path) as r:
            lines = r.to_csv().decode("utf-8").strip().split("\n")
        assert lines[0] == "idx,time,unix,sta_idx,region,number,kind,length,subsec_hi,subsec_lo,temp,humidity"
        assert lines[1].endswith(",1,7,100,raw,4,0,0,23.5,60")
        assert lines[3].endswith(",,")          # raw, wrong width → blank cells
        assert lines[4].endswith(",24,61")      # compressed keyframe, decompressed + decoded
        assert len(lines) == 5                  # header + 4 records


def test_csv_raw_keeps_integers():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mla")
        _build(path)
        with GlueReader(path) as r:
            lines = r.to_csv(raw=True).decode("utf-8").strip().split("\n")
        assert lines[1].endswith(",235,600")


def test_subsec_export_one_or_two_columns():
    """subsec is two opaque bytes: the export carries it split (default, two byte
    columns) or as a single 16-bit column."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "s.mla")
        sb = MlaSchemaBuilder(); sb.log("datetime"); sb.data("v", unit="pct", width=1)
        hal = MlaPosixHAL.create(path, file_size=64 * 1024)
        with hal:
            core = MlaCore(hal)
            core.format(file_size=64 * 1024, keyframe_intv=0, schema_table=sb.table())
            core.append(1000, 0, b"\x01", subsec=0x0203)   # hi = 2, lo = 3
            core.sync()
        with GlueReader(path) as r:
            split = r.to_csv().decode("utf-8").strip().split("\n")
            one   = r.to_csv(subsec_split=False).decode("utf-8").strip().split("\n")
        assert split[0].split(",")[8:10] == ["subsec_hi", "subsec_lo"]
        assert split[1].split(",")[8:10] == ["2", "3"]
        assert one[0].split(",")[8] == "subsec"
        assert one[1].split(",")[8] == "515"           # 0x0203


def test_sqlite_is_queryable():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.mla")
        _build(path)
        with GlueReader(path) as r:
            blob = r.to_sqlite()
        db = os.path.join(d, "out.db")
        with open(db, "wb") as f:
            f.write(blob)
        con = sqlite3.connect(db)
        try:
            n = con.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            assert n == 4
            cols = [c[1] for c in con.execute("PRAGMA table_info(records)")]
            assert "temp" in cols and "region" in cols
            temp = con.execute("SELECT temp FROM records ORDER BY idx LIMIT 1").fetchone()[0]
            assert temp == 23.5
            region = con.execute("SELECT region FROM records ORDER BY idx LIMIT 1").fetchone()[0]
            assert region == 7
        finally:
            con.close()


def test_archive_reader_concatenates_rotated_files():
    """GlueArchiveReader sweeps a whole rotated archive (a directory of files) and
    exports it as one dataset with a global idx — the read-side mirror of
    GLUE-IN's GlueArchiveLogger. Each file is opened with a fresh GlueReader, so
    per-file decode stays correct; raw records keep this test self-contained."""
    sb = MlaSchemaBuilder(); sb.log("datetime")
    sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
    sb.data("humidity", unit="pct", width=2, exp10=-1)
    st = MlaStationTable(); st.station(region=7, number=100)
    n = 200
    truth = [(200 + i, 500 + i) for i in range(n)]

    with tempfile.TemporaryDirectory() as d:
        with MlaArchive(d, file_size=2048, schema_table=sb.table(),
                        station_table=st.table()) as arch:
            for i, (t, h) in enumerate(truth):
                arch.append(1000 + i, 1, _pack(t, h))      # raw, self-contained

        ar = GlueArchiveReader(d)
        assert ar.file_count >= 2, ar.file_count          # actually rotated
        assert ar.record_count == n

        idxs = [dr.index for dr in ar]                    # global, contiguous
        assert idxs == list(range(n)), (idxs[:5], idxs[-5:])

        got = []
        for dr in ar:
            v = dict((nm, val) for nm, _u, val in dr.values)
            got.append((round(v["temp"] * 10), round(v["humidity"] * 10)))
        assert got == truth, (got[:3], truth[:3])

        lines = ar.to_csv().decode("utf-8").strip().split("\n")
        assert len(lines) == n + 1                        # header + every record
        assert lines[1].split(",")[0] == "0"
        assert lines[-1].split(",")[0] == str(n - 1)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
