/*
 * nic_mla_test.c  —  Tests for both C libraries against a RAM/file HAL (v1.1).
 *
 * Build & run:
 *     cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c \
 *        hal/nic_mla_hal_posix.c -o mlatest
 *     ./mlatest [cross_compat_output_path]
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "nic_mla.h"
#include "nic_mla_write.h"
#include "hal/nic_mla_hal_posix.h"

static int g_pass = 0, g_fail = 0;
static void check(const char *name, int cond) {
    if (cond) { g_pass++; printf("  PASS  %s\n", name); }
    else      { g_fail++; printf("  FAIL  %s\n", name); }
}
static void section(const char *t) { printf("\n-- %s\n", t); }

/* ── RAM HAL ─────────────────────────────────────────────────────────────── */
typedef struct { uint8_t *mem; uint32_t size; } ram_t;
static int ram_read(void *ctx, uint32_t off, void *buf, uint16_t n) {
    ram_t *r = (ram_t*)ctx; if ((uint32_t)off + n > r->size) return -1;
    memcpy(buf, r->mem + off, n); return 0;
}
static int ram_write(void *ctx, uint32_t off, const void *buf, uint16_t n) {
    ram_t *r = (ram_t*)ctx; if ((uint32_t)off + n > r->size) return -1;
    memcpy(r->mem + off, buf, n); return 0;
}
static void ram_sync(void *ctx) { (void)ctx; }
static uint32_t ram_size(void *ctx) { return ((ram_t*)ctx)->size; }

static mla_hal_t ram_hal(ram_t *r, uint32_t size) {
    mla_hal_t h;
    r->mem = (uint8_t*)malloc(size); r->size = size;
    memset(r->mem, 0xFF, size);
    h.read = ram_read; h.write = ram_write; h.sync = ram_sync; h.size = ram_size; h.ctx = r;
    return h;
}

static int count_cb(void *user, mla_t *m, const mla_log_t *rec) {
    (void)m; (void)rec; (*(int*)user)++; return 0;
}

/* An example schema + station table, byte-identical to what tools/mla_schema.py
 * would emit (one datetime log field, two data fields; two stations). */
static const uint8_t SCHEMA[] = {
    0x01, 0x01, 0x02,                                  /* ver, n_log=1, n_data=2 */
    /* datetime: width4 unit14(unix_s) exp0 flags0 off0 + "datetime" */
    0x04,0x0E,0x00,0x00,0x00,0x00, 'd','a','t','e','t','i','m','e',
    /* temp: width2 unit1(degC) exp-1 flags1(signed) off0 + "temp\0\0\0\0" */
    0x02,0x01,0xFF,0x01,0x00,0x00, 't','e','m','p',0,0,0,0,
    /* hum:  width2 unit4(pct) exp-1 flags0 off0 + "hum\0\0\0\0\0" */
    0x02,0x04,0xFF,0x00,0x00,0x00, 'h','u','m',0,0,0,0,0
};
static const uint8_t STATION[] = {
    0x53, 0x02,                                        /* tag, n=2 */
    55,0, 0xA8,0x61, 0xFF,0xFF,                         /* region 55, number 25000 */
    55,0, 0xA9,0x61, 0xFF,0xFF                          /* region 55, number 25001 */
};

