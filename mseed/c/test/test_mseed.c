/* SPDX-License-Identifier: MIT
 * miniSEED writer/reader tests: byte-exact vs the Python reference, round-trip,
 * preserved codes/rate/time, and sub-Hz rate encoding. */
#include "nic_mseed.h"
#include "vectors.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

static int passed = 0, failed = 0;
static void check(const char *name, int cond) {
    if (cond) { passed++; printf("  PASS  %s\n", name); }
    else      { failed++; printf("  FAIL  %s\n", name); }
}

#define CAP 8192

int main(void) {
    printf("miniSEED writer tests (C)\n");

    const size_t NB = sizeof(VEC_B) / sizeof(VEC_B[0]);
    uint8_t out[CAP];
    nic_mseed_params_t p = { "NQ", "ST01", "", "HHZ", 100.0, NIC_STEIM2, 512 };

    long n = nic_mseed_write_stream(&p, VEC_B, NB, 1700000000, 0.25, 1, out, CAP);
    check("write_stream returns whole records", n > 0 && n % 512 == 0);
    check("byte-exact vs Python ref (miniSEED stream)",
          n == (long)sizeof(MSEED_S2) && memcmp(out, MSEED_S2, sizeof(MSEED_S2)) == 0);

    /* round-trip: read every record back and compare samples + header */
    int32_t rec_samp[2048];
    size_t got = 0; int ok = 1;
    nic_mseed_rechdr_t h0; int have_h0 = 0;
    for (long off = 0; off < n; off += 512) {
        nic_mseed_rechdr_t h;
        int32_t s[1024];
        if (nic_mseed_read_record(out + off, (size_t)(n - off), &h, s, 1024) != 0) { ok = 0; break; }
        if (!have_h0) { h0 = h; have_h0 = 1; }
        for (uint16_t k = 0; k < h.nsamples && got < 2048; k++) rec_samp[got++] = s[k];
    }
    check("round-trip: all records decode", ok);
    int same = (got == NB);
    for (size_t k = 0; same && k < NB; k++) if (rec_samp[k] != VEC_B[k]) same = 0;
    check("round-trip: samples match", same);
    check("round-trip: codes preserved",
          have_h0 && strcmp(h0.network, "NQ") == 0 && strcmp(h0.station, "ST01") == 0 &&
          strcmp(h0.channel, "HHZ") == 0);
    check("round-trip: rate preserved (100 Hz)", have_h0 && fabs(h0.rate_hz - 100.0) < 1e-9);
    check("round-trip: start time preserved",
          have_h0 && h0.start_unix == 1700000000 && fabs(h0.start_frac - 0.25) < 1e-6);

    /* second record's start = t0 + n0/rate */
    if (n > 512) {
        nic_mseed_rechdr_t h1; int32_t s[1024];
        nic_mseed_read_record(out + 512, (size_t)(n - 512), &h1, s, 1024);
        double t0 = 1700000000 + 0.25;
        double expect = t0 + (double)h0.nsamples / 100.0;
        double actual = (double)h1.start_unix + h1.start_frac;
        check("record-2 time = t0 + n0/rate", fabs(actual - expect) < 1.0 / 10000.0 + 1e-9);
    }

    /* sub-Hz rate encodes as a negative factor (period) and round-trips */
    int32_t few[5] = { 1, 2, 3, 4, 5 };
    nic_mseed_params_t pslow = { "NQ", "ST01", "", "LHZ", 0.1, NIC_STEIM2, 512 };
    long ns = nic_mseed_write_stream(&pslow, few, 5, 1700000000, 0.0, 1, out, CAP);
    int16_t f, m; nic_mseed_rate_factor_mult(0.1, &f, &m);
    check("sub-Hz rate -> negative factor", f == -10 && m == 1);
    if (ns > 0) {
        nic_mseed_rechdr_t h; int32_t s[8];
        nic_mseed_read_record(out, (size_t)ns, &h, s, 8);
        check("sub-Hz rate round-trips", fabs(h.rate_hz - 0.1) < 1e-9 && h.nsamples == 5);
    }

    /* reclen = 4096 round-trips too */
    nic_mseed_params_t pbig = { "NQ", "ST01", "", "HHZ", 100.0, NIC_STEIM2, 4096 };
    long nb = nic_mseed_write_stream(&pbig, VEC_B, NB, 1700000000, 0.25, 1, out, CAP);
    check("reclen=4096 whole records", nb > 0 && nb % 4096 == 0);
    if (nb > 0) {
        got = 0; ok = 1;
        for (long off = 0; off < nb; off += 4096) {
            nic_mseed_rechdr_t h; int32_t s[1024];
            if (nic_mseed_read_record(out + off, (size_t)(nb - off), &h, s, 1024) != 0) { ok = 0; break; }
            for (uint16_t k = 0; k < h.nsamples && got < 2048; k++) rec_samp[got++] = s[k];
        }
        int eq = ok && got == NB;
        for (size_t k = 0; eq && k < NB; k++) if (rec_samp[k] != VEC_B[k]) eq = 0;
        check("reclen=4096 round-trip samples", eq);
    }

    printf("\nResult: %d/%d PASS | %d FAIL\n", passed, passed + failed, failed);
    return failed ? 1 : 0;
}
