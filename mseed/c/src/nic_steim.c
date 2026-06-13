/* SPDX-License-Identifier: MIT
 *
 * nic_steim.c — Steim-1 and Steim-2 compression (FDSN/SEED), portable C, no deps.
 * Port of nic_mseed/steim.py; see that file's header for the format description.
 *
 * 64-byte frames = 16 big-endian uint32 words. Word 0 of each frame is a control
 * word: sixteen 2-bit nibbles (MSB-first) saying how each word is packed. Frame 0
 * reserves word 1 = X0 (first sample) and word 2 = Xn (last); data starts at word
 * 3 in frame 0, word 1 elsewhere. Greedy most-packed-first encoding.
 */
#include "nic_mseed.h"

#define FRAME_WORDS 16

/* ── bit helpers ───────────────────────────────────────────────────────────── */

static int fits(int64_t v, int bits) {
    int64_t lim = (int64_t)1 << (bits - 1);
    return v >= -lim && v <= lim - 1;
}

/* two's-complement `bits`-wide representation of v, as a uint32 */
static uint32_t umask(int64_t v, int bits) {
    if (bits >= 32) return (uint32_t)((uint64_t)v & 0xFFFFFFFFu);
    return (uint32_t)((uint64_t)v & (((uint64_t)1 << bits) - 1));
}

/* sign-extend a `bits`-wide unsigned value to int32 */
static int32_t sx(uint32_t raw, int bits) {
    if (bits >= 32) return (int32_t)raw;
    if (raw & ((uint32_t)1 << (bits - 1)))
        return (int32_t)(raw - ((uint32_t)1 << bits));
    return (int32_t)raw;
}

static void put_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);  p[3] = (uint8_t)v;
}
static uint32_t get_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8)  |  (uint32_t)p[3];
}

/* candidate packings, most-packed first: {count, bits, nibble, dnib(-1=none)} */
typedef struct { int count, bits, nibble, dnib; } pack_t;
static const pack_t STEIM2_TBL[] = {
    {7, 4, 0x3, 0x2}, {6, 5, 0x3, 0x1}, {5, 6, 0x3, 0x0},
    {4, 8, 0x1, -1},  {3,10, 0x2, 0x3}, {2,15, 0x2, 0x2}, {1,30, 0x2, 0x1},
};
static const pack_t STEIM1_TBL[] = {
    {4, 8, 0x1, -1}, {2,16, 0x2, -1}, {1,32, 0x3, -1},
};

static int64_t diff_at(const int32_t *s, size_t i, int32_t prev) {
    return (i == 0) ? (int64_t)s[0] - prev : (int64_t)s[i] - (int64_t)s[i - 1];
}

static uint32_t pack_word(const int64_t *d, const pack_t *e) {
    uint32_t word = 0;
    if (e->nibble == 0x1) {                       /* 4 x 8-bit (both versions) */
        for (int i = 0; i < 4; i++) word |= umask(d[i], 8) << (24 - 8 * i);
        return word;
    }
    if (e->dnib < 0) {                            /* Steim-1 16/32-bit */
        if (e->bits == 16) return (umask(d[0], 16) << 16) | umask(d[1], 16);
        return umask(d[0], 32);                   /* 1 x 32-bit */
    }
    if (e->count == 7 && e->bits == 4) {          /* special: low 28 bits, dnib at 31-30 */
        word = (uint32_t)e->dnib << 30;
        for (int i = 0; i < 7; i++) word |= umask(d[i], 4) << (24 - 4 * i);
        return word;
    }
    word = (uint32_t)e->dnib << 30;               /* MSB-first just below the dnib */
    for (int i = 0; i < e->count; i++)
        word |= umask(d[i], e->bits) << (30 - e->bits * (i + 1));
    return word;
}

