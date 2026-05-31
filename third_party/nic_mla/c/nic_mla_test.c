/*
 * nic_mla_test.c  —  Tests for both C libraries against a RAM/file HAL.
 *
 * Build & run:
 *     cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c -o mlatest
 *     ./mlatest [path_for_cross-compat_output]
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

/* ── file access: via the reusable POSIX HAL adapter (c/hal/) ─────────────── */
/*    (mla_posix_create / mla_posix_hal / mla_posix_close) */

/* ── callback that counts matches in a query ──────────────────────────────── */
static int count_cb(void *user, mla_t *m, const mla_log_t *rec) {
    (void)m; (void)rec; (*(int*)user)++; return 0;
}

/* ─────────────────────────────────────────────────────────────────────────── */
int main(int argc, char **argv) {
    printf("NIC-MLA C Test Suite\n====================\n");

    /* 1. CRC vector */
    section("CRC-16 / CCITT-FALSE");
    check("vector 0x29B1", mla_crc16((const uint8_t*)"123456789", 9) == 0x29B1);

    /* 2. Full library — round-trip + checkpoint + query */
    section("Full library: format/append/mount/read/query (+checkpoint)");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 64 * 1024);
        mla_t m; const int N = 200; int i, ok;
        mla_format(&m, hal, 64 * 1024, MLA_CRC_FULL, 8, /*ckpt*/4, 8, /*index_kb*/2);
        for (i = 0; i < N; i++) {
            uint8_t d[3]; d[0]=d[1]=d[2]=(uint8_t)i;
            mla_append(&m, (uint32_t)(1500000 + i), 2, (uint16_t)(i % 7), d, 3, MLA_ENC_RAW, 0);
        }
        check("after writing count=200", m.count == (uint32_t)N);
        check("checkpoints took extra slots", m.n_slots > (uint32_t)N);

        /* fresh mount over the same RAM */
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
        check("read_record data matches (incl. around checkpoints)", ok);

        { int cnt = 0; mla_foreach(&m2, NULL, count_cb, &cnt);
          check("foreach returns 200 data records", cnt == N); }
        { int cnt = 0; mla_filter_t f; memset(&f, 0, sizeof(f));
          f.has_channel = 1; f.region = 3;
          mla_foreach(&m2, &f, count_cb, &cnt);
          /* channels i%7==3 → i=3,10,17,...,199 → 29 */
          check("query region=3", cnt == 29); }

        /* index-accelerated scan must equal a brute-force foreach over the same
         * time window, and must actually seek past slot 0 for a late window */
        { mla_filter_t f; int a = 0, b = 0;
          memset(&f, 0, sizeof(f));
          f.has_time = 1; f.time_from = 1500120; f.time_to = 1500160;
          mla_scan(&m2, &f, count_cb, &a);
          mla_foreach(&m2, &f, count_cb, &b);
          check("scan() == foreach over time window", a == b && a == 41);
          check("index seeks past slot 0", mla_index_start_slot(&m2, 1500120) > 0); }
        free(r.mem);
    }

    /* 3. Torn data write — mount abandons the lock, top_ptr rewinds */
    section("Torn data write — recovery on mount");
    {
        ram_t r; mla_hal_t hal = ram_hal(&r, 8 * 1024);
        mla_t m; uint8_t good[4] = {0x11,0x22,0x33,0x44};
        uint32_t torn_off; mla_log_t lk; uint8_t lb[MLA_LOG_REC_SIZE];
        mla_format(&m, hal, 8 * 1024, MLA_CRC_FULL, 8, 0, 8, 0);
        mla_append(&m, 0, 1, 0, good, 4, MLA_ENC_RAW, 0);
        torn_off = m.top_ptr;
        /* write only the lock for the next slot, leave the data 0xFF */
        lk.timestamp=1; lk.offset=torn_off; lk.station=2; lk.region=9;
        lk.seq=1; lk.rec_type=MLA_ENC_RAW; lk.length=20; lk.kf_back=0;
        lk.reserved=0; lk.flags=MLA_FLAG_LIVE;
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
        mla_format(&m, hal, 16 * 1024, MLA_CRC_FULL, 8, 0, 8, 0);
        for (i = 0; i < 5; i++) {
            uint8_t d[16]; memset(d, (uint8_t)(i*13), 8 + i);
            mla_append(&m, 0, 1, (uint16_t)i, d, (uint16_t)(8 + i), MLA_ENC_RAW, 0);
        }
        /* zero out the log region */
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
    section("Write-only library: format/append/mount (+ file for Python)");
    {
        const char *path = (argc > 1) ? argv[1] : "/tmp/mla_c_out.bin";
        mla_posix_file_t s; mla_hal_t hal;
        mla_writer_t w; int i;
        mla_posix_create(&s, path, 64 * 1024);
        hal = mla_posix_hal(&s);
        mla_w_format(&w, hal, 64 * 1024, MLA_CRC_FULL, 12, 4, 8);
        for (i = 0; i < 50; i++) {
            uint8_t d[5]; d[0]=(uint8_t)(i*2); d[1]=(uint8_t)(i*3);
            d[2]=0xAB; d[3]=0xCD; d[4]=(uint8_t)i;
            mla_w_append(&w, (uint32_t)(1700000000u + i), 42, (uint16_t)i, d, 5, MLA_ENC_RAW, 0);
        }
        check("write-only: count=50", w.count == 50);

        /* resume: fresh mount over the same file */
        mla_writer_t w2;
        check("write-only mount OK", mla_w_mount(&w2, hal) == MLA_OK);
        check("write-only mount count=50", w2.count == 50);
        check("write-only seq continues at 50", w2.seq == 50);
        mla_w_append(&w2, 1700000099u, 42, 99, (const uint8_t*)"\x01\x02\x03", 3, MLA_ENC_RAW, 0);
        check("write-only append after mount OK", w2.count == 51);

        /* verify with the full library over the same file */
        mla_t mf;
        mla_mount(&mf, hal);
        check("full library reads the write-only file (count=51)", mf.count == 51);

        mla_posix_close(&s);
        printf("  (cross-compat file written: %s)\n", path);    }

    /* 6. Full library WITH index region → cross-compat file for Python */
    section("Full library + index region (cross-compat file for Python)");
    {
        const char *path = (argc > 2) ? argv[2] : "/tmp/mla_c_idx.bin";
        mla_posix_file_t s; mla_hal_t hal; mla_t m; int i;
        mla_posix_create(&s, path, 64 * 1024);
        hal = mla_posix_hal(&s);
        mla_format(&m, hal, 64 * 1024, MLA_CRC_FULL, 12, /*ckpt*/4, 8, /*index_kb*/2);
        for (i = 0; i < 300; i++) {
            uint8_t d[4]; d[0]=(uint8_t)i; d[1]=(uint8_t)(i>>8); d[2]=0xAB; d[3]=0xCD;
            mla_append(&m, (uint32_t)(1700000000u + i), (uint16_t)(1 + i % 3), 0, d, 4, MLA_ENC_RAW, 0);
        }
        check("indexed: count=300", m.count == 300);
        check("indexed: data_base past region", m.data_base == 512u + 2u * 1024u);
        check("indexed: anchors written", m.idx_n > 0);
        { mla_filter_t f; int a = 0, b = 0; memset(&f, 0, sizeof(f));
          f.has_time = 1; f.time_from = 1700000150u; f.time_to = 1700000200u;
          mla_scan(&m, &f, count_cb, &a);
          mla_foreach(&m, &f, count_cb, &b);
          check("indexed: scan() == foreach", a == b && a == 51); }
        mla_posix_close(&s);
        printf("  (indexed cross-compat file written: %s)\n", path);
    }

    printf("\n====================\n");
    printf("Result: %d/%d PASS  |  %d FAIL\n", g_pass, g_pass + g_fail, g_fail);
    if (g_fail == 0) printf("All OK :)  * Viva La Resistance *\n");
    return g_fail == 0 ? 0 : 1;
}
