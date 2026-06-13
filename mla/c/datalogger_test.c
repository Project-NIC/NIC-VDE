// SPDX-License-Identifier: MIT
//
// NIC-MLA — C reader can mount and read records from a datalogger (profile-ref)
// .mla. Proves the prefix-sizing path for the 0x4C tag; value decode stays a
// host concern (Python), same as for the v1.2 schema.

#include "nic_mla.h"
#include "nic_mla_format.h"
#include <stdio.h>
#include <string.h>

/* ── tiny in-RAM HAL ── */
typedef struct { uint8_t *buf; uint32_t size; } ram_t;
static int ram_read(void *c, uint32_t o, void *b, uint16_t n) {
    ram_t *r = (ram_t *)c; if ((uint64_t)o + n > r->size) return -1;
    memcpy(b, r->buf + o, n); return 0;
}
static int ram_write(void *c, uint32_t o, const void *b, uint16_t n) {
    ram_t *r = (ram_t *)c; if ((uint64_t)o + n > r->size) return -1;
    memcpy(r->buf + o, b, n); return 0;
}
static void ram_sync(void *c) { (void)c; }
static uint32_t ram_size(void *c) { return ((ram_t *)c)->size; }
static mla_hal_t ram_hal(ram_t *r) {
    mla_hal_t h; h.read = ram_read; h.write = ram_write;
    h.sync = ram_sync; h.size = ram_size; h.ctx = r; return h;
}

/* one 14 B field descriptor (its content is irrelevant to a container read) */
#define DESC(c) 0x02,0x00,0x00,0x00,0x00,0x00, (c),0,0,0,0,0,0,0

/* datalogger tables: 0 log fields, 2 profiles (1 field each), 2 stations */
static const uint8_t DL_BLOB[] = {
    0x4C, 0x00,                                /* LOG: n_log = 0 */
    0x50, 0x02,                                /* PROFILES: n = 2 */
        0x01, DESC('t'),                       /*   profile 0 */
        0x01, DESC('p'),                       /*   profile 1 */
    0x54, 0x02,                                /* STATIONS: n = 2 */
        1,2,3,4,5,6,7,8, 0,                    /*   station 1 -> profile 0 */
        9,10,11,12,13,14,15,16, 1              /*   station 2 -> profile 1 */
};

typedef struct { int n; uint8_t sta[8]; uint8_t v0[8]; } collect_t;
static int cb(void *user, mla_t *m, const mla_log_t *rec) {
    collect_t *c = (collect_t *)user;
    uint8_t buf[64]; uint16_t len = 0;
    if (mla_read_data(m, rec, buf, sizeof buf, &len) == MLA_OK && c->n < 8) {
        c->sta[c->n] = rec->station;
        c->v0[c->n]  = len ? buf[0] : 0xFF;
        c->n++;
    }
    return 0;
}

static int fails = 0;
static void ok(const char *name, int cond) {
    printf("  %s %s\n", cond ? "ok  " : "FAIL", name);
    if (!cond) fails++;
}

int main(void) {
    static uint8_t mem[64 * 1024];
    ram_t r = { mem, sizeof mem };
    mla_hal_t hal = ram_hal(&r);
    mla_t m, m2;
    uint8_t p1[2] = { 0x11, 0x22 }, p2[2] = { 0x33, 0x44 };
    collect_t c; memset(&c, 0, sizeof c);
    int cnt;

    printf("\n=== NIC-MLA datalogger C reader ===\n\n");

    ok("format with datalogger tables",
       mla_format_ex(&m, hal, sizeof mem, MLA_CRC_FULL, 12, 8,
                     DL_BLOB, (uint16_t)sizeof DL_BLOB, 0, 0) == MLA_OK);
    mla_append(&m, 1700000000u, 0, 1, p1, 2, 0, 0);
    mla_append(&m, 1700000060u, 0, 2, p2, 2, 0, 0);

    /* a fresh mount must SIZE the datalogger prefix (tag 0x4C) and read records */
    ok("mount sizes the datalogger prefix", mla_mount(&m2, hal) == MLA_OK);
    cnt = mla_foreach(&m2, 0, cb, &c);
    ok("2 records read", cnt == 2 && c.n == 2);
    ok("station indices 1,2", c.sta[0] == 1 && c.sta[1] == 2);
    ok("payloads 0x11,0x33", c.v0[0] == 0x11 && c.v0[1] == 0x33);

    printf("\n%s\n", fails ? "FAILURES" : "=== ALL OK ===");
    return fails ? 1 : 0;
}
