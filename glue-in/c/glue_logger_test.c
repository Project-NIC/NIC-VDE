/*
 * glue_logger_test.c  —  NIC-GLUE-IN write path round-trip (no-heap API).
 *
 * Caller-allocated logger + channel over a caller-provided HAL (POSIX here,
 * FatFs on an MCU). Writes a NIC-DMD-compressed stream + a raw event, then reads
 * the container back (MLA reader + DMD decoder) and checks byte-for-byte, with
 * kf_back correct (keyframe = 0).
 *
 * Build:  make test
 * MIT  |  ★ Viva La Resistánce ★
 */
#define _POSIX_C_SOURCE 200809L
#include "glue_logger.h"
#include "nic_mla.h"
#include "nic_dmd.h"
#include "hal/nic_mla_hal_posix.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *what, int ok)
{
    if (ok) g_pass++;
    else { g_fail++; printf("  FAIL: %s\n", what); }
}

#define NREC 6

static const uint8_t SCHEMA[] = {
    MLA_SCHEMA_VER, 0, 2,
    2, 0, 0xFF, 0x01, 0,0, 't','e','m','p', 0,0,0,0,
    2, 0, 0xFF, 0x00, 0,0, 'h','u','m', 0,0,0,0,0,
};
static const uint8_t STATION[] = { MLA_STATION_VER, 1, 7,0, 100,0, 0,0 };

static uint8_t TRUTH[NREC][4];

typedef struct {
    int idx, started, fail_value, fail_kf, raw_seen;
    dmd_decoder_t dec;
    uint8_t blk[512];
} chk_t;

static int chk_cb(void *user, mla_t *m, const mla_log_t *rec)
{
    chk_t *c = user;
    uint16_t len = 0;
    if (mla_read_data(m, rec, c->blk, sizeof c->blk, &len) != MLA_OK) { c->fail_value = 1; return 1; }
    if (mla_flags_compressed(rec->flags)) {
        uint8_t out[4];
        if (dmd_decompress(&c->dec, c->blk, len, out) != 0) { c->fail_value = 1; return 1; }
        if (memcmp(out, TRUTH[c->idx], 4) != 0) c->fail_value = 1;
        if (!c->started) { if (mla_flags_kf_back(rec->flags) != 0) c->fail_kf = 1; c->started = 1; }
        c->idx++;
    } else {
        c->raw_seen = 1;
    }
    return 0;
}

int main(void)
{
    char path[] = "/tmp/glue_logger_mlaXXXXXX";
    int fd = mkstemp(path); if (fd >= 0) close(fd);

    /* ── write: caller-allocated logger + channel, caller-provided HAL ── */
    mla_posix_file_t wf;
    check("create file", mla_posix_create(&wf, path, 64u * 1024u) == MLA_OK);

    glue_logger_t logger;
    check("format", glue_logger_format(&logger, mla_posix_hal(&wf), 64u * 1024u,
                                       MLA_CRC_FULL, DMD_KEYFRAME_EVERY,
                                       SCHEMA, (uint16_t)sizeof SCHEMA,
                                       STATION, (uint16_t)sizeof STATION) == MLA_OK);

    glue_channel_t ch;
    glue_channel_init(&ch, &logger, 1, 4);

    for (int i = 0; i < NREC; i++) {
        mla_put_u16(TRUTH[i],     (uint16_t)(235 + i));
        mla_put_u16(TRUTH[i] + 2, (uint16_t)(600 + i));
        check("channel log returns blob len",
              glue_channel_log(&ch, (uint32_t)(1000 + i), TRUTH[i], 0) > 0);
    }
    check("log_event raw", glue_log_event(&logger, 2000, 1, "PING", 0) == MLA_OK);
    check("record count", glue_logger_record_count(&logger) == NREC + 1);
    glue_logger_sync(&logger);
    fclose(wf.f);                 /* caller owns the HAL/file */

    /* ── read it back and verify ── */
    mla_posix_file_t rf;
    check("reopen", mla_posix_open(&rf, path) == MLA_OK);
    mla_t rm;
    check("mount", mla_mount(&rm, mla_posix_hal(&rf)) == MLA_OK);

    chk_t c; memset(&c, 0, sizeof c);
    dmd_decoder_init(&c.dec, 4);
    int n = mla_foreach(&rm, NULL, chk_cb, &c);
    fclose(rf.f);

    check("foreach saw all records", n == NREC + 1);
    check("all compressed rows round-trip byte-exact", c.idx == NREC && !c.fail_value);
    check("first compressed record is a keyframe (kf_back 0)", !c.fail_kf);
    check("raw event present", c.raw_seen == 1);

    remove(path);
    printf("\n%s  %d/%d passed\n", g_fail ? "FAIL" : "OK", g_pass, g_pass + g_fail);
    return g_fail ? 1 : 0;
}
