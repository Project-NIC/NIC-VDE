/* SPDX-License-Identifier: MIT
 *
 * nic_mseed.c — minimal miniSEED 2.x record writer (big-endian, Steim-1/2).
 * Port of nic_mseed/mseed.py.
 *
 * One record = Fixed Section of Data Header (48 B) + Blockette 1000 (8 B) +
 * pad to the 64 B data offset + Steim frames filling the rest of a fixed,
 * power-of-two record (default 512 B -> 7 frames). Exactly the layout libmseed /
 * ObsPy / SeisComP read. Only FSDH + B1000 are emitted (the universal subset).
 */
#include "nic_mseed.h"

#include <math.h>
#include <string.h>
#include <time.h>

#define FSDH        48
#define B1000        8
#define DATA_OFFSET 64

static void put_be16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v >> 8); p[1] = (uint8_t)v; }
static void put_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24); p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);  p[3] = (uint8_t)v;
}
static uint16_t get_be16(const uint8_t *p) { return (uint16_t)((p[0] << 8) | p[1]); }

/* ASCII, space-padded / truncated to n bytes (SEED code convention) */
static void pad_code(uint8_t *dst, const char *code, size_t n) {
    size_t i = 0;
    if (code) for (; i < n && code[i]; i++) dst[i] = (uint8_t)code[i];
    for (; i < n; i++) dst[i] = ' ';
}

/* round-half-to-even, matching Python's round() */
static long round_half_even(double r) {
    double fl = floor(r), d = r - fl;
    long n = (long)fl;
    if (d < 0.5) return n;
    if (d > 0.5) return n + 1;
    return (n % 2 == 0) ? n : n + 1;
}

void nic_mseed_rate_factor_mult(double rate_hz, int16_t *factor, int16_t *mult) {
    if (rate_hz <= 0) { *factor = 0; *mult = 0; return; }
    if (rate_hz >= 1 && fabs(rate_hz - floor(rate_hz + 0.5)) < 1e-9) {
        *factor = (int16_t)(long)floor(rate_hz + 0.5); *mult = 1; return;
    }
    double period = 1.0 / rate_hz;
    if (fabs(period - floor(period + 0.5)) < 1e-9) {
        *factor = (int16_t)(-(long)floor(period + 0.5)); *mult = 1; return;
    }
    *factor = (int16_t)(long)floor(rate_hz + 0.5); *mult = 1;   /* fallback */
}

/* SEED BTIME (10 B): year, day-of-year, h, m, s, unused, 0.0001 s ticks */
static void write_btime(uint8_t *p, int64_t unix_seconds, double fraction) {
    time_t tt = (time_t)unix_seconds;
    struct tm tmv;
#if defined(_WIN32)
    gmtime_s(&tmv, &tt);
#else
    gmtime_r(&tt, &tmv);
#endif
    long tenk = round_half_even(fraction * 10000.0);
    if (tenk < 0) tenk = 0; else if (tenk > 9999) tenk = 9999;
    put_be16(p + 0, (uint16_t)(tmv.tm_year + 1900));
    put_be16(p + 2, (uint16_t)(tmv.tm_yday + 1));
    p[4] = (uint8_t)tmv.tm_hour; p[5] = (uint8_t)tmv.tm_min;
    p[6] = (uint8_t)tmv.tm_sec;  p[7] = 0;
    put_be16(p + 8, (uint16_t)tenk);
}

int nic_mseed_write_record(const nic_mseed_params_t *p,
                           const int32_t *samples, size_t nsamp,
                           uint32_t seq, int32_t prev,
                           int64_t start_unix, double start_frac,
                           uint8_t *out, size_t *used) {
    if (!p || !samples || !out || !used || nsamp == 0) return NIC_MSEED_EINVAL;
    int reclen = p->reclen;
    if (reclen < 128 || (reclen & (reclen - 1))) return NIC_MSEED_EINVAL;
    int reclen_exp = 0; for (int r = reclen; r > 1; r >>= 1) reclen_exp++;
    int frames_per_record = (reclen - DATA_OFFSET) / 64;
    int encoding = (p->version == NIC_STEIM2) ? 11 : 10;

    /* Steim payload fills the record body (offset 64 .. reclen) */
    size_t used_samples = 0;
    int rc = nic_steim_encode_record(samples, nsamp, p->version,
                                     frames_per_record, prev,
                                     out + DATA_OFFSET, &used_samples);
    if (rc != NIC_MSEED_OK) return rc;

    uint8_t *h = out;
    memset(h, 0, DATA_OFFSET);
    /* sequence number: 6 ASCII digits */
    char seqbuf[8];
    unsigned s6 = (unsigned)(seq % 1000000u);
    for (int i = 5; i >= 0; i--) { seqbuf[i] = (char)('0' + s6 % 10); s6 /= 10; }
    memcpy(h + 0, seqbuf, 6);
    h[6] = 'D'; h[7] = ' ';
    pad_code(h + 8,  p->station,  5);
    pad_code(h + 13, p->location, 2);
    pad_code(h + 15, p->channel,  3);
    pad_code(h + 18, p->network,  2);
    write_btime(h + 20, start_unix, start_frac);
    put_be16(h + 30, (uint16_t)used_samples);
    int16_t factor, mult; nic_mseed_rate_factor_mult(p->sample_rate_hz, &factor, &mult);
    put_be16(h + 32, (uint16_t)factor);
    put_be16(h + 34, (uint16_t)mult);
    h[36] = 0; h[37] = 0; h[38] = 0; h[39] = 1;   /* flags + #blockettes */
    put_be32(h + 40, 0);                          /* time correction */
    put_be16(h + 44, DATA_OFFSET);                /* offset to data */
    put_be16(h + 46, FSDH);                       /* offset to first blockette */
    /* Blockette 1000 */
    put_be16(h + 48, 1000); put_be16(h + 50, 0);
    h[52] = (uint8_t)encoding; h[53] = 1;         /* encoding, word order = big-endian */
    h[54] = (uint8_t)reclen_exp; h[55] = 0;
    /* h[56..63] already zero (pad) */

    *used = used_samples;
    return NIC_MSEED_OK;
}

