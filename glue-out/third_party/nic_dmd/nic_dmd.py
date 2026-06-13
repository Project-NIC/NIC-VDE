# SPDX-License-Identifier: MIT

"""
NIC DMD — Delta Markov Duda
===========================
Adaptive compression for embedded devices.
Nibble Huffman compression integrated as a standard method.

Header (1 byte):
  MSB                    LSB
   7    6    5    4    3    2    1    0
  [huf][ans][flg][dlt][dlt][smp][smp][smp]

  bit 7:   nibble Huffman (1=ON)
  bit 6:   µANS compression (1=ON)
  bit 5:   zero-byte flagging (1=ON)
  bit 4-3: delta: 00=none, 01=1B, 10=2B, 11=FULL (big-int with carry)
  bit 2-0: sample number 0-6 (0 = keyframe, 7 = reserved for protocol version)

  Combination bit 7 + bit 5 = FLAG+HUF

Implementation notes:
  Code mirrors the C implementation for ATmega328 — same logic,
  same data types (uint8_t / uint16_t), same optimisations:
  [Z1] µANS state = uint16_t (range 32..8191)
  [Z2] uint8_t indices in loops
  [Z3] popcount LUT (256 B ROM in C, tuple in Python)
  [Z4] rotating mask in FLAG instead of variable shift
  [Z5] delta + ZigZag in a single pass
  [Z6] ANS byte rotation instead of (byte >> j) & 1
  [Z7] ANS byte assembly by left-shift (no reversal in decoder)
  [Z8] ANS countdown loop (from len-1 downward)
  [P3] HUF bit buffer uint16_t with flush per nibble
  [P4] DMD_DELTA_FULL — big-int with carry propagation
  [P5] ANS early exit per byte
  [P6] FLAG early exit per byte, rotating mask
  [P7] FLAG+HUF combined mode
  [P8] 4-way selection passing best_size as limit

License: MIT
NIC — Native Intellect Community
https://github.com/Project-NIC
"""

__version__ = "1.2"

# ---------------------------------------------------------------------------
# Constants — match the #define values in the C header
# ---------------------------------------------------------------------------

# Calibrated on the combined meteo+GPS dataset (see benchmarks)
DMD_ANS_SCALE    = 32    # uint8_t
DMD_ANS_WEIGHT_0 = 29    # uint8_t — weight of zero bit
DMD_ANS_WEIGHT_1 = 3     # uint8_t — weight of one bit

DMD_DELTA_NONE = 0
DMD_DELTA_1B   = 1
DMD_DELTA_2B   = 2
DMD_DELTA_FULL = 3       # [P4] big-int with carry propagation

# Sample value 7 is reserved for protocol versioning, so the cycle is shortened
DMD_KEYFRAME_EVERY = 7

# ---------------------------------------------------------------------------
# [Z3] Popcount LUT — 256 values, mirrors the PROGMEM table in C
# ---------------------------------------------------------------------------

_POPCOUNT_LUT = (
    0,1,1,2,1,2,2,3, 1,2,2,3,2,3,3,4, 1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7, 4,5,5,6,5,6,6,7, 5,6,6,7,6,7,7,8,
)

def _count_onebits(data: bytes) -> int:
    """Count set bits — mirrors count_ones() in C."""
    n = 0
    for b in data:
        n += _POPCOUNT_LUT[b]
    return n

# ---------------------------------------------------------------------------
# [P2] Fixed nibble Huffman table in ROM
# Trained on combined data (meteo + GPS) after delta+ZZ
# 64 B ROM — hi nibble codes + lo nibble codes
# ---------------------------------------------------------------------------

_HUF_HI_LEN  = (1, 3, 3, 4, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6)
_HUF_HI_CODE = (
    0x01,  # 0x0
    0x03,  # 0x1
    0x00,  # 0x2
    0x04,  # 0x3
    0x0D,  # 0x4
    0x0C,  # 0x5
    0x0E,  # 0x6
    0x16,  # 0x7
    0x15,  # 0x8
    0x17,  # 0x9
    0x0F,  # 0xA
    0x14,  # 0xB
    0x0B,  # 0xC
    0x0A,  # 0xD
    0x08,  # 0xE
    0x09,  # 0xF
)

