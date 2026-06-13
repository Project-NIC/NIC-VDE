/*
 * glue_out.c  —  NIC-GLUE-OUT: streaming, no-heap read/export (embedded). MIT
 * ★ Viva La Resistánce ★
 */
#include "glue_out.h"

#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

/* ── small helpers ──────────────────────────────────────────────────────────── */
static int64_t le_int(const uint8_t *p, int width, int is_signed)
{
    uint64_t u = 0;
    for (int i = 0; i < width; i++) u |= (uint64_t)p[i] << (8 * i);
    if (is_signed && width < 8) {
        uint64_t sign = (uint64_t)1 << (width * 8 - 1);
        if (u & sign) u |= ~((sign << 1) - 1);
    }
    return (int64_t)u;
}

static void fmt_real(char *buf, size_t n, double v)   /* %.6f, strip trailing 0 / . */
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

static const char *kind_str(const mla_log_t *r)
{
    if (!mla_flags_compressed(r->flags)) return "raw";
    return mla_flags_kf_back(r->flags) == 0 ? "keyframe" : "delta";
}

/* ── prefix → schema / station tables (port of mla_schema.py, fixed arrays) ──── */
static int parse_tables(glue_out_t *g, const uint8_t *pfx, uint32_t plen)
{
    g->n_data = 0; g->pkt_len = 0; g->n_sta = 0;
    if (plen <= MLA_SCHEMA_OFF) return GLUE_OUT_OK;
    uint8_t ver = pfx[MLA_SCHEMA_OFF];
    uint32_t schema_bytes = 0;

    if (ver != 0x00 && ver != 0xFF) {
        if (ver != MLA_SCHEMA_VER || plen < MLA_SCHEMA_OFF + 3u) return GLUE_OUT_E_FMT;
        uint8_t n_log  = pfx[MLA_SCHEMA_OFF + 1];
        uint8_t n_data = pfx[MLA_SCHEMA_OFF + 2];
        schema_bytes = 3u + (uint32_t)MLA_FIELD_SIZE * (n_log + n_data);
        if (plen < MLA_SCHEMA_OFF + schema_bytes) return GLUE_OUT_E_FMT;
        if (n_data > GLUE_OUT_MAX_FIELDS) return GLUE_OUT_E_FMT;

        const uint8_t *d = pfx + MLA_SCHEMA_OFF + 3 + (uint32_t)MLA_FIELD_SIZE * n_log;
        for (int i = 0; i < n_data; i++) {
            const uint8_t *f = d + (size_t)i * MLA_FIELD_SIZE;
            glue_out_field_t *gf = &g->field[i];
            gf->width = f[0]; gf->exp10 = (int8_t)f[2];
            gf->is_signed = (uint8_t)(f[3] & 0x01);
            gf->offset = (int16_t)(f[4] | (f[5] << 8));
            size_t nl = 0; while (nl < 8 && f[6 + nl]) nl++;
            memcpy(gf->name, f + 6, nl); gf->name[nl] = '\0';
            if (!gf->name[0]) snprintf(gf->name, sizeof gf->name, "data%d", i);
            g->pkt_len += gf->width;
        }
        g->n_data = n_data;
    }

    uint32_t off = MLA_SCHEMA_OFF + schema_bytes;
    if (plen >= off + 2u && pfx[off] == MLA_STATION_VER) {
        uint8_t n = pfx[off + 1];
        if (n > GLUE_OUT_MAX_STA) n = GLUE_OUT_MAX_STA;
        if (plen >= off + 2u + (uint32_t)MLA_STATION_REC * n) {
            memcpy(g->sta, pfx + off + 2, (size_t)n * MLA_STATION_REC);
            g->n_sta = n;
        }
    }
    return GLUE_OUT_OK;
}

int glue_out_mount(glue_out_t *g, mla_hal_t hal)
{
    if (!g) return GLUE_OUT_E_ARG;
    memset(g, 0, sizeof *g);
    if (mla_mount(&g->mla, hal) != MLA_OK) return GLUE_OUT_E_IO;

    uint32_t psz = g->mla.data_base ? g->mla.data_base : MLA_PREFIX_SIZE;
    if (psz > sizeof g->pfx) psz = sizeof g->pfx;
    for (uint32_t got = 0; got < psz; ) {
        uint32_t c = psz - got; if (c > 32768u) c = 32768u;
        if (hal.read(hal.ctx, got, g->pfx + got, (uint16_t)c) != MLA_OK) return GLUE_OUT_E_IO;
        got += c;
    }
    return parse_tables(g, g->pfx, psz);
}

