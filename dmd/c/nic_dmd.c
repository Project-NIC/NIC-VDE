// SPDX-License-Identifier: MIT

#include "nic_dmd.h"
#include <string.h>

#if defined(__AVR__)
#include <avr/pgmspace.h>
#define DMD_PROGMEM PROGMEM
#define DMD_READ_BYTE(addr) pgm_read_byte(addr)
#else
#define DMD_PROGMEM
#define DMD_READ_BYTE(addr) (*(addr))
#endif

#define ANS_SCALE    32
#define ANS_WEIGHT_0 29
#define ANS_WEIGHT_1 3

#define DELTA_NONE 0
#define DELTA_1B   1
#define DELTA_2B   2
#define DELTA_FULL 3

/* Lookup tables and Huffman trees (ROM) */
static const uint8_t DMD_PROGMEM _POPCOUNT_LUT[256] = {
    0,1,1,2,1,2,2,3, 1,2,2,3,2,3,3,4, 1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    1,2,2,3,2,3,3,4, 2,3,3,4,3,4,4,5, 2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    2,3,3,4,3,4,4,5, 3,4,4,5,4,5,5,6, 3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7,
    3,4,4,5,4,5,5,6, 4,5,5,6,5,6,6,7, 4,5,5,6,5,6,6,7, 5,6,6,7,6,7,7,8
};

static const uint8_t DMD_PROGMEM _HUF_HI_LEN[]  = {1, 3, 3, 4, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6, 6};
static const uint8_t DMD_PROGMEM _HUF_HI_CODE[] = {0x01, 0x03, 0x00, 0x04, 0x0D, 0x0C, 0x0E, 0x16, 0x15, 0x17, 0x0F, 0x14, 0x0B, 0x0A, 0x08, 0x09};
static const uint8_t DMD_PROGMEM _HUF_LO_LEN[]  = {1, 4, 4, 5, 4, 5, 5, 5, 5, 5, 5, 5, 6, 6, 6, 6};
static const uint8_t DMD_PROGMEM _HUF_LO_CODE[] = {0x01, 0x04, 0x07, 0x0B, 0x03, 0x04, 0x0A, 0x03, 0x05, 0x02, 0x00, 0x01, 0x1B, 0x1A, 0x19, 0x18};

/* -------------------------------------------------------------------------
   ZigZag and Delta helper functions
------------------------------------------------------------------------- */
static inline uint8_t _zigzag_enc(uint8_t x) {
    int8_t s = (int8_t)x;
    return (uint8_t)((s << 1) ^ (s >> 7));
}

static inline uint8_t _zigzag_dec(uint8_t x) {
    return (uint8_t)((x >> 1) ^ -(x & 1));
}

static uint16_t _count_onebits(const uint8_t *data, uint8_t len) {
    uint16_t n = 0;
    for (uint8_t i = 0; i < len; i++) {
        n += DMD_READ_BYTE(&_POPCOUNT_LUT[data[i]]);
    }
    return n;
}

static void _delta_encode_zz(const uint8_t *current, const uint8_t *previous, uint8_t len, uint8_t delta_type, uint8_t *out) {
    if (delta_type == DELTA_1B) {
        for (uint8_t i = 0; i < len; i++) {
            out[i] = _zigzag_enc(current[i] - previous[i]);
        }
    } else if (delta_type == DELTA_FULL) {
        uint8_t borrow = 0;
        for (int16_t i = len - 1; i >= 0; i--) {
            int16_t d = (int16_t)current[i] - previous[i] - borrow;
            out[i] = _zigzag_enc((uint8_t)(d & 0xFF));
            borrow = (d < 0) ? 1 : 0;
        }
    } else if (delta_type == DELTA_2B) {
        for (uint8_t i = 0; i < len; ) {
            if (i + 1 < len) {
                uint16_t c = (current[i] << 8) | current[i + 1];
                uint16_t p = (previous[i] << 8) | previous[i + 1];
                uint16_t d = c - p;
                out[i] = _zigzag_enc((uint8_t)(d >> 8));
                out[i + 1] = _zigzag_enc((uint8_t)(d & 0xFF));
                i += 2;
            } else {
                out[i] = _zigzag_enc(current[i] - previous[i]);
                i++;
            }
        }
    }
}

