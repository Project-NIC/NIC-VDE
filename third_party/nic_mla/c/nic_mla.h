/*
 * nic_mla.h  —  NIC-MLA: COMPLETE library (Python reference ported to C)
 *
 * For more capable platforms (ARM Arduino — SAMD/STM32/Teensy/ESP, PC).
 * Full functionality: format / mount / append / read_record / foreach / recover.
 * Same binary format as Python and the write-only library.
 *
 * (File rotation and the compression method are outside this core.)
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef NIC_MLA_H
#define NIC_MLA_H

#include "nic_mla_format.h"

typedef struct {
    mla_hal_t hal;
    uint32_t  file_size;
    uint8_t   flags;             /* CRC mode */
    uint8_t   log_rec_size;      /* = 16 */
    uint32_t  data_base;         /* first data byte = prefix size */
    /* RAM state */
    uint32_t  top_ptr;
    uint32_t  bot_ptr;
    uint32_t  n_slots;           /* physical slots written (incl. abandoned) */
    uint32_t  count;             /* valid data records */
} mla_t;

/* ── Lifecycle ──────────────────────────────────────────────────────────── */
int mla_format(mla_t *m, mla_hal_t hal,
               uint32_t file_size, uint8_t crc_mode,
               uint8_t cluster_shift, uint8_t keyframe_intv);

/* Same as mla_format, embedding the SCHEMA and STATION tables verbatim (see
 * tools/mla_schema.py). Either may be NULL/0. Returns MLA_E_RANGE if the prefix
 * would exceed MLA_MAX_PREFIX_SEC sectors. */
int mla_format_ex(mla_t *m, mla_hal_t hal,
                  uint32_t file_size, uint8_t crc_mode,
                  uint8_t cluster_shift, uint8_t keyframe_intv,
                  const uint8_t *schema, uint16_t schema_len,
                  const uint8_t *station, uint16_t station_len);

int mla_mount(mla_t *m, mla_hal_t hal);

/* ── Write ──────────────────────────────────────────────────────────────── */
/* station = 1-byte index into the prefix station table (0 = none). */
int mla_append(mla_t *m, uint32_t timestamp, uint8_t station,
               const uint8_t *data, uint16_t len,
               uint8_t rec_type, uint8_t kf_back);

/* ── Read ───────────────────────────────────────────────────────────────── */
int mla_read_data(mla_t *m, const mla_log_t *rec,
                  uint8_t *buf, uint16_t bufcap, uint16_t *out_len);
int mla_read_record(mla_t *m, uint32_t index, mla_log_t *rec_out,
                    uint8_t *buf, uint16_t bufcap, uint16_t *out_len);

/* ── Iteration / query (host-side filtering) ────────────────────────────── */
typedef struct {
    uint8_t  has_time;     uint32_t time_from, time_to;
    uint8_t  has_station;  uint8_t  station;
    uint8_t  has_rec_type; uint8_t  rec_type;
    uint8_t  has_enc;      uint8_t  enc;     /* low nibble of rec_type */
} mla_filter_t;

typedef int (*mla_iter_cb)(void *user, mla_t *m, const mla_log_t *rec);

/* Walk the valid data records (from the oldest), optionally filtering.
 * filter == NULL → no filter. Returns the count of matching records (>=0) or <0. */
int mla_foreach(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user);

/* ── Emergency recovery ─────────────────────────────────────────────────── */
int mla_recover(mla_t *m, mla_hal_t hal, uint32_t *out_count);

/* ── Properties ─────────────────────────────────────────────────────────── */
static inline uint32_t mla_free(const mla_t *m) {
    uint32_t used = m->top_ptr + m->log_rec_size;
    return (m->bot_ptr > used) ? (m->bot_ptr - used) : 0u;
}

#endif /* NIC_MLA_H */