int glue_out_has_schema(const glue_out_t *g) { return g && g->n_data > 0; }

/* ── streaming export ───────────────────────────────────────────────────────── */
typedef struct {
    glue_out_t   *g;
    glue_out_sink sink;
    void         *ctx;
    int           split;   /* subsec: 1 = hi/lo, 0 = u16 */
    int           sql;     /* 0 = CSV, 1 = SQL INSERT rows */
    const char   *table;
    uint32_t      idx;
    int           err;
} ec_t;

static int emitf(ec_t *e, const char *fmt, ...)
{
    char b[160];
    va_list ap; va_start(ap, fmt);
    int n = vsnprintf(b, sizeof b, fmt, ap);
    va_end(ap);
    if (n < 0) return -1;
    if (n > (int)sizeof b - 1) n = sizeof b - 1;
    return e->sink(e->ctx, b, n);
}

/* CSV: replace , and CR/LF so a cell stays one column (mirrors the Python lib) */
static int emit_csv_text(ec_t *e, const char *s)
{
    char b[64]; int n = 0;
    for (; *s; ++s) {
        char c = *s; if (c == ',') c = ';'; else if (c == '\n' || c == '\r') c = ' ';
        b[n++] = c;
        if (n == (int)sizeof b - 1) { if (e->sink(e->ctx, b, n)) return -1; n = 0; }
    }
    return n ? e->sink(e->ctx, b, n) : 0;
}

/* SQL: '…' with '' escaping */
static int emit_sql_text(ec_t *e, const char *s)
{
    if (emitf(e, "'")) return -1;
    char b[64]; int n = 0;
    for (; *s; ++s) {
        if (*s == '\'') b[n++] = '\'';
        b[n++] = *s;
        if (n >= (int)sizeof b - 2) { if (e->sink(e->ctx, b, n)) return -1; n = 0; }
    }
    if (n && e->sink(e->ctx, b, n)) return -1;
    return emitf(e, "'");
}

static int emit_value_field(ec_t *e, const glue_out_field_t *f, const uint8_t *p)
{
    int64_t scaled = le_int(p, f->width, f->is_signed) + f->offset;
    if (f->exp10 >= 0) {
        int64_t m = 1; for (int k = 0; k < f->exp10; k++) m *= 10;
        return emitf(e, "%lld", (long long)(scaled * m));
    }
    double d = (double)scaled; for (int k = 0; k < -f->exp10; k++) d /= 10.0;
    char b[40]; fmt_real(b, sizeof b, d);
    return emitf(e, "%s", b);
}

static int emit_null_or_blank(ec_t *e) { return e->sql ? emitf(e, "NULL") : 0; }