static void _delta_decode_zz(const uint8_t *data, const uint8_t *previous, uint8_t len, uint8_t delta_type, uint8_t *out) {
    if (delta_type == DELTA_1B) {
        for (uint8_t i = 0; i < len; i++) {
            out[i] = _zigzag_dec(data[i]) + previous[i];
        }
    } else if (delta_type == DELTA_FULL) {
        uint8_t carry = 0;
        for (int16_t i = len - 1; i >= 0; i--) {
            uint16_t s = _zigzag_dec(data[i]) + previous[i] + carry;
            out[i] = (uint8_t)(s & 0xFF);
            carry = (uint8_t)(s >> 8);
        }
    } else if (delta_type == DELTA_2B) {
        for (uint8_t i = 0; i < len; ) {
            if (i + 1 < len) {
                uint16_t d = (_zigzag_dec(data[i]) << 8) | _zigzag_dec(data[i + 1]);
                uint16_t p = (previous[i] << 8) | previous[i + 1];
                uint16_t o = d + p;
                out[i] = (uint8_t)(o >> 8);
                out[i + 1] = (uint8_t)(o & 0xFF);
                i += 2;
            } else {
                out[i] = _zigzag_dec(data[i]) + previous[i];
                i++;
            }
        }
    }
}

/* -------------------------------------------------------------------------
   [P3] Nibble Huffman Encoding and Decoding
------------------------------------------------------------------------- */
static int _huffman_encode(const uint8_t *data, uint8_t len, uint8_t limit, uint8_t *out) {
    if (limit < 2) return -1;
    uint16_t bit_buf = 0;
    uint8_t bit_cnt = 0;
    uint8_t out_pos = 1;
    uint16_t total_bits = 0;
    uint16_t bits_cap = (limit - 1) * 8;

    for (uint8_t i = 0; i < len; i++) {
        uint8_t hi = data[i] >> 4;
        uint8_t lo = data[i] & 0x0F;

        uint8_t hi_len = DMD_READ_BYTE(&_HUF_HI_LEN[hi]);
        uint8_t hi_code = DMD_READ_BYTE(&_HUF_HI_CODE[hi]);
        uint8_t lo_len = DMD_READ_BYTE(&_HUF_LO_LEN[lo]);
        uint8_t lo_code = DMD_READ_BYTE(&_HUF_LO_CODE[lo]);

        total_bits += hi_len + lo_len;
        if (total_bits > bits_cap) return -1; // early exit

        bit_buf = ((bit_buf << hi_len) | hi_code) & 0xFFFF;
        bit_cnt += hi_len;
        while (bit_cnt >= 8) {
            bit_cnt -= 8;
            out[out_pos++] = (bit_buf >> bit_cnt) & 0xFF;
        }
        bit_buf &= (1 << bit_cnt) - 1;

        bit_buf = ((bit_buf << lo_len) | lo_code) & 0xFFFF;
        bit_cnt += lo_len;
        while (bit_cnt >= 8) {
            bit_cnt -= 8;
            out[out_pos++] = (bit_buf >> bit_cnt) & 0xFF;
        }
        bit_buf &= (1 << bit_cnt) - 1;
    }

    if (bit_cnt > 0) {
        out[out_pos++] = (bit_buf << (8 - bit_cnt)) & 0xFF;
        out[0] = bit_cnt;
    } else {
        out[0] = 8;
    }
    return out_pos;
}

