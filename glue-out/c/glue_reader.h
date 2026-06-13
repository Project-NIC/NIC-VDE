/*
 * glue_reader.h  —  NIC-GLUE-OUT: host load-all reader (C mirror of reader.py).
 *
 * Mounts an .mla, reads the schema/station tables, decompresses NIC-DMD blobs and
 * decodes the schema, then exports the named table via glue_export (CSV / SQL dump
 * / real SQLite .db). Host model: the whole container is read into RAM on open
 * (use the streaming glue_out.{h,c} on an MCU). The glue names the columns.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef GLUE_READER_H
#define GLUE_READER_H

#include <stdio.h>
#include "glue_export.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct glue_reader glue_reader_t;

glue_reader_t *glue_reader_open(const char *path);   /* NULL on error */
void           glue_reader_close(glue_reader_t *r);
int  glue_reader_record_count(const glue_reader_t *r);
int  glue_reader_has_schema(const glue_reader_t *r);

/* subsec_split: 1 → subsec_hi/lo (default), 0 → subsec. table may be NULL → "records". */
int  glue_reader_to_csv(glue_reader_t *r, FILE *out, int subsec_split);
int  glue_reader_to_sqldump(glue_reader_t *r, FILE *out, int subsec_split, const char *table);
#ifdef GLUE_WITH_SQLITE
int  glue_reader_to_sqlite(glue_reader_t *r, const char *path, int subsec_split, const char *table);
#endif

#ifdef __cplusplus
}
#endif
#endif /* GLUE_READER_H */
