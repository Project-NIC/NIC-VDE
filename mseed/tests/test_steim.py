# SPDX-License-Identifier: MIT
"""Round-trip + edge tests for the Steim-1/2 codec (no external deps)."""
import os
import random
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nic_mseed.steim import STEIM1, STEIM2, encode, decode, encode_record, decode_record

_p = _f = 0
def check(name, cond):
    global _p, _f
    if cond: _p += 1; print(f"  PASS  {name}")
    else:    _f += 1; print(f"  FAIL  {name}")


def _roundtrip(samples, version, fpr=7):
    recs = encode(samples, version, frames_per_record=fpr)
    # recover per-record counts by decoding greedily: we know total; re-derive by
    # decoding each record with the max it could hold, then trim — instead, encode
    # exposes counts via encode_record, so reproduce the split here:
    counts, prev, i = [], 0, 0
    s = list(samples)
    for rec in recs:
        _, used = encode_record(s[i:], version, fpr, prev)
        counts.append(used); prev = s[i + used - 1]; i += used
    return decode(recs, counts, version)


def main():
    print("Steim-1/2 codec tests")
    random.seed(1)
    cases = {
        "single sample":        [12345],
        "constant":             [777] * 50,
        "small ramp":           list(range(0, 300)),
        "tiny diffs (4-bit)":   [0] + list(__import__("itertools").accumulate(
                                    [random.randint(-7, 7) for _ in range(500)])),
        "medium diffs":         [0] + list(__import__("itertools").accumulate(
                                    [random.randint(-3000, 3000) for _ in range(500)])),
        "large (15/30-bit)":    [0] + list(__import__("itertools").accumulate(
                                    [random.randint(-10_000_000, 10_000_000) for _ in range(300)])),
        "negatives + zeros":    [(-1) ** i * (i % 9) for i in range(400)],
        "alternating big/small":[(1 << 20) if i % 2 else 1 for i in range(200)],
    }
    for name, samples in cases.items():
        for ver, vn in ((STEIM1, "S1"), (STEIM2, "S2")):
            try:
                got = _roundtrip(samples, ver)
                check(f"{vn} round-trip: {name}", got == list(samples))
            except OverflowError as e:
                check(f"{vn} round-trip: {name} (overflow handled)", ver == STEIM2 and False or True) \
                    if False else check(f"{vn} {name}: unexpected overflow", False)

    # Steim-2 must reject a >30-bit difference; Steim-1 must accept up to 32-bit.
    big = [0, (1 << 29)]            # diff = 2^29 — fits 30-bit signed? max is 2^29-1 → no
    try:
        encode(big, STEIM2); check("S2 rejects >30-bit diff", False)
    except OverflowError:
        check("S2 rejects >30-bit diff", True)
    check("S1 accepts 32-bit diff", _roundtrip([0, (1 << 29)], STEIM1) == [0, (1 << 29)])

    # Frame math: a constant signal (all-zero diffs) packs 7×4-bit and compresses
    # hard — a 7-frame record holds 103 data words × 7 = 721 such samples.
    flat = [100] * 1000
    recs = encode(flat, STEIM2, frames_per_record=7)
    total = len(recs) * 7 * 64
    check("constant signal: ≥2× compression vs raw int32", total < len(flat) * 4 // 2)
    check("constant signal round-trips", _roundtrip(flat, STEIM2) == flat)

    print(f"\nResult: {_p}/{_p+_f} PASS | {_f} FAIL")
    sys.exit(0 if _f == 0 else 1)


if __name__ == "__main__":
    main()
