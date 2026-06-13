/* SPDX-License-Identifier: MIT
 * Steim-1/2 codec tests: internal round-trip, byte-exact vs the Python reference,
 * overflow handling, and the constant-signal compression claim. */
#include "nic_mseed.h"
#include "vectors.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int passed = 0, failed = 0;
static void check(const char *name, int cond) {
    if (cond) { passed++; printf("  PASS  %s\n", name); }
    else      { failed++; printf("  FAIL  %s\n", name); }
}

#define MAXR 4096
#define MAXN 4096

/* encode the whole series record by record, decode each back, compare */
static int roundtrip(const int32_t *s, size_t n, int version, int fpr) {
    uint8_t rec[MAXR];
    int32_t dec[MAXN];
    size_t i = 0; int32_t prev = 0;
    while (i < n) {
        size_t used = 0;
        if (nic_steim_encode_record(s + i, n - i, version, fpr, prev, rec, &used) != 0) return 0;
        if (used == 0) return 0;
        if (nic_steim_decode_record(rec, (size_t)fpr * 64, used, version, dec) != 0) return 0;
        for (size_t k = 0; k < used; k++) if (dec[k] != s[i + k]) return 0;
        prev = s[i + used - 1];
        i += used;
    }
    return 1;
}

int main(void) {
    printf("Steim-1/2 codec tests (C)\n");

    /* ---- internal round-trip on deterministic series ---- */
    static int32_t buf[MAXN];

    int32_t single[1] = { 12345 };
    check("S1 round-trip: single", roundtrip(single, 1, NIC_STEIM1, 7));
    check("S2 round-trip: single", roundtrip(single, 1, NIC_STEIM2, 7));

    for (int i = 0; i < 50; i++) buf[i] = 777;
    check("S1 round-trip: constant", roundtrip(buf, 50, NIC_STEIM1, 7));
    check("S2 round-trip: constant", roundtrip(buf, 50, NIC_STEIM2, 7));

    for (int i = 0; i < 300; i++) buf[i] = i;
    check("S1 round-trip: ramp", roundtrip(buf, 300, NIC_STEIM1, 7));
    check("S2 round-trip: ramp", roundtrip(buf, 300, NIC_STEIM2, 7));

    /* pseudo-random walk (deterministic LCG), small then medium diffs */
    uint32_t st = 1u; int32_t acc = 0;
    for (int i = 0; i < 500; i++) { st = st * 1103515245u + 12345u;
        acc += (int32_t)((st >> 16) % 15) - 7; buf[i] = acc; }
    check("S1 round-trip: tiny diffs", roundtrip(buf, 500, NIC_STEIM1, 7));
    check("S2 round-trip: tiny diffs", roundtrip(buf, 500, NIC_STEIM2, 7));

    st = 99u; acc = 0;
    for (int i = 0; i < 500; i++) { st = st * 1103515245u + 12345u;
        acc += (int32_t)((st >> 12) % 6001) - 3000; buf[i] = acc; }
    check("S1 round-trip: medium diffs", roundtrip(buf, 500, NIC_STEIM1, 7));
    check("S2 round-trip: medium diffs", roundtrip(buf, 500, NIC_STEIM2, 7));

    st = 7u; acc = 0;
    for (int i = 0; i < 300; i++) { st = st * 1103515245u + 12345u;
        acc += (int32_t)((st >> 8) % 20000001) - 10000000; buf[i] = acc; }
    check("S1 round-trip: large 30-bit diffs", roundtrip(buf, 300, NIC_STEIM1, 7));
    check("S2 round-trip: large 30-bit diffs", roundtrip(buf, 300, NIC_STEIM2, 7));

    for (int i = 0; i < 400; i++) buf[i] = (i & 1 ? -1 : 1) * (i % 9);
    check("S1 round-trip: negatives + zeros", roundtrip(buf, 400, NIC_STEIM1, 7));
    check("S2 round-trip: negatives + zeros", roundtrip(buf, 400, NIC_STEIM2, 7));

    for (int i = 0; i < 200; i++) buf[i] = (i & 1) ? (1 << 20) : 1;
    check("S1 round-trip: alternating big/small", roundtrip(buf, 200, NIC_STEIM1, 7));
    check("S2 round-trip: alternating big/small", roundtrip(buf, 200, NIC_STEIM2, 7));

    /* ---- byte-exact vs the Python reference (ObsPy-validated) ---- */
    uint8_t rec[MAXR]; size_t used = 0;
    int rc2 = nic_steim_encode_record(VEC_A, sizeof(VEC_A) / sizeof(VEC_A[0]),
                                      NIC_STEIM2, 7, 0, rec, &used);
    check("S2 byte-exact vs Python ref (record)",
          rc2 == 0 && used == sizeof(VEC_A) / sizeof(VEC_A[0]) &&
          memcmp(rec, REC_S2, sizeof(REC_S2)) == 0);
    int rc1 = nic_steim_encode_record(VEC_A, sizeof(VEC_A) / sizeof(VEC_A[0]),
                                      NIC_STEIM1, 7, 0, rec, &used);
    check("S1 byte-exact vs Python ref (record)",
          rc1 == 0 && used == sizeof(VEC_A) / sizeof(VEC_A[0]) &&
          memcmp(rec, REC_S1, sizeof(REC_S1)) == 0);

    /* ---- overflow handling ---- */
    int32_t big[2] = { 0, (1 << 29) };           /* diff = 2^29 > 30-bit signed max */
    rc2 = nic_steim_encode_record(big, 2, NIC_STEIM2, 7, 0, rec, &used);
    check("S2 rejects >30-bit diff", rc2 == NIC_MSEED_EOVERFLOW);
    check("S1 accepts up to 32-bit diff", roundtrip(big, 2, NIC_STEIM1, 7));

    /* ---- constant signal compresses hard ---- */
    for (int i = 0; i < 1000; i++) buf[i] = 100;
    size_t i = 0, total = 0; int32_t prev = 0;
    while (i < 1000) {
        if (nic_steim_encode_record(buf + i, 1000 - i, NIC_STEIM2, 7, prev, rec, &used) != 0) break;
        total += 7 * 64; prev = buf[i + used - 1]; i += used;
    }
    check("constant signal: >=2x compression vs raw int32", total < 1000 * 4 / 2);
    check("constant signal round-trips", roundtrip(buf, 1000, NIC_STEIM2, 7));

    printf("\nResult: %d/%d PASS | %d FAIL\n", passed, passed + failed, failed);
    return failed ? 1 : 0;
}
