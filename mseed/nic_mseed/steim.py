# SPDX-License-Identifier: MIT
"""
steim.py — Steim-1 and Steim-2 compression (FDSN/SEED), pure Python, no deps.

Steim is the lossless integer difference compression used by miniSEED. Data is
laid out in **64-byte frames** = 16 big-endian uint32 words. Word 0 of each
frame is a *control word*: sixteen 2-bit nibbles, one per word (MSB-first), that
say how the corresponding word is packed:

    00  non-data    (control word itself; in frame 0 also the X0/Xn words)
    01  four  8-bit differences
    10  Steim-1: two 16-bit diffs   · Steim-2: see the data word's top-2-bit dnib
    11  Steim-1: one 32-bit diff    · Steim-2: see dnib

Steim-2 sub-codes (dnib = top 2 bits of the data word):
    nibble 10 → dnib 01: 1×30-bit · 10: 2×15-bit · 11: 3×10-bit
    nibble 11 → dnib 00: 5×6-bit  · 01: 6×5-bit  · 10: 7×4-bit (low 28 bits)

Frame 0 reserves word 1 = X0 (first sample, the forward integration constant)
and word 2 = Xn (last sample, reverse constant). The decoder reconstructs
sample[0] = X0, then sample[i] = sample[i-1] + diff[i].

This module is container-agnostic: it just turns a list of ints into frames and
back. The miniSEED record framing lives in `mseed.py`.

Reference: SEED Manual v2.4, Appendix B (Steim-1/2); matches libmseed output.
"""
from __future__ import annotations

import struct
from typing import Iterable

STEIM1 = 1
STEIM2 = 2

_FRAME_WORDS = 16
_FRAME_BYTES = 64


def _fits(v: int, bits: int) -> bool:
    lim = 1 << (bits - 1)
    return -lim <= v <= lim - 1


def _u(v: int, bits: int) -> int:
    """Two's-complement `bits`-wide unsigned representation of v."""
    return v & ((1 << bits) - 1)


def _sx(raw: int, bits: int) -> int:
    """Sign-extend a `bits`-wide unsigned value to a Python int."""
    if raw & (1 << (bits - 1)):
        return raw - (1 << bits)
    return raw


# Candidate packings, most-packed first: (count, bits, nibble, dnib_or_None).
_STEIM2 = [
    (7, 4, 0b11, 0b10),
    (6, 5, 0b11, 0b01),
    (5, 6, 0b11, 0b00),
    (4, 8, 0b01, None),
    (3, 10, 0b10, 0b11),
    (2, 15, 0b10, 0b10),
    (1, 30, 0b10, 0b01),
]
_STEIM1 = [
    (4, 8, 0b01, None),
    (2, 16, 0b10, None),
    (1, 32, 0b11, None),
]


def _pack_word(diffs: list[int], count: int, bits: int, nibble: int, dnib) -> int:
    """Assemble one 32-bit data word from `count` diffs of `bits` bits each."""
    if nibble == 0b01:                       # 4 × 8-bit (both versions)
        return sum(_u(diffs[i], 8) << (24 - 8 * i) for i in range(4))
    if dnib is None:                         # Steim-1 16/32-bit
        if bits == 16:
            return (_u(diffs[0], 16) << 16) | _u(diffs[1], 16)
        return _u(diffs[0], 32)              # 1 × 32-bit
    # Steim-2 dnib-coded words
    if count == 7 and bits == 4:             # special: low 28 bits, dnib at 31-30
        word = dnib << 30
        for i in range(7):
            word |= _u(diffs[i], 4) << (24 - 4 * i)
        return word
    word = dnib << 30
    for i in range(count):                   # MSB-first just below the dnib
        word |= _u(diffs[i], bits) << (30 - bits * (i + 1))
    return word


def _encode_words(diffs: list[int], version: int):
    """Greedily pack diffs → list of (nibble, word32, n_diffs_consumed)."""
    table = _STEIM2 if version == STEIM2 else _STEIM1
    out = []
    i, n = 0, len(diffs)
    while i < n:
        for count, bits, nibble, dnib in table:
            if i + count <= n and all(_fits(diffs[i + k], bits) for k in range(count)):
                out.append((nibble, _pack_word(diffs[i:i + count], count, bits, nibble, dnib),
                            count))
                i += count
                break
        else:
            raise OverflowError(
                f"difference {diffs[i]} too large for Steim-{version} "
                f"(max field is {'30' if version == STEIM2 else '32'} bits)")
    return out


