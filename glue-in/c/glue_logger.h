/*
 * glue_logger.h  —  NIC-GLUE-IN: write-side connector in C (embedded model).
 *
 * C mirror of logger.py. Built like NIC-DMD: **no heap** — the caller allocates
 * the structs (stack/static) and passes a HAL, so it runs on an STM-class MCU
 * (FatFs HAL) or a PC (POSIX HAL). Buffer sizes follow NIC-DMD's build mode:
 *   • default                  — universal (PC / testing);
 *   • -DDMD_PKT_MAX_BUILD=N     — everything fixed to N, no VLA, minimal RAM
 *                                 (IAR/Keil/SDCC). Set N to your max packet width.
 * A channel embeds a dmd_encoder_t, so its size is governed by that macro too.
 *
 *   row ──▶ [ optional NIC-DMD ] ──▶ NIC-MLA container
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef GLUE_LOGGER_H
#define GLUE_LOGGER_H

#include <stdint.h>
#include "nic_mla.h"     /* mla_t, mla_hal_t, mla_format_ex, mla_append */
#include "nic_dmd.h"     /* dmd_encoder_t, dmd_compress, DMD_OUT_MAX */

#ifdef __cplusplus
extern "C" {
#endif

/* Caller-allocated. No heap; no ownership of the HAL/file (the caller closes it). */
typedef struct {
    mla_t mla;
} glue_logger_t;

typedef struct {
    glue_logger_t *log;
    uint8_t        station;
    uint8_t        pkt_len;
    int            since_kf;     /* records since the owning keyframe → kf_back */
    dmd_encoder_t  enc;          /* size governed by DMD_PKT_MAX_BUILD */
} glue_channel_t;

/* Format a fresh container over a caller-provided HAL (FatFs on MCU, POSIX on
 * host). Tables may be NULL/0; keyframe_intv is the prefix hint (e.g.
 * DMD_KEYFRAME_EVERY, or 0 for pure RAW). Returns MLA_OK/<0. */
int glue_logger_format(glue_logger_t *g, mla_hal_t hal,
                       uint32_t file_size, uint8_t crc_mode, uint8_t keyframe_intv,
                       const uint8_t *schema,  uint16_t schema_len,
                       const uint8_t *station, uint16_t station_len);

/* Mount an existing container over a caller-provided HAL. */
int glue_logger_mount(glue_logger_t *g, mla_hal_t hal);

/* Classic path: store one row verbatim / a text event, uncompressed. */
int glue_log_raw(glue_logger_t *g, uint32_t ts, uint8_t station,
                 const uint8_t *data, uint16_t len, uint16_t subsec);
int glue_log_event(glue_logger_t *g, uint32_t ts, uint8_t station,
                   const char *text, uint16_t subsec);

/* A NIC-DMD-compressed stream for one station / fixed width. The caller owns the
 * glue_channel_t (one per stream); manage them in your own array on the MCU. */
void glue_channel_init(glue_channel_t *ch, glue_logger_t *g, uint8_t station, uint8_t pkt_len);

/* Compress one fixed-width row (exactly pkt_len) and append it. Returns the
 * stored blob length (>0) or <0. */
int  glue_channel_log(glue_channel_t *ch, uint32_t ts, const uint8_t *row, uint16_t subsec);

/* Drop the delta history so the next packet is a fresh keyframe (rotation seam). */
void glue_channel_reset(glue_channel_t *ch);

void     glue_logger_sync(glue_logger_t *g);
uint32_t glue_logger_record_count(const glue_logger_t *g);

#ifdef __cplusplus
}
#endif
#endif /* GLUE_LOGGER_H */
