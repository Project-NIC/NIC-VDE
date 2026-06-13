/*
 * glue_reader.c  —  NIC-GLUE-OUT: host load-all reader (C mirror of reader.py). MIT
 * ★ Viva La Resistánce ★
 */
#include "glue_reader.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "nic_mla.h"
#include "nic_dmd.h"
#include "hal/nic_mla_hal_posix.h"

typedef struct { char name[9]; uint8_t width; int8_t exp10; uint8_t is_signed; int16_t offset; } gr_field_t;
typedef struct { mla_log_t rec; uint8_t *payload; uint16_t plen; int undecoded; } gr_row_t;

struct glue_reader {
    gr_field_t *fields; int n_data; int pkt_len;
    uint8_t (*stations)[MLA_STATION_REC]; int n_stations;
    gr_row_t *rows; int count;
    glue_col_t *cols; int ncols;
    glue_cell_t *cells;
    char time_buf[32];
    char *val_buf;
    int cursor, subsec_split;
};

static int64_t le_int(const uint8_t *p, int width, int is_signed)
{
    uint64_t u = 0;
    for (int i = 0; i < width; i++) u |= (uint64_t)p[i] << (8 * i);
    if (is_signed && width < 8) { uint64_t s = (uint64_t)1 << (width * 8 - 1); if (u & s) u |= ~((s << 1) - 1); }
    return (int64_t)u;
}
static const char *kind_str(const mla_log_t *r)
{
    if (!mla_flags_compressed(r->flags)) return "raw";
    return mla_flags_kf_back(r->flags) == 0 ? "keyframe" : "delta";
}

static int parse_tables(glue_reader_t *r, const uint8_t *pfx, uint32_t plen)
{
    r->fields = NULL; r->n_data = 0; r->pkt_len = 0; r->stations = NULL; r->n_stations = 0;
    if (plen <= MLA_SCHEMA_OFF) return 0;
    uint8_t ver = pfx[MLA_SCHEMA_OFF]; uint32_t schema_bytes = 0;
    if (ver != 0x00 && ver != 0xFF) {
        if (ver != MLA_SCHEMA_VER || plen < MLA_SCHEMA_OFF + 3u) return -1;
        uint8_t n_log = pfx[MLA_SCHEMA_OFF + 1], n_data = pfx[MLA_SCHEMA_OFF + 2];
        schema_bytes = 3u + (uint32_t)MLA_FIELD_SIZE * (n_log + n_data);
        if (plen < MLA_SCHEMA_OFF + schema_bytes) return -1;
        r->fields = calloc(n_data ? n_data : 1, sizeof *r->fields);
        if (!r->fields) return -1;
        const uint8_t *d = pfx + MLA_SCHEMA_OFF + 3 + (uint32_t)MLA_FIELD_SIZE * n_log;
        for (int i = 0; i < n_data; i++) {
            const uint8_t *f = d + (size_t)i * MLA_FIELD_SIZE;
            gr_field_t *g = &r->fields[i];
            g->width = f[0]; g->exp10 = (int8_t)f[2]; g->is_signed = (uint8_t)(f[3] & 0x01);
            g->offset = (int16_t)(f[4] | (f[5] << 8));
            size_t nl = 0; while (nl < 8 && f[6 + nl]) nl++;
            memcpy(g->name, f + 6, nl); g->name[nl] = '\0';
            if (!g->name[0]) snprintf(g->name, sizeof g->name, "data%d", i);
            r->pkt_len += g->width;
        }
        r->n_data = n_data;
    }
    uint32_t off = MLA_SCHEMA_OFF + schema_bytes;
    if (plen >= off + 2u && pfx[off] == MLA_STATION_VER) {
        uint8_t n = pfx[off + 1];
        if (plen < off + 2u + (uint32_t)MLA_STATION_REC * n) return -1;
        r->stations = malloc((size_t)(n ? n : 1) * MLA_STATION_REC);
        if (!r->stations) return -1;
        memcpy(r->stations, pfx + off + 2, (size_t)n * MLA_STATION_REC);
        r->n_stations = n;
    }
    return 0;
}

typedef struct { glue_reader_t *r; uint8_t *block; dmd_decoder_t *dec[256]; int err; } load_ctx_t;

