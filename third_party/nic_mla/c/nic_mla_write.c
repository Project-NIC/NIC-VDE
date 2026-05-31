/*
 * nic_mla_write.c  —  NIC-MLA WRITE-ONLY implementation
 * MIT  |  ★ Viva La Resistánce ★
 */
#include "nic_mla_write.h"

int mla_w_format_ex(mla_writer_t *w, mla_hal_t hal,
                    uint32_t file_size, uint8_t crc_mode,
                    uint8_t cluster_shift, uint8_t checkpoint_shift,
                    uint8_t keyframe_intv,
                    const uint8_t *table, uint16_t table_len) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  zero[32];
    mla_prefix_t p;
    uint16_t crc, n;
    uint32_t pos;

    if (table == 0) table_len = 0;
    if (table_len > MLA_SCHEMA_MAX) return MLA_E_RANGE;

    w->hal             = hal;
    w->file_size       = file_size;
    w->flags           = crc_mode;
    w->checkpoint_shift= checkpoint_shift;
    w->log_rec_size    = MLA_LOG_REC_SIZE;

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
    p.data_base        = MLA_PREFIX_SIZE;
    p.region_end       = file_size;
    p.checkpoint_shift = checkpoint_shift;
    mla_prefix_build_hdr(hdr, &p);

    /* CRC over [0..509]: header, then the schema table, then zero padding */
    crc = mla_crc16_ex(0xFFFFu, hdr, MLA_PFX_HDR_SIZE);
    if (table_len) crc = mla_crc16_ex(crc, table, table_len);
    memset(zero, 0, sizeof(zero));
    { uint16_t rem = (uint16_t)(510u - MLA_PFX_HDR_SIZE - table_len);
      while (rem) { n = rem < sizeof(zero) ? rem : (uint16_t)sizeof(zero);
                    crc = mla_crc16_ex(crc, zero, n); rem = (uint16_t)(rem - n); } }

    if (w->hal.write(w->hal.ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    if (table_len &&
        w->hal.write(w->hal.ctx, MLA_SCHEMA_OFF, table, table_len) != 0) return MLA_E_IO;
    pos = (uint32_t)MLA_PFX_HDR_SIZE + table_len;
    while (pos < 510u) {
        n = (uint16_t)((510u - pos) < sizeof(zero) ? (510u - pos) : sizeof(zero));
        if (w->hal.write(w->hal.ctx, pos, zero, n) != 0) return MLA_E_IO;
        pos += n;
    }
    { uint8_t crcb[2]; mla_put_u16(crcb, crc);
      if (w->hal.write(w->hal.ctx, 510, crcb, 2) != 0) return MLA_E_IO; }
    w->hal.sync(w->hal.ctx);

    w->top_ptr = MLA_PREFIX_SIZE;
    w->bot_ptr = file_size;
    w->count   = 0;
    w->seq     = 0;
    return MLA_OK;
}

int mla_w_format(mla_writer_t *w, mla_hal_t hal,
                 uint32_t file_size, uint8_t crc_mode,
                 uint8_t cluster_shift, uint8_t checkpoint_shift,
                 uint8_t keyframe_intv) {
    return mla_w_format_ex(w, hal, file_size, crc_mode, cluster_shift,
                           checkpoint_shift, keyframe_intv, 0, 0);
}

/* ── verify the prefix by streaming + load its fields ───────────────────── */
static int read_prefix(mla_writer_t *w, mla_prefix_t *p) {
    uint8_t  buf[32];
    uint16_t crc = 0xFFFFu, n, stored;
    uint32_t pos = 0;

    /* CRC over [0..509] in 32 B chunks */
    while (pos < 510u) {
        n = (uint16_t)((510u - pos) < sizeof(buf) ? (510u - pos) : sizeof(buf));
        if (w->hal.read(w->hal.ctx, pos, buf, n) != 0) return MLA_E_IO;
        crc = mla_crc16_ex(crc, buf, n);
        pos += n;
    }
    if (w->hal.read(w->hal.ctx, 510, buf, 2) != 0) return MLA_E_IO;
    stored = mla_get_u16(buf);
    if (crc != stored) return MLA_E_BADFMT;

    /* header for the fields */
    {
        uint8_t hdr[MLA_PFX_HDR_SIZE];
        if (w->hal.read(w->hal.ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
        if (!mla_prefix_parse_hdr(hdr, p)) return MLA_E_BADFMT;
    }
    return MLA_OK;
}

int mla_w_mount(mla_writer_t *w, mla_hal_t hal) {
    mla_prefix_t p;
    uint32_t fs, lo, hi, mid, max_slots, slot, start_slot, top, count;
    uint16_t rs;
    int32_t  last_seq = -1;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    int rc;

    w->hal = hal;
    rc = read_prefix(w, &p);
    if (rc != MLA_OK) return rc;

    w->file_size        = p.file_size;
    w->flags            = p.flags;
    w->checkpoint_shift = p.checkpoint_shift;
    w->log_rec_size     = p.log_rec_size;
    fs = p.file_size;
    rs = p.log_rec_size;

    /* binary search for the boundary (number of used slots).
     * Respect data_base so a file with an index region (written by the full lib)
     * is handled correctly; the write-only path itself never reserves one. */
    max_slots = (fs - p.data_base) / rs;
    lo = 0; hi = max_slots;
    while (lo < hi) {
        uint16_t i; int all_ff = 1;
        mid = (lo + hi) / 2;
        if (w->hal.read(w->hal.ctx, fs - (mid + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
        for (i = 0; i < rs; i++) if (rec_buf[i] != 0xFF) { all_ff = 0; break; }
        if (all_ff) hi = mid; else lo = mid + 1;
    }

    w->bot_ptr = fs - lo * rs;
    if (lo == 0) {
        w->top_ptr = p.data_base;
        w->count   = 0;
        w->seq     = 0;
        return MLA_OK;
    }

    /* newest checkpoint (from the newest slot backward) */
    start_slot = 0; count = 0; top = MLA_PREFIX_SIZE;
    {
        uint32_t s;
        for (s = lo; s-- > 0; ) {
            if (w->hal.read(w->hal.ctx, fs - (s + 1) * rs, rec_buf, rs) != 0) return MLA_E_IO;
            if (mla_log_parse(rec_buf, &rec) && mla_log_is_live(&rec) && mla_log_is_checkpoint(&rec)) {
                count    = ((uint32_t)rec.station << 16) | rec.region;
                top      = rec.offset;
                last_seq = rec.seq;
                start_slot = s + 1;
                break;
            }
            if (s == 0) break;
        }
    }

    /* forward scan of the tail */
    for (slot = start_slot; slot < lo; slot++) {
        uint32_t addr = fs - (slot + 1) * rs;
        int is_newest = (slot == lo - 1);
        if (w->hal.read(w->hal.ctx, addr, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec)) continue;     /* torn lock */
        last_seq = rec.seq;
        if (mla_log_is_checkpoint(&rec)) {
            count = ((uint32_t)rec.station << 16) | rec.region;
            top   = rec.offset;
            continue;
        }
        if (mla_log_is_abandoned(&rec)) {
            if (is_newest) top = rec.offset;
            continue;
        }
        /* LIVE data record */
        if (is_newest) {
            uint8_t magic[2];
            if (w->hal.read(w->hal.ctx, rec.offset, magic, 2) != 0) return MLA_E_IO;
            if (magic[0] != MLA_DATA_MAGIC0 || magic[1] != MLA_DATA_MAGIC1) {
                uint8_t ab = MLA_FLAG_ABANDONED;
                if (w->hal.write(w->hal.ctx, addr + 20, &ab, 1) != 0) return MLA_E_IO;
                w->hal.sync(w->hal.ctx);
                top = rec.offset;
                continue;
            }
        }
        top = mla_log_block_end(&rec);
        count++;
    }

    w->top_ptr = top;
    w->count   = count;
    w->seq     = (last_seq >= 0) ? (uint16_t)((last_seq + 1) & 0xFFFF) : 0;
    return MLA_OK;
}

/* ── checkpoint ─────────────────────────────────────────────────────────── */
static int write_checkpoint(mla_writer_t *w, uint32_t timestamp) {
    uint32_t new_bot = w->bot_ptr - w->log_rec_size;
    uint8_t  buf[MLA_LOG_REC_SIZE];
    mla_log_t cp;
    if (new_bot <= w->top_ptr) return MLA_OK; /* no room — optional */
    cp.timestamp = timestamp;
    cp.offset    = w->top_ptr;
    cp.station   = (uint16_t)((w->count >> 16) & 0xFFFF);
    cp.region   = (uint16_t)(w->count & 0xFFFF);
    cp.seq       = w->seq;
    cp.rec_type  = MLA_REC_CHECKPOINT;
    cp.length    = 0;
    cp.kf_back   = 0;
    cp.reserved  = 0;
    cp.flags     = MLA_FLAG_LIVE;
    mla_log_build(buf, &cp);
    if (w->hal.write(w->hal.ctx, new_bot, buf, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;
    w->bot_ptr = new_bot;
    return MLA_OK;
}

int mla_w_append(mla_writer_t *w, uint32_t timestamp,
                 uint16_t station, uint16_t region,
                 const uint8_t *data, uint16_t len,
                 uint8_t rec_type, uint16_t kf_back) {
    uint32_t block_sz, new_bot;
    uint8_t  lock[MLA_LOG_REC_SIZE];
    mla_log_t r;
    uint16_t crc;
    uint8_t  mb[2], crcb[2];

    if (len < 1) return MLA_E_RANGE;       /* 1..65535 (uint16 upper bound is fine) */
    block_sz = 2u + len + 2u;
    if (w->top_ptr + block_sz > w->bot_ptr - w->log_rec_size) return MLA_E_FULL;

    new_bot = w->bot_ptr - w->log_rec_size;

    /* Step 1 — lock */
    r.timestamp = timestamp; r.offset = w->top_ptr;
    r.station = station; r.region = region;
    r.seq = w->seq; r.rec_type = rec_type; r.length = len; r.kf_back = kf_back;
    r.reserved = 0; r.flags = MLA_FLAG_LIVE;
    mla_log_build(lock, &r);
    if (w->hal.write(w->hal.ctx, new_bot, lock, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;

    /* Step 2 — data: MAGIC, payload (directly), CRC — without a large buffer */
    mb[0] = MLA_DATA_MAGIC0; mb[1] = MLA_DATA_MAGIC1;
    if (w->hal.write(w->hal.ctx, w->top_ptr, mb, 2) != 0) return MLA_E_IO;
    if (w->hal.write(w->hal.ctx, w->top_ptr + 2, data, len) != 0) return MLA_E_IO;
    crc = ((w->flags & 0x3) >= MLA_CRC_DATA) ? mla_crc16(data, len) : 0xFFFFu;
    mla_put_u16(crcb, crc);
    if (w->hal.write(w->hal.ctx, w->top_ptr + 2u + len, crcb, 2) != 0) return MLA_E_IO;

    /* Step 3 — RAM */
    w->top_ptr += block_sz;
    w->bot_ptr  = new_bot;
    w->count   += 1;
    w->seq      = (uint16_t)((w->seq + 1) & 0xFFFF);

    /* Step 4 — checkpoint */
    if (w->checkpoint_shift && (w->count % (1u << w->checkpoint_shift)) == 0)
        return write_checkpoint(w, timestamp);
    return MLA_OK;
}
