/*
 * glue_export.c  —  NIC-GLUE-OUT: dumb tabular export (C port of export.py). MIT
 * ★ Viva La Resistánce ★
 */
#include "glue_export.h"

#include <stdlib.h>
#include <string.h>
#include <ctype.h>

static void fmt_real(char *buf, size_t n, double v)
{
    snprintf(buf, n, "%.6f", v);
    char *dot = strchr(buf, '.');
    if (dot) {
        char *e = buf + strlen(buf) - 1;
        while (e > dot && *e == '0') *e-- = '\0';
        if (e == dot) *e = '\0';
    }
    if (buf[0] == '\0') { buf[0] = '0'; buf[1] = '\0'; }
}

/* ── CSV ──────────────────────────────────────────────────────────────────── */
static void csv_text(FILE *out, const char *s)
{
    for (; *s; ++s) {
        char c = *s;
        if (c == ',') c = ';'; else if (c == '\n' || c == '\r') c = ' ';
        fputc(c, out);
    }
}
static void csv_cell(FILE *out, const glue_cell_t *c)
{
    char b[64];
    switch (c->type) {
    case GLUE_NULL:                              break;
    case GLUE_INT:  fprintf(out, "%lld", c->i);  break;
    case GLUE_REAL: fmt_real(b, sizeof b, c->r); fputs(b, out); break;
    case GLUE_TEXT: csv_text(out, c->s ? c->s : ""); break;
    }
}
int glue_export_csv(FILE *out, const glue_table_t *t)
{
    if (!out || !t || t->ncols <= 0 || !t->next_row) return GLUE_E_ARG;
    for (int j = 0; j < t->ncols; ++j) { if (j) fputc(',', out); csv_text(out, t->cols[j].name ? t->cols[j].name : ""); }
    fputc('\n', out);
    glue_cell_t *cells = calloc((size_t)t->ncols, sizeof *cells);
    if (!cells) return GLUE_E_MEM;
    int rc;
    while ((rc = t->next_row(t->ctx, cells)) == 1) {
        for (int j = 0; j < t->ncols; ++j) { if (j) fputc(',', out); csv_cell(out, &cells[j]); }
        fputc('\n', out);
    }
    free(cells);
    if (rc < 0) return GLUE_E_ROW;
    return ferror(out) ? GLUE_E_IO : GLUE_OK;
}

/* ── SQL identifier sanitising (mirrors _sql_ident) ─────────────────────────── */
static int eq_ci(const char *a, const char *b)
{
    for (; *a && *b; ++a, ++b) if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
    return *a == *b;
}
static char *sql_ident(const char *name, char **used, int n_used)
{
    const char *src = (name && *name) ? name : "col";
    char *base = malloc(strlen(src) + 3);
    if (!base) return NULL;
    char *w = base;
    if (isdigit((unsigned char)src[0])) { *w++ = 'c'; *w++ = '_'; }
    for (const char *p = src; *p; ++p) *w++ = isalnum((unsigned char)*p) ? *p : '_';
    *w = '\0';
    if (!base[0]) strcpy(base, "col");
    char cand[256]; snprintf(cand, sizeof cand, "%s", base);
    for (int n = 2; ; ++n) {
        int clash = 0;
        for (int k = 0; k < n_used; ++k) if (used[k] && eq_ci(used[k], cand)) { clash = 1; break; }
        if (!clash) break;
        snprintf(cand, sizeof cand, "%s_%d", base, n);
    }
    free(base);
    char *out = malloc(strlen(cand) + 1);
    if (out) strcpy(out, cand);
    return out;
}
static char **build_idents(const glue_table_t *t)
{
    char **id = calloc((size_t)t->ncols, sizeof *id);
    if (!id) return NULL;
    for (int j = 0; j < t->ncols; ++j) {
        id[j] = sql_ident(t->cols[j].name, id, j);
        if (!id[j]) { for (int k = 0; k < j; ++k) free(id[k]); free(id); return NULL; }
    }
    return id;
}
static void free_idents(char **id, int n) { if (!id) return; for (int j = 0; j < n; ++j) free(id[j]); free(id); }

