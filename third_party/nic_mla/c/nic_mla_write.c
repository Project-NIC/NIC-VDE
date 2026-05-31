/*
 * nic_mla_write.c  —  NIC-MLA WRITE-ONLY implementation
 * MIT  |  ★ Viva La Resistánce ★
 */
#include "nic_mla_write.h"

/* Write the prefix (header + schema + station + zero padding + CRC), streaming
 * so the chip needs no large buffer. The prefix spans `psize` bytes (a 512 B
 * multiple) with the CRC in its last 2 bytes. */
static int write_prefix(mla_writer_t *w, const mla_prefix_t *p, uint32_t psize,
                        const uint8_t *schema, uint16_t schema_len,
                        const uint8_t *station, uint16_t station_len) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  zero[32];
    uint16_t crc, n;
    uint32_t pos, body = psize - 2u;

    mla_prefix_build_hdr(hdr, p);
    memset(zero, 0, sizeof(zero));

    /* CRC over [0 .. body): header, schema, station, then zero padding */
    crc = mla_crc16_ex(0xFFFFu, hdr, MLA_PFX_HDR_SIZE);
    if (schema_len)  crc = mla_crc16_ex(crc, schema, schema_len);
    if (station_len) crc = mla_crc16_ex(crc, station, station_len);
    { uint32_t rem = body - MLA_PFX_HDR_SIZE - schema_len - station_len;
      while (rem) { n = (uint16_t)(rem < sizeof(zero) ? rem : sizeof(zero));
                    crc = mla_crc16_ex(crc, zero, n); rem -= n; } }

    /* Write: header, schema, station */
    if (w->hal.write(w->hal.ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    pos = MLA_PFX_HDR_SIZE;
    if (schema_len) {
        if (w->hal.write(w->hal.ctx, pos, schema, schema_len) != 0) return MLA_E_IO;
        pos += schema_len;
    }
    if (station_len) {
        if (w->hal.write(w->hal.ctx, pos, station, station_len) != 0) return MLA_E_IO;
        pos += station_len;
    }
    /* Zero padding up to body */
    while (pos < body) {
        n = (uint16_t)((body - pos) < sizeof(zero) ? (body - pos) : sizeof(zero));
        if (w->hal.write(w->hal.ctx, pos, zero, n) != 0) return MLA_E_IO;
        pos += n;
    }
    { uint8_t crcb[2]; mla_put_u16(crcb, crc);
      if (w->hal.write(w->hal.ctx, body, crcb, 2) != 0) return MLA_E_IO; }
    w->hal.sync(w->hal.ctx);
    return MLA_OK;
}

int mla_w_format_ex(mla_writer_t *w, mla_hal_t hal,
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

    w->hal          = hal;
    w->file_size    = file_size;
    w->flags        = crc_mode;
    w->log_rec_size = MLA_LOG_REC_SIZE;
    w->data_base    = psize;

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

    rc = write_prefix(w, &p, psize, schema, schema_len, station, station_len);
    if (rc != MLA_OK) return rc;

    w->top_ptr = psize;
    w->bot_ptr = file_size;
    w->count   = 0;
    return MLA_OK;
}

int mla_w_format(mla_writer_t *w, mla_hal_t hal,
                 uint32_t file_size, uint8_t crc_mode,
                 uint8_t cluster_shift, uint8_t keyframe_intv) {
    return mla_w_format_ex(w, hal, file_size, crc_mode, cluster_shift,
                           keyframe_intv, 0, 0, 0, 0);
}

/* ── verify the prefix by streaming + load its fields ───────────────────── */
/* The prefix size is self-describing from the table headers; we first read the
 * header sector, derive the size, then CRC the whole prefix. */
static int read_prefix(mla_writer_t *w, mla_prefix_t *p, uint32_t *psize_out) {
    uint8_t  hdr[MLA_PFX_HDR_SIZE];
    uint8_t  buf[32];
    uint16_t crc = 0xFFFFu, n, stored, schema_len = 0, station_len = 0;
    uint32_t pos, body, psize;

    if (w->hal.read(w->hal.ctx, 0, hdr, MLA_PFX_HDR_SIZE) != 0) return MLA_E_IO;
    if (!mla_prefix_parse_hdr(hdr, p)) return MLA_E_BADFMT;

    /* Table sizes come from the header bytes just past the structured header.
     * Read enough to see both table headers (schema: 3 B, station: 2 B). */
    {
        uint8_t th[5];
        if (w->hal.read(w->hal.ctx, MLA_SCHEMA_OFF, th, 5) != 0) return MLA_E_IO;
        if (th[0] == MLA_SCHEMA_VER) schema_len = mla_schema_size(th[1], th[2]);
        {
            uint8_t sh[2];
            if (w->hal.read(w->hal.ctx, MLA_SCHEMA_OFF + schema_len, sh, 2) != 0) return MLA_E_IO;
            if (sh[0] == MLA_STATION_VER) station_len = mla_station_size(sh[1]);
        }
    }
    psize = mla_prefix_size(schema_len, station_len);
    if (psize == 0) return MLA_E_BADFMT;
    body = psize - 2u;

    /* CRC over [0 .. body) */
    pos = 0;
    while (pos < body) {
        n = (uint16_t)((body - pos) < sizeof(buf) ? (body - pos) : sizeof(buf));
        if (w->hal.read(w->hal.ctx, pos, buf, n) != 0) return MLA_E_IO;
        crc = mla_crc16_ex(crc, buf, n);
        pos += n;
    }
    if (w->hal.read(w->hal.ctx, body, buf, 2) != 0) return MLA_E_IO;
    stored = mla_get_u16(buf);
    if (crc != stored) return MLA_E_BADFMT;

    if (psize_out) *psize_out = psize;
    return MLA_OK;
}

int mla_w_mount(mla_writer_t *w, mla_hal_t hal) {
    mla_prefix_t p;
    uint32_t fs, lo, hi, mid, max_slots, slot, top, count, psize;
    uint16_t rs;
    uint8_t  rec_buf[MLA_LOG_REC_SIZE];
    mla_log_t rec;
    int rc;

    w->hal = hal;
    rc = read_prefix(w, &p, &psize);
    if (rc != MLA_OK) return rc;

    w->file_size    = p.file_size;
    w->flags        = p.flags;
    w->log_rec_size = p.log_rec_size;
    w->data_base    = p.data_base;
    fs = p.file_size;
    rs = p.log_rec_size;

    /* binary search for the log boundary (number of used slots) */
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
        w->top_ptr = p.data_base; w->count = 0;
        return MLA_OK;
    }

    /* forward scan: end of the newest valid record's data = top_ptr */
    top = p.data_base; count = 0;
    for (slot = 0; slot < lo; slot++) {
        uint32_t addr = fs - (slot + 1) * rs;
        if (w->hal.read(w->hal.ctx, addr, rec_buf, rs) != 0) return MLA_E_IO;
        if (!mla_log_parse(rec_buf, &rec)) continue;     /* burned/abandoned slot */
        if (slot == lo - 1) {                            /* newest — check data MAGIC */
            uint8_t magic[2];
            if (w->hal.read(w->hal.ctx, rec.offset, magic, 2) != 0) return MLA_E_IO;
            if (magic[0] != MLA_DATA_MAGIC0 || magic[1] != MLA_DATA_MAGIC1) {
                uint8_t z[MLA_LOG_REC_SIZE]; memset(z, 0, rs);
                if (w->hal.write(w->hal.ctx, addr, z, rs) != 0) return MLA_E_IO;
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
    return MLA_OK;
}

int mla_w_append(mla_writer_t *w, uint32_t timestamp, uint8_t station,
                 const uint8_t *data, uint16_t len,
                 uint8_t rec_type, uint8_t kf_back) {
    uint32_t block_sz, new_bot;
    uint8_t  lock[MLA_LOG_REC_SIZE];
    mla_log_t r;
    uint16_t crc;
    uint8_t  mb[2], crcb[2];

    if (len < 1) return MLA_E_RANGE;
    block_sz = 2u + len + 2u;
    new_bot  = w->bot_ptr - w->log_rec_size;
    if (w->top_ptr + block_sz > new_bot) return MLA_E_FULL;

    /* Step 1 — lock */
    r.offset = w->top_ptr; r.timestamp = timestamp; r.length = len;
    r.rec_type = rec_type; r.kf_back = kf_back; r.station = station; r.reserved = 0;
    mla_log_build(lock, &r);
    if (w->hal.write(w->hal.ctx, new_bot, lock, MLA_LOG_REC_SIZE) != 0) return MLA_E_IO;

    /* Step 2 — data: MAGIC, payload, CRC (no large buffer) */
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
    return MLA_OK;
}
