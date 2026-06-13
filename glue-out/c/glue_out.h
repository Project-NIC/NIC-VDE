/*
 * glue_out.h  —  NIC-GLUE-OUT: STREAMING read/export, embedded-capable (no heap).
 *
 * The MCU-friendly counterpart to the host glue_reader: it sweeps an .mla one
 * record at a time (mla_foreach), decompresses NIC-DMD per station, decodes the
 * schema, and emits rows through a write callback — so it runs on an STM-class
 * MCU (nothing is loaded whole into RAM, no malloc). The container, region/number
 * resolution and column naming are done here (the glue names everything); the
 * output sink just receives bytes.
 *
 * Embeddable outputs: CSV and SQL text dump (both streamed). A real binary
 * SQLite .db needs libsqlite3 and stays host-only (use the .sql dump on the MCU).
 *
 * Caller-allocated, no heap. The struct embeds fixed-capacity tables; tune with
 * -DGLUE_OUT_MAX_FIELDS / _MAX_STA / _MAX_CHAN / _BLK.
 *
 * MIT  |  ★ Viva La Resistánce ★
 */
#ifndef GLUE_OUT_H
#define GLUE_OUT_H

#include <stdint.h>
#include "nic_mla.h"
#include "nic_dmd.h"

#ifdef __cplusplus
extern "C" {
#endif

#ifndef GLUE_OUT_MAX_FIELDS
#define GLUE_OUT_MAX_FIELDS 32      /* schema DATA fields                       */
#endif
#ifndef GLUE_OUT_MAX_STA
#define GLUE_OUT_MAX_STA 32         /* station-table entries kept for region/num */
#endif
#ifndef GLUE_OUT_MAX_CHAN
#define GLUE_OUT_MAX_CHAN 8         /* per-station DMD decoders (station 1..N)    */
#endif
#ifndef GLUE_OUT_BLK
#define GLUE_OUT_BLK 300            /* one record's data buffer                   */
#endif

#define GLUE_OUT_OK      0
#define GLUE_OUT_E_ARG  (-1)
#define GLUE_OUT_E_IO   (-2)
#define GLUE_OUT_E_FMT  (-3)

/* Output sink: receive `len` bytes. Return 0 on success, <0 to abort.
 * (Wrap a FILE* on host, or FatFs/UART on an MCU.) */
typedef int (*glue_out_sink)(void *ctx, const char *buf, int len);

typedef struct {
    char    name[9];
    uint8_t width;
    int8_t  exp10;
    uint8_t is_signed;
    int16_t offset;
} glue_out_field_t;

typedef struct {
    mla_t            mla;
    glue_out_field_t field[GLUE_OUT_MAX_FIELDS];
    int              n_data;
    int              pkt_len;
    uint8_t          sta[GLUE_OUT_MAX_STA * MLA_STATION_REC];
    int              n_sta;
    dmd_decoder_t    dec[GLUE_OUT_MAX_CHAN + 1];   /* indexed by station 1..N */
    uint8_t          dec_on[GLUE_OUT_MAX_CHAN + 1];
    uint8_t          blk[GLUE_OUT_BLK];
    uint8_t          pfx[MLA_PREFIX_SIZE];
} glue_out_t;

/* Mount over a caller-provided HAL and parse the schema/station tables. */
int glue_out_mount(glue_out_t *g, mla_hal_t hal);

int glue_out_has_schema(const glue_out_t *g);

/* Stream the whole container to the sink. subsec_split: 1 → subsec_hi/lo, 0 → subsec.
 * table (sqldump) may be NULL → "records". Return GLUE_OUT_OK/<0. */
int glue_out_csv(glue_out_t *g, glue_out_sink sink, void *ctx, int subsec_split);
int glue_out_sqldump(glue_out_t *g, glue_out_sink sink, void *ctx,
                     int subsec_split, const char *table);

#ifdef __cplusplus
}
#endif
#endif /* GLUE_OUT_H */