_HUF_LO_LEN  = (1, 4, 4, 5, 4, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6)
_HUF_LO_CODE = (
    0x01,  # 0x0
    0x04,  # 0x1
    0x07,  # 0x2
    0x0B,  # 0x3
    0x03,  # 0x4
    0x04,  # 0x5
    0x0A,  # 0x6
    0x03,  # 0x7
    0x05,  # 0x8
    0x02,  # 0x9
    0x00,  # 0xA
    0x01,  # 0xB
    0x1B,  # 0xC
    0x1A,  # 0xD
    0x19,  # 0xE
    0x18,  # 0xF
)

# [N1] Precomputed masks for bit operations.
# Must cover the full 16-bit bit buffer — bit_cnt can reach up to 13
# (5 remaining + 8 freshly loaded), so 9 entries would not suffice.
_MASKS = tuple((1 << i) - 1 for i in range(17))

# ---------------------------------------------------------------------------
# [P3] Nibble Huffman encoding
# uint16_t bit buffer with flush per nibble — exactly as in C.
# Output format: [1 B valid bits in last byte][stream MSB-first]
# ---------------------------------------------------------------------------

def _huffman_encode(data: bytes, limit: int) -> bytes | None:
    """
    Encode data with nibble Huffman.
    limit = maximum allowed output length including the 1 B header.
    Returns None if result >= limit. [P3] early exit.
    """
    if limit < 2:
        return None

    # [P3] uint16_t bit buffer with flush per nibble
    bit_buf    = 0       # uint16_t
    bit_cnt    = 0       # uint8_t
    out        = bytearray(limit)
    out_pos    = 1       # out[0] reserved for "valid bits"
    total_bits = 0
    bits_cap   = (limit - 1) * 8   # uint16_t

    for b in data:
        hi = b >> 4
        lo = b & 0x0F

        hi_len  = _HUF_HI_LEN[hi]
        hi_code = _HUF_HI_CODE[hi]
        lo_len  = _HUF_LO_LEN[lo]
        lo_code = _HUF_LO_CODE[lo]

        total_bits += hi_len + lo_len
        if total_bits > bits_cap:   # [P3] early exit
            return None

        # Emit hi nibble
        bit_buf = ((bit_buf << hi_len) | hi_code) & 0xFFFF
        bit_cnt += hi_len
        while bit_cnt >= 8:
            bit_cnt -= 8
            out[out_pos] = (bit_buf >> bit_cnt) & 0xFF
            out_pos += 1
        bit_buf &= (1 << bit_cnt) - 1

        # Emit lo nibble
        bit_buf = ((bit_buf << lo_len) | lo_code) & 0xFFFF
        bit_cnt += lo_len
        while bit_cnt >= 8:
            bit_cnt -= 8
            out[out_pos] = (bit_buf >> bit_cnt) & 0xFF
            out_pos += 1
        bit_buf &= (1 << bit_cnt) - 1

    # Flush remaining bits — align to MSB
    if bit_cnt > 0:
        out[out_pos] = (bit_buf << (8 - bit_cnt)) & 0xFF
        out_pos += 1
        out[0] = bit_cnt       # valid bits in last byte (1..7)
    else:
        out[0] = 8             # last byte is full

    return bytes(out[:out_pos])


# ---------------------------------------------------------------------------
# [P3] Nibble Huffman decoding
# uint16_t bit buffer — exactly as in C.
# ---------------------------------------------------------------------------

