/*
 * glue_logger.c  —  NIC-GLUE-IN: write-side connector (C, no heap). MIT
 * ★ Viva La Resistánce ★
 */
#include "glue_logger.h"
#include <string.h>

int glue_logger_format(glue_logger_t *g, mla_hal_t hal,
                       uint32_t file_size, uint8_t crc_mode, uint8_t keyframe_intv,
                       const uint8_t *schema,  uint16_t schema_len,
                       const uint8_t *station, uint16_t station_len)
{
    if (!g) return MLA_E_IO;
    return mla_format_ex(&g->mla, hal, file_size, crc_mode, 12, keyframe_intv,
                         schema, schema_len, station, station_len);
}

int glue_logger_mount(glue_logger_t *g, mla_hal_t hal)
{
    if (!g) return MLA_E_IO;
    return mla_mount(&g->mla, hal);
}

int glue_log_raw(glue_logger_t *g, uint32_t ts, uint8_t station,
                 const uint8_t *data, uint16_t len, uint16_t subsec)
{
    if (!g) return MLA_E_IO;
    return mla_append(&g->mla, ts, subsec, station, data, len, 0, 0);
}

int glue_log_event(glue_logger_t *g, uint32_t ts, uint8_t station,
                   const char *text, uint16_t subsec)
{
    if (!g || !text) return MLA_E_IO;
    return mla_append(&g->mla, ts, subsec, station,
                      (const uint8_t *)text, (uint16_t)strlen(text), 0, 0);
}

void glue_channel_init(glue_channel_t *ch, glue_logger_t *g, uint8_t station, uint8_t pkt_len)
{
    ch->log = g;
    ch->station = station;
    ch->pkt_len = pkt_len;
    ch->since_kf = 0;
    dmd_encoder_init(&ch->enc, pkt_len);
}

int glue_channel_log(glue_channel_t *ch, uint32_t ts, const uint8_t *row, uint16_t subsec)
{
    if (!ch || !row) return MLA_E_IO;

    /* The only buffer glue owns: the compressed blob (worst case = pkt_len + 1).
     * Like NIC-DMD: VLA sized to this channel by default, or fixed with no VLA
     * when built -DDMD_PKT_MAX_BUILD=N (MCU / IAR / Keil / SDCC). */
#ifdef DMD_PKT_MAX_BUILD
    uint8_t out[DMD_OUT_MAX];                       /* fixed (= N + 1), no VLA */
#else
    uint8_t out[ch->pkt_len + 1];                  /* VLA, exact worst case    */
#endif
    uint16_t olen = dmd_compress(&ch->enc, row, out);
    int is_keyframe = (out[0] & 0x07) == 0;         /* sample number == 0 */
    ch->since_kf = is_keyframe ? 0 : ch->since_kf + 1;

    int rc = mla_append(&ch->log->mla, ts, subsec, ch->station,
                        out, olen, 1, (uint8_t)ch->since_kf);
    return (rc == MLA_OK) ? (int)olen : rc;
}

void glue_channel_reset(glue_channel_t *ch)
{
    if (!ch) return;
    dmd_encoder_init(&ch->enc, ch->pkt_len);        /* fresh → next is a keyframe */
    ch->since_kf = 0;
}

void glue_logger_sync(glue_logger_t *g)
{
    if (g) g->mla.hal.sync(g->mla.hal.ctx);
}

uint32_t glue_logger_record_count(const glue_logger_t *g)
{
    return g ? g->mla.count : 0u;
}