def encode_record(samples: list[int], version: int, frames_per_record: int,
                  prev: int = 0) -> tuple[bytes, int]:
    """Encode as many leading `samples` as fit into one record of
    `frames_per_record` 64-byte frames.

    Returns (record_bytes, n_samples_used). The caller slices off the used
    samples and calls again (with prev = last used sample) for the next record.
    """
    if frames_per_record < 1:
        raise ValueError("need at least one frame per record")
    diffs = [samples[0] - prev]
    diffs += [samples[i] - samples[i - 1] for i in range(1, len(samples))]
    entries = _encode_words(diffs, version)

    capacity = 13 + (frames_per_record - 1) * 15   # frame0: words 3..15, rest 1..15
    taken, used_words, used_samples = [], 0, 0
    for nibble, word, count in entries:
        if used_words + 1 > capacity:
            break
        taken.append((nibble, word))
        used_words += 1
        used_samples += count
    if used_samples == 0:
        raise ValueError("a single difference does not fit in one record")

    x0 = samples[0]
    xn = samples[used_samples - 1]

    # Lay the data words out across the frames; word 0 of each frame is control.
    frames = bytearray()
    di = 0
    for f in range(frames_per_record):
        words = [0] * _FRAME_WORDS
        nibs = [0] * _FRAME_WORDS
        start = 3 if f == 0 else 1
        if f == 0:
            words[1], words[2] = _u(x0, 32), _u(xn, 32)
        w = start
        while w < _FRAME_WORDS and di < len(taken):
            nib, word = taken[di]
            words[w], nibs[w] = word, nib
            di += 1
            w += 1
        control = 0
        for j in range(_FRAME_WORDS):
            control |= nibs[j] << (2 * (15 - j))
        words[0] = control
        frames += struct.pack(">16I", *words)
    return bytes(frames), used_samples


def encode(samples: list[int], version: int = STEIM2,
           frames_per_record: int = 7) -> list[bytes]:
    """Encode a full sample series into a list of fixed-size record payloads
    (each `frames_per_record` × 64 bytes). Convenience over `encode_record`."""
    samples = list(samples)
    if not samples:
        return []
    out, prev, i = [], 0, 0
    while i < len(samples):
        rec, used = encode_record(samples[i:], version, frames_per_record, prev)
        out.append(rec)
        prev = samples[i + used - 1]
        i += used
    return out


# ── Decoder (for validation / round-trip; the world uses libmseed/ObsPy) ──────

def decode_record(frames: bytes, n_samples: int, version: int) -> list[int]:
    """Decode one Steim record back to `n_samples` integers."""
    nframes = len(frames) // _FRAME_BYTES
    diffs: list[int] = []
    x0 = None
    for f in range(nframes):
        base = f * _FRAME_BYTES
        words = struct.unpack(">16I", frames[base:base + _FRAME_BYTES])
        control = words[0]
        start = 1
        if f == 0:
            x0 = _sx(words[1], 32)
            start = 3
        for w in range(start, _FRAME_WORDS):
            nib = (control >> (2 * (15 - w))) & 0b11
            if nib == 0b00:
                continue
            word = words[w]
            diffs.extend(_decode_word(word, nib, version))
            if len(diffs) >= n_samples:
                break
        if len(diffs) >= n_samples:
            break

    # Integrate: sample[0] = X0, sample[i] = sample[i-1] + diff[i].
    out = [x0]
    for d in diffs[1:n_samples]:
        out.append(out[-1] + d)
    return out[:n_samples]


def _decode_word(word: int, nibble: int, version: int) -> list[int]:
    if nibble == 0b01:                                   # 4 × 8-bit
        return [_sx((word >> (24 - 8 * i)) & 0xFF, 8) for i in range(4)]
    if version == STEIM1:
        if nibble == 0b10:                               # 2 × 16-bit
            return [_sx((word >> 16) & 0xFFFF, 16), _sx(word & 0xFFFF, 16)]
        return [_sx(word & 0xFFFFFFFF, 32)]              # 1 × 32-bit
    dnib = (word >> 30) & 0b11
    if nibble == 0b10:
        if dnib == 0b01:                                 # 1 × 30-bit
            return [_sx(word & ((1 << 30) - 1), 30)]
        if dnib == 0b10:                                 # 2 × 15-bit
            return [_sx((word >> 15) & 0x7FFF, 15), _sx(word & 0x7FFF, 15)]
        if dnib == 0b11:                                 # 3 × 10-bit
            return [_sx((word >> (30 - 10 * (i + 1))) & 0x3FF, 10) for i in range(3)]
    else:  # nibble == 0b11
        if dnib == 0b00:                                 # 5 × 6-bit
            return [_sx((word >> (30 - 6 * (i + 1))) & 0x3F, 6) for i in range(5)]
        if dnib == 0b01:                                 # 6 × 5-bit
            return [_sx((word >> (30 - 5 * (i + 1))) & 0x1F, 5) for i in range(6)]
        if dnib == 0b10:                                 # 7 × 4-bit (low 28 bits)
            return [_sx((word >> (24 - 4 * i)) & 0xF, 4) for i in range(7)]
    raise ValueError(f"bad Steim-2 dnib {dnib:02b} for nibble {nibble:02b}")


def decode(records: Iterable[bytes], counts: Iterable[int], version: int) -> list[int]:
    """Decode a sequence of records (with their per-record sample counts)."""
    out: list[int] = []
    for rec, n in zip(records, counts):
        out.extend(decode_record(rec, n, version))
    return out
