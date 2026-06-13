// SPDX-License-Identifier: MIT

#ifndef NIC_DMD_H
#define NIC_DMD_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

/* Buffer sizing — two modes:
   - DEFAULT (without -DDMD_PKT_MAX_BUILD): working buffers via C99 VLA (sized
     at runtime per packet), persistent previous[] = 255 B. Universal, suitable
     for PC / testing.
   - with -DDMD_PKT_MAX_BUILD=N: everything fixed to N, NO VLA, minimal RAM,
     works on compilers without VLA (IAR/Keil/SDCC). Recommended for MCU —
     set N to your maximum packet length. */
#ifndef DMD_PKT_MAX_BUILD
#define DMD_ENC_BUF_SIZE 255
#define DMD_VLA(type, name, size) type name[size]
#else
#define DMD_ENC_BUF_SIZE DMD_PKT_MAX_BUILD
#define DMD_VLA(type, name, size) type name[DMD_PKT_MAX_BUILD]
#endif

/* Protocol constants */
#define DMD_OUT_MAX (DMD_ENC_BUF_SIZE + 1)
#define DMD_KEYFRAME_EVERY 7 // Value 7 is reserved for protocol versioning

typedef struct {
    uint8_t pkt_len;
    uint8_t sample_num;
    uint8_t previous[DMD_ENC_BUF_SIZE];
} dmd_encoder_t;

typedef struct {
    uint8_t pkt_len;
    uint8_t previous[DMD_ENC_BUF_SIZE];
} dmd_decoder_t;

void dmd_encoder_init(dmd_encoder_t *enc, uint8_t pkt_len);
void dmd_decoder_init(dmd_decoder_t *dec, uint8_t pkt_len);

/* Compress a packet. Returns output length = 1 B header + payload.
   Return value range: 2 to 256 (uint16_t).
   Worst case: 255 B incompressible packet -> 256 B output (RAW),
   i.e. maximum expansion of 1 B (header). Compression always succeeds. */
uint16_t dmd_compress(dmd_encoder_t *enc, const uint8_t *current, uint8_t *output);

/* Decompress a packet. Returns 0 on success, negative code on error:
     0  = OK
    -1  = corrupted/invalid input (in_len=0 or payload length mismatch)
    -3  = reserved protocol version (sample_num=7 in header)
   in_len is uint16_t to accommodate the maximum 256 B packet. */
int dmd_decompress(dmd_decoder_t *dec, const uint8_t *input, uint16_t in_len, uint8_t *output);

#endif /* NIC_DMD_H */
