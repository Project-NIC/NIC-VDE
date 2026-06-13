/*
 * glue_archive_test.c  —  rotation → keyframe (each file independently decodable).
 * Two interleaved DMD streams into a rotating archive; each file read back on its
 * own with a fresh decoder must start every stream on a keyframe and round-trip.
 * MIT  |  ★ Viva La Resistánce ★
 */
#define _POSIX_C_SOURCE 200809L
#include "glue_archive.h"
#include "nic_mla.h"
#include "nic_dmd.h"
#include "mla_archive.h"
#include "hal/nic_mla_hal_posix.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *what, int ok)
{
    if (ok) g_pass++; else { g_fail++; printf("  FAIL: %s\n", what); }
}

#define NREC 150
static const uint8_t SCHEMA[] = {
    MLA_SCHEMA_VER, 0, 2,
    2, 0, 0xFF, 0x01, 0,0, 't','e','m','p', 0,0,0,0,
    2, 0, 0xFF, 0x00, 0,0, 'h','u','m', 0,0,0,0,0,
};
static const uint8_t STATION[] = { MLA_STATION_VER, 2, 7,0, 100,0, 0,0,  8,0, 200,0, 0,0 };

static uint8_t T1[NREC][4], T2[NREC][4], G1[NREC][4], G2[NREC][4];
static int g1 = 0, g2 = 0, fail_kf = 0, fail_dec = 0;

typedef struct { dmd_decoder_t dec[3]; int first[3]; uint8_t blk[512]; } fctx_t;

static int file_cb(void *user, mla_t *m, const mla_log_t *rec)
{
    fctx_t *c = user;
    uint16_t len = 0;
    if (mla_read_data(m, rec, c->blk, sizeof c->blk, &len) != MLA_OK) { fail_dec = 1; return 1; }
    if (!mla_flags_compressed(rec->flags)) return 0;
    int st = rec->station; if (st < 1 || st > 2) return 0;
    if (c->first[st]) { if (mla_flags_kf_back(rec->flags) != 0) fail_kf = 1; c->first[st] = 0; }
    uint8_t out[4];
    if (dmd_decompress(&c->dec[st], c->blk, len, out) != 0) { fail_dec = 1; return 1; }
    if (st == 1) { if (g1 < NREC) memcpy(G1[g1++], out, 4); }
    else         { if (g2 < NREC) memcpy(G2[g2++], out, 4); }
    return 0;
}

int main(void)
{
    char dir[] = "/tmp/ga_testXXXXXX";
    if (!mkdtemp(dir)) { printf("mkdtemp failed\n"); return 1; }

    ga_t *g = glue_archive_create(dir, 2048, MLA_CRC_FULL, DMD_KEYFRAME_EVERY,
                                  SCHEMA, (uint16_t)sizeof SCHEMA,
                                  STATION, (uint16_t)sizeof STATION, NULL);
    check("create archive", g != NULL);
    if (!g) { printf("\nFAIL %d/%d\n", g_pass, g_pass + g_fail); return 1; }

    ga_channel_t *c1 = glue_archive_channel(g, 1, 4);
    ga_channel_t *c2 = glue_archive_channel(g, 2, 4);
    for (int i = 0; i < NREC; i++) {
        mla_put_u16(T1[i], (uint16_t)(235 + i * 7)); mla_put_u16(T1[i] + 2, (uint16_t)(600 + i));
        mla_put_u16(T2[i], (uint16_t)(100 + i * 3)); mla_put_u16(T2[i] + 2, (uint16_t)(900 + i * 2));
        glue_archive_channel_log(c1, (uint32_t)(1000 + i), T1[i], 0);
        glue_archive_channel_log(c2, (uint32_t)(1000 + i), T2[i], 0);
    }
    int nfiles = glue_archive_file_count(g);
    glue_archive_close(g);
    check("rotated into >= 2 files", nfiles >= 2);

    char path[1024];
    for (int seq = 0; ; seq++) {
        snprintf(path, sizeof path, "%s/MLA%05d.MLA", dir, seq);
        mla_posix_file_t rf;
        if (mla_posix_open(&rf, path) != MLA_OK) break;
        mla_t rm;
        if (mla_mount(&rm, mla_posix_hal(&rf)) == MLA_OK) {
            fctx_t c; memset(&c, 0, sizeof c);
            dmd_decoder_init(&c.dec[1], 4); dmd_decoder_init(&c.dec[2], 4);
            c.first[1] = c.first[2] = 1;
            mla_foreach(&rm, NULL, file_cb, &c);
        }
        fclose(rf.f);
        remove(path);
    }
    rmdir(dir);

    check("stream 1 round-trips byte-exact", g1 == NREC && memcmp(G1, T1, sizeof T1) == 0);
    check("stream 2 round-trips byte-exact", g2 == NREC && memcmp(G2, T2, sizeof T2) == 0);
    check("first record of each stream in each file is a keyframe", !fail_kf);
    check("no decode errors across standalone files", !fail_dec);

    printf("\n%s  %d/%d passed\n", g_fail ? "FAIL" : "OK", g_pass, g_pass + g_fail);
    return g_fail ? 1 : 0;
}
