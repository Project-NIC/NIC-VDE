/*
 * mla_archive.c  —  NIC-MLA: file rotation manager (host-only). MIT
 * ★ Viva La Resistánce ★
 */
#define _POSIX_C_SOURCE 200809L
#include "mla_archive.h"

#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <dirent.h>

static char *dup_str(const char *s)
{
    size_t n = strlen(s) + 1;
    char *p = malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}
static uint8_t *dup_mem(const uint8_t *s, uint16_t n)
{
    if (!s || !n) return NULL;
    uint8_t *p = malloc(n);
    if (p) memcpy(p, s, n);
    return p;
}
static void path_of(const mla_archive_t *a, int seq, char *out, size_t cap)
{
    snprintf(out, cap, "%s/%s%0*d.MLA", a->dir, a->base, a->digits, seq);
}

static int name_seq(const mla_archive_t *a, const char *n)   /* -1 if not a match */
{
    size_t bl = strlen(a->base);
    if (strncmp(n, a->base, bl) != 0) return -1;
    const char *p = n + bl;
    int seq = 0, nd = 0;
    while (p[nd] >= '0' && p[nd] <= '9') { seq = seq * 10 + (p[nd] - '0'); nd++; }
    if (nd != a->digits || strcmp(p + nd, ".MLA") != 0) return -1;
    return seq;
}

static int max_existing_seq(const mla_archive_t *a)
{
    DIR *d = opendir(a->dir);
    if (!d) return -1;
    int best = -1; struct dirent *e;
    while ((e = readdir(d))) { int s = name_seq(a, e->d_name); if (s > best) best = s; }
    closedir(d);
    return best;
}

int mla_archive_file_count(const mla_archive_t *a)
{
    DIR *d = opendir(a->dir);
    if (!d) return 0;
    int n = 0; struct dirent *e;
    while ((e = readdir(d))) if (name_seq(a, e->d_name) >= 0) n++;
    closedir(d);
    return n;
}

static int create_and_format(mla_archive_t *a, int seq)
{
    char path[1024];
    path_of(a, seq, path, sizeof path);
    if (mla_posix_create(&a->pf, path, a->file_size) != MLA_OK) return MLA_E_IO;
    int rc = mla_format_ex(&a->mla, mla_posix_hal(&a->pf), a->file_size, a->crc_mode, 12,
                           a->keyframe_intv, a->schema, a->schema_len,
                           a->station, a->station_len);
    if (rc != MLA_OK) { fclose(a->pf.f); return rc; }
    a->have_writer = 1; a->seq = seq;
    return MLA_OK;
}

static int ensure_writer(mla_archive_t *a)
{
    if (a->have_writer) return MLA_OK;
    int last = max_existing_seq(a);
    if (last < 0) return create_and_format(a, 0);
    char path[1024];
    path_of(a, last, path, sizeof path);
    if (mla_posix_open(&a->pf, path) != MLA_OK) return MLA_E_IO;
    if (mla_mount(&a->mla, mla_posix_hal(&a->pf)) != MLA_OK) { fclose(a->pf.f); return MLA_E_BADFMT; }
    a->have_writer = 1; a->seq = last;
    return MLA_OK;
}

int mla_archive_open(mla_archive_t *a, const char *dir, uint32_t file_size,
                     uint8_t crc_mode, uint8_t keyframe_intv,
                     const uint8_t *schema,  uint16_t schema_len,
                     const uint8_t *station, uint16_t station_len,
                     const char *base, mla_on_rotate_fn on_rotate, void *ctx)
{
    memset(a, 0, sizeof *a);
    a->dir = dup_str(dir);
    if (!a->dir) return MLA_E_IO;
    snprintf(a->base, sizeof a->base, "%s", base ? base : "MLA");
    a->digits = 5;
    a->file_size = file_size;
    a->crc_mode = crc_mode;
    a->keyframe_intv = keyframe_intv;
    a->schema  = dup_mem(schema, schema_len);   a->schema_len  = a->schema ? schema_len : 0;
    a->station = dup_mem(station, station_len);  a->station_len = a->station ? station_len : 0;
    a->on_rotate = on_rotate; a->on_rotate_ctx = ctx;
    return ensure_writer(a);
}

int mla_archive_will_rotate(mla_archive_t *a, uint16_t data_len)
{
    if (ensure_writer(a) != MLA_OK) return 0;
    return mla_free(&a->mla) < (uint32_t)data_len + 4u;   /* block = MAGIC + data + CRC */
}

static int rotate(mla_archive_t *a)
{
    mla_archive_sync(a);
    if (a->pf.f) fclose(a->pf.f);
    a->have_writer = 0;
    int prev = a->seq;
    int rc = create_and_format(a, prev + 1);
    if (rc != MLA_OK) return rc;
    if (a->on_rotate) a->on_rotate(a->on_rotate_ctx, (uint16_t)prev, (uint16_t)a->seq);
    return MLA_OK;
}

int mla_archive_append(mla_archive_t *a, uint32_t ts, uint16_t subsec, uint8_t station,
                       const uint8_t *data, uint16_t len, uint8_t compressed, uint8_t kf_back)
{
    if (ensure_writer(a) != MLA_OK) return MLA_E_IO;
    int rc = mla_append(&a->mla, ts, subsec, station, data, len, compressed, kf_back);
    if (rc == MLA_OK) return 0;
    if (rc != MLA_E_FULL) return rc;
    rc = rotate(a);
    if (rc != MLA_OK) return rc;
    rc = mla_append(&a->mla, ts, subsec, station, data, len, compressed, kf_back);
    return (rc == MLA_OK) ? 1 : rc;
}

void mla_archive_sync(mla_archive_t *a)
{
    if (a->have_writer) a->mla.hal.sync(a->mla.hal.ctx);
}

void mla_archive_close(mla_archive_t *a)
{
    if (!a) return;
    mla_archive_sync(a);
    if (a->have_writer && a->pf.f) fclose(a->pf.f);
    a->have_writer = 0;
    free(a->dir); a->dir = NULL;
    free(a->schema); a->schema = NULL;
    free(a->station); a->station = NULL;
}
