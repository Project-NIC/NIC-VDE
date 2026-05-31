/*
 * nic_mla_write.h  —  NIC-MLA: WRITE-ONLY library for ATmega / small Arduino
 *
 * The minimal WRITE-only path, exactly as it should run on an ATmega328:
 *   • format()  — initialize a new container
 *   • mount()   — restore the top/bot pointers after a restart (to continue)
 *   • append()  — add a record (commit: LOCK first, DATA second)
 *
 * No search, record reading, editing or recovery — the host does that
 * (see nic_mla.h). No dynamic allocation; the largest stack buffer is 32 B.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef NIC_MLA_WRITE_H
#define NIC_MLA_WRITE_H

#include "nic_mla_format.h"

typedef struct {
    mla_hal_t hal;
    uint32_t  file_size;
    uint8_t   flags;             /* CRC mode */
    uint8_t   log_rec_size;      /* = 16 */
    uint32_t  data_base;         /* first data byte = prefix size */
    /* RAM state */
    uint32_t  top_ptr;           /* where to write the next data block */
    uint32_t  bot_ptr;           /* the next lock goes to bot_ptr - log_rec_size */
    uint32_t  count;             /* valid data records */
} mla_writer_t;

/* Initialize a new container (writes the prefix; assumes the medium is 0xFF).
 * crc_mode: MLA_CRC_NONE/DATA/FULL. */
int mla_w_format(mla_writer_t *w, mla_hal_t hal,
                 uint32_t file_size, uint8_t crc_mode,
                 uint8_t cluster_shift, uint8_t keyframe_intv);

/* Same as mla_w_format, but also embeds the self-describing SCHEMA and STATION
 * tables (see tools/mla_schema.py) verbatim into the prefix free space. Either
 * may be NULL/0. The prefix grows in 512 B sectors to fit them; returns
 * MLA_E_RANGE if it would exceed MLA_MAX_PREFIX_SEC sectors. */
int mla_w_format_ex(mla_writer_t *w, mla_hal_t hal,
                    uint32_t file_size, uint8_t crc_mode,
                    uint8_t cluster_shift, uint8_t keyframe_intv,
                    const uint8_t *schema, uint16_t schema_len,
                    const uint8_t *station, uint16_t station_len);

/* Load an existing container and restore the pointers (after a restart). */
int mla_w_mount(mla_writer_t *w, mla_hal_t hal);

/* Append a data record. data/len = payload (1..65535). station = 1-byte index
 * into the prefix station table (0 = none). rec_type/kf_back optional.
 * Returns MLA_OK, or MLA_E_FULL / MLA_E_RANGE / MLA_E_IO. */
int mla_w_append(mla_writer_t *w, uint32_t timestamp, uint8_t station,
                 const uint8_t *data, uint16_t len,
                 uint8_t rec_type, uint8_t kf_back);

/* Free bytes (approximate, excluding the next record). */
static inline uint32_t mla_w_free(const mla_writer_t *w) {
    uint32_t used = w->top_ptr + w->log_rec_size;
    return (w->bot_ptr > used) ? (w->bot_ptr - used) : 0u;
}

#endif /* NIC_MLA_WRITE_H */
