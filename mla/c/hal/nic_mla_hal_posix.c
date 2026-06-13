/*
 * nic_mla_hal_posix.c  —  HAL adapter for POSIX/stdio
 * MIT  |  ★ Viva La Resistánce ★
 */
#include <string.h>
#include "nic_mla_hal_posix.h"

static int posix_read(void *ctx, uint32_t off, void *buf, uint16_t n) {
    mla_posix_file_t *s = (mla_posix_file_t*)ctx;
    if (fseek(s->f, (long)off, SEEK_SET) != 0) return MLA_E_IO;
    return (fread(buf, 1, n, s->f) == (size_t)n) ? MLA_OK : MLA_E_IO;
}
static int posix_write(void *ctx, uint32_t off, const void *buf, uint16_t n) {
    mla_posix_file_t *s = (mla_posix_file_t*)ctx;
    if (fseek(s->f, (long)off, SEEK_SET) != 0) return MLA_E_IO;
    return (fwrite(buf, 1, n, s->f) == (size_t)n) ? MLA_OK : MLA_E_IO;
}
static void posix_sync(void *ctx) { fflush(((mla_posix_file_t*)ctx)->f); }
static uint32_t posix_size(void *ctx) { return ((mla_posix_file_t*)ctx)->size; }

int mla_posix_open(mla_posix_file_t *s, const char *path) {
    long sz;
    s->f = fopen(path, "r+b");
    if (!s->f) return MLA_E_IO;
    if (fseek(s->f, 0, SEEK_END) != 0) return MLA_E_IO;
    sz = ftell(s->f);
    if (sz < 0) return MLA_E_IO;
    s->size = (uint32_t)sz;
    return MLA_OK;
}

int mla_posix_create(mla_posix_file_t *s, const char *path, uint32_t size) {
    uint8_t ff[256];
    uint32_t i;
    FILE *f = fopen(path, "wb");
    if (!f) return MLA_E_IO;
    memset(ff, 0xFF, sizeof(ff));
    for (i = 0; i < size; i += sizeof(ff)) {
        uint16_t n = (uint16_t)((size - i < sizeof(ff)) ? (size - i) : sizeof(ff));
        if (fwrite(ff, 1, n, f) != n) { fclose(f); return MLA_E_IO; }
    }
    fclose(f);
    return mla_posix_open(s, path);
}

void mla_posix_close(mla_posix_file_t *s) {
    if (s->f) { fclose(s->f); s->f = NULL; }
}

mla_hal_t mla_posix_hal(mla_posix_file_t *s) {
    mla_hal_t h;
    h.read = posix_read; h.write = posix_write;
    h.sync = posix_sync; h.size = posix_size; h.ctx = s;
    return h;
}
