/*
 * glue_archive.c  —  NIC-GLUE-IN: rotating write-side connector (C). MIT
 * ★ Viva La Resistánce ★
 */
#include "glue_archive.h"

#include <stdlib.h>
#include <string.h>

#include "nic_dmd.h"
#include "mla_archive.h"

struct ga_channel {
    ga_t          *ga;
    uint8_t        station;
    uint8_t        pkt_len;
    dmd_encoder_t  enc;
    int            since_kf;
};

struct glue_archive {
    mla_archive_t arch;
    ga_channel_t *chan[256];
};

/* on_rotate seam: reset every open stream so its next record is a keyframe
 * (the trigger stream is keyframed up front already). */
static void ga_on_rotate(void *ctx, uint16_t prev, uint16_t nw)
{
    (void)prev; (void)nw;
    ga_t *g = ctx;
    for (int i = 0; i < 256; i++) {
        ga_channel_t *ch = g->chan[i];
        if (ch) { dmd_encoder_init(&ch->enc, ch->pkt_len); ch->since_kf = 0; }
    }
}

ga_t *glue_archive_create(const char *dir, uint32_t file_size,
                          uint8_t crc_mode, uint8_t keyframe_intv,
                          const uint8_t *schema,  uint16_t schema_len,
                          const uint8_t *station, uint16_t station_len,
                          const char *base)
{
    ga_t *g = calloc(1, sizeof *g);
    if (!g) return NULL;
    if (mla_archive_open(&g->arch, dir, file_size, crc_mode, keyframe_intv,
                         schema, schema_len, station, station_len,
                         base, ga_on_rotate, g) != MLA_OK) {
        free(g);
        return NULL;
    }
    return g;
}

ga_channel_t *glue_archive_channel(ga_t *g, uint8_t station, uint8_t pkt_len)
{
    if (!g || station == 0) return NULL;
    ga_channel_t *ch = g->chan[station];
    if (ch) return (ch->pkt_len == pkt_len) ? ch : NULL;
    ch = calloc(1, sizeof *ch);
    if (!ch) return NULL;
    ch->ga = g; ch->station = station; ch->pkt_len = pkt_len; ch->since_kf = 0;
    dmd_encoder_init(&ch->enc, pkt_len);
    g->chan[station] = ch;
    return ch;
}

int glue_archive_channel_log(ga_channel_t *ch, uint32_t ts, const uint8_t *row, uint16_t subsec)
{
    if (!ch || !row) return -1;

    /* worst-case DMD output = pkt_len + 1: if this record might not fit, reset
     * first so it is a keyframe — a delta never crosses a file boundary. */
    if (mla_archive_will_rotate(&ch->ga->arch, (uint16_t)(ch->pkt_len + 1))) {
        dmd_encoder_init(&ch->enc, ch->pkt_len);
        ch->since_kf = 0;
    }

#ifdef DMD_PKT_MAX_BUILD
    uint8_t out[DMD_OUT_MAX];                       /* fixed, no VLA */
#else
    uint8_t out[ch->pkt_len + 1];                  /* VLA */
#endif
    uint16_t olen = dmd_compress(&ch->enc, row, out);
    int is_kf = (out[0] & 0x07) == 0;
    ch->since_kf = is_kf ? 0 : ch->since_kf + 1;

    int rc = mla_archive_append(&ch->ga->arch, ts, subsec, ch->station,
                                out, olen, 1, (uint8_t)ch->since_kf);
    return (rc < 0) ? rc : (int)olen;
}

int glue_archive_log_raw(ga_t *g, uint32_t ts, uint8_t station,
                         const uint8_t *data, uint16_t len, uint16_t subsec)
{
    if (!g) return -1;
    int rc = mla_archive_append(&g->arch, ts, subsec, station, data, len, 0, 0);
    return (rc < 0) ? rc : 0;
}

int  glue_archive_file_count(ga_t *g) { return g ? mla_archive_file_count(&g->arch) : 0; }
void glue_archive_sync(ga_t *g)       { if (g) mla_archive_sync(&g->arch); }

void glue_archive_close(ga_t *g)
{
    if (!g) return;
    mla_archive_close(&g->arch);
    for (int i = 0; i < 256; i++) free(g->chan[i]);
    free(g);
}
