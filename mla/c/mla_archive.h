/*
 * mla_archive.h  —  NIC-MLA: file rotation manager (host-only, C port of
 *                   nic_mla_archive.py's MlaArchive).
 *
 * A rotating set of containers in one directory: MLA00000.MLA, MLA00001.MLA, …
 * When the current file fills up, the next one is started automatically, with
 * the SAME schema/station tables in its prefix — each file is independently
 * mountable. An on_rotate callback fires right after a rollover (the seam a
 * compression glue uses to force a keyframe).
 *
 * Host-only (POSIX dirent + stdio); NOT for the MCU. MIT  |  ★ Viva La Resistánce ★
 */
#ifndef MLA_ARCHIVE_H
#define MLA_ARCHIVE_H

#include "nic_mla.h"
#include "hal/nic_mla_hal_posix.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef void (*mla_on_rotate_fn)(void *ctx, uint16_t prev_seq, uint16_t new_seq);

typedef struct {
    char    *dir;
    char     base[16];
    int      digits;
    uint32_t file_size;
    uint8_t  crc_mode;
    uint8_t  keyframe_intv;
    uint8_t *schema;   uint16_t schema_len;
    uint8_t *station;  uint16_t station_len;
    mla_on_rotate_fn on_rotate; void *on_rotate_ctx;

    int               have_writer;
    int               seq;
    mla_posix_file_t  pf;
    mla_t             mla;
} mla_archive_t;

int  mla_archive_open(mla_archive_t *a, const char *dir, uint32_t file_size,
                      uint8_t crc_mode, uint8_t keyframe_intv,
                      const uint8_t *schema,  uint16_t schema_len,
                      const uint8_t *station, uint16_t station_len,
                      const char *base, mla_on_rotate_fn on_rotate, void *ctx);

int  mla_archive_will_rotate(mla_archive_t *a, uint16_t data_len);

int  mla_archive_append(mla_archive_t *a, uint32_t ts, uint16_t subsec, uint8_t station,
                        const uint8_t *data, uint16_t len, uint8_t compressed, uint8_t kf_back);

int  mla_archive_file_count(const mla_archive_t *a);
void mla_archive_sync(mla_archive_t *a);
void mla_archive_close(mla_archive_t *a);

#ifdef __cplusplus
}
#endif
#endif /* MLA_ARCHIVE_H */