def _huf_decode_nibble(stream: bytes, stream_len: int,
                       in_pos: list, bit_buf: list, bit_cnt: list,
                       valid_last: int,
                       codes_tab: tuple, lens_tab: tuple) -> int:
    """
    Decode one nibble from the bit buffer.
    in_pos, bit_buf, bit_cnt are single-element lists (mutable references).
    """
    # Load enough bits — max code is 6 bits
    while bit_cnt[0] < 6 and in_pos[0] < stream_len:
        next_byte = stream[in_pos[0]]
        in_pos[0] += 1
        if in_pos[0] == stream_len and valid_last < 8:
            bit_buf[0] = ((bit_buf[0] << valid_last) | (next_byte >> (8 - valid_last))) & 0xFFFF
            bit_cnt[0] += valid_last
        else:
            bit_buf[0] = ((bit_buf[0] << 8) | next_byte) & 0xFFFF
            bit_cnt[0] += 8

    # Try every symbol
    for sym in range(16):
        code_len = lens_tab[sym]
        if bit_cnt[0] < code_len:
            continue
        # [N1] Use precomputed mask instead of (1 << code_len) - 1
        peek = (bit_buf[0] >> (bit_cnt[0] - code_len)) & _MASKS[code_len]
        if peek == codes_tab[sym]:
            bit_cnt[0] -= code_len
            bit_buf[0] &= _MASKS[bit_cnt[0]]
            return sym

    # [V3] Hard error check
    raise ValueError(f"Invalid Huffman code at index {in_pos[0]}")


def _huffman_decode(data: bytes, n_symbols: int) -> bytes:
    valid_last = data[0]
    if valid_last == 0:
        valid_last = 8
    stream     = data[1:]
    stream_len = len(stream)

    # Mutable references for in_pos, bit_buf, bit_cnt
    in_pos  = [0]
    bit_buf = [0]
    bit_cnt = [0]

    result = bytearray(n_symbols)
    for i in range(n_symbols):
        hi = _huf_decode_nibble(stream, stream_len, in_pos, bit_buf, bit_cnt,
                                valid_last, _HUF_HI_CODE, _HUF_HI_LEN)
        lo = _huf_decode_nibble(stream, stream_len, in_pos, bit_buf, bit_cnt,
                                valid_last, _HUF_LO_CODE, _HUF_LO_LEN)
        result[i] = (hi << 4) | lo
    return bytes(result)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def _build_header(sample_num: int, use_huf: bool, use_ans: bool,
                  use_flag: bool, delta_type: int) -> int:
    # [V4] Value 7 is reserved for future protocol extensions
    h  = sample_num & 0x07
    h |= (delta_type & 0x03) << 3
    if use_flag: h |= (1 << 5)
    if use_ans:  h |= (1 << 6)
    if use_huf:  h |= (1 << 7)
    return h


def _parse_header(h: int) -> dict:
    sample_num = h & 0x07
    # [V4] Reject unsupported protocol version
    if sample_num == 7:
        raise ValueError("Unsupported protocol version (sample_num=7 is reserved)")
    return {
        'use_huf':    bool(h & (1 << 7)),
        'use_ans':    bool(h & (1 << 6)),
        'use_flag':   bool(h & (1 << 5)),
        'delta_type': (h >> 3) & 0x03,
        'sample_num': sample_num,
    }


# Public alias — decoding a 1-byte DMD header is part of the supported surface
# (tooling/tests inspect which method a packet used). `_parse_header` stays for
# backward compatibility.
def parse_header(h: int) -> dict:
    """Decode a DMD header byte into its fields (see _parse_header)."""
    return _parse_header(h)


# ---------------------------------------------------------------------------
# ZigZag — mirrors int8_t arithmetic in C
# ---------------------------------------------------------------------------

def _zigzag_enc(x: int) -> int:
    s = x if x <= 127 else x - 256   # uint8_t -> int8_t
    return ((s << 1) ^ (s >> 7)) & 0xFF


def _zigzag_dec(x: int) -> int:
    return ((x >> 1) ^ -(x & 1)) & 0xFF


# ---------------------------------------------------------------------------
# [Z5] Delta encoding + ZigZag in a single pass
# [P4] DMD_DELTA_FULL — big-int subtraction with carry propagation LSB to MSB
# ---------------------------------------------------------------------------

