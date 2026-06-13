/* glue_export_test.c — dumb exporter self-test (CSV + SQL dump). MIT */
#include "glue_export.h"
#include <stdlib.h>
#include <string.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *w, int ok){ if(ok)g_pass++; else{g_fail++;printf("  FAIL: %s\n",w);} }

typedef struct { int row, nrows; } src_t;
static int next_row(void *ctx, glue_cell_t *c)
{
    src_t *s = ctx;
    if (s->row >= s->nrows) return 0;
    int r = s->row++;
    if (r == 0) { c[0]=(glue_cell_t){.type=GLUE_INT,.i=0}; c[1]=(glue_cell_t){.type=GLUE_TEXT,.s="a,b"};
                  c[2]=(glue_cell_t){.type=GLUE_REAL,.r=23.5}; }
    else        { c[0]=(glue_cell_t){.type=GLUE_INT,.i=1}; c[1]=(glue_cell_t){.type=GLUE_TEXT,.s="O'Brien"};
                  c[2]=(glue_cell_t){.type=GLUE_NULL}; }
    return 1;
}
static const glue_col_t COLS[] = { {"idx","INTEGER"},{"name","TEXT"},{"val","NUMERIC"} };

static char *to_buf(int sql, glue_table_t *t)
{
    FILE *f = tmpfile(); if (!f) return NULL;
    if (sql) glue_export_sqldump(f, t, "records"); else glue_export_csv(f, t);
    fflush(f); long n = ftell(f); rewind(f);
    char *b = malloc((size_t)n + 1); if (b){ size_t g = fread(b,1,(size_t)n,f); b[g]='\0'; } fclose(f);
    return b;
}

int main(void)
{
    src_t s = {0,2}; glue_table_t t = { COLS, 3, next_row, &s };
    s.row = 0; char *csv = to_buf(0,&t);
    check("csv", csv && strcmp(csv,"idx,name,val\n0,a;b,23.5\n1,O'Brien,\n")==0);
    if (csv && strcmp(csv,"idx,name,val\n0,a;b,23.5\n1,O'Brien,\n")) printf("---got---\n%s",csv);
    s.row = 0; char *sql = to_buf(1,&t);
    check("sql create", sql && strstr(sql,"CREATE TABLE records (idx INTEGER, name TEXT, val NUMERIC);"));
    check("sql row0", sql && strstr(sql,"INSERT INTO records VALUES (0, 'a,b', 23.5);"));
    check("sql row1 escaped+NULL", sql && strstr(sql,"INSERT INTO records VALUES (1, 'O''Brien', NULL);"));
    check("sql txn", sql && strstr(sql,"BEGIN;") && strstr(sql,"COMMIT;"));
    free(csv); free(sql);
    printf("\n%s  %d/%d passed\n", g_fail?"FAIL":"OK", g_pass, g_pass+g_fail);
    return g_fail?1:0;
}
