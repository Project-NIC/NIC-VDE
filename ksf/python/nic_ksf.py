"""
NIC-KSF — Kolmogorov Shannon Feistel
SPECK-128 CTR encryption library

MIT License
Copyright (c) 2026 NIC — Native Intellect Community

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

★ Viva La Resistánce ★
"""

import string
from typing import Union

__version__ = "1.2"

KSF_SPECK_ROUNDS = 32


def _load64(data: bytes, off: int):
    lo = data[off] | (data[off+1]<<8) | (data[off+2]<<16) | (data[off+3]<<24)
    hi = data[off+4] | (data[off+5]<<8) | (data[off+6]<<16) | (data[off+7]<<24)
    return hi & 0xFFFFFFFF, lo & 0xFFFFFFFF


def _store64(out: bytearray, off: int, hi: int, lo: int):
    out[off+0] = lo & 0xFF
    out[off+1] = (lo >> 8) & 0xFF
    out[off+2] = (lo >> 16) & 0xFF
    out[off+3] = (lo >> 24) & 0xFF
    out[off+4] = hi & 0xFF
    out[off+5] = (hi >> 8) & 0xFF
    out[off+6] = (hi >> 16) & 0xFF
    out[off+7] = (hi >> 24) & 0xFF


def _ror8(hi: int, lo: int):
    t  = lo & 0xFF
    lo = ((lo >> 8) | (hi << 24)) & 0xFFFFFFFF
    hi = ((hi >> 8) | (t  << 24)) & 0xFFFFFFFF
    return hi, lo


def _rol3(hi: int, lo: int):
    t  = hi >> 29
    hi = ((hi << 3) | (lo >> 29)) & 0xFFFFFFFF
    lo = ((lo << 3) | t) & 0xFFFFFFFF
    return hi, lo


def _key_expand(key: bytes):
    khi, klo = _load64(key, 0)
    lhi, llo = _load64(key, 8)
    rk = [(khi, klo)]
    for i in range(KSF_SPECK_ROUNDS - 1):
        lhi, llo = _ror8(lhi, llo)
        s_lo = (llo + klo) & 0xFFFFFFFF
        s_hi = (lhi + khi + (1 if s_lo < llo else 0)) & 0xFFFFFFFF
        lhi  = s_hi
        llo  = s_lo ^ i
        khi, klo = _rol3(khi, klo)
        khi ^= lhi
        klo ^= llo
        rk.append((khi, klo))
    return rk


def _encrypt_block(rk, block: bytearray):
    xhi, xlo = _load64(block, 8)
    yhi, ylo = _load64(block, 0)
    for i in range(KSF_SPECK_ROUNDS):
        xhi, xlo = _ror8(xhi, xlo)
        s_lo = (xlo + ylo) & 0xFFFFFFFF
        s_hi = (xhi + yhi + (1 if s_lo < xlo else 0)) & 0xFFFFFFFF
        xhi  = s_hi ^ rk[i][0]
        xlo  = s_lo ^ rk[i][1]
        yhi, ylo = _rol3(yhi, ylo)
        yhi ^= xhi
        ylo ^= xlo
    _store64(block, 8, xhi, xlo)
    _store64(block, 0, yhi, ylo)


def _ctr(key: bytes, data: Union[bytes, bytearray]) -> bytearray:
    rk   = _key_expand(key)
    out  = bytearray(data)
    done = 0
    blk_i = 0
    while done < len(out):
        ctr = bytearray(16)
        ctr[15] = blk_i & 0xFF
        _encrypt_block(rk, ctr)
        chunk = min(16, len(out) - done)
        for i in range(chunk):
            out[done + i] ^= ctr[i]
        done  += chunk
        blk_i += 1
    return out


def ksf_encrypt(key: bytes, data: Union[bytes, bytearray]) -> bytearray:
    """Encrypt data with a 128-bit key (CTR mode). Returns a bytearray."""
    return _ctr(key, data)


def ksf_decrypt(key: bytes, data: Union[bytes, bytearray]) -> bytearray:
    """Decrypt data with a 128-bit key. Identical to ksf_encrypt."""
    return _ctr(key, data)