def _delta_encode_zz(current: bytes, previous: bytes,
                     delta_type: int) -> bytearray:
    """Delta encoding + ZigZag in a single pass. [Z5]"""
    n = len(current)
    out = bytearray(n)

    if delta_type == DMD_DELTA_1B:
        for i in range(n):
            d = (current[i] - previous[i]) & 0xFF
            out[i] = _zigzag_enc(d)
        return out

    if delta_type == DMD_DELTA_FULL:
        # [P4] Big-int subtraction — from LSB (end of buffer) to MSB, carry borrow
        borrow = 0
        i = n - 1
        while i >= 0:
            d = current[i] - previous[i] - borrow
            db = d & 0xFF
            out[i] = _zigzag_enc(db)
            borrow = 1 if d < 0 else 0
            i -= 1
        return out

    # DMD_DELTA_2B — per 16-bit big-endian word
    i = 0
    while i < n:
        if i + 1 < n:
            c = (current[i] << 8) | current[i + 1]
            p = (previous[i] << 8) | previous[i + 1]
            d = (c - p) & 0xFFFF
            out[i]     = _zigzag_enc((d >> 8) & 0xFF)
            out[i + 1] = _zigzag_enc(d & 0xFF)
            i += 2
        else:
            d = (current[i] - previous[i]) & 0xFF
            out[i] = _zigzag_enc(d)
            i += 1
    return out


def _delta_decode_zz(data: bytes, previous: bytes,
                     delta_type: int) -> bytearray:
    """Inverse ZigZag + delta decoding in a single pass. [Z5]"""
    n = len(data)
    out = bytearray(n)

    if delta_type == DMD_DELTA_1B:
        for i in range(n):
            d = _zigzag_dec(data[i])
            out[i] = (d + previous[i]) & 0xFF
        return out

    if delta_type == DMD_DELTA_FULL:
        # [P4] Big-int addition with carry LSB to MSB
        carry = 0
        i = n - 1
        while i >= 0:
            d = _zigzag_dec(data[i])
            s = d + previous[i] + carry
            out[i] = s & 0xFF
            carry  = (s >> 8) & 0xFF
            i -= 1
        return out

    # DMD_DELTA_2B
    i = 0
    while i < n:
        if i + 1 < n:
            zh = _zigzag_dec(data[i])
            zl = _zigzag_dec(data[i + 1])
            d  = (zh << 8) | zl
            p  = (previous[i] << 8) | previous[i + 1]
            o  = (d + p) & 0xFFFF
            out[i]     = (o >> 8) & 0xFF
            out[i + 1] = o & 0xFF
            i += 2
        else:
            d = _zigzag_dec(data[i])
            out[i] = (d + previous[i]) & 0xFF
            i += 1
    return out


# ---------------------------------------------------------------------------
# [Z1][Z6][Z7][Z8] µANS encoding / decoding
# state = uint16_t (range 32..8191)
# [Z6] byte rotation instead of (byte >> j) & 1
# [Z7] byte assembly by left-shift in decoder (no reversal)
# [Z8] countdown loop from len-1 downward
# [P5] early exit per byte based on output stream length
# Output: [1 B length][2 B state big-endian][stream]
# ---------------------------------------------------------------------------

