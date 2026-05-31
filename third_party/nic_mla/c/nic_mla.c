/*
 * nic_mla.c  —  NIC-MLA COMPLETE implementation (Python port)
 * MIT  |  ★ Viva La Resistánce ★
 */
#include "nic_mla.h"

/* ── prefix: write by streaming ─────────────────────────────────────────── */
static int write_prefix(mla_hal_t *hal, const mla_prefix_t *p) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  zero[32];
    uint16_t crc, n;
    uint32_t pos;

    mla_prefix_build_hdr(hdr, p);
    crc = mla_crc16_ex(0xFFFFu, hdr, MLA_PFX_HDR_SIZE);
    memset(zero, 0, sizeof(zero));
    { uint16_t rem = (uint16_t)(510u - MLA_PFX_HDR_SIZE);
      while (rem) { n = rem < sizeof(zero) ? rem : (uint16_t)sizeof(zero);
                    crc = mla_crc16_ex(crc, zero, n); rem = (uint16_t)(rem - n); } }

    if (hal->write(hal->ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    pos = MLA_PFX_HDR_SIZE;
    while (pos < 510u) {
        n = (uint16_t)((510u - pos) < sizeof(zero) ? (510u - pos) : sizeof(zero));
        if (hal->write(hal->ctx, pos, zero, n) != 0) return MLA_E_IO;
        pos += n;
    }
    { uint8_t crcb[2]; mla_put_u16(crcb, crc);
      if (hal->write(hal->ctx, 510, crcb, 2) != 0) return MLA_E_IO; }
    hal->sync(hal->ctx);
    return MLA_OK;
}

static int read_prefix(mla_hal_t *hal, mla_prefix_t *p) {
    uint8_t  buf[32];
    uint16_t crc = 0xFFFFu, n, stored;
    uint32_t pos = 0;
    while (pos < 510u) {
        n = (uint16_t)((510u - pos) < sizeof(buf) ? (510u - pos) : sizeof(buf));
        if (hal->read(hal->ctx, pos, buf, n) != 0) return MLA_E_IO;
        crc = mla_crc16_ex(crc, buf, n);
        pos += n;
    }
    if (hal->read(hal->ctx, 510, buf, 2) != 0) return MLA_E_IO;
    stored = mla_get_u16(buf);
    if (crc != stored) return MLA_E_BADFMT;
    {
        uint8_t hdr[MLA_PFX_HDR_SIZE];
        if (hal->read(hal->ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
        if (!mla_prefix_parse_hdr(hdr, p)) return MLA_E_BADFMT;
    }
    return MLA_OK;
}

/* Anchor capacity of the reserved index region (0 = index disabled). */
static uint32_t idx_capacity(const mla_t *m) {
    return (m->data_base - MLA_PREFIX_SIZE) / MLA_IDX_REC_SIZE;
}

/* ── format ─────────────────────────────────────────────────────────────── */
int mla_format(mla_t *m, mla_hal_t hal,
               uint32_t file_size, uint8_t crc_mode,
               uint8_t cluster_shift, uint8_t checkpoint_shift,
               uint8_t keyframe_intv, uint8_t index_kb) {
    mla_prefix_t p;
    uint32_t data_base = MLA_PREFIX_SIZE + (uint32_t)index_kb * 1024u;
    int rc;

    m->hal = hal;
    p.version          = MLA_VERSION;
    p.cluster_shift    = cluster_shift;
    p.log_rec_size     = MLA_LOG_REC_SIZE;
    p.flags            = crc_mode;
    p.file_size        = file_size;
    p.phys_addr        = 0;
    p.container_kind   = 0;
    p.file_seq         = 0;
    p.keyframe_intv    = keyframe_intv;
    p.enc_caps         = 0;
    p.data_base        = data_base;
    p.region_end       = file_size;
    p.checkpoint_shift = checkpoint_shift;

    rc = write_prefix(&m->hal, &p);
    if (rc != MLA_OK) return rc;

    m->file_size        = file_size;
    m->flags            = crc_mode;
    m->checkpoint_shift = checkpoint_shift;
    m->log_rec_size     = MLA_LOG_REC_SIZE;
    m->data_base        = data_base;
    m->top_ptr          = data_base;
    m->bot_ptr          = file_size;
    m->n_slots          = 0;
    m->count            = 0;
    m->seq              = 0;
    m->idx_n            = 0;
    m->last_sta         = 0;
    return MLA_OK;
}

/* ── mount ──────────────────────────────────────────────────────────────── */
int mla_mount(mla_t *m, mla_hal_t hal) {
    mla_prefix_t p;
    uint32_t fs, lo, hi, mid, max_slots, slot, start_slot, top, count;
    uint16_t rs;
    int32_t  last_seq = -1;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    int rc;

    m->hal = hal;
    rc = read_prefix(&m->hal, &p);
    if (rc != MLA_OK) return rc;

    m->file_size        = p.file_size;
    m->flags            = p.flags;
    m->checkpoint_shift = p.checkpoint_shift;
    m->log_rec_size     = p.log_rec_size;
    m->data_base        = p.data_base;
    m->last_sta         = 0;
    fs = p.file_size;
    rs = p.log_rec_size;

    /* recover index anchor count (linear scan until first 0xFF status) */
    {
        uint32_t cap = idx_capacity(m), i;
        uint8_t st;
        m->idx_n = 0;
        for (i = 0; i < cap; i++) {
            if (m->hal.read(m->hal.ctx, MLA_PREFIX_SIZE + i * MLA_IDX_REC_SIZE + 10, &st, 1) != 0)
                return MLA_E_IO;
            if (st == MLA_IDX_UNUSED) break;
            m->idx_n++;
        }
    }

    max_slots = (fs - p.data_base) / rs;
    lo = 0; hi = max_slots;
    while (lo < hi) {
        uint16_t i; int all_ff = 1;
        mid = (lo + hi) / 2;
        if (m->hal.read(m->hal.ctx, fs - (mid + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
        for (i = 0; i < rs; i++) if (rec_buf[i] != 0xFF) { all_ff = 0; break; }
        if (all_ff) hi = mid; else lo = mid + 1;
    }

    m->n_slots = lo;
    m->bot_ptr = fs - lo * rs;
    if (lo == 0) {
        m->top_ptr = p.data_base; m->count = 0; m->seq = 0;
        return MLA_OK;
    }

    start_slot = 0; count = 0; top = p.data_base;
    { uint32_t s;
      for (s = lo; s-- > 0; ) {
          if (m->hal.read(m->hal.ctx, fs - (s + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
          if (mla_log_parse(rec_buf, &rec) && mla_log_is_live(&rec) && mla_log_is_checkpoint(&rec)) {
              count = ((uint32_t)rec.station << 16) | rec.region;
              top = rec.offset; last_seq = rec.seq; start_slot = s + 1; break;
          }
          if (s == 0) break;
      }
    }

    for (slot = start_slot; slot < lo; slot++) {
        uint32_t addr = fs - (slot + 1) * rs;
        int is_newest = (slot == lo - 1);
        if (m->hal.read(m->hal.ctx, addr, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec)) continue;
        last_seq = rec.seq;
        if (mla_log_is_checkpoint(&rec)) {
            count = ((uint32_t)rec.station << 16) | rec.region;
            top = rec.offset; continue;
        }
        if (mla_log_is_abandoned(&rec)) {
            if (is_newest) top = rec.offset;
            continue;
        }
        if (is_newest) {
            uint8_t magic[2];
            if (m->hal.read(m->hal.ctx, rec.offset, magic, 2) != 0) return MLA_E_IO;
            if (magic[0] != MLA_DATA_MAGIC0 || magic[1] != MLA_DATA_MAGIC1) {
                uint8_t ab = MLA_FLAG_ABANDONED;
                if (m->hal.write(m->hal.ctx, addr + 20, &ab, 1) != 0) return MLA_E_IO;
                m->hal.sync(m->hal.ctx);
                top = rec.offset; continue;
            }
        }
        top = mla_log_block_end(&rec);
        count++;
    }

    m->top_ptr = top;
    m->count   = count;
    m->seq     = (last_seq >= 0) ? (uint16_t)((last_seq + 1) & 0xFFFF) : 0;
    return MLA_OK;
}

/* ── append + checkpoint ────────────────────────────────────────────────── */

/* Append one 12 B index anchor (pure speed-up; skipped if disabled/full). */
static int write_anchor(mla_t *m, uint32_t timestamp, uint32_t slot, uint16_t station) {
    uint8_t buf[MLA_IDX_REC_SIZE];
    mla_idx_t a;
    if (m->idx_n >= idx_capacity(m)) return MLA_OK;   /* disabled or full */
    a.timestamp = timestamp; a.slot = slot; a.station = station;
    a.status = MLA_IDX_LIVE; a.reserved = 0xFF;
    mla_idx_build(buf, &a);
    if (m->hal.write(m->hal.ctx, MLA_PREFIX_SIZE + m->idx_n * MLA_IDX_REC_SIZE,
                     buf, MLA_IDX_REC_SIZE) != 0) return MLA_E_IO;
    m->idx_n += 1;
    return MLA_OK;
}

static int write_checkpoint(mla_t *m, uint32_t timestamp) {
    uint32_t new_bot = m->bot_ptr - m->log_rec_size;
    uint8_t  buf[MLA_LOG_REC_SIZE];
    mla_log_t cp;
    if (new_bot <= m->top_ptr) return MLA_OK;
    cp.timestamp = timestamp; cp.offset = m->top_ptr;
    cp.station = (uint16_t)((m->count >> 16) & 0xFFFF);
    cp.region = (uint16_t)(m->count & 0xFFFF);
    cp.seq = m->seq; cp.rec_type = MLA_REC_CHECKPOINT;
    cp.length = 0; cp.kf_back = 0; cp.reserved = 0; cp.flags = MLA_FLAG_LIVE;
    mla_log_build(buf, &cp);
    if (m->hal.write(m->hal.ctx, new_bot, buf, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;
    m->bot_ptr = new_bot;
    m->n_slots += 1;
    /* anchor the checkpoint slot so the host can seek straight there by time */
    return write_anchor(m, timestamp, m->n_slots - 1, m->last_sta);
}

int mla_append(mla_t *m, uint32_t timestamp,
               uint16_t station, uint16_t region,
               const uint8_t *data, uint16_t len,
               uint8_t rec_type, uint16_t kf_back) {
    uint32_t block_sz, new_bot;
    uint8_t  lock[MLA_LOG_REC_SIZE], mb[2], crcb[2];
    mla_log_t r;
    uint16_t crc;

    if (len < 1) return MLA_E_RANGE;
    block_sz = 2u + len + 2u;
    if (m->top_ptr + block_sz > m->bot_ptr - m->log_rec_size) return MLA_E_FULL;
    new_bot = m->bot_ptr - m->log_rec_size;

    r.timestamp = timestamp; r.offset = m->top_ptr;
    r.station = station; r.region = region;
    r.seq = m->seq; r.rec_type = rec_type; r.length = len; r.kf_back = kf_back;
    r.reserved = 0; r.flags = MLA_FLAG_LIVE;
    mla_log_build(lock, &r);
    if (m->hal.write(m->hal.ctx, new_bot, lock, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;

    mb[0] = MLA_DATA_MAGIC0; mb[1] = MLA_DATA_MAGIC1;
    if (m->hal.write(m->hal.ctx, m->top_ptr, mb, 2) != 0) return MLA_E_IO;
    if (m->hal.write(m->hal.ctx, m->top_ptr + 2, data, len) != 0) return MLA_E_IO;
    crc = ((m->flags & 0x3) >= MLA_CRC_DATA) ? mla_crc16(data, len) : 0xFFFFu;
    mla_put_u16(crcb, crc);
    if (m->hal.write(m->hal.ctx, m->top_ptr + 2u + len, crcb, 2) != 0) return MLA_E_IO;

    m->top_ptr += block_sz;
    m->bot_ptr  = new_bot;
    m->n_slots += 1;
    m->count   += 1;
    m->seq      = (uint16_t)((m->seq + 1) & 0xFFFF);
    m->last_sta = station;

    if (m->checkpoint_shift && (m->count % (1u << m->checkpoint_shift)) == 0)
        return write_checkpoint(m, timestamp);
    return MLA_OK;
}

/* ── read ───────────────────────────────────────────────────────────────── */
int mla_read_data(mla_t *m, const mla_log_t *rec,
                  uint8_t *buf, uint16_t bufcap, uint16_t *out_len) {
    uint8_t magic[2], crcb[2];
    if (rec->length > bufcap) return MLA_E_RANGE;
    if (m->hal.read(m->hal.ctx, rec->offset, magic, 2) != 0) return MLA_E_IO;
    if (magic[0] != MLA_DATA_MAGIC0 || magic[1] != MLA_DATA_MAGIC1) return MLA_E_BADFMT;
    if (m->hal.read(m->hal.ctx, rec->offset + 2, buf, rec->length) != 0) return MLA_E_IO;
    if ((m->flags & 0x3) >= MLA_CRC_DATA) {
        if (m->hal.read(m->hal.ctx, rec->offset + 2u + rec->length, crcb, 2) != 0) return MLA_E_IO;
        if (mla_crc16(buf, rec->length) != mla_get_u16(crcb)) return MLA_E_BADFMT;
    }
    if (out_len) *out_len = rec->length;
    return MLA_OK;
}

int mla_read_record(mla_t *m, uint32_t index, mla_log_t *rec_out,
                    uint8_t *buf, uint16_t bufcap, uint16_t *out_len) {
    uint32_t slot, live = 0, fs = m->file_size;
    uint16_t rs = m->log_rec_size;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    if (index >= m->count) return MLA_E_RANGE;
    for (slot = 0; slot < m->n_slots; slot++) {
        if (m->hal.read(m->hal.ctx, fs - (slot + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec) || !mla_log_is_live(&rec) || mla_log_is_checkpoint(&rec))
            continue;
        if (live == index) {
            if (rec_out) *rec_out = rec;
            return mla_read_data(m, &rec, buf, bufcap, out_len);
        }
        live++;
    }
    return MLA_E_NOTFOUND;
}

/* ── iteration / query ──────────────────────────────────────────────────── */
static int filter_match(const mla_filter_t *f, const mla_log_t *r) {
    if (!f) return 1;
    if (f->has_time && (r->timestamp < f->time_from || r->timestamp > f->time_to)) return 0;
    if (f->has_station && r->station != f->station) return 0;
    if (f->has_channel && r->region != f->region) return 0;
    if (f->has_rec_type && r->rec_type != f->rec_type) return 0;
    if (f->has_enc && (r->rec_type & 0x0F) != f->enc) return 0;
    return 1;
}

/* Shared walk from a starting slot (used by both foreach and the indexed scan). */
static int foreach_from(mla_t *m, uint32_t start_slot,
                        const mla_filter_t *filter, mla_iter_cb cb, void *user) {
    uint32_t slot, matched = 0, fs = m->file_size;
    uint16_t rs = m->log_rec_size;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    for (slot = start_slot; slot < m->n_slots; slot++) {
        if (m->hal.read(m->hal.ctx, fs - (slot + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec) || !mla_log_is_live(&rec) || mla_log_is_checkpoint(&rec))
            continue;
        if (!filter_match(filter, &rec)) continue;
        matched++;
        if (cb && cb(user, m, &rec)) break;
    }
    return (int)matched;
}

int mla_foreach(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user) {
    return foreach_from(m, 0, filter, cb, user);
}

uint32_t mla_index_start_slot(mla_t *m, uint32_t time_from) {
    uint32_t i, start = 0;
    uint8_t  buf[MLA_IDX_REC_SIZE];
    mla_idx_t a;
    for (i = 0; i < m->idx_n; i++) {
        if (m->hal.read(m->hal.ctx, MLA_PREFIX_SIZE + i * MLA_IDX_REC_SIZE,
                        buf, MLA_IDX_REC_SIZE) != 0) return 0;
        mla_idx_parse(buf, &a);
        if (a.status != MLA_IDX_LIVE) continue;
        /* Anchors carry no CRC; ignore a bogus (torn) slot past the live tail.
         * A too-low start only costs speed; a too-high one could skip records. */
        if (a.slot >= m->n_slots) continue;
        /* strict '<': an anchor's slot is the checkpoint slot (just after its
         * triggering record); starting one bucket back never skips a record
         * whose timestamp equals time_from. */
        if (a.timestamp < time_from) start = a.slot;
        else break;
    }
    return start;
}

int mla_scan(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user) {
    uint32_t start = (filter && filter->has_time)
                     ? mla_index_start_slot(m, filter->time_from) : 0;
    return foreach_from(m, start, filter, cb, user);
}

/* ── emergency recovery ─────────────────────────────────────────────────── */
int mla_recover(mla_t *m, mla_hal_t hal, uint32_t *out_count) {
    mla_prefix_t p;
    uint32_t fs, pos, data_end, length, bot;
    uint16_t rs;
    uint8_t  mb[2], crcb[2];
    int rc;

    m->hal = hal;
    rc = read_prefix(&m->hal, &p);
    if (rc != MLA_OK) return rc;
    if ((p.flags & 0x3) < MLA_CRC_DATA) return MLA_E_NOSUP;

    fs = p.file_size; rs = p.log_rec_size;
    m->file_size = fs; m->flags = p.flags;
    m->checkpoint_shift = p.checkpoint_shift; m->log_rec_size = rs;
    m->data_base = p.data_base; m->idx_n = 0; m->last_sta = 0;

    /* scan the data region; recovered records are written straight into the log */
    pos = p.data_base; data_end = p.data_base;
    bot = fs;
    m->n_slots = 0; m->count = 0; m->seq = 0;

    while (pos + 4u < fs) {
        if (m->hal.read(m->hal.ctx, pos, mb, 2) != 0) return MLA_E_IO;
        if (mb[0] != MLA_DATA_MAGIC0 || mb[1] != MLA_DATA_MAGIC1) { pos++; continue; }
        {
            int found = 0;
            for (length = 1; length <= 65535u; length++) {
                uint32_t end = pos + 2u + length + 2u;
                uint16_t crc = 0xFFFFu;
                uint32_t rd = pos + 2u, remaining = length;
                uint8_t  chunk[64];
                if (end > fs) break;
                /* data CRC by streaming */
                while (remaining) {
                    uint16_t n = remaining < sizeof(chunk) ? (uint16_t)remaining : (uint16_t)sizeof(chunk);
                    if (m->hal.read(m->hal.ctx, rd, chunk, n) != 0) return MLA_E_IO;
                    crc = mla_crc16_ex(crc, chunk, n);
                    rd += n; remaining -= n;
                }
                if (m->hal.read(m->hal.ctx, pos + 2u + length, crcb, 2) != 0) return MLA_E_IO;
                if (crc == mla_get_u16(crcb)) {
                    /* write the log record */
                    if (data_end + 4u <= bot - rs) {
                        mla_log_t r;
                        uint8_t lb[MLA_LOG_REC_SIZE];
                        uint32_t new_bot = bot - rs;
                        r.timestamp = 0; r.offset = pos;
                        r.station = 0; r.region = 0; r.seq = (uint16_t)(m->count & 0xFFFF);
                        r.rec_type = MLA_ENC_RAW; r.length = (uint16_t)length; r.kf_back = 0;
                        r.reserved = 0; r.flags = MLA_FLAG_LIVE;
                        mla_log_build(lb, &r);
                        if (m->hal.write(m->hal.ctx, new_bot, lb, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;
                        bot = new_bot; m->n_slots++; m->count++;
                        m->seq = (uint16_t)((m->seq + 1) & 0xFFFF);
                    }
                    data_end = end; pos = end; found = 1;
                    break;
                }
            }
            if (!found) pos++;
        }
    }

    m->top_ptr = data_end;
    m->bot_ptr = bot;
    m->hal.sync(m->hal.ctx);
    if (out_count) *out_count = m->count;
    return MLA_OK;
}
