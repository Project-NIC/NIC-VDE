/*
 * glue_out_test.c  —  streaming, no-heap read/export round-trip.
 * Builds a NIC-DMD-compressed .mla, then streams it out as CSV / SQL via the
 * embedded glue_out reader and checks the named output.  MIT
 * ★ Viva La Resistánce ★
 */
#define _POSIX_C_SOURCE 200809L
#include "glue_out.h"
#include "nic_mla.h"
#include "nic_dmd.h"
#include "hal/nic_mla_hal_posix.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *what, int ok)
{
    if (ok) g_pass++;
    else { g_fail++; printf("  FAIL: %s\n", what); }
}

#define NREC 5
static const uint8_t SCHEMA[] = {
    MLA_SCHEMA_VER, 0, 2,
    2, 0, 0xFF, 0x01, 0,0, 't','e','m','p', 0,0,0,0,
    2, 0, 0xFF, 0x00, 0,0, 'h','u','m', 0,0,0,0,0,
};
static const uint8_t STATION[] = { MLA_STATION_VER, 1, 7,0, 100,0, 0,0 };

static void build_mla(const char *path)
{
    mla_posix_file_t wf; mla_posix_create(&wf, path, 64u * 1024u);
    mla_t m;
    mla_format_ex(&m, mla_posix_hal(&wf), 64u * 1024u, MLA_CRC_FULL, 12, 0,
                  SCHEMA, (uint16_t)sizeof SCHEMA, STATION, (uint16_t)sizeof STATION);
    dmd_encoder_t enc; dmd_encoder_init(&enc, 4);
    int since = 0;
    for (int i = 0; i < NREC; i++) {
        uint8_t row[4];
        mla_put_u16(row, (uint16_t)(235 + i)); mla_put_u16(row + 2, (uint16_t)(600 + i));
        uint8_t out[DMD_OUT_MAX]; uint16_t ol = dmd_compress(&enc, row, out);
        since = (out[0] & 7) == 0 ? 0 : since + 1;
        mla_append(&m, (uint32_t)(1000 + i), 0, 1, out, ol, 1, (uint8_t)since);
    }
    fclose(wf.f);
}

typedef struct { char *p; int cap, n; } buf_t;
static int bufsink(void *ctx, const char *s, int n)
{
    buf_t *b = ctx;
    if (b->n + n < b->cap) { memcpy(b->p + b->n, s, n); b->n += n; }
    return 0;
}

static int line_count(const char *s) { int n = 0; for (; *s; ++s) if (*s == '\n') n++; return n; }

int main(void)
{
    char path[] = "/tmp/glue_out_mlaXXXXXX";
    int fd = mkstemp(path); if (fd >= 0) close(fd);
    build_mla(path);

    static glue_out_t g;                    /* ~3 KB — static/stack, no heap */
    mla_posix_file_t rf;
    check("open", mla_posix_open(&rf, path) == MLA_OK);
    check("mount+parse", glue_out_mount(&g, mla_posix_hal(&rf)) == GLUE_OUT_OK);
    check("has schema", glue_out_has_schema(&g));

    char csv[4096]; buf_t cb = { csv, sizeof csv, 0 };
    check("csv ok", glue_out_csv(&g, bufsink, &cb, 1) == GLUE_OUT_OK);
    csv[cb.n] = '\0';

    char *nl = strchr(csv, '\n');
    check("csv header named",
          nl && strncmp(csv, "idx,time,unix,sta_idx,region,number,kind,length,"
                             "subsec_hi,subsec_lo,temp,hum", (size_t)(nl - csv)) == 0);
    check("csv row0 station+kind resolved", strstr(csv, ",1,7,100,keyframe,") != NULL);
    check("csv row0 decoded value (keyframe)", strstr(csv, ",0,0,23.5,60") != NULL);
    check("csv row1 decoded delta value", strstr(csv, "23.6") != NULL);
    check("csv line count = header + records", line_count(csv) == NREC + 1);

    /* re-mount for a second streaming pass (SQL dump) */
    fclose(rf.f);
    mla_posix_open(&rf, path);
    glue_out_mount(&g, mla_posix_hal(&rf));
    char sql[4096]; buf_t sb = { sql, sizeof sql, 0 };
    check("sqldump ok", glue_out_sqldump(&g, bufsink, &sb, 1, "records") == GLUE_OUT_OK);
    sql[sb.n] = '\0';
    check("sql create table named",
          strstr(sql, "CREATE TABLE records (idx INTEGER, time TEXT, unix INTEGER, "
                      "sta_idx INTEGER, region INTEGER, number INTEGER, kind TEXT, "
                      "length INTEGER, subsec_hi INTEGER, subsec_lo INTEGER, "
                      "temp NUMERIC, hum NUMERIC);") != NULL);
    check("sql inserts decoded value", strstr(sql, ", 23.5, 60);") != NULL);
    check("sql transaction", strstr(sql, "BEGIN;") && strstr(sql, "COMMIT;"));
    fclose(rf.f);

    remove(path);
    printf("\nsizeof(glue_out_t) = %zu B (static, no heap)\n", sizeof(glue_out_t));
    printf("%s  %d/%d passed\n", g_fail ? "FAIL" : "OK", g_pass, g_pass + g_fail);
    return g_fail ? 1 : 0;
}
