# SPDX-License-Identifier: MIT
"""
mseed.py — minimal miniSEED 2.x record writer (big-endian, Steim-1/2).

One record = Fixed Section of Data Header (48 B) + Blockette 1000 (8 B) + pad to
the data offset (64 B) + Steim frames filling the rest of a fixed, power-of-two
record (default 512 B → 7 frames). This is the exact layout libmseed / ObsPy /
SeisComp read.

Only the universally-supported subset is emitted: FSDH + B1000. No B1001 timing
blockette, no data-record extensions — kept deliberately small, like the rest of
the NIC ecosystem.
"""
from __future__ import annotations

import math
import struct
import time

from .steim import STEIM1, STEIM2, encode_record

_FSDH = 48
_B1000 = 8
_DATA_OFFSET = 64          # FSDH(48) + B1000(8) padded up to 64


def _pad(code: str, n: int) -> bytes:
    """ASCII, space-padded / truncated to n bytes (SEED code convention)."""
    return code.encode("ascii", "replace")[:n].ljust(n, b" ")


def _btime(unix_seconds: int, fraction: float) -> bytes:
    """SEED BTIME (10 B): year, day-of-year, h, m, s, unused, 0.0001 s."""
    t = time.gmtime(unix_seconds)
    tenk = int(round(fraction * 10000.0))
    tenk = 0 if tenk < 0 else (9999 if tenk > 9999 else tenk)
    return struct.pack(">HHBBBBH", t.tm_year, t.tm_yday, t.tm_hour,
                       t.tm_min, t.tm_sec, 0, tenk)


def rate_factor_mult(rate_hz: float) -> tuple[int, int]:
    """SEED sample-rate (factor, multiplier). Positive integer Hz → (rate, 1);
    integer sub-Hz period → (-period, 1)."""
    if rate_hz <= 0:
        return 0, 0
    if rate_hz >= 1 and abs(rate_hz - round(rate_hz)) < 1e-9:
        return int(round(rate_hz)), 1
    period = 1.0 / rate_hz
    if abs(period - round(period)) < 1e-9:
        return -int(round(period)), 1
    return int(round(rate_hz)), 1            # fallback: nearest integer Hz


def _record(seq: int, net: str, sta: str, loc: str, cha: str,
            start_unix: int, start_frac: float, nsamples: int,
            rate_hz: float, encoding: int, reclen_exp: int,
            payload: bytes) -> bytes:
    reclen = 1 << reclen_exp
    body_len = reclen - _DATA_OFFSET
    if len(payload) != body_len:
        raise ValueError(f"payload {len(payload)} B != record body {body_len} B")

    fsdh = bytearray(_FSDH)
    fsdh[0:6] = f"{seq % 1_000_000:06d}".encode("ascii")
    fsdh[6:7] = b"D"                          # data quality indicator
    fsdh[7:8] = b" "                          # reserved
    fsdh[8:13] = _pad(sta, 5)
    fsdh[13:15] = _pad(loc, 2)
    fsdh[15:18] = _pad(cha, 3)
    fsdh[18:20] = _pad(net, 2)
    fsdh[20:30] = _btime(start_unix, start_frac)
    factor, mult = rate_factor_mult(rate_hz)
    struct.pack_into(">H", fsdh, 30, nsamples)
    struct.pack_into(">hh", fsdh, 32, factor, mult)
    fsdh[36] = 0                              # activity flags
    fsdh[37] = 0                              # I/O flags
    fsdh[38] = 0                              # data quality flags
    fsdh[39] = 1                              # number of blockettes that follow
    struct.pack_into(">i", fsdh, 40, 0)       # time correction
    struct.pack_into(">H", fsdh, 44, _DATA_OFFSET)
    struct.pack_into(">H", fsdh, 46, _FSDH)   # first blockette at offset 48

    b1000 = struct.pack(">HHBBBB", 1000, 0, encoding, 1, reclen_exp, 0)
    pad = b"\x00" * (_DATA_OFFSET - _FSDH - _B1000)
    return bytes(fsdh) + b1000 + pad + payload