static int load_cb(void *user, mla_t *m, const mla_log_t *rec)
{
    load_ctx_t *L = user; glue_reader_t *r = L->r;
    uint16_t len = 0;
    if (mla_read_data(m, rec, L->block, 65535u, &len) != MLA_OK) { L->err = 1; return 1; }
    gr_row_t *grow = realloc(r->rows, (size_t)(r->count + 1) * sizeof *r->rows);
    if (!grow) { L->err = 1; return 1; }
    r->rows = grow;
    gr_row_t *row = &r->rows[r->count];
    row->rec = *rec; row->payload = NULL; row->plen = 0; row->undecoded = 0;
    if (mla_flags_compressed(rec->flags)) {
        if (r->pkt_len > 0 && r->pkt_len <= 255) {
            dmd_decoder_t *dec = L->dec[rec->station];
            if (!dec) { dec = malloc(sizeof *dec); if (!dec) { L->err = 1; return 1; }
                        dmd_decoder_init(dec, (uint8_t)r->pkt_len); L->dec[rec->station] = dec; }
            uint8_t out[256];
            if (dmd_decompress(dec, L->block, len, out) == 0) {
                row->plen = (uint16_t)r->pkt_len; row->payload = malloc(row->plen);
                if (!row->payload) { L->err = 1; return 1; }
                memcpy(row->payload, out, row->plen);
            } else row->undecoded = 1;
        } else row->undecoded = 1;
    } else if (len) {
        row->payload = malloc(len); if (!row->payload) { L->err = 1; return 1; }
        memcpy(row->payload, L->block, len); row->plen = len;
    }
    r->count++;
    return 0;
}

glue_reader_t *glue_reader_open(const char *path)
{
    glue_reader_t *r = calloc(1, sizeof *r);
    if (!r) return NULL;
    mla_posix_file_t local;
    if (mla_posix_open(&local, path) != MLA_OK) { free(r); return NULL; }
    mla_hal_t hal = mla_posix_hal(&local);
    mla_t m;
    if (mla_mount(&m, hal) != MLA_OK) goto fail;
    uint32_t psz = m.data_base ? m.data_base : MLA_PREFIX_SIZE;
    uint8_t *pfx = malloc(psz); if (!pfx) goto fail;
    for (uint32_t got = 0; got < psz; ) {
        uint32_t c = psz - got; if (c > 32768u) c = 32768u;
        if (hal.read(hal.ctx, got, pfx + got, (uint16_t)c) != MLA_OK) { free(pfx); goto fail; }
        got += c;
    }
    if (parse_tables(r, pfx, psz) != 0) { free(pfx); goto fail; }
    free(pfx);
    load_ctx_t L; memset(&L, 0, sizeof L); L.r = r;
    L.block = malloc(65536); if (!L.block) goto fail;
    mla_foreach(&m, NULL, load_cb, &L);
    for (int i = 0; i < 256; i++) free(L.dec[i]);
    free(L.block);
    if (L.err) goto fail;
    int max_cols = 8 + 2 + (r->n_data > 0 ? r->n_data : 1);
    r->cols = calloc((size_t)max_cols, sizeof *r->cols);
    r->cells = calloc((size_t)max_cols, sizeof *r->cells);
    r->val_buf = malloc(3u * 65536u);
    if (!r->cols || !r->cells || !r->val_buf) goto fail;
    fclose(local.f);
    return r;
fail:
    glue_reader_close(r);
    return NULL;
}

void glue_reader_close(glue_reader_t *r)
{
    if (!r) return;
    if (r->rows) { for (int i = 0; i < r->count; i++) free(r->rows[i].payload); free(r->rows); }
    free(r->fields); free(r->stations); free(r->cols); free(r->cells); free(r->val_buf);
    free(r);
}

int glue_reader_record_count(const glue_reader_t *r) { return r ? r->count : 0; }
int glue_reader_has_schema(const glue_reader_t *r)   { return r && r->n_data > 0; }

static void build_columns(glue_reader_t *r, int split)
{
    static const glue_col_t base[8] = {
        {"idx","INTEGER"},{"time","TEXT"},{"unix","INTEGER"},{"sta_idx","INTEGER"},
        {"region","INTEGER"},{"number","INTEGER"},{"kind","TEXT"},{"length","INTEGER"},
    };
    int k = 0;
    for (int i = 0; i < 8; i++) r->cols[k++] = base[i];
    if (split) { r->cols[k++] = (glue_col_t){"subsec_hi","INTEGER"}; r->cols[k++] = (glue_col_t){"subsec_lo","INTEGER"}; }
    else         r->cols[k++] = (glue_col_t){"subsec","INTEGER"};
    if (r->n_data > 0) for (int i = 0; i < r->n_data; i++) r->cols[k++] = (glue_col_t){ r->fields[i].name, "NUMERIC" };
    else r->cols[k++] = (glue_col_t){"value","TEXT"};
    r->ncols = k; r->subsec_split = split;
}

