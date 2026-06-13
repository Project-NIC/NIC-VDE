# SPDX-License-Identifier: MIT
"""
Tests for the NIC-GLUE-IN write path.

Verifies the two things the glue is responsible for:
  • the classic RAW path round-trips and stores records uncompressed
    (``compressed`` False, ``kf_back`` 0);
  • the DMD path round-trips losslessly *and* lines the ports up — every record
    is stored compressed, ``kf_back`` is 0 exactly on DMD's keyframe cadence and
    counts records back to that keyframe otherwise;
  • ChannelBank.reset_all (the rotation→keyframe seam, 2b) forces a keyframe.

Runnable directly (``python3 tests/test_glue.py``) or under pytest.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nic_glue_in  # noqa: E402  — also puts third_party on sys.path
from nic_glue_in import (  # noqa: E402
    GlueLogger, GlueArchiveLogger, ChannelBank, MlaSchemaBuilder, MlaStationTable,
    DMD_KEYFRAME_EVERY,
)
import glob  # noqa: E402
from nic_dmd import DmdDecoder  # noqa: E402
from nic_mla import MlaCore, MlaPosixHAL  # noqa: E402

ROW_WIDTH = 8


def _schema_stations():
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    for name in ("a", "b", "c", "d"):
        sb.data(name, unit="raw", width=2)
    st = MlaStationTable()
    st.station(region=1, number=100)
    return sb.table(), st.table()


def _read_all(path):
    with MlaPosixHAL(path) as hal:
        core = MlaCore(hal)
        core.mount()
        return list(core)   # [(MlaLog, data)]


def test_raw_roundtrip():
    schema, stations = _schema_stations()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "raw.mla")
        rows = [bytes([i & 0xFF]) * ROW_WIDTH for i in range(5)]
        with GlueLogger(path, schema_table=schema, station_table=stations,
                        keyframe_intv=0) as log:
            for i, row in enumerate(rows):
                log.log_raw(1000 + i, 1, row)
            log.log_event(2000, 1, "PING")

        recs = _read_all(path)
        assert len(recs) == 6, recs
        for i, (rec, data) in enumerate(recs[:5]):
            assert rec.compressed is False
            assert rec.kf_back == 0
            assert data == rows[i]
        ev_rec, ev_data = recs[5]
        # MLA v1.1 has no record-type tag — an event is just an uncompressed
        # record; distinguish it by context, not by a type byte.
        assert ev_rec.compressed is False
        assert ev_data == b"PING"


def test_dmd_roundtrip_and_keyframes():
    schema, stations = _schema_stations()
    n = DMD_KEYFRAME_EVERY * 2 + 3        # span several keyframe cycles
    rows = [bytes([(i * 3) & 0xFF, i & 0xFF]) * (ROW_WIDTH // 2) for i in range(n)]

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "dmd.mla")
        with GlueLogger(path, schema_table=schema, station_table=stations) as log:
            # default keyframe_intv: the glue seeds DMD's cadence
            assert log.keyframe_intv == DMD_KEYFRAME_EVERY
            ch = log.open_compressed_channel(1, pkt_len=ROW_WIDTH)
            for i, row in enumerate(rows):
                ch.log(3000 + i, row)

        recs = _read_all(path)
        assert len(recs) == n

        # lossless round-trip: replay the stream through a fresh decoder
        dec = DmdDecoder(ROW_WIDTH)
        for i, (rec, blob) in enumerate(recs):
            assert rec.compressed is True, i      # every DMD record is compressed
            if i % DMD_KEYFRAME_EVERY == 0:
                assert rec.kf_back == 0, (i, rec.kf_back)        # keyframe
            else:
                assert rec.kf_back == i % DMD_KEYFRAME_EVERY, (i, rec.kf_back)
            assert dec.decompress(blob) == rows[i], i


def test_channelbank_reset_forces_keyframe():
    """The rotation→keyframe seam (2b): after a writer rolls over, the glue
    resets its channels (ChannelBank.on_rotate → reset_all) so each stream's
    next record is a keyframe and the new file is independently decodable."""
    schema, stations = _schema_stations()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bank.mla")
        with GlueLogger(path, schema_table=schema, station_table=stations) as log:
            bank = ChannelBank(log)
            rows = [bytes([(i * 5) & 0xFF, i & 0xFF]) * (ROW_WIDTH // 2) for i in range(4)]
            for i, row in enumerate(rows):
                bank.log(1, ROW_WIDTH, 5000 + i, row)
            # Simulate MLA's rotation callback: MlaArchive(on_rotate=bank.on_rotate)
            bank.on_rotate(0, 1)
            bank.log(1, ROW_WIDTH, 6000, rows[0])

        recs = _read_all(path)
        assert recs[0][0].kf_back == 0           # first record is always a keyframe
        assert recs[1][0].kf_back == 1           # then deltas
        # The record right after the reset must be a fresh keyframe.
        assert recs[len(rows)][0].kf_back == 0, recs[len(rows)][0].kf_back
        assert recs[len(rows)][0].compressed is True


def test_width_mismatch_rejected():
    schema, stations = _schema_stations()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bad.mla")
        with GlueLogger(path, schema_table=schema, station_table=stations) as log:
            ch = log.open_compressed_channel(1, pkt_len=ROW_WIDTH)
            try:
                ch.log(1, b"\x00" * (ROW_WIDTH + 1))
            except ValueError:
                pass
            else:
                raise AssertionError("expected ValueError on wrong-width row")


def test_archive_rotation_files_independently_decodable():
    """GlueArchiveLogger rolls over to new files; each file starts every stream on
    a keyframe (2b), so a single file decodes on its own. Verified per file with a
    FRESH decoder seeded only from that file, across two interleaved streams — the
    triggering stream keyframes via will_rotate, the other via the on_rotate seam.
    """
    schema, _ = _schema_stations()
    st = MlaStationTable()
    st.station(region=1, number=100)
    st.station(region=2, number=200)
    n = 200
    rows1 = [bytes([(i * 7) & 0xFF, i & 0xFF]) * (ROW_WIDTH // 2) for i in range(n)]
    rows2 = [bytes([(i * 3) & 0xFF, (i * 2) & 0xFF]) * (ROW_WIDTH // 2) for i in range(n)]

    with tempfile.TemporaryDirectory() as d:
        with GlueArchiveLogger(d, file_size=2048, schema_table=schema,
                               station_table=st.table()) as lg:
            bank = ChannelBank(lg)          # auto-wires the rotation seam
            for i in range(n):
                bank.log(1, ROW_WIDTH, 5000 + i, rows1[i])
                bank.log(2, ROW_WIDTH, 5000 + i, rows2[i])

        files = sorted(glob.glob(os.path.join(d, "MLA*.MLA")))
        assert len(files) >= 2, f"expected a rotation, got {files}"

        got1, got2 = [], []
        for fp in files:
            recs = _read_all(fp)            # opens THIS file on its own
            assert recs, fp
            dec: dict[int, DmdDecoder] = {}
            for rec, blob in recs:
                stn = rec.station
                assert rec.compressed is True
                if stn not in dec:
                    # first record of this stream in this file must be a keyframe
                    assert rec.kf_back == 0, (fp, stn, "first record not a keyframe")
                    assert (blob[0] & 0x07) == 0, (fp, stn)
                    dec[stn] = DmdDecoder(ROW_WIDTH)
                (got1 if stn == 1 else got2).append(dec[stn].decompress(blob))

        assert got1 == rows1
        assert got2 == rows2


def test_subsec_passthrough():
    """log_raw / log_event carry the two opaque subsec bytes through to MLA."""
    schema, stations = _schema_stations()
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "ss.mla")
        with GlueLogger(path, schema_table=schema, station_table=stations,
                        keyframe_intv=0) as log:
            log.log_raw(1000, 1, b"\x01" * ROW_WIDTH, subsec=0x0102)
            log.log_event(1001, 1, "PING", subsec=0x00FF)
        recs = _read_all(path)
        assert recs[0][0].subsec == 0x0102
        assert recs[1][0].subsec == 0x00FF


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
