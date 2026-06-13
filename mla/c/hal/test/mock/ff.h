/*
 * Minimal FatFs (ChaN) mock — for COMPILE-TESTING the NIC-MLA FatFs HAL only.
 *
 * Provides just the types / prototypes that nic_mla_hal_fatfs.{c,h} reference,
 * so CI can compile the adapter and catch drift against `mla_hal_t` WITHOUT
 * vendoring the real FatFs library. This is NOT a FatFs implementation and is
 * never linked — the check compiles to an object file only.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef MOCK_FF_H
#define MOCK_FF_H

#include <stdint.h>

typedef unsigned int  UINT;
typedef unsigned char BYTE;
typedef uint32_t      FSIZE_t;

typedef enum { FR_OK = 0, FR_DISK_ERR } FRESULT;

typedef struct { int _opaque; } FIL;

#define FA_READ          0x01
#define FA_WRITE         0x02
#define FA_CREATE_ALWAYS 0x08

FRESULT f_open (FIL *fp, const char *path, BYTE mode);
FRESULT f_read (FIL *fp, void *buff, UINT btr, UINT *br);
FRESULT f_write(FIL *fp, const void *buff, UINT btw, UINT *bw);
FRESULT f_lseek(FIL *fp, FSIZE_t ofs);
FRESULT f_sync (FIL *fp);

#endif /* MOCK_FF_H */