/* ─────────────────────────────────────────────────────────────────────────── */
int main(int argc, char **argv) {
    printf("NIC-MLA C Test Suite (v1.1)\n===========================\n");

    /* 1. CRC vector + log-record round-trip (v1.1: subsec + packed flags byte) */
    section("CRC-16 + log record");
    check("CRC vector 0x29B1", mla_crc16((const uint8_t*)"123456789", 9) == 0x29B1);
    {
        mla_log_t r, r2; uint8_t buf[MLA_LOG_REC_SIZE];
        r.offset=1234; r.timestamp=1700000000u; r.subsec=512; r.length=28;
        r.flags=mla_flags_make(1, 3); r.station=7;
        mla_log_build(buf, &r);
        check("log record is 16 B", MLA_LOG_REC_SIZE == 16);
        check("log parse CRC ok", mla_log_parse(buf, &r2) == 1);
        check("log round-trip", r2.offset==1234 && r2.timestamp==1700000000u
              && r2.subsec==512 && r2.length==28 && r2.station==7
              && mla_flags_compressed(r2.flags)==1 && mla_flags_kf_back(r2.flags)==3);
        /* kf_back uses the full 7 bits and does not collide with the compressed bit */
        { mla_log_t rk, rk2; uint8_t kb[MLA_LOG_REC_SIZE];
          rk.offset=0; rk.timestamp=0; rk.subsec=0; rk.length=1;
          rk.flags=mla_flags_make(0, 127); rk.station=0;
          mla_log_build(kb, &rk); mla_log_parse(kb, &rk2);
          check("kf_back 127, not compressed",
                mla_flags_kf_back(rk2.flags)==127 && mla_flags_compressed(rk2.flags)==0); }
        { uint8_t z[MLA_LOG_REC_SIZE]; mla_log_t rz; memset(z,0,sizeof(z));
          check("zeroed slot invalid", mla_log_parse(z, &rz) == 0); }

        /* Byte-exact cross-format vector: these 16 bytes MUST equal what the
         * Python reference emits for the same record (see c/cross_check.py). */
        { static const uint8_t EXPECT[MLA_LOG_REC_SIZE] = {
              0xD2,0x04,0x00,0x00,  0x00,0xF1,0x53,0x65,  0x00,0x02,  0x1C,0x00,
              0x83, 0x07,  0x00,0x00 };
          /* fix up the CRC field from our own build (last 2 bytes) and compare
             the 14 B body byte-for-byte */
          check("log record body matches Python byte-exact",
                memcmp(buf, EXPECT, 14) == 0); }
    }

    /* 2. Full library — round-trip + query */
    section("Full library: format/append/mount/read/query");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 64 * 1024);
        mla_t m; const int N = 200; int i, ok;
        mla_format(&m, hal, 64 * 1024, MLA_CRC_FULL, 12, 8);
        for (i = 0; i < N; i++) {
            uint8_t d[3]; d[0]=d[1]=d[2]=(uint8_t)i;
            mla_append(&m, (uint32_t)(1500000 + i), 0, (uint8_t)(1 + i % 7), d, 3, 0, 0);
        }
        check("after writing count=200", m.count == (uint32_t)N);
        check("no extra slots (no checkpoints)", m.n_slots == (uint32_t)N);

        mla_t m2;
        check("mount OK", mla_mount(&m2, hal) == MLA_OK);
        check("after mount count=200", m2.count == (uint32_t)N);

        ok = 1;
        { int idx[5] = {0, 63, 64, 99, 199}; int k;
          for (k = 0; k < 5; k++) {
              mla_log_t rec; uint8_t buf[8]; uint16_t len;
              if (mla_read_record(&m2, idx[k], &rec, buf, sizeof(buf), &len) != MLA_OK) ok = 0;
              else if (len != 3 || buf[0] != (uint8_t)idx[k] ||
                       rec.timestamp != (uint32_t)(1500000 + idx[k])) ok = 0;
          }
        }
        check("read_record data matches", ok);

        { int cnt = 0; mla_foreach(&m2, NULL, count_cb, &cnt);
          check("foreach returns 200", cnt == N); }
        { int cnt = 0; mla_filter_t f; memset(&f, 0, sizeof(f));
          f.has_station = 1; f.station = 4;
          mla_foreach(&m2, &f, count_cb, &cnt);
          /* station index = 1 + i%7 == 4 → i=3,10,...,199 → 29 */
          check("query station index=4", cnt == 29); }
        { int cnt = 0; mla_filter_t f; memset(&f, 0, sizeof(f));
          f.has_time = 1; f.time_from = 1500120; f.time_to = 1500160;
          mla_foreach(&m2, &f, count_cb, &cnt);
          check("query time window", cnt == 41); }
        free(r.mem);
    }

    /* 3. Torn data write — mount zeroes the lock, top_ptr rewinds */
    section("Torn data write — recovery on mount");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 8 * 1024);
        mla_t m; uint8_t good[4] = {0x11,0x22,0x33,0x44};
        uint32_t torn_off; mla_log_t lk; uint8_t lb[MLA_LOG_REC_SIZE];
        mla_format(&m, hal, 8 * 1024, MLA_CRC_FULL, 12, 8);
        mla_append(&m, 0, 0, 1, good, 4, 0, 0);
        torn_off = m.top_ptr;
        lk.offset=torn_off; lk.timestamp=1; lk.subsec=0; lk.length=20;
        lk.flags=0; lk.station=2;
        mla_log_build(lb, &lk);
        hal.write(hal.ctx, m.bot_ptr - MLA_LOG_REC_SIZE, lb, MLA_LOG_REC_SIZE);

        mla_t m2; mla_mount(&m2, hal);
        check("count=1 (torn lock abandoned)", m2.count == 1);
        check("top_ptr rewound to torn_off", m2.top_ptr == torn_off);
        { mla_log_t rec; uint8_t buf[8]; uint16_t len;
          check("good record readable",
                mla_read_record(&m2, 0, &rec, buf, sizeof(buf), &len) == MLA_OK
                && len == 4 && buf[0] == 0x11); }
        free(r.mem);
    }

    /* 4. recover() */
    section("Emergency recovery — recover()");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 16 * 1024);
        mla_t m; int i; uint32_t nrec = 0;
        mla_format(&m, hal, 16 * 1024, MLA_CRC_FULL, 12, 8);
        for (i = 0; i < 5; i++) {
            uint8_t d[16]; memset(d, (uint8_t)(i*13), 8 + i);
            mla_append(&m, 0, 0, 1, d, (uint16_t)(8 + i), 0, 0);
        }
        { uint8_t z[256]; uint32_t pos; memset(z, 0, sizeof(z));
          for (pos = m.bot_ptr; pos < 16u*1024u; pos += sizeof(z))
              hal.write(hal.ctx, pos, z, (uint16_t)((16u*1024u - pos < sizeof(z)) ? (16u*1024u - pos) : sizeof(z))); }
        mla_t m2; mla_recover(&m2, hal, &nrec);
        check("recover found 5 records", nrec == 5);
        { int cnt = 0; mla_foreach(&m2, NULL, count_cb, &cnt);
          check("5 readable after recovery", cnt == 5); }
        free(r.mem);
    }

    /* 5. WRITE-ONLY library — write + mount resume + cross-compat file */
    section("Write-only: format/append/mount (+ file for Python)");
    {
        const char *path = (argc > 1) ? argv[1] : "/tmp/mla_c_out.bin";
        mla_posix_file_t s; mla_hal_t hal;
        mla_writer_t w; int i;
        mla_posix_create(&s, path, 64 * 1024);
        hal = mla_posix_hal(&s);
        /* Embed the schema + station tables so Python can decode end-to-end. */
        mla_w_format_ex(&w, hal, 64 * 1024, MLA_CRC_FULL, 12, 8,
                        SCHEMA, (uint16_t)sizeof(SCHEMA),
                        STATION, (uint16_t)sizeof(STATION));
        for (i = 0; i < 50; i++) {
            /* temp(2) + hum(2) = 4 B payload */
            uint8_t d[4]; mla_put_u16(d, (uint16_t)(200 + i));
            mla_put_u16(d + 2, (uint16_t)(500 + i));
            /* subsec = two opaque glue-owned bytes; here just incremented to exercise the field */
            mla_w_append(&w, (uint32_t)(1700000000u + i), (uint16_t)i,
                         (uint8_t)(1 + i % 2), d, 4, 0, 0);
        }
        check("write-only: count=50", w.count == 50);

        mla_writer_t w2;
        check("write-only mount OK", mla_w_mount(&w2, hal) == MLA_OK);
        check("write-only mount count=50", w2.count == 50);
        { uint8_t d[4] = {0x10,0x20,0x30,0x40};
          mla_w_append(&w2, 1700000099u, 0, 2, d, 4, 0, 0); }
        check("write-only append after mount OK", w2.count == 51);

        mla_t mf;
        mla_mount(&mf, hal);
        check("full library reads the write-only file (count=51)", mf.count == 51);

        mla_posix_close(&s);
        printf("  (cross-compat file written: %s)\n", path);
    }

    /* 6. Extended prefix (schema + station tables) round-trips through mount */
    section("Extended prefix: schema + station tables");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 64 * 1024);
        mla_t m, m2; int i;
        check("format_ex OK",
              mla_format_ex(&m, hal, 64 * 1024, MLA_CRC_FULL, 12, 8,
                            SCHEMA, (uint16_t)sizeof(SCHEMA),
                            STATION, (uint16_t)sizeof(STATION)) == MLA_OK);
        check("prefix still 512 B (tables fit)", m.data_base == 512u);
        for (i = 0; i < 10; i++) {
            uint8_t d[4]; mla_put_u16(d, (uint16_t)i); mla_put_u16(d+2, (uint16_t)(i*2));
            mla_append(&m, (uint32_t)(1700000000u + i), 0, (uint8_t)(1 + i % 2), d, 4, 0, 0);
        }
        check("mount after tables OK", mla_mount(&m2, hal) == MLA_OK);
        check("count=10 after mount", m2.count == 10);
        { mla_log_t rec; uint8_t buf[8]; uint16_t len;
          check("record readable with tables present",
                mla_read_record(&m2, 5, &rec, buf, sizeof(buf), &len) == MLA_OK
                && len == 4 && rec.station == (uint8_t)(1 + 5 % 2)); }
        free(r.mem);
    }

    /* 7. v1.1 fields — subsec / compressed / kf_back survive a mount + filter */
    section("v1.1 record fields: subsec, compressed, kf_back");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 16 * 1024);
        mla_t m, m2; int i;
        mla_format(&m, hal, 16 * 1024, MLA_CRC_FULL, 12, 64);
        for (i = 0; i < 8; i++) {
            uint8_t d[4]; mla_put_u16(d, (uint16_t)i); mla_put_u16(d+2, 0);
            /* even = raw keyframe, odd = compressed delta (kf_back = i) */
            mla_append(&m, 1700000000u + i, (uint16_t)(i * 1000),
                       1, d, 4, (uint8_t)(i & 1), (uint8_t)(i & 1 ? i : 0));
        }
        check("mount OK", mla_mount(&m2, hal) == MLA_OK);
        { mla_log_t rec; uint8_t buf[8]; uint16_t len; int ok = 1;
          for (i = 0; i < 8; i++) {
              if (mla_read_record(&m2, (uint32_t)i, &rec, buf, sizeof(buf), &len) != MLA_OK) ok = 0;
              else if (rec.subsec != (uint16_t)(i * 1000)) ok = 0;
              else if (mla_flags_compressed(rec.flags) != (i & 1)) ok = 0;
              else if ((i & 1) && mla_flags_kf_back(rec.flags) != (uint8_t)i) ok = 0;
          }
          check("subsec + compressed + kf_back round-trip", ok); }
        { int cnt = 0; mla_filter_t f; memset(&f, 0, sizeof(f));
          f.has_compressed = 1; f.compressed = 1;
          mla_foreach(&m2, &f, count_cb, &cnt);
          check("filter compressed==1 → 4 records", cnt == 4); }
        free(r.mem);
    }

    /* 8. Prefix resilience — mount falls back to the tail mirror */
    section("Prefix resilience: mirror fallback");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 16 * 1024);
        mla_t m, m2; int i;
        mla_format(&m, hal, 16 * 1024, MLA_CRC_FULL, 12, 0);
        for (i = 0; i < 4; i++) { uint8_t d[3] = {(uint8_t)i,(uint8_t)i,(uint8_t)i};
                                  mla_append(&m, 1700000000u + i, 0, 1, d, 3, 0, 0); }
        /* Trash the primary prefix (offset 0) — a single bad sector at the head. */
        { uint8_t z[MLA_PREFIX_SIZE]; memset(z, 0, sizeof(z));
          hal.write(hal.ctx, 0, z, sizeof(z)); }
        check("mount recovers via mirror", mla_mount(&m2, hal) == MLA_OK);
        check("count=4 through mirror mount", m2.count == 4);
        { mla_log_t rec; uint8_t buf[8]; uint16_t len;
          check("data intact through mirror",
                mla_read_record(&m2, 2, &rec, buf, sizeof(buf), &len) == MLA_OK
                && len == 3 && buf[0] == 2); }
        free(r.mem);
    }

    printf("\n===========================\n");
    printf("Result: %d/%d PASS  |  %d FAIL\n", g_pass, g_pass + g_fail, g_fail);
    if (g_fail == 0) printf("All OK :)  * Viva La Resistance *\n");
    return g_fail == 0 ? 0 : 1;
}