long nic_mseed_write_stream(const nic_mseed_params_t *p,
                            const int32_t *samples, size_t nsamp,
                            int64_t start_unix, double start_frac,
                            uint32_t seq_start,
                            uint8_t *out, size_t out_cap) {
    if (!p || !samples || !out) return NIC_MSEED_EINVAL;
    int reclen = p->reclen;
    if (reclen < 128 || (reclen & (reclen - 1))) return NIC_MSEED_EINVAL;
    if (nsamp == 0) return 0;
    if (p->sample_rate_hz <= 0) return NIC_MSEED_EINVAL;

    double t0 = (double)start_unix + start_frac;
    size_t i = 0, off = 0;
    uint32_t seq = seq_start;
    int32_t prev = 0;
    while (i < nsamp) {
        if (off + (size_t)reclen > out_cap) return NIC_MSEED_ENOSPACE;
        double rec_start = t0 + (double)i / p->sample_rate_hz;
        int64_t u = (int64_t)floor(rec_start);
        size_t used = 0;
        int rc = nic_mseed_write_record(p, samples + i, nsamp - i, seq, prev,
                                        u, rec_start - (double)u, out + off, &used);
        if (rc != NIC_MSEED_OK) return rc;
        prev = samples[i + used - 1];
        i += used;
        off += (size_t)reclen;
        seq++;
    }
    return (long)off;
}

int nic_mseed_read_record(const uint8_t *rec, size_t rec_cap,
                          nic_mseed_rechdr_t *hdr,
                          int32_t *samples_out, size_t out_cap) {
    if (!rec || !hdr || rec_cap < DATA_OFFSET) return NIC_MSEED_EINVAL;
    memset(hdr, 0, sizeof(*hdr));
    memcpy(hdr->station,  rec + 8,  5);
    memcpy(hdr->location, rec + 13, 2);
    memcpy(hdr->channel,  rec + 15, 3);
    memcpy(hdr->network,  rec + 18, 2);
    /* trim trailing spaces */
    for (char *f = hdr->station; *f; f++) if (*f == ' ') { *f = 0; break; }
    for (char *f = hdr->channel; *f; f++) if (*f == ' ') { *f = 0; break; }
    for (char *f = hdr->network; *f; f++) if (*f == ' ') { *f = 0; break; }
    if (hdr->location[0] == ' ') hdr->location[0] = 0;

    uint16_t year = get_be16(rec + 20), doy = get_be16(rec + 22);
    uint8_t hh = rec[24], mm = rec[25], ss = rec[26];
    uint16_t tenk = get_be16(rec + 28);
    uint16_t nsamples = get_be16(rec + 30);
    int16_t factor = (int16_t)get_be16(rec + 32), mult = (int16_t)get_be16(rec + 34);
    uint16_t blk_off = get_be16(rec + 46);
    if (get_be16(rec + blk_off) != 1000) return NIC_MSEED_EINVAL;
    uint8_t enc = rec[blk_off + 4], rlexp = rec[blk_off + 6];
    size_t reclen = (size_t)1 << rlexp;
    uint16_t data_off = get_be16(rec + 44);
    int version = (enc == 11) ? NIC_STEIM2 : NIC_STEIM1;

    if (nsamples > out_cap) return NIC_MSEED_ENOSPACE;
    if (reclen > rec_cap) return NIC_MSEED_EINVAL;
    int rc = nic_steim_decode_record(rec + data_off, reclen - data_off,
                                     nsamples, version, samples_out);
    if (rc != NIC_MSEED_OK) return rc;

    if (factor > 0 && mult > 0)       hdr->rate_hz = (double)factor * mult;
    else if (factor > 0 && mult < 0)  hdr->rate_hz = -(double)factor / mult;
    else if (factor < 0 && mult > 0)  hdr->rate_hz = -(double)mult / factor;
    else                              hdr->rate_hz = 1.0;

    /* day-of-year -> unix seconds (UTC) */
    struct tm tmv; memset(&tmv, 0, sizeof(tmv));
    tmv.tm_year = year - 1900; tmv.tm_mday = 1; tmv.tm_hour = hh;
    tmv.tm_min = mm; tmv.tm_sec = ss;
    tmv.tm_yday = doy - 1;
#if defined(_WIN32)
    time_t base = _mkgmtime(&tmv);
#else
    time_t base = timegm(&tmv);
#endif
    hdr->start_unix = (int64_t)base + (int64_t)(doy - 1) * 86400; /* mday=1 + yday days */
    hdr->start_frac = tenk / 10000.0;
    hdr->nsamples = nsamples;
    hdr->reclen = reclen;
    return NIC_MSEED_OK;
}