/* ── SQL text dump ──────────────────────────────────────────────────────────── */
static void sql_quote(FILE *out, const char *s)
{
    fputc('\'', out);
    for (; *s; ++s) { if (*s == '\'') fputc('\'', out); fputc(*s, out); }
    fputc('\'', out);
}
static void sql_value(FILE *out, const glue_cell_t *c)
{
    char b[64];
    switch (c->type) {
    case GLUE_NULL: fputs("NULL", out); break;
    case GLUE_INT:  fprintf(out, "%lld", c->i); break;
    case GLUE_REAL: fmt_real(b, sizeof b, c->r); fputs(b, out); break;
    case GLUE_TEXT: sql_quote(out, c->s ? c->s : ""); break;
    }
}
int glue_export_sqldump(FILE *out, const glue_table_t *t, const char *table)
{
    if (!out || !t || t->ncols <= 0 || !t->next_row) return GLUE_E_ARG;
    if (!table) table = "records";
    char **id = build_idents(t);
    if (!id) return GLUE_E_MEM;
    fprintf(out, "CREATE TABLE %s (", table);
    for (int j = 0; j < t->ncols; ++j)
        fprintf(out, "%s%s %s", j ? ", " : "", id[j], t->cols[j].sql_decl ? t->cols[j].sql_decl : "TEXT");
    fputs(");\nBEGIN;\n", out);
    glue_cell_t *cells = calloc((size_t)t->ncols, sizeof *cells);
    if (!cells) { free_idents(id, t->ncols); return GLUE_E_MEM; }
    int rc;
    while ((rc = t->next_row(t->ctx, cells)) == 1) {
        fprintf(out, "INSERT INTO %s VALUES (", table);
        for (int j = 0; j < t->ncols; ++j) { if (j) fputs(", ", out); sql_value(out, &cells[j]); }
        fputs(");\n", out);
    }
    free(cells);
    free_idents(id, t->ncols);
    if (rc < 0) return GLUE_E_ROW;
    if (ferror(out)) return GLUE_E_IO;
    fputs("COMMIT;\n", out);
    return ferror(out) ? GLUE_E_IO : GLUE_OK;
}

#ifdef GLUE_WITH_SQLITE
#include "sqlite3.h"
int glue_export_sqlite(const char *path, const glue_table_t *t, const char *table)
{
    if (!path || !t || t->ncols <= 0 || !t->next_row) return GLUE_E_ARG;
    if (!table) table = "records";
    remove(path);
    char **id = build_idents(t);
    if (!id) return GLUE_E_MEM;
    sqlite3 *db = NULL; int rc_db = GLUE_OK;
    if (sqlite3_open(path, &db) != SQLITE_OK) { rc_db = GLUE_E_IO; goto done; }
    {
        size_t cap = 64; for (int j = 0; j < t->ncols; ++j) cap += strlen(id[j]) + 12;
        char *sql = malloc(cap); if (!sql) { rc_db = GLUE_E_MEM; goto done; }
        int p = snprintf(sql, cap, "CREATE TABLE %s (", table);
        for (int j = 0; j < t->ncols; ++j)
            p += snprintf(sql + p, cap - p, "%s%s %s", j ? ", " : "", id[j],
                          t->cols[j].sql_decl ? t->cols[j].sql_decl : "TEXT");
        snprintf(sql + p, cap - p, ");");
        int e = sqlite3_exec(db, sql, NULL, NULL, NULL); free(sql);
        if (e != SQLITE_OK) { rc_db = GLUE_E_IO; goto done; }
    }
    {
        size_t cap = 64 + (size_t)t->ncols * 2; char *sql = malloc(cap);
        if (!sql) { rc_db = GLUE_E_MEM; goto done; }
        int p = snprintf(sql, cap, "INSERT INTO %s VALUES (", table);
        for (int j = 0; j < t->ncols; ++j) p += snprintf(sql + p, cap - p, "%s?", j ? "," : "");
        snprintf(sql + p, cap - p, ");");
        sqlite3_stmt *st = NULL;
        if (sqlite3_prepare_v2(db, sql, -1, &st, NULL) != SQLITE_OK) { free(sql); rc_db = GLUE_E_IO; goto done; }
        free(sql);
        sqlite3_exec(db, "BEGIN", NULL, NULL, NULL);
        glue_cell_t *cells = calloc((size_t)t->ncols, sizeof *cells);
        if (!cells) { sqlite3_finalize(st); rc_db = GLUE_E_MEM; goto done; }
        int rc;
        while ((rc = t->next_row(t->ctx, cells)) == 1) {
            for (int j = 0; j < t->ncols; ++j) switch (cells[j].type) {
                case GLUE_NULL: sqlite3_bind_null(st, j + 1); break;
                case GLUE_INT:  sqlite3_bind_int64(st, j + 1, cells[j].i); break;
                case GLUE_REAL: sqlite3_bind_double(st, j + 1, cells[j].r); break;
                case GLUE_TEXT: sqlite3_bind_text(st, j + 1, cells[j].s ? cells[j].s : "", -1, SQLITE_TRANSIENT); break;
            }
            if (sqlite3_step(st) != SQLITE_DONE) { rc = -1; break; }
            sqlite3_reset(st);
        }
        free(cells); sqlite3_finalize(st);
        if (rc < 0) { sqlite3_exec(db, "ROLLBACK", NULL, NULL, NULL); rc_db = GLUE_E_ROW; goto done; }
        sqlite3_exec(db, "COMMIT", NULL, NULL, NULL);
    }
done:
    if (db) sqlite3_close(db);
    free_idents(id, t->ncols);
    return rc_db;
}
#endif /* GLUE_WITH_SQLITE */
