/*
 * nic_mla_hal_fatfs.h  —  HAL adapter for FatFs (ChaN) — STM32 bare-metal etc.
 *
 * For embedded WITHOUT Arduino (STM32 CubeIDE/HAL, …), where FAT is handled by
 * the FatFs library. Our format does NOT change — this is just ~30 lines of glue
 * over f_read/f_write/f_lseek/f_sync. (For Arduino/ESP/STM32duino use SdFat instead.)
 *
 * Build: add FatFs (ff.h) and this adapter to your project. Mount the volume
 * (f_mount) and open a FIL before calling mla_*.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef NIC_MLA_HAL_FATFS_H
#define NIC_MLA_HAL_FATFS_H

#include "ff.h"               /* FatFs (ChaN) */
#include "../nic_mla_format.h"

/* ctx = pointer to an open FIL; size = container size (pre-allocated). */
typedef struct {
    FIL      *fil;
    uint32_t  size;
} mla_fatfs_file_t;

/* Build a HAL bound to an open FatFs file. */
mla_hal_t mla_fatfs_hal(mla_fatfs_file_t *s);

/* Optional helper: create/pre-allocate a 0xFF container of the given size
 * and leave it open in *fil. Returns MLA_OK / MLA_E_IO. */
int mla_fatfs_create(mla_fatfs_file_t *s, FIL *fil, const char *path, uint32_t size);

#endif /* NIC_MLA_HAL_FATFS_H */
