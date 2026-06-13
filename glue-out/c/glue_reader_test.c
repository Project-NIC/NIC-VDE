/* glue_reader_test.c — build an .mla (C writer + DMD), read it back via the host
 * glue_reader and check the named CSV / SQL dump.  MIT */
#define _POSIX_C_SOURCE 200809L
#include "glue_reader.h"
#include "nic_mla.h"
#include "nic_dmd.h"
#include "hal/nic_mla_hal_posix.h"
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_pass = 0, g_fail = 0;
static void check(const char *w, int ok){ if(ok)g_pass++; else{g_fail++;printf("  FAIL: %s\n",w);} }

#define NREC 5
static const uint8_t SCHEMA[] = {
    MLA_SCHEMA_VER, 0, 2,
    2,0,0xFF,0x01,0,0,'t','e','m','p',0,0,0,0,
    2,0,0xFF,0x00,0,0,'h','u','m',0,0,0,0,0,
};
static const uint8_t STATION[] = { MLA_STATION_VER, 1, 7,0, 100,0, 0,0 };

static void build_mla(const char *path)
{
    mla_posix_file_t wf; mla_posix_create(&wf, path, 64u*1024u);
    mla_t m;
    mla_format_ex(&m, mla_posix_hal(&wf), 64u*1024u, MLA_CRC_FULL, 12, 0,
                  SCHEMA, sizeof SCHEMA, STATION, sizeof STATION);
    dmd_encoder_t e; dmd_encoder_init(&e,4); int s=0;
    for (int i=0;i<NREC;i++){ uint8_t r[4]; mla_put_u16(r,(uint16_t)(235+i)); mla_put_u16(r+2,(uint16_t)(600+i));
        uint8_t o[DMD_OUT_MAX]; uint16_t ol=dmd_compress(&e,r,o); s=(o[0]&7)==0?0:s+1;
        mla_append(&m,(uint32_t)(1000+i),0,1,o,ol,1,(uint8_t)s); }
    fclose(wf.f);
}
static char *to_buf(glue_reader_t *r, int sql)
{
    FILE *f = tmpfile();
    if (sql) glue_reader_to_sqldump(r,f,1,"records"); else glue_reader_to_csv(r,f,1);
    fflush(f); long n=ftell(f); rewind(f);
    char *b=malloc((size_t)n+1); if(b){size_t g=fread(b,1,(size_t)n,f);b[g]='\0';} fclose(f); return b;
}

int main(void)
{
    char path[]="/tmp/glue_reader_XXXXXX"; int fd=mkstemp(path); if(fd>=0)close(fd);
    build_mla(path);
    glue_reader_t *r = glue_reader_open(path);
    check("open", r!=NULL);
    if(!r){ printf("\nFAIL\n"); return 1; }
    check("count", glue_reader_record_count(r)==NREC);
    check("schema", glue_reader_has_schema(r));
    char *csv = to_buf(r,0);
    char *nl = csv?strchr(csv,'\n'):NULL;
    check("csv header", nl && strncmp(csv,"idx,time,unix,sta_idx,region,number,kind,length,subsec_hi,subsec_lo,temp,hum",(size_t)(nl-csv))==0);
    check("csv station+kind", csv && strstr(csv,",1,7,100,keyframe,"));
    check("csv decoded", csv && strstr(csv,",0,0,23.5,60"));
    char *sql = to_buf(r,1);
    check("sql create", sql && strstr(sql,"temp NUMERIC, hum NUMERIC);"));
    check("sql insert decoded", sql && strstr(sql,", 23.5, 60);"));
    free(csv); free(sql); glue_reader_close(r); remove(path);
    printf("\n%s  %d/%d passed\n", g_fail?"FAIL":"OK", g_pass, g_pass+g_fail);
    return g_fail?1:0;
}
