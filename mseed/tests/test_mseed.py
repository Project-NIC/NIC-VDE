# SPDX-License-Identifier: MIT
"""
miniSEED writer tests.

Always: round-trip through this package's own minimal reader (structural check).
If ObsPy is installed: also validate against it (the gold standard) — this is the
proof of spec-compliance; run it on a machine with ObsPy.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_mseed.steim import STEIM1, STEIM2
from nic_mseed.mseed import write_stream, read_stream

_p = _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else:    _f += 1; print(f"  FAIL  {name}")


def main():
    print("miniSEED writer tests")
    import itertools, random
    random.seed(7)
    samples = list(itertools.accumulate([random.randint(-2000, 2000) for _ in range(1500)]))
    start_unix = 1_748_000_000

    for ver, vn in ((STEIM1, "S1"), (STEIM2, "S2")):
        for reclen in (512, 4096):
            blob = write_stream(samples, start_unix=start_unix, start_frac=0.25,
                                sample_rate_hz=100.0, network="NQ", station="QK001",
                                location="", channel="HHZ", version=ver, reclen=reclen)
            check(f"{vn} reclen={reclen}: byte length is whole records",
                  len(blob) % reclen == 0 and len(blob) > 0)
            recs = read_stream(blob)
            recovered = [s for r in recs for s in r["samples"]]
            check(f"{vn} reclen={reclen}: round-trip samples", recovered == samples)
            check(f"{vn} reclen={reclen}: codes preserved",
                  recs[0]["net"] == "NQ" and recs[0]["sta"] == "QK001"
                  and recs[0]["cha"] == "HHZ")
            check(f"{vn} reclen={reclen}: rate preserved", abs(recs[0]["rate_hz"] - 100.0) < 1e-9)
            check(f"{vn} reclen={reclen}: start time preserved",
                  recs[0]["start_unix"] == start_unix and abs(recs[0]["start_frac"] - 0.25) < 1e-4)
            # second record's start advances by (samples_in_rec0 / rate)
            if len(recs) > 1:
                n0 = recs[0]["nsamples"]
                t0 = recs[0]["start_unix"] + recs[0]["start_frac"]
                t1 = recs[1]["start_unix"] + recs[1]["start_frac"]
                check(f"{vn} reclen={reclen}: record-2 time = t0 + n0/rate",
                      abs((t1 - t0) - n0 / 100.0) < 1e-3)

    # sub-Hz rate encodes as a negative factor (period)
    blob = write_stream([1, 2, 3, 4, 5], start_unix=start_unix, sample_rate_hz=0.1,
                        network="NQ", station="SLOW", location="", channel="LHZ")
    check("sub-Hz rate round-trips", abs(read_stream(blob)[0]["rate_hz"] - 0.1) < 1e-9)

    # Optional gold-standard validation against ObsPy.
    try:
        import io
        from obspy import read as obspy_read
        blob = write_stream(samples, start_unix=start_unix, sample_rate_hz=100.0,
                            network="NQ", station="QK001", location="", channel="HHZ",
                            version=STEIM2, reclen=512)
        st = obspy_read(io.BytesIO(blob))
        ok = list(st[0].data.astype(int)) == samples
        check("ObsPy reads our Steim-2 miniSEED and samples match", ok)
        check("ObsPy sees rate 100 Hz", abs(st[0].stats.sampling_rate - 100.0) < 1e-6)
    except ImportError:
        print("  SKIP  ObsPy not installed — run this test on a machine with ObsPy "
              "for gold-standard validation")

    print(f"\nResult: {_p}/{_p+_f} PASS | {_f} FAIL")
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
