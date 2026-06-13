/*
 * glue_export.h  —  NIC-GLUE-OUT: dumb tabular export (C port of export.py).
 *
 * Turns a generic table (columns + a row source) into bytes:
 *   • CSV                 — no dependency
 *   • SQL text dump (.sql) — no dependency
 *   • SQLite .db (binary)  — only with the SQLite amalgamation + -DGLUE_WITH_SQLITE
 *
 * Host-side serializer (the embedded streaming path is glue_out.{h,c}). Knows
 * nothing about MLA — glue_reader feeds it rows.  MIT  |  ★ Viva La Resistánce ★
 */
#ifndef GLUE_EXPORT_H
#define GLUE_EXPORT_H

#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum { GLUE_NULL = 0, GLUE_INT, GLUE_REAL, GLUE_TEXT } glue_celltype_t;

typedef struct {
    glue_celltype_t type;
    long long       i;
    double          r;
    const char     *s;   /* borrowed; must outlive the export call */
} glue_cell_t;

typedef struct { const char *name; const char *sql_decl; } glue_col_t;

/* Fill `cells` (ncols) for the next row. 1 = row produced, 0 = done, <0 = error. */
typedef int (*glue_row_fn)(void *ctx, glue_cell_t *cells);

typedef struct {
    const glue_col_t *cols;
    int               ncols;
    glue_row_fn       next_row;
    void             *ctx;
} glue_table_t;

#define GLUE_OK      0
#define GLUE_E_IO   (-1)
#define GLUE_E_ROW  (-2)
#define GLUE_E_MEM  (-3)
#define GLUE_E_ARG  (-4)

int glue_export_csv(FILE *out, const glue_table_t *t);
int glue_export_sqldump(FILE *out, const glue_table_t *t, const char *table);
#ifdef GLUE_WITH_SQLITE
int glue_export_sqlite(const char *path, const glue_table_t *t, const char *table);
#endif

#ifdef __cplusplus
}
#endif
#endif /* GLUE_EXPORT_H */
