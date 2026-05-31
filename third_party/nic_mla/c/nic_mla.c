/*
 * nic_mla.c  —  NIC-MLA COMPLETE implementation (Python port)
 * MIT  |  ★ Viva La Resistánce ★
 */
#include "nic_mla.h"

/* ── prefix: write by streaming (header + tables + padding + CRC) ────────── */
static int write_prefix(mla_hal_t *hal, const mla_prefix_t *p, uint32_t psize,
                        const uint8_t *schema, uint16_t schema_len,
                        const uint8_t *station, uint16_t station_len) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  zero[32];
    uint16_t crc, n;
    uint32_t pos, body = psize - 2u;

    mla_prefix_build_hdr(hdr, p);
    memset(zero, 0, sizeof(zero));

    crc = mla_crc16_ex(0xFFFFu, hdr, MLA_PFX_HDR_SIZE);
    if (schema_len)  crc = mla_crc16_ex(crc, schema, schema_len);
    if (station_len) crc = mla_crc16_ex(crc, station, station_len);
    { uint32_t rem = body - MLA_PFX_HDR_SIZE - schema_len - station_len;
      while (rem) { n = (uint16_t)(rem < sizeof(zero) ? rem : sizeof(zero));
                    crc = mla_crc16_ex(crc, zero, n); rem -= n; } }

    if (hal->write(hal->ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    pos = MLA_PFX_HDR_SIZE;
    if (schema_len) {
        if (hal->write(hal->ctx, pos, schema, schema_len) != 0) return MLA_E_IO;
        pos += schema_len;
    }
    if (station_len) {
        if (hal->write(hal->ctx, pos, station, station_len) != 0) return MLA_E_IO;
        pos += station_len;
    }
    while (pos < body) {
        n = (uint16_t)((body - pos) < sizeof(zero) ? (body - pos) : sizeof(zero));
        if (hal->write(hal->ctx, pos, zero, n) != 0) return MLA_E_IO;
        pos += n;
    }
    { uint8_t crcb[2]; mla_put_u16(crcb, crc);
      if (hal->write(hal->ctx, body, crcb, 2) != 0) return MLA_E_IO; }
    hal->sync(hal->ctx);
    return MLA_OK;
}

static int read_prefix(mla_hal_t *hal, mla_prefix_t *p, uint32_t *psize_out) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  buf[32];
    uint16_t crc = 0xFFFFu, n, stored, schema_len = 0, station_len = 0;
    uint32_t pos, body, psize;

    if (hal->read(hal->ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    if (!mla_prefix_parse_hdr(hdr, p)) return MLA_E_BADFMT;

    {
        uint8_t th[5];
        if (hal->read(hal->ctx, MLA_SCHEMA_OFF, th, 5) != 0) return MLA_E_IO;
        if (th[0] == MLA_SCHEMA_VER) schema_len = mla_schema_size(th[1], th[2]);
        {
            uint8_t sh[2];
            if (hal->read(hal->ctx, MLA_SCHEMA_OFF + schema_len, sh, 2) != 0) return MLA_E_IO;
            if (sh[0] == MLA_STATION_VER) station_len = mla_station_size(sh[1]);
        }
    }
    psize = mla_prefix_size(schema_len, station_len);
    if (psize == 0) return MLA_E_BADFMT;
    body = psize - 2u;

    pos = 0;
    while (pos < body) {
        n = (uint16_t)((body - pos) < sizeof(buf) ? (body - pos) : sizeof(buf));
        if (hal->read(hal->ctx, pos, buf, n) != 0) return MLA_E_IO;
        crc = mla_crc16_ex(crc, buf, n);
        pos += n;
    }
    if (hal->read(hal->ctx, body, buf, 2) != 0) return MLA_E_IO;
    stored = mla_get_u16(buf);
    if (crc != stored) return MLA_E_BADFMT;

    if (psize_out) *psize_out = psize;
    return MLA_OK;
}

/* ── format ─────────────────────────────────────────────────────────────── */
int mla_format_ex(mla_t *m, mla_hal_t hal,
                  uint32_t file_size, uint8_t crc_mode,
                  uint8_t cluster_shift, uint8_t keyframe_intv,
                  const uint8_t *schema, uint16_t schema_len,
                  const uint8_t *station, uint16_t station_len) {
    mla_prefix_t p;
    uint32_t psize;
    int rc;

    if (schema == 0)  schema_len = 0;
    if (station == 0) station_len = 0;
    psize = mla_prefix_size(schema_len, station_len);
    if (psize == 0) return MLA_E_RANGE;

    m->hal = hal;
    p.version       = MLA_VERSION;
    p.cluster_shift = cluster_shift;
    p.log_rec_size  = MLA_LOG_REC_SIZE;
    p.flags         = crc_mode;
    p.file_size     = file_size;
    p.container_kind= 0;
    p.file_seq      = 0;
    p.keyframe_intv = keyframe_intv;
    p.enc_caps      = 0;
    p.data_base     = psize;
    p.region_end    = file_size;

    rc = write_prefix(&m->hal, &p, psize, schema, schema_len, station, station_len);
    if (rc != MLA_OK) return rc;

    m->file_size    = file_size;
    m->flags        = crc_mode;
    m->log_rec_size = MLA_LOG_REC_SIZE;
    m->data_base    = psize;
    m->top_ptr      = psize;
    m->bot_ptr      = file_size;
    m->n_slots      = 0;
    m->count        = 0;
    return MLA_OK;
}

int mla_format(mla_t *m, mla_hal_t hal,
               uint32_t file_size, uint8_t crc_mode,
               uint8_t cluster_shift, uint8_t keyframe_intv) {
    return mla_format_ex(m, hal, file_size, crc_mode, cluster_shift,
                         keyframe_intv, 0, 0, 0, 0);
}

/* ── mount ──────────────────────────────────────────────────────────────── */
int mla_mount(mla_t *m, mla_hal_t hal) {
    mla_prefix_t p;
    uint32_t fs, lo, hi, mid, max_slots, slot, top, count, psize;
    uint16_t rs;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    int rc;

    m->hal = hal;
    rc = read_prefix(&m->hal, &p, &psize);
    if (rc != MLA_OK) return rc;

    m->file_size    = p.file_size;
    m->flags        = p.flags;
    m->log_rec_size = p.log_rec_size;
    m->data_base    = p.data_base;
    fs = p.file_size;
    rs = p.log_rec_size;

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
        m->top_ptr = p.data_base; m->count = 0;
        return MLA_OK;
    }

    top = p.data_base; count = 0;
    for (slot = 0; slot < lo; slot++) {
        uint32_t addr = fs - (slot + 1) * rs;
        if (m->hal.read(m->hal.ctx, addr, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec)) continue;
        if (slot == lo - 1) {
            uint8_t magic[2];
            if (m->hal.read(m->hal.ctx, rec.offset, magic, 2) != 0) return MLA_E_IO;
            if (magic[0] != MLA_DATA_MAGIC0 || magic[1] != MLA_DATA_MAGIC1) {
                uint8_t z[MLA_LOG_REC_SIZE]; memset(z, 0, rs);
                if (m->hal.write(m->hal.ctx, addr, z, rs) != 0) return MLA_E_IO;
                m->hal.sync(m->hal.ctx);
                top = rec.offset;
                continue;
            }
        }
        top = mla_log_block_end(&rec);
        count++;
    }
    m->top_ptr = top;
    m->count   = count;
    return MLA_OK;
}

/* ── append ─────────────────────────────────────────────────────────────── */
int mla_append(mla_t *m, uint32_t timestamp, uint8_t station,
               const uint8_t *data, uint16_t len,
               uint8_t rec_type, uint8_t kf_back) {
    uint32_t block_sz, new_bot;
    uint8_t  lock[MLA_LOG_REC_SIZE], mb[2], crcb[2];
    mla_log_t r;
    uint16_t crc;

    if (len < 1) return MLA_E_RANGE;
    block_sz = 2u + len + 2u;
    new_bot = m->bot_ptr - m->log_rec_size;
    if (m->top_ptr + block_sz > new_bot) return MLA_E_FULL;

    r.offset = m->top_ptr; r.timestamp = timestamp; r.length = len;
    r.rec_type = rec_type; r.kf_back = kf_back; r.station = station; r.reserved = 0;
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
        if (!mla_log_parse(rec_buf, &rec)) continue;
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
    if (f->has_rec_type && r->rec_type != f->rec_type) return 0;
    if (f->has_enc && (r->rec_type & 0x0F) != f->enc) return 0;
    return 1;
}

int mla_foreach(mla_t *m, const mla_filter_t *filter, mla_iter_cb cb, void *user) {
    uint32_t slot, matched = 0, fs = m->file_size;
    uint16_t rs = m->log_rec_size;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    for (slot = 0; slot < m->n_slots; slot++) {
        if (m->hal.read(m->hal.ctx, fs - (slot + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec)) continue;
        if (!filter_match(filter, &rec)) continue;
        matched++;
        if (cb && cb(user, m, &rec)) break;
    }
    return (int)matched;
}

/* ── emergency recovery ─────────────────────────────────────────────────── */
int mla_recover(mla_t *m, mla_hal_t hal, uint32_t *out_count) {
    mla_prefix_t p;
    uint32_t fs, pos, data_end, length, bot, psize;
    uint16_t rs;
    uint8_t  mb[2], crcb[2];
    int rc;

    m->hal = hal;
    rc = read_prefix(&m->hal, &p, &psize);
    if (rc != MLA_OK) return rc;
    if ((p.flags & 0x3) < MLA_CRC_DATA) return MLA_E_NOSUP;

    fs = p.file_size; rs = p.log_rec_size;
    m->file_size = fs; m->flags = p.flags;
    m->log_rec_size = rs; m->data_base = p.data_base;

    pos = p.data_base; data_end = p.data_base; bot = fs;
    m->n_slots = 0; m->count = 0;

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
                while (remaining) {
                    uint16_t n = remaining < sizeof(chunk) ? (uint16_t)remaining : (uint16_t)sizeof(chunk);
                    if (m->hal.read(m->hal.ctx, rd, chunk, n) != 0) return MLA_E_IO;
                    crc = mla_crc16_ex(crc, chunk, n);
                    rd += n; remaining -= n;
                }
                if (m->hal.read(m->hal.ctx, pos + 2u + length, crcb, 2) != 0) return MLA_E_IO;
                if (crc == mla_get_u16(crcb)) {
                    if (data_end + 4u <= bot - rs) {
                        mla_log_t r;
                        uint8_t lb[MLA_LOG_REC_SIZE];
                        uint32_t new_bot = bot - rs;
                        r.offset = pos; r.timestamp = 0; r.length = (uint16_t)length;
                        r.rec_type = MLA_ENC_RAW; r.kf_back = 0; r.station = 0; r.reserved = 0;
                        mla_log_build(lb, &r);
                        if (m->hal.write(m->hal.ctx, new_bot, lb, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;
                        bot = new_bot; m->n_slots++; m->count++;
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