def write_stream(samples, *, start_unix: int, sample_rate_hz: float,
                 network: str, station: str, location: str, channel: str,
                 start_frac: float = 0.0, version: int = STEIM2,
                 reclen: int = 512, seq_start: int = 1) -> bytes:
    """Encode one channel's integer sample series into a miniSEED byte stream
    (concatenated fixed-length records). Time of each record is derived from the
    start time, the sample rate and the number of samples already emitted."""
    if reclen & (reclen - 1) or reclen < 128:
        raise ValueError("reclen must be a power of two >= 128")
    reclen_exp = reclen.bit_length() - 1
    frames_per_record = (reclen - _DATA_OFFSET) // 64
    encoding = 11 if version == STEIM2 else 10

    samples = [int(s) for s in samples]
    out = bytearray()
    seq, i, prev = seq_start, 0, 0
    t0 = start_unix + start_frac
    while i < len(samples):
        payload, used = encode_record(samples[i:], version, frames_per_record, prev)
        rec_start = t0 + i / sample_rate_hz
        u = int(math.floor(rec_start))
        out += _record(seq, network, station, location, channel,
                       u, rec_start - u, used, sample_rate_hz,
                       encoding, reclen_exp, payload)
        prev = samples[i + used - 1]
        i += used
        seq += 1
    return bytes(out)


# ── Minimal reader (validation / round-trip; real consumers use ObsPy) ────────

def read_stream(blob: bytes, version: int | None = None):
    """Parse a miniSEED byte stream this writer produced → list of records:
    [{net,sta,loc,cha,start_unix,start_frac,rate_hz,nsamples,samples}]. Reads the
    record length and encoding from each record's Blockette 1000."""
    from .steim import decode_record
    out = []
    pos = 0
    while pos + _FSDH <= len(blob):
        fsdh = blob[pos:pos + _FSDH]
        sta = fsdh[8:13].decode("ascii").strip()
        loc = fsdh[13:15].decode("ascii").strip()
        cha = fsdh[15:18].decode("ascii").strip()
        net = fsdh[18:20].decode("ascii").strip()
        yr, doy, hh, mm, ss, _u, tenk = struct.unpack(">HHBBBBH", fsdh[20:30])
        nsamples, = struct.unpack(">H", fsdh[30:32])
        factor, mult = struct.unpack(">hh", fsdh[32:36])
        data_off, = struct.unpack(">H", fsdh[44:46])
        blk_off, = struct.unpack(">H", fsdh[46:48])
        btype, _next, enc, _wo, rlexp, _r = struct.unpack(">HHBBBB", blob[pos + blk_off:pos + blk_off + 8])
        if btype != 1000:
            raise ValueError("expected Blockette 1000")
        reclen = 1 << rlexp
        ver = STEIM2 if enc == 11 else STEIM1
        payload = blob[pos + data_off:pos + reclen]
        samples = decode_record(payload, nsamples, ver)
        # rate
        if factor > 0 and mult > 0:   rate = factor * mult
        elif factor > 0 and mult < 0: rate = -factor / mult
        elif factor < 0 and mult > 0: rate = -mult / factor
        else:                          rate = 1.0
        # start time → unix (from year + day-of-year)
        unix = struct_to_unix(yr, doy, hh, mm, ss)
        out.append(dict(net=net, sta=sta, loc=loc, cha=cha, nsamples=nsamples,
                        rate_hz=rate, start_unix=unix, start_frac=tenk / 10000.0,
                        samples=samples))
        pos += reclen
    return out


def struct_to_unix(year: int, doy: int, hh: int, mm: int, ss: int) -> int:
    import calendar
    # days since epoch for Jan 1 of `year`, plus (doy-1) days
    jan1 = calendar.timegm((year, 1, 1, 0, 0, 0, 0, 1, 0))
    return jan1 + (doy - 1) * 86400 + hh * 3600 + mm * 60 + ss
