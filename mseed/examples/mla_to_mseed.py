#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""
Example: build a tiny seismic-style .mla, then export it to miniSEED.

It writes one station with three axes (z/n/e) compressed through NIC-DMD — the
NIC-Quake shape — then converts the container to a standard miniSEED file and
prints what came out. Open the .mseed in ObsPy / SeisComp / SWARM.

Usage:  python3 examples/mla_to_mseed.py [out_dir]
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import nic_mseed  # noqa: E402  — puts third_party (nic_mla, nic_dmd) on sys.path
from nic_mseed import MseedExporter, STEIM2
from nic_mseed.mseed import read_stream
from nic_mla import MlaCore, MlaPosixHAL
from mla_schema import MlaSchemaBuilder, MlaStationTable
from nic_dmd import DmdEncoder

RATE = 100.0          # Hz — device ODR
N = 500               # samples per axis
WIDTH = 6             # three int16 axes


def build_sample(path: str) -> None:
    sb = MlaSchemaBuilder()
    sb.log("datetime")
    sb.data("z", unit="raw", width=2, signed=True)
    sb.data("n", unit="raw", width=2, signed=True)
    sb.data("e", unit="raw", width=2, signed=True)
    st = MlaStationTable()
    st.station(region=55, number=25000)

    hal = MlaPosixHAL.create(path, file_size=512 * 1024)
    with hal:
        core = MlaCore(hal)
        core.format(file_size=512 * 1024, schema_table=sb.table(), station_table=st.table())
        enc = DmdEncoder(WIDTH)
        since_kf = 0
        t0 = 1_748_000_000
        for i in range(N):
            z = int(800 * math.sin(i / 8.0))
            n = int(500 * math.sin(i / 13.0 + 1))
            e = int(300 * math.sin(i / 5.0 + 2))
            row = b"".join(v.to_bytes(2, "little", signed=True) for v in (z, n, e))
            blob = enc.compress(row)
            since_kf = 0 if (blob[0] & 0x07) == 0 else since_kf + 1
            # subsec = sample index within the second (RATE samples/s)
            core.append(t0 + i // int(RATE), station=1, data=blob,
                        subsec=i % int(RATE), compressed=True, kf_back=since_kf)


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(__file__)
    os.makedirs(out_dir, exist_ok=True)
    mla = os.path.join(out_dir, "quake.mla")
    mseed = os.path.join(out_dir, "quake.mseed")

    build_sample(mla)
    stats = MseedExporter(
        sample_rate_hz=RATE, network="NQ", version=STEIM2,
        channel_map={"z": "HHZ", "n": "HHN", "e": "HHE"},
        subsec_unit="index",
    ).export(mla, mseed)

    print(f"[mla  ] {mla}  ({os.path.getsize(mla)} B)")
    print(f"[mseed] {mseed}  ({stats['bytes']} B, {stats['channels']} channels, "
          f"{stats['records']} records, {stats['samples']} samples)")
    recs = read_stream(open(mseed, "rb").read())
    chans = sorted({(r["net"], r["sta"], r["cha"]) for r in recs})
    print("       channels:", ", ".join(f"{n}.{s}..{c}" for n, s, c in chans))
    print("Open quake.mseed in ObsPy:  from obspy import read; read('quake.mseed').plot()")


if __name__ == "__main__":
    main()