static int _huf_decode_nibble(const uint8_t *stream, uint8_t stream_len, uint8_t *in_pos, uint16_t *bit_buf, uint8_t *bit_cnt, uint8_t valid_last, const uint8_t *codes_tab, const uint8_t *lens_tab) {
    while (*bit_cnt < 6 && *in_pos < stream_len) {
        uint8_t next_byte = stream[*in_pos];
        (*in_pos)++;
        if (*in_pos == stream_len && valid_last < 8) {
            *bit_buf = ((*bit_buf << valid_last) | (next_byte >> (8 - valid_last))) & 0xFFFF;
            *bit_cnt += valid_last;
        } else {
            *bit_buf = ((*bit_buf << 8) | next_byte) & 0xFFFF;
            *bit_cnt += 8;
        }
    }

    for (uint8_t sym = 0; sym < 16; sym++) {
        uint8_t code_len = DMD_READ_BYTE(&lens_tab[sym]);
        if (*bit_cnt < code_len) continue;

        uint16_t mask = (1 << code_len) - 1;
        uint16_t peek = (*bit_buf >> (*bit_cnt - code_len)) & mask;
        if (peek == DMD_READ_BYTE(&codes_tab[sym])) {
            *bit_cnt -= code_len;
            *bit_buf &= (1 << *bit_cnt) - 1;
            return sym;
        }
    }
    return -1; // decoding error
}

