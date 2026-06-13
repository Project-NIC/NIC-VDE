/* SPDX-License-Identifier: MIT
 *
 * nic_mseed.h — Steim-1/2 + miniSEED 2.x record writer, portable C (C11, no deps).
 *
 * A faithful C port of the NIC-MSEED codec core (the "container-agnostic" layer:
 * integers -> Steim frames -> miniSEED records, and back). It is byte-exact with
 * the Python reference (tests/vectors.h are generated from it), which is itself
 * validated against ObsPy — so this output drops straight into ObsPy / SeisComP /
 * the FDSN toolchain.
 *
 * Embeddable like the rest of the NIC C ecosystem: no malloc, no globals; the
 * caller owns every buffer. It lets the data station (ESP32) write miniSEED in C,
 * not only a host PC. The MLA+DMD wiring (from_mla) stays in the Python tool — a C
 * converter would need C MLA/DMD readers, a separate follow-up.
 */
#ifndef NIC_MSEED_H
#define NIC_MSEED_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define NIC_STEIM1 1
#define NIC_STEIM2 2

/* Return codes: 0 = OK, negative = error. */
#define NIC_MSEED_OK         0
#define NIC_MSEED_EOVERFLOW (-1)  /* a difference exceeds the version's largest field   */
#define NIC_MSEED_EINVAL    (-2)  /* bad argument                                       */
#define NIC_MSEED_ENOSPACE  (-3)  /* output buffer too small / a diff won't fit a record */

#define NIC_STEIM_FRAME_BYTES 64

/* ── Steim-1/2 codec (container-agnostic) ──────────────────────────────────── */

/* Encode as many leading `samples` as fit into one record of `frames_per_record`
 * 64-byte frames. Writes exactly frames_per_record*64 bytes to `out`. `prev` is
 * the previous record's last sample (0 for the first record). On success sets
 * *used_samples (>=1) and returns NIC_MSEED_OK. */
int nic_steim_encode_record(const int32_t *samples, size_t nsamp,
                            int version, int frames_per_record, int32_t prev,
                            uint8_t *out, size_t *used_samples);

/* Decode one Steim record (frames_len bytes, a multiple of 64) back to exactly
 * `n_samples` integers, written to `out`. Returns NIC_MSEED_OK. */
int nic_steim_decode_record(const uint8_t *frames, size_t frames_len,
                            size_t n_samples, int version, int32_t *out);

/* ── miniSEED 2.x record writer ────────────────────────────────────────────── */

/* SEED sample-rate (factor, multiplier): positive integer Hz -> (rate, 1);
 * integer sub-Hz period -> (-period, 1). */
void nic_mseed_rate_factor_mult(double rate_hz, int16_t *factor, int16_t *mult);

typedef struct {
    const char *network;    /* SEED network  code, <= 2 chars */
    const char *station;    /* SEED station  code, <= 5 chars */
    const char *location;   /* SEED location code, <= 2 chars */
    const char *channel;    /* SEED channel  code, <= 3 chars */
    double      sample_rate_hz;
    int         version;    /* NIC_STEIM1 or NIC_STEIM2       */
    int         reclen;     /* power of two >= 128 (e.g. 512) */
} nic_mseed_params_t;

/* Write ONE miniSEED record (p->reclen bytes) into `out` (capacity >= reclen).
 * Encodes as many of the `nsamp` samples as fit; sets *used. `seq` is the record
 * sequence number, `prev` the previous record's last sample, and
 * start_unix/start_frac this record's start time. Returns NIC_MSEED_OK. */
int nic_mseed_write_record(const nic_mseed_params_t *p,
                           const int32_t *samples, size_t nsamp,
                           uint32_t seq, int32_t prev,
                           int64_t start_unix, double start_frac,
                           uint8_t *out, size_t *used);

/* Convenience: encode a whole channel series into `out` (capacity out_cap),
 * looping fixed-length records; each record's time derives from the start time,
 * the rate and the samples already emitted. Returns total bytes written, or a
 * negative NIC_MSEED_E* code (ENOSPACE if out_cap is too small). */
long nic_mseed_write_stream(const nic_mseed_params_t *p,
                            const int32_t *samples, size_t nsamp,
                            int64_t start_unix, double start_frac,
                            uint32_t seq_start,
                            uint8_t *out, size_t out_cap);

/* One decoded record's header (companion to a minimal reader, for round-trips). */
typedef struct {
    char     network[4], station[8], location[4], channel[8];
    int64_t  start_unix;
    double   start_frac;
    double   rate_hz;
    uint16_t nsamples;
    size_t   reclen;
} nic_mseed_rechdr_t;

/* Parse ONE record at `rec` (>= its reclen, read from Blockette 1000): fill `hdr`
 * and decode up to out_cap samples into `samples_out`. Returns NIC_MSEED_OK. */
int nic_mseed_read_record(const uint8_t *rec, size_t rec_cap,
                          nic_mseed_rechdr_t *hdr,
                          int32_t *samples_out, size_t out_cap);

#ifdef __cplusplus
}
#endif

#endif /* NIC_MSEED_H */
