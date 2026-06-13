// SPDX-License-Identifier: MIT
//
// NIC-MLA — host-side value decoder (see mla_decode.h). No libm dependency: the
// power of ten is built by a small loop, so it links anywhere.

#include "mla_decode.h"
#include <string.h>

void mla_field_parse(const uint8_t desc[MLA_FIELD_DESC_SIZE], mla_field_t *f) {
    f->width     = desc[0];
    f->unit      = desc[1];
    f->exp10     = (int8_t)desc[2];
    f->is_signed = (uint8_t)(desc[3] & 0x01u);
    f->offset    = (int16_t)((uint16_t)desc[4] | ((uint16_t)desc[5] << 8));
    memcpy(f->name, desc + 6, MLA_NAME_LEN);
    f->name[MLA_NAME_LEN] = '\0';
}

double mla_decode_value(const mla_field_t *f, const uint8_t *raw) {
    int64_t v = 0;
    int     i, e;
    double  scaled, p;

    for (i = (int)f->width - 1; i >= 0; i--)
        v = (v << 8) | raw[i];                       // little-endian

    if (f->is_signed) {                              // sign-extend from width*8 bits
        int bits = (int)f->width * 8;
        if (bits < 64 && (v & ((int64_t)1 << (bits - 1))))
            v -= ((int64_t)1 << bits);
    }

    scaled = (double)(v + (int64_t)f->offset);
    e = f->exp10;
    p = 1.0;
    if (e > 0) { while (e-- > 0) p *= 10.0; return scaled * p; }
    if (e < 0) { while (e++ < 0) p *= 10.0; return scaled / p; }
    return scaled;
}
