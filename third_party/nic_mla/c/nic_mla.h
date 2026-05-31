/*
 * nic_mla.h  —  NIC-MLA: COMPLETE library (Python reference ported to C)
 *
 * For more capable platforms (ARM Arduino — SAMD/STM32/Teensy/ESP, PC).
 * Full functionality: format / mount / append / read_record / foreach+query /
 * recover. Same binary format as Python and the write-only library.
 *
 * (File rotation and the compression method are outside this core — rotation is
 *  platform glue over the filesystem, compression is separate.)
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
    uint8_t   checkpoint_shift;
    uint8_t   log_rec_size;      /* = 24 */
    uint32_t  data_base;         /* first byte of data (after prefix + index region) */
    /* RAM state */
    uint32_t  top_ptr;
    uint32_t  bot_ptr;
    uint32_t  n_slots;           /* physical slots (incl. abandoned/torn/checkpoint) */
    uint32_t  count;             /* valid data records (excluding checkpoints) */
    uint16_t  seq;
    uint32_t  idx_n;             /* index anchors written so far */
    uint16_t  last_sta;          /* station of the most recent data record (anchor hint) */
} mla_t;

/* ── Lifecycle ──────────────────────────────────────────────────────────── */
/* index_kb — KB reserved between prefix and data for the host-side skip-table
 * (0 = disabled, format byte-identical to before). */
int mla_format(mla_t *m, mla_hal_t hal,
               uint32_t file_size, uint8_t crc_mode,
               uint8_t cluster_shift, uint8_t checkpoint_shift,
               uint8_t keyframe_intv, uint8_t index_kb);
int mla_mount(mla_t *m, mla_hal_t hal);

/* ── Write ──────────────────────────────────────────────────────────────── */
int mla_append(mla_t *m, uint32_t timestamp,
               uint16_t station, uint16_t region,
               const uint8_t *data, uint16_t len,
               uint8_t rec_type, uint16_t kf_back);

/* ── Read ───────────────────────────────────────────────────────────────── */
/* Read a data block described by a log record. Returns the length in *out_len;
 * MLA_E_RANGE if it does not fit in bufcap, MLA_E_BADFMT on bad MAGIC/CRC. */
int mla_read_data(mla_t *m, const mla_log_t *rec,
                  uint8_t *buf, uint16_t bufcap, uint16_t *out_len);

/* Read the n-th valid data record (0 = oldest). */
int mla_read_record(mla_t *m, uint32_t index, mla_log_t *rec_out,
                    uint8_t *buf, uint16_t bufcap, uint16_t *out_len);

/* ── Iteration / query (host-side filtering) ────────────────────────────── */
typedef struct {
    uint8_t  has_time;     uint32_t time_from, time_to;
    uint8_t  has_station;  uint16_t station;
    uint8_t  has_channel;  uint16_t region;
    uint8_t  has_rec_type; uint8_t  rec_type;
    uint8_t  has_enc;      uint8_t  enc;     /* low nibble of rec_type */
} mla_filter_t;

/* The callback receives a record; for the data it calls mla_read_data(m, rec, ...).
 * A non-zero return ends the iteration early. */
typedef int (*mla_iter_cb)(void *user, mla_t *m, const mla_log_t *rec);

/* Walk the valid data records (from the oldest), optionally filtering.
 * filter == NULL → no filter. Returns the count of matching records (>=0) or <0. */
int mla_foreach(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user);

/* Same as mla_foreach but uses the index region to jump near filter->time_from
 * before scanning forward (falls back to a full scan when there is no index).
 * Identical results to mla_foreach — only faster. */
int mla_scan(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user);

/* Pick a safe starting log slot for records at >= time_from, using the index
 * (newest anchor with timestamp <= time_from). Returns 0 if no index/match. */
uint32_t mla_index_start_slot(mla_t *m, uint32_t time_from);

/* ── Emergency recovery ─────────────────────────────────────────────────── */
int mla_recover(mla_t *m, mla_hal_t hal, uint32_t *out_count);

/* ── Properties ─────────────────────────────────────────────────────────── */
static inline uint32_t mla_free(const mla_t *m) {
    uint32_t used = m->top_ptr + m->log_rec_size;
    return (m->bot_ptr > used) ? (m->bot_ptr - used) : 0u;
}

#endif /* NIC_MLA_H */