int nic_steim_encode_record(const int32_t *samples, size_t nsamp,
                            int version, int frames_per_record, int32_t prev,
                            uint8_t *out, size_t *used_samples) {
    if (!samples || !out || !used_samples || nsamp == 0 || frames_per_record < 1)
        return NIC_MSEED_EINVAL;
    if (version != NIC_STEIM1 && version != NIC_STEIM2) return NIC_MSEED_EINVAL;

    const pack_t *tbl = (version == NIC_STEIM2) ? STEIM2_TBL : STEIM1_TBL;
    const int tbl_n   = (version == NIC_STEIM2) ? 7 : 3;
    const size_t capacity = 13 + (size_t)(frames_per_record - 1) * 15;

    /* zero everything: empty slots and their control nibbles read as 00 (non-data) */
    for (size_t i = 0; i < (size_t)frames_per_record * NIC_STEIM_FRAME_BYTES; i++)
        out[i] = 0;

    size_t di = 0, used_words = 0;
    /* per-frame control accumulators */
    /* walk the data-word slots in order: frame f, word (f==0?3:1)..15 */
    for (int f = 0; f < frames_per_record && di < nsamp; f++) {
        uint8_t *frame = out + (size_t)f * NIC_STEIM_FRAME_BYTES;
        uint32_t control = 0;
        int wstart = (f == 0) ? 3 : 1;
        for (int w = wstart; w < FRAME_WORDS && di < nsamp; w++) {
            if (used_words >= capacity) break;
            /* pick the first packing that fits at di */
            const pack_t *chosen = NULL;
            int64_t d[7];
            for (int t = 0; t < tbl_n; t++) {
                const pack_t *e = &tbl[t];
                if (di + (size_t)e->count > nsamp) continue;
                int ok = 1;
                for (int k = 0; k < e->count; k++) {
                    d[k] = diff_at(samples, di + (size_t)k, prev);
                    if (!fits(d[k], e->bits)) { ok = 0; break; }
                }
                if (ok) { chosen = e; break; }
            }
            if (!chosen) return NIC_MSEED_EOVERFLOW;   /* even the largest field can't hold it */
            put_be32(frame + (size_t)w * 4, pack_word(d, chosen));
            control |= (uint32_t)chosen->nibble << (2 * (15 - w));
            di += (size_t)chosen->count;
            used_words++;
        }
        put_be32(frame, control);                      /* word 0 = control */
    }

    if (di == 0) return NIC_MSEED_ENOSPACE;            /* a single diff didn't fit one record */

    /* frame 0: word 1 = X0 (first sample), word 2 = Xn (last used sample) */
    put_be32(out + 4, umask((int64_t)samples[0], 32));
    put_be32(out + 8, umask((int64_t)samples[di - 1], 32));

    *used_samples = di;
    return NIC_MSEED_OK;
}

/* decode one data word into up to 7 diffs; returns the count produced */
static int decode_word(uint32_t word, int nibble, int version, int32_t *d) {
    if (nibble == 0x1) {                                  /* 4 x 8-bit */
        for (int i = 0; i < 4; i++) d[i] = sx((word >> (24 - 8 * i)) & 0xFF, 8);
        return 4;
    }
    if (version == NIC_STEIM1) {
        if (nibble == 0x2) { d[0] = sx((word >> 16) & 0xFFFF, 16);
                             d[1] = sx(word & 0xFFFF, 16); return 2; }
        d[0] = sx(word, 32); return 1;                    /* 1 x 32-bit */
    }
    int dnib = (word >> 30) & 0x3;
    if (nibble == 0x2) {
        if (dnib == 0x1) { d[0] = sx(word & ((1u << 30) - 1), 30); return 1; }
        if (dnib == 0x2) { d[0] = sx((word >> 15) & 0x7FFF, 15);
                           d[1] = sx(word & 0x7FFF, 15); return 2; }
        if (dnib == 0x3) { for (int i = 0; i < 3; i++)
                               d[i] = sx((word >> (30 - 10 * (i + 1))) & 0x3FF, 10);
                           return 3; }
    } else {                                              /* nibble == 0x3 */
        if (dnib == 0x0) { for (int i = 0; i < 5; i++)
                               d[i] = sx((word >> (30 - 6 * (i + 1))) & 0x3F, 6);
                           return 5; }
        if (dnib == 0x1) { for (int i = 0; i < 6; i++)
                               d[i] = sx((word >> (30 - 5 * (i + 1))) & 0x1F, 5);
                           return 6; }
        if (dnib == 0x2) { for (int i = 0; i < 7; i++)
                               d[i] = sx((word >> (24 - 4 * i)) & 0xF, 4);
                           return 7; }
    }
    return -1;                                            /* bad dnib */
}

int nic_steim_decode_record(const uint8_t *frames, size_t frames_len,
                            size_t n_samples, int version, int32_t *out) {
    if (!frames || !out || n_samples == 0) return NIC_MSEED_EINVAL;
    if (version != NIC_STEIM1 && version != NIC_STEIM2) return NIC_MSEED_EINVAL;
    size_t nframes = frames_len / NIC_STEIM_FRAME_BYTES;

    int32_t x0 = 0;
    size_t filled = 0;          /* how many of out[] are set                     */
    size_t dindex = 0;          /* running diff index (diff 0 is the X0 placeholder) */

    for (size_t f = 0; f < nframes && filled < n_samples; f++) {
        const uint8_t *frame = frames + f * NIC_STEIM_FRAME_BYTES;
        uint32_t control = get_be32(frame);
        int wstart = 1;
        if (f == 0) { x0 = sx(get_be32(frame + 4), 32); wstart = 3; }
        for (int w = wstart; w < FRAME_WORDS && filled < n_samples; w++) {
            int nib = (control >> (2 * (15 - w))) & 0x3;
            if (nib == 0x0) continue;
            int32_t d[7];
            int ndec = decode_word(get_be32(frame + (size_t)w * 4), nib, version, d);
            if (ndec < 0) return NIC_MSEED_EINVAL;
            for (int k = 0; k < ndec && filled < n_samples; k++) {
                if (dindex == 0) { out[0] = x0; filled = 1; }   /* diff 0 ↔ X0 */
                else { out[filled] = out[filled - 1] + d[k]; filled++; }
                dindex++;
            }
        }
    }
    return (filled == n_samples) ? NIC_MSEED_OK : NIC_MSEED_EINVAL;
}
