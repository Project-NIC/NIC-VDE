/*
 * nic_mla_hal_fatfs.c  —  HAL adapter for FatFs (ChaN)
 *
 * NOTE: compiles only in a project where FatFs (ff.h) is available.
 *       Here in the repository it serves as reference glue (like the Arduino .ino).
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#include <string.h>
#include "nic_mla_hal_fatfs.h"

static int fatfs_read(void *ctx, uint32_t off, void *buf, uint16_t n) {
    mla_fatfs_file_t *s = (mla_fatfs_file_t*)ctx;
    UINT br;
    if (f_lseek(s->fil, off) != FR_OK) return MLA_E_IO;
    if (f_read(s->fil, buf, n, &br) != FR_OK || br != n) return MLA_E_IO;
    return MLA_OK;
}
static int fatfs_write(void *ctx, uint32_t off, const void *buf, uint16_t n) {
    mla_fatfs_file_t *s = (mla_fatfs_file_t*)ctx;
    UINT bw;
    if (f_lseek(s->fil, off) != FR_OK) return MLA_E_IO;
    if (f_write(s->fil, buf, n, &bw) != FR_OK || bw != n) return MLA_E_IO;
    return MLA_OK;
}
static void fatfs_sync(void *ctx) { f_sync(((mla_fatfs_file_t*)ctx)->fil); }
static uint32_t fatfs_size(void *ctx) { return ((mla_fatfs_file_t*)ctx)->size; }

mla_hal_t mla_fatfs_hal(mla_fatfs_file_t *s) {
    mla_hal_t h;
    h.read = fatfs_read; h.write = fatfs_write;
    h.sync = fatfs_sync; h.size = fatfs_size; h.ctx = s;
    return h;
}

int mla_fatfs_create(mla_fatfs_file_t *s, FIL *fil, const char *path, uint32_t size) {
    uint8_t ff[64];
    uint32_t i;
    UINT bw;
    if (f_open(fil, path, FA_READ | FA_WRITE | FA_CREATE_ALWAYS) != FR_OK) return MLA_E_IO;
    memset(ff, 0xFF, sizeof(ff));
    for (i = 0; i < size; i += sizeof(ff)) {
        uint16_t n = (uint16_t)((size - i < sizeof(ff)) ? (size - i) : sizeof(ff));
        if (f_write(fil, ff, n, &bw) != FR_OK || bw != n) return MLA_E_IO;
    }
    f_sync(fil);
    s->fil = fil; s->size = size;
    return MLA_OK;
}
