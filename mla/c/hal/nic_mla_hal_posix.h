/*
 * nic_mla_hal_posix.h  —  HAL adapter for POSIX/stdio (Linux, Raspberry Pi, PC, macOS, Windows)
 *
 * For anything with an OS and a filesystem (SSD/SD/USB; ext4/exFAT/NTFS/FAT32/FAT16).
 * Works with a single file via stdio (fopen/fseek/fread/fwrite) — the FS is handled
 * by the OS underneath.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef NIC_MLA_HAL_POSIX_H
#define NIC_MLA_HAL_POSIX_H

#include <stdio.h>
#include "../nic_mla_format.h"

typedef struct {
    FILE     *f;
    uint32_t  size;
} mla_posix_file_t;

/* Open an existing container (r+b) and determine its size. Returns MLA_OK / MLA_E_IO. */
int  mla_posix_open(mla_posix_file_t *s, const char *path);

/* Create a new fixed-size container, pre-filled with 0xFF, and open it (r+b). */
int  mla_posix_create(mla_posix_file_t *s, const char *path, uint32_t size);

void mla_posix_close(mla_posix_file_t *s);

/* Build a HAL bound to the given file. */
mla_hal_t mla_posix_hal(mla_posix_file_t *s);

#endif /* NIC_MLA_HAL_POSIX_H */