def _uans_encode(data: bytes, limit: int) -> bytes | None:
    """
    limit = maximum total output length. Stream may be at most limit-3 bytes.
    Returns None if overflow occurs. [P5] early exit.
    """
    n      = len(data)
    state  = DMD_ANS_SCALE   # uint16_t
    output = bytearray()

    if limit < 4:
        return None
    stream_limit = limit - 3

    # [Z8] countdown from n-1 downward
    bi = n - 1
    while bi >= 0:
        byte = data[bi]

        # [Z6] byte rotation — bit always from LSB
        for _ in range(8):
            bit    = byte & 1          # uint8_t
            weight = DMD_ANS_WEIGHT_0 if bit == 0 else DMD_ANS_WEIGHT_1
            byte   = byte >> 1         # rotate

            while state >= weight * 256:
                # [P5] Hard bound inside the bit loop — mirrors C implementation.
                # Behaviour is unchanged: any packet that would overflow is rejected.
                if len(output) >= stream_limit:
                    return None
                output.append(state & 0xFF)
                state >>= 8

            state = (state // weight) * DMD_ANS_SCALE \
                  + (0 if bit == 0 else DMD_ANS_WEIGHT_0) \
                  + (state % weight)

        # [P5] Early exit after each byte
        if len(output) >= stream_limit:
            return None

        bi -= 1

    total = 3 + len(output)
    if total > limit:
        return None

    result = bytearray(total)
    result[0] = n & 0xFF
    result[1] = (state >> 8) & 0xFF
    result[2] = state & 0xFF
    # stream reversed
    for i in range(len(output)):
        result[3 + i] = output[len(output) - 1 - i]

    return bytes(result)


def _uans_decode(data: bytes) -> bytes:
    length     = data[0]                               # uint8_t
    state      = ((data[1] << 8) | data[2]) & 0xFFFF  # uint16_t
    si         = 3
    stream_end = len(data)
    result     = bytearray(length)

    for i in range(length):
        # [Z7] byte assembly by left-shift — bits arrive MSB-first
        byte = 0
        for _ in range(8):
            pos = state % DMD_ANS_SCALE     # uint8_t
            if pos < DMD_ANS_WEIGHT_0:
                bit    = 0
                weight = DMD_ANS_WEIGHT_0
                offset = pos
            else:
                bit    = 1
                weight = DMD_ANS_WEIGHT_1
                offset = pos - DMD_ANS_WEIGHT_0

            # [Z7] left-shift — no reversal at the end
            byte = ((byte << 1) | bit) & 0xFF

            state = (weight * (state // DMD_ANS_SCALE) + offset) & 0xFFFF

            if state < DMD_ANS_SCALE and si < stream_end:
                state = ((state << 8) | data[si]) & 0xFFFF
                si += 1

        result[i] = byte

    return bytes(result)


# ---------------------------------------------------------------------------
# [P6][Z4] Zero-byte flagging
# Rotating mask instead of variable shift [Z4]
# Early exit per byte [P6]
# Format: [1 B length][ceil(N/8) B map][non-zero bytes]
# ---------------------------------------------------------------------------

def _flag_encode(data: bytes, limit: int) -> bytes | None:
    """
    limit = maximum allowed output length.
    Returns None if result >= limit. [P6] early exit, [Z4] rotating mask.
    """
    n        = len(data)
    map_size = (n + 7) // 8

    # Quick check — minimum is 1 + map_size (all zeros)
    if 1 + map_size >= limit:
        return None

    flag_map = bytearray(map_size)
    non_zero = bytearray()
    nz_limit = limit - 1 - map_size   # maximum non-zero bytes that fit
    nz_count = 0  # [N1] cached counter for optimisation

    # [Z4] Rotating mask
    mask    = 0x80
    map_pos = 0

    for b in data:
        if b == 0:
            flag_map[map_pos] |= mask
        else:
            # [P6] Early exit
            if nz_count >= nz_limit:
                return None
            non_zero.append(b)
            nz_count += 1

        mask >>= 1
        if mask == 0:
            mask = 0x80
            map_pos += 1

    return bytes([n]) + bytes(flag_map) + bytes(non_zero)


def _flag_decode(data: bytes) -> bytes:
    n        = data[0]
    map_size = (n + 7) // 8
    nz_idx   = 1 + map_size
    result   = bytearray(n)

    # [Z4] Rotating mask
    mask    = 0x80
    map_pos = 1

    for i in range(n):
        if data[map_pos] & mask:
            result[i] = 0
        else:
            result[i] = data[nz_idx]
            nz_idx += 1

        mask >>= 1
        if mask == 0:
            mask = 0x80
            map_pos += 1

    return bytes(result)


# ---------------------------------------------------------------------------
# [P8] Compression of a single packet — 4-way method selection
# ---------------------------------------------------------------------------

def dmd_compress(current: bytes, previous: bytes, sample_num: int) -> bytes:
    """
    Compress a single packet.
    Returns compressed data including the header (1 B).
    Maximum expansion: 1 B (header) — data loss never occurs.
    """
    n_raw      = len(current)
    is_keyframe = (sample_num == 0)

    # ------------------------------------------------------------------
    # Step 1: Delta + ZigZag in a single pass [Z5] — keyframe skips this
    # ------------------------------------------------------------------
    if is_keyframe:
        work       = bytearray(current)
        delta_type = DMD_DELTA_NONE
    else:
        best_score = _count_onebits(current)
        best_dt    = DMD_DELTA_NONE
        work       = bytearray(current)

        for dt in (DMD_DELTA_1B, DMD_DELTA_2B, DMD_DELTA_FULL):
            tmp   = _delta_encode_zz(current, previous, dt)
            score = _count_onebits(tmp)
            if score < best_score:
                best_score = score
                best_dt    = dt
                work       = tmp

        delta_type = best_dt

    # ------------------------------------------------------------------
    # Step 2: Try compression candidates
    # best_size = current smallest result, passed as limit [P8]
    # ------------------------------------------------------------------

    best_size      = n_raw
    winning_method = 0        # 0=RAW, 1=ANS, 2=HUF, 3=FLAG, 4=FLAG+HUF
    payload        = bytearray(current)   # RAW fallback

    # (a) µANS — only if zero_ratio >= 45% (threshold calibrated on meteo+GPS dataset)
    zero_count = 0
    for b in work:
        if b == 0:
            zero_count += 1
    if zero_count * 100 >= n_raw * 45:
        ans_data = _uans_encode(work, best_size)
        if ans_data is not None and len(ans_data) < best_size:
            best_size      = len(ans_data)
            winning_method = 1
            payload        = bytearray(ans_data)

    # (b) Huffman
    huf_data = _huffman_encode(work, best_size)
    if huf_data is not None and len(huf_data) < best_size:
        best_size      = len(huf_data)
        winning_method = 2
        payload        = bytearray(huf_data)

    # (c) FLAG
    flag_data = _flag_encode(work, best_size)
    if flag_data is not None and len(flag_data) < best_size:
        best_size      = len(flag_data)
        winning_method = 3
        payload        = bytearray(flag_data)

    # (d) FLAG+HUF — tried INDEPENDENTLY of (c), matching the C implementation.
    # Builds its own zero map (does not rely on flag_data) so it works even when
    # plain FLAG exceeded its limit and returned None — Huffman on non-zero bytes
    # may still fit where plain FLAG cannot.
    map_size    = (n_raw + 7) // 8
    flag_hdr_sz = 1 + map_size
    if best_size > flag_hdr_sz + 1:
        # Build zero map + list of non-zero bytes from work (byte-identical to C temp_map)
        temp_map    = bytearray(flag_hdr_sz)
        temp_map[0] = n_raw
        nonzero     = bytearray()
        mask        = 0x80
        map_pos     = 1
        for b in work:
            if b == 0:
                temp_map[map_pos] |= mask
            else:
                nonzero.append(b)
            mask >>= 1
            if mask == 0:
                mask = 0x80
                map_pos += 1
        if nonzero:
            huf_limit = best_size - flag_hdr_sz
            huf_nz    = _huffman_encode(nonzero, huf_limit)
            if huf_nz is not None:
                total = flag_hdr_sz + len(huf_nz)
                if total < best_size:
                    best_size      = total
                    winning_method = 4
                    # FLAG header (zero map) + HUF stream of non-zero bytes
                    payload = temp_map + bytearray(huf_nz)

    # ------------------------------------------------------------------
    # Step 3: set flags based on winner
    # ------------------------------------------------------------------
    use_huf  = False
    use_ans  = False
    use_flag = False

    if winning_method == 1:
        use_ans  = True
    elif winning_method == 2:
        use_huf  = True
    elif winning_method == 3:
        use_flag = True
    elif winning_method == 4:
        use_huf  = True
        use_flag = True
    else:
        # RAW fallback — no compression, no delta
        delta_type = DMD_DELTA_NONE

    header = _build_header(sample_num, use_huf, use_ans, use_flag, delta_type)
    return bytes([header]) + bytes(payload)


# ---------------------------------------------------------------------------
# Decompression of a single packet
# ---------------------------------------------------------------------------

def dmd_decompress(data: bytes, previous: bytes) -> bytes:
    """
    Decompress a single packet.
    previous must be the previously decompressed packet of the same length.
    """
    h          = _parse_header(data[0])
    payload    = data[1:]
    payload_len = len(payload)
    delta_type = h['delta_type']
    pkt_len    = len(previous)

    # Layer 1: decompress payload
    if h['use_huf'] and h['use_flag']:
        # [P7] FLAG+HUF — FLAG map + Huffman on non-zero bytes
        n        = payload[0]
        map_size = (n + 7) // 8
        flag_map = payload[1:1 + map_size]
        huf_part = payload[1 + map_size:]
        huf_part_len = payload_len - 1 - map_size

        # Count non-zero bytes from the map — [Z4] rotating mask
        n_nonzero = 0
        mask      = 0x80
        map_pos   = 0
        for i in range(n):
            if not (flag_map[map_pos] & mask):
                n_nonzero += 1
            mask >>= 1
            if mask == 0:
                mask = 0x80
                map_pos += 1

        # Decompress non-zero bytes
        nonzero = _huffman_decode(huf_part, n_nonzero)

        # Reconstruct work — [Z4] rotating mask
        work    = bytearray(n)
        mask    = 0x80
        map_pos = 0
        nz_idx  = 0
        for i in range(n):
            if flag_map[map_pos] & mask:
                work[i] = 0
            else:
                work[i] = nonzero[nz_idx]
                nz_idx += 1
            mask >>= 1
            if mask == 0:
                mask = 0x80
                map_pos += 1

    elif h['use_huf']:
        work = bytearray(_huffman_decode(payload, pkt_len))
    elif h['use_ans']:
        work = bytearray(_uans_decode(payload))
    elif h['use_flag']:
        work = bytearray(_flag_decode(payload))
    else:
        # RAW
        if delta_type == DMD_DELTA_NONE:
            return bytes(payload[:pkt_len])
        work = bytearray(payload[:pkt_len])

    # Layer 2: inverse ZigZag + inverse delta in a single pass [Z5]
    if delta_type != DMD_DELTA_NONE:
        return bytes(_delta_decode_zz(work, previous, delta_type))

    return bytes(work)


# ---------------------------------------------------------------------------
# Stateful encoder / decoder
# ---------------------------------------------------------------------------

class DmdEncoder:
    """Stateful encoder — one instance per communication channel."""

    def __init__(self, pkt_len: int):
        self.pkt_len    = pkt_len & 0xFF    # uint8_t
        self.previous   = bytes(pkt_len)
        self.sample_num = 0

    def compress(self, data: bytes) -> bytes:
        assert len(data) == self.pkt_len, \
            f"Data length {len(data)} != pkt_len {self.pkt_len}"
        result          = dmd_compress(data, self.previous, self.sample_num)
        self.previous   = data
        self.sample_num = (self.sample_num + 1) % DMD_KEYFRAME_EVERY
        return result

    def reset(self):
        self.previous   = bytes(self.pkt_len)
        self.sample_num = 0


class DmdDecoder:
    """Stateful decoder — one instance per communication channel."""

    def __init__(self, pkt_len: int):
        self.pkt_len  = pkt_len & 0xFF    # uint8_t
        self.previous = bytes(pkt_len)

    def decompress(self, data: bytes) -> bytes:
        result        = dmd_decompress(data, self.previous)
        self.previous = result
        return result

    def reset(self):
        self.previous = bytes(self.pkt_len)