static void value_fallback(glue_reader_t *r, const gr_row_t *row, glue_cell_t *c)
{
    const mla_log_t *rec = &row->rec;
    if (row->undecoded || !row->payload) {
        snprintf(r->val_buf, 64, "<%s %uB>", kind_str(rec), (unsigned)rec->length);
        c->type = GLUE_TEXT; c->s = r->val_buf;
    } else if (row->plen == 1 || row->plen == 2 || row->plen == 4) {
        c->type = GLUE_INT; c->i = le_int(row->payload, row->plen, 0);
    } else {
        char *w = r->val_buf;
        for (uint16_t i = 0; i < row->plen; i++) w += sprintf(w, i ? " %02x" : "%02x", row->payload[i]);
        c->type = GLUE_TEXT; c->s = r->val_buf;
    }
}

static int gr_next_row(void *ctx, glue_cell_t *cells)
{
    glue_reader_t *r = ctx;
    if (r->cursor >= r->count) return 0;
    gr_row_t *row = &r->rows[r->cursor];
    mla_log_t *rec = &row->rec;
    int k = 0;
    cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = r->cursor };
    time_t t = (time_t)rec->timestamp; struct tm *tp = gmtime(&t);
    if (tp) strftime(r->time_buf, sizeof r->time_buf, "%Y-%m-%d %H:%M:%S", tp); else r->time_buf[0] = '\0';
    cells[k++] = (glue_cell_t){ .type = GLUE_TEXT, .s = r->time_buf };
    cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = rec->timestamp };
    cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = rec->station };
    if (r->stations && rec->station >= 1 && rec->station <= r->n_stations) {
        const uint8_t *s = r->stations[rec->station - 1];
        cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = mla_get_u16(s) };
        cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = mla_get_u16(s + 2) };
    } else { cells[k++] = (glue_cell_t){ .type = GLUE_NULL }; cells[k++] = (glue_cell_t){ .type = GLUE_NULL }; }
    cells[k++] = (glue_cell_t){ .type = GLUE_TEXT, .s = kind_str(rec) };
    cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = rec->length };
    if (r->subsec_split) {
        cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = (rec->subsec >> 8) & 0xFF };
        cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = rec->subsec & 0xFF };
    } else cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = rec->subsec };
    if (r->n_data > 0) {
        int fits = (row->payload && row->plen == (uint16_t)r->pkt_len);
        size_t pos = 0;
        for (int i = 0; i < r->n_data; i++) {
            const gr_field_t *f = &r->fields[i];
            if (!fits) { cells[k++] = (glue_cell_t){ .type = GLUE_NULL }; continue; }
            int64_t scaled = le_int(row->payload + pos, f->width, f->is_signed) + f->offset; pos += f->width;
            if (f->exp10 >= 0) { int64_t mul = 1; for (int e = 0; e < f->exp10; e++) mul *= 10;
                cells[k++] = (glue_cell_t){ .type = GLUE_INT, .i = scaled * mul }; }
            else { double dd = (double)scaled; for (int e = 0; e < -f->exp10; e++) dd /= 10.0;
                cells[k++] = (glue_cell_t){ .type = GLUE_REAL, .r = dd }; }
        }
    } else value_fallback(r, row, &cells[k++]);
    r->cursor++;
    return 1;
}

int glue_reader_to_csv(glue_reader_t *r, FILE *out, int split)
{
    if (!r) return GLUE_E_ARG;
    build_columns(r, split); r->cursor = 0;
    glue_table_t t = { r->cols, r->ncols, gr_next_row, r };
    return glue_export_csv(out, &t);
}
int glue_reader_to_sqldump(glue_reader_t *r, FILE *out, int split, const char *table)
{
    if (!r) return GLUE_E_ARG;
    build_columns(r, split); r->cursor = 0;
    glue_table_t t = { r->cols, r->ncols, gr_next_row, r };
    return glue_export_sqldump(out, &t, table);
}
#ifdef GLUE_WITH_SQLITE
int glue_reader_to_sqlite(glue_reader_t *r, const char *path, int split, const char *table)
{
    if (!r) return GLUE_E_ARG;
    build_columns(r, split); r->cursor = 0;
    glue_table_t t = { r->cols, r->ncols, gr_next_row, r };
    return glue_export_sqlite(path, &t, table);
}
#endif