static int _huffman_decode(const uint8_t *data, uint8_t data_len, uint8_t n_symbols, uint8_t *out) {
    if (data_len == 0) return -1;
    uint8_t valid_last = data[0];
    if (valid_last == 0) valid_last = 8;
    const uint8_t *stream = data + 1;
    uint8_t stream_len = data_len - 1;

    uint8_t in_pos = 0;
    uint16_t bit_buf = 0;
    uint8_t bit_cnt = 0;

    for (uint8_t i = 0; i < n_symbols; i++) {
        int hi = _huf_decode_nibble(stream, stream_len, &in_pos, &bit_buf, &bit_cnt, valid_last, _HUF_HI_CODE, _HUF_HI_LEN);
        if (hi < 0) return -1;
        int lo = _huf_decode_nibble(stream, stream_len, &in_pos, &bit_buf, &bit_cnt, valid_last, _HUF_LO_CODE, _HUF_LO_LEN);
        if (lo < 0) return -1;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    return 0;
}

/* -------------------------------------------------------------------------
   [Z1][Z6][Z7][Z8] µANS Encoding and Decoding
------------------------------------------------------------------------- */
static int _uans_encode(const uint8_t *data, uint8_t len, uint8_t limit, uint8_t *out) {
    if (limit < 4) return -1;
    uint8_t stream_limit = limit - 3;
    uint16_t state = ANS_SCALE;

    DMD_VLA(uint8_t, stream_buf, limit);
    uint8_t stream_len = 0;

    for (int16_t bi = len - 1; bi >= 0; bi--) {
        uint8_t byte = data[bi];
        for (uint8_t i = 0; i < 8; i++) {
            uint8_t bit = byte & 1;
            uint8_t weight = (bit == 0) ? ANS_WEIGHT_0 : ANS_WEIGHT_1;
            byte >>= 1;

            while (state >= weight * 256) {
                /* [P5] Hard bound inside the bit loop — the between-byte early
                   exit alone would allow renormalisation to write past the end
                   of stream_buf (limit B). Behaviour is unchanged: any packet
                   that would overflow is rejected either way. */
                if (stream_len >= stream_limit) return -1;
                stream_buf[stream_len++] = state & 0xFF;
                state >>= 8;
            }
            state = (state / weight) * ANS_SCALE + ((bit == 0) ? 0 : ANS_WEIGHT_0) + (state % weight);
        }
        if (stream_len >= stream_limit) return -1; // early exit
    }

    uint8_t total = 3 + stream_len;
    if (total > limit) return -1;

    out[0] = len;
    out[1] = (state >> 8) & 0xFF;
    out[2] = state & 0xFF;
    for (uint8_t i = 0; i < stream_len; i++) {
        out[3 + i] = stream_buf[stream_len - 1 - i];
    }
    return total;
}

static int _uans_decode(const uint8_t *data, uint8_t data_len, uint8_t *out) {
    if (data_len < 3) return -1;
    uint8_t length = data[0];
    uint16_t state = (data[1] << 8) | data[2];
    uint8_t si = 3;

    for (uint8_t i = 0; i < length; i++) {
        uint8_t byte = 0;
        for (uint8_t j = 0; j < 8; j++) {
            uint8_t pos = state % ANS_SCALE;
            uint8_t bit, weight, offset;
            if (pos < ANS_WEIGHT_0) {
                bit = 0; weight = ANS_WEIGHT_0; offset = pos;
            } else {
                bit = 1; weight = ANS_WEIGHT_1; offset = pos - ANS_WEIGHT_0;
            }
            byte = (byte << 1) | bit;
            state = weight * (state / ANS_SCALE) + offset;

            if (state < ANS_SCALE && si < data_len) {
                state = (state << 8) | data[si++];
            }
        }
        out[i] = byte;
    }
    return 0;
}

/* -------------------------------------------------------------------------
   [P6][Z4] Zero-byte flagging
------------------------------------------------------------------------- */
static int _flag_encode(const uint8_t *data, uint8_t len, uint8_t limit, uint8_t *out) {
    uint8_t map_size = (len + 7) / 8;
    if (1 + map_size >= limit) return -1;

    uint8_t nz_limit = limit - 1 - map_size;
    uint8_t nz_count = 0;
    uint8_t mask = 0x80;
    uint8_t map_pos = 1;

    out[0] = len;
    memset(&out[1], 0, map_size);
    uint8_t nz_idx = 1 + map_size;

    for (uint8_t i = 0; i < len; i++) {
        if (data[i] == 0) {
            out[map_pos] |= mask;
        } else {
            if (nz_count >= nz_limit) return -1; // early exit
            out[nz_idx++] = data[i];
            nz_count++;
        }
        mask >>= 1;
        if (mask == 0) { mask = 0x80; map_pos++; }
    }
    return 1 + map_size + nz_count;
}

/* -------------------------------------------------------------------------
   Encoder and decoder initialisation
------------------------------------------------------------------------- */
void dmd_encoder_init(dmd_encoder_t *enc, uint8_t pkt_len) {
    enc->pkt_len = pkt_len;
    enc->sample_num = 0;
    memset(enc->previous, 0, pkt_len);
}

void dmd_decoder_init(dmd_decoder_t *dec, uint8_t pkt_len) {
    dec->pkt_len = pkt_len;
    memset(dec->previous, 0, pkt_len);
}

/* -------------------------------------------------------------------------
   Main compression loop
------------------------------------------------------------------------- */
uint16_t dmd_compress(dmd_encoder_t *enc, const uint8_t *current, uint8_t *output) {
    uint8_t n_raw = enc->pkt_len;
#if defined(__GNUC__)
    /* pkt_len >= 1 is guaranteed by the API (see README). This hint lets the
       optimiser treat VLA sizes as >= 1 and suppresses a false-positive
       -Wstringop-overflow on the memcpy into payload[]. */
    if (n_raw == 0) __builtin_unreachable();
#endif
    bool is_keyframe = (enc->sample_num == 0);

    DMD_VLA(uint8_t, work, n_raw);
    uint8_t delta_type = DELTA_NONE;

    if (is_keyframe) {
        memcpy(work, current, n_raw);
    } else {
        uint16_t best_score = _count_onebits(current, n_raw);
        memcpy(work, current, n_raw);

        uint8_t dts[] = {DELTA_1B, DELTA_2B, DELTA_FULL};
        DMD_VLA(uint8_t, tmp, n_raw);
        for (uint8_t i = 0; i < 3; i++) {
            _delta_encode_zz(current, enc->previous, n_raw, dts[i], tmp);
            uint16_t score = _count_onebits(tmp, n_raw);
            if (score < best_score) {
                best_score = score;
                delta_type = dts[i];
                memcpy(work, tmp, n_raw);
            }
        }
    }

    uint8_t best_size = n_raw;
    uint8_t winning_method = 0; // 0=RAW, 1=ANS, 2=HUF, 3=FLAG, 4=FLAG+HUF
    DMD_VLA(uint8_t, payload, n_raw);
    memcpy(payload, current, n_raw); // RAW fallback: must hold original bytes, not delta-transformed work

    // (a) uANS
    uint8_t zero_count = 0;
    for (uint8_t i = 0; i < n_raw; i++) {
        if (work[i] == 0) zero_count++;
    }
    if (zero_count * 100 >= n_raw * 45) {
        DMD_VLA(uint8_t, ans_data, n_raw);
        int ans_sz = _uans_encode(work, n_raw, best_size, ans_data);
        if (ans_sz > 0 && ans_sz < best_size) {
            best_size = ans_sz;
            winning_method = 1;
            memcpy(payload, ans_data, ans_sz);
        }
    }

    // (b) Huffman
    DMD_VLA(uint8_t, huf_data, n_raw);
    int huf_sz = _huffman_encode(work, n_raw, best_size, huf_data);
    if (huf_sz > 0 && huf_sz < best_size) {
        best_size = huf_sz;
        winning_method = 2;
        memcpy(payload, huf_data, huf_sz);
    }

    // (c) FLAG
    DMD_VLA(uint8_t, flag_data, n_raw);
    int flag_sz = _flag_encode(work, n_raw, best_size, flag_data);
    if (flag_sz > 0 && flag_sz < best_size) {
        best_size = flag_sz;
        winning_method = 3;
        memcpy(payload, flag_data, flag_sz);
    }

    // (d) FLAG+HUF
    uint8_t map_size = (n_raw + 7) / 8;
    uint8_t flag_hdr_sz = 1 + map_size;
    if (best_size > flag_hdr_sz + 1) {
        DMD_VLA(uint8_t, nonzero, n_raw);
        uint8_t nz_len = 0;
        DMD_VLA(uint8_t, temp_map, flag_hdr_sz);
        temp_map[0] = n_raw;
        memset(&temp_map[1], 0, map_size);
        uint8_t mask = 0x80;
        uint8_t map_pos = 1;

        for(uint8_t i=0; i<n_raw; i++) {
            if (work[i] == 0) {
                temp_map[map_pos] |= mask;
            } else {
                nonzero[nz_len++] = work[i];
            }
            mask >>= 1;
            if (mask == 0) { mask = 0x80; map_pos++; }
        }

        if (nz_len > 0) {
            uint8_t huf_limit = best_size - flag_hdr_sz;
            DMD_VLA(uint8_t, huf_nz, n_raw);
            int huf_nz_sz = _huffman_encode(nonzero, nz_len, huf_limit, huf_nz);
            if (huf_nz_sz > 0) {
                int total = flag_hdr_sz + huf_nz_sz;
                if (total < best_size) {
                    best_size = total;
                    winning_method = 4;
                    memcpy(payload, temp_map, flag_hdr_sz);
                    memcpy(payload + flag_hdr_sz, huf_nz, huf_nz_sz);
                }
            }
        }
    }

    // Set header flags
    bool use_huf = false, use_ans = false, use_flag = false;
    if (winning_method == 1) use_ans = true;
    else if (winning_method == 2) use_huf = true;
    else if (winning_method == 3) use_flag = true;
    else if (winning_method == 4) { use_huf = true; use_flag = true; }
    else { delta_type = DELTA_NONE; } // RAW fallback

    uint8_t header = enc->sample_num & 0x07;
    header |= (delta_type & 0x03) << 3;
    if (use_flag) header |= (1 << 5);
    if (use_ans)  header |= (1 << 6);
    if (use_huf)  header |= (1 << 7);

    output[0] = header;
    memcpy(&output[1], payload, best_size);

    memcpy(enc->previous, current, n_raw);
    enc->sample_num = (enc->sample_num + 1) % DMD_KEYFRAME_EVERY;

    /* Output = 1 B header + best_size. Maximum is 256 (255 B RAW packet),
       which fits in uint16_t — no overflow, compression always succeeds. */
    return (uint16_t)best_size + 1;
}

/* -------------------------------------------------------------------------
   Main decompression loop
------------------------------------------------------------------------- */
int dmd_decompress(dmd_decoder_t *dec, const uint8_t *input, uint16_t in_len, uint8_t *output) {
    if (in_len == 0) return -1;
    uint8_t n_raw = dec->pkt_len;
    uint8_t header = input[0];
    const uint8_t *payload = input + 1;
    uint8_t payload_len = in_len - 1;

    uint8_t sample_num = header & 0x07;
    if (sample_num == 7) return -3;

    bool use_huf = (header & (1 << 7)) != 0;
    bool use_ans = (header & (1 << 6)) != 0;
    bool use_flag = (header & (1 << 5)) != 0;
    uint8_t delta_type = (header >> 3) & 0x03;

    DMD_VLA(uint8_t, work, n_raw);

    if (use_huf && use_flag) {
        if (payload_len == 0) return -1;
        uint8_t n = payload[0];
        if (n != n_raw) return -1;
        uint8_t map_size = (n + 7) / 8;
        if (payload_len < 1 + map_size) return -1;

        const uint8_t *flag_map = payload + 1;
        const uint8_t *huf_part = payload + 1 + map_size;
        uint8_t huf_part_len = payload_len - 1 - map_size;

        uint8_t n_nonzero = 0;
        uint8_t mask = 0x80;
        uint8_t map_pos = 0;
        for (uint8_t i = 0; i < n; i++) {
            if (!(flag_map[map_pos] & mask)) n_nonzero++;
            mask >>= 1;
            if (mask == 0) { mask = 0x80; map_pos++; }
        }

        DMD_VLA(uint8_t, nonzero, n_raw);
        if (_huffman_decode(huf_part, huf_part_len, n_nonzero, nonzero) < 0) return -1;

        mask = 0x80;
        map_pos = 0;
        uint8_t nz_idx = 0;
        for (uint8_t i = 0; i < n; i++) {
            if (flag_map[map_pos] & mask) {
                work[i] = 0;
            } else {
                work[i] = nonzero[nz_idx++];
            }
            mask >>= 1;
            if (mask == 0) { mask = 0x80; map_pos++; }
        }
    } else if (use_huf) {
        if (_huffman_decode(payload, payload_len, n_raw, work) < 0) return -1;
    } else if (use_ans) {
        if (_uans_decode(payload, payload_len, work) < 0) return -1;
    } else if (use_flag) {
        if (payload_len == 0) return -1;
        uint8_t n = payload[0];
        if (n != n_raw) return -1;
        uint8_t map_size = (n + 7) / 8;
        if (payload_len < 1 + map_size) return -1;
        uint8_t nz_idx = 1 + map_size;
        uint8_t mask = 0x80;
        uint8_t map_pos = 1;
        for (uint8_t i = 0; i < n; i++) {
            if (payload[map_pos] & mask) {
                work[i] = 0;
            } else {
                if (nz_idx >= payload_len) return -1;
                work[i] = payload[nz_idx++];
            }
            mask >>= 1;
            if (mask == 0) { mask = 0x80; map_pos++; }
        }
    } else {
        if (delta_type == DELTA_NONE) {
            if (payload_len < n_raw) return -1;
            memcpy(output, payload, n_raw);
            memcpy(dec->previous, output, n_raw);
            return 0;
        }
        if (payload_len < n_raw) return -1;
        memcpy(work, payload, n_raw);
    }

    if (delta_type != DELTA_NONE) {
        _delta_decode_zz(work, dec->previous, n_raw, delta_type, output);
    } else {
        memcpy(output, work, n_raw);
    }

    memcpy(dec->previous, output, n_raw);
    return 0;
}