static int emit_record(void *user, mla_t *m, const mla_log_t *rec)
{
    ec_t *e = user; glue_out_t *g = e->g;
    uint16_t len = 0;
    if (mla_read_data(m, rec, g->blk, sizeof g->blk, &len) != MLA_OK) { e->err = 1; return 1; }

    /* decode payload: raw passes through; compressed → per-station DMD decoder */
    const uint8_t *payload = NULL; int plen = 0; uint8_t dbuf[256];
    if (mla_flags_compressed(rec->flags)) {
        if (g->pkt_len > 0 && g->pkt_len <= 255 &&
            rec->station >= 1 && rec->station <= GLUE_OUT_MAX_CHAN) {
            if (!g->dec_on[rec->station]) {
                dmd_decoder_init(&g->dec[rec->station], (uint8_t)g->pkt_len);
                g->dec_on[rec->station] = 1;
            }
            if (dmd_decompress(&g->dec[rec->station], g->blk, len, dbuf) == 0) {
                payload = dbuf; plen = g->pkt_len;
            }
        }
    } else {
        payload = g->blk; plen = len;
    }

    /* region / number from the station table */
    int has_rn = (g->n_sta && rec->station >= 1 && rec->station <= g->n_sta);
    long region = 0, number = 0;
    if (has_rn) {
        const uint8_t *s = &g->sta[(rec->station - 1) * MLA_STATION_REC];
        region = mla_get_u16(s); number = mla_get_u16(s + 2);
    }

    /* time → ISO UTC (gmtime is C-standard; fine for single-threaded export) */
    char tbuf[24]; time_t t = (time_t)rec->timestamp;
    struct tm tmv; struct tm *tp = gmtime(&t);
    if (tp) tmv = *tp; else memset(&tmv, 0, sizeof tmv);
    strftime(tbuf, sizeof tbuf, "%Y-%m-%d %H:%M:%S", &tmv);

    const char *sep = e->sql ? ", " : ",";
    if (e->sql && emitf(e, "INSERT INTO %s VALUES (", e->table ? e->table : "records")) { e->err = 1; return 1; }

    /* base columns */
    if (emitf(e, "%u%s", e->idx, sep)) { e->err = 1; return 1; }
    if (e->sql) { if (emit_sql_text(e, tbuf)) { e->err = 1; return 1; } }
    else        { if (emit_csv_text(e, tbuf)) { e->err = 1; return 1; } }
    emitf(e, "%s%u%s%u%s", sep, (unsigned)rec->timestamp, sep, (unsigned)rec->station, sep);
    if (has_rn) emitf(e, "%ld%s%ld%s", region, sep, number, sep);
    else        { emit_null_or_blank(e); emitf(e, "%s", sep); emit_null_or_blank(e); emitf(e, "%s", sep); }
    if (e->sql) emit_sql_text(e, kind_str(rec)); else emit_csv_text(e, kind_str(rec));
    emitf(e, "%s%u%s", sep, (unsigned)rec->length, sep);

    /* subsec */
    if (e->split) emitf(e, "%u%s%u", (rec->subsec >> 8) & 0xFF, sep, rec->subsec & 0xFF);
    else          emitf(e, "%u", rec->subsec);

    /* data fields (or schemaless single value) */
    if (g->n_data > 0) {
        int fits = (payload && plen == g->pkt_len);
        size_t pos = 0;
        for (int i = 0; i < g->n_data; i++) {
            emitf(e, "%s", sep);
            if (fits) { emit_value_field(e, &g->field[i], payload + pos); pos += g->field[i].width; }
            else      emit_null_or_blank(e);
        }
    } else {
        emitf(e, "%s", sep);
        if (payload && (plen == 1 || plen == 2 || plen == 4))
            emitf(e, "%lld", (long long)le_int(payload, plen, 0));
        else if (e->sql) emitf(e, "'<%s %uB>'", kind_str(rec), (unsigned)rec->length);
        else             emitf(e, "<%s %uB>", kind_str(rec), (unsigned)rec->length);
    }

    if (e->sql) emitf(e, ");\n"); else emitf(e, "\n");
    e->idx++;
    return e->err ? 1 : 0;
}

static const char *BASE = "idx,time,unix,sta_idx,region,number,kind,length";

static int run(glue_out_t *g, glue_out_sink sink, void *ctx, int split, int sql, const char *table)
{
    if (!g || !sink) return GLUE_OUT_E_ARG;
    for (int i = 1; i <= GLUE_OUT_MAX_CHAN; i++) g->dec_on[i] = 0;   /* fresh decoders */

    ec_t e = { g, sink, ctx, split, sql, table, 0, 0 };

    if (sql) {
        if (emitf(&e, "CREATE TABLE %s (idx INTEGER, time TEXT, unix INTEGER, sta_idx INTEGER, "
                      "region INTEGER, number INTEGER, kind TEXT, length INTEGER",
                  table ? table : "records")) return GLUE_OUT_E_IO;
        if (split) emitf(&e, ", subsec_hi INTEGER, subsec_lo INTEGER");
        else       emitf(&e, ", subsec INTEGER");
        if (g->n_data > 0) for (int i = 0; i < g->n_data; i++) emitf(&e, ", %s NUMERIC", g->field[i].name);
        else emitf(&e, ", value TEXT");
        emitf(&e, ");\nBEGIN;\n");
    } else {
        if (sink(ctx, BASE, (int)strlen(BASE))) return GLUE_OUT_E_IO;
        if (split) sink(ctx, ",subsec_hi,subsec_lo", 20); else sink(ctx, ",subsec", 7);
        if (g->n_data > 0) for (int i = 0; i < g->n_data; i++) { sink(ctx, ",", 1); sink(ctx, g->field[i].name, (int)strlen(g->field[i].name)); }
        else sink(ctx, ",value", 6);
        sink(ctx, "\n", 1);
    }

    mla_foreach(&g->mla, NULL, emit_record, &e);
    if (e.err) return GLUE_OUT_E_IO;
    if (sql) emitf(&e, "COMMIT;\n");
    return GLUE_OUT_OK;
}

int glue_out_csv(glue_out_t *g, glue_out_sink sink, void *ctx, int split)
{
    return run(g, sink, ctx, split, 0, NULL);
}
int glue_out_sqldump(glue_out_t *g, glue_out_sink sink, void *ctx, int split, const char *table)
{
    return run(g, sink, ctx, split, 1, table);
}
