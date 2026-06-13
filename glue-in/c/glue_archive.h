/*
 * glue_archive.h  —  NIC-GLUE-IN: rotating write-side connector in C.
 *
 * C port of logger.py's GlueArchiveLogger: same compressed-channel write API as
 * glue_logger, but over a rotating MlaArchive. Each rotated file starts every
 * stream on a keyframe, so any single file decodes on its own:
 *   • the stream that triggers a rollover is keyframed up front
 *     (will_rotate(pkt_len+1) → reset → keyframe; a delta never crosses a file),
 *   • every other open stream is reset by the on_rotate callback.
 *
 * Host-only (uses NIC-MLA's mla_archive). MIT  |  ★ Viva La Resistánce ★
 */
#ifndef GLUE_ARCHIVE_H
#define GLUE_ARCHIVE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct glue_archive ga_t;
typedef struct ga_channel    ga_channel_t;

ga_t *glue_archive_create(const char *dir, uint32_t file_size,
                          uint8_t crc_mode, uint8_t keyframe_intv,
                          const uint8_t *schema,  uint16_t schema_len,
                          const uint8_t *station, uint16_t station_len,
                          const char *base);

ga_channel_t *glue_archive_channel(ga_t *g, uint8_t station, uint8_t pkt_len);
int  glue_archive_channel_log(ga_channel_t *ch, uint32_t ts, const uint8_t *row, uint16_t subsec);
int  glue_archive_log_raw(ga_t *g, uint32_t ts, uint8_t station,
                          const uint8_t *data, uint16_t len, uint16_t subsec);
int  glue_archive_file_count(ga_t *g);
void glue_archive_sync(ga_t *g);
void glue_archive_close(ga_t *g);

#ifdef __cplusplus
}
#endif
#endif /* GLUE_ARCHIVE_H */
