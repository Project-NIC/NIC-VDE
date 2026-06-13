/*
 * NIC-KSF — Kolmogorov Shannon Feistel
 * SPECK-128 CTR encryption library
 * Variant: 32-bit (two uint32_t) — for older avr-gcc
 *
 * MIT License
 * Copyright (c) 2026 NIC — Native Intellect Community
 *
 * ★ Viva La Resistánce ★
 */

#include "nic_ksf.h"
#include <string.h>

#define KSF_SPECK_ROUNDS 32

static inline void _load64(const uint8_t *p, uint32_t *hi, uint32_t *lo) {
    *lo = (uint32_t)p[0] | ((uint32_t)p[1]<<8) | ((uint32_t)p[2]<<16) | ((uint32_t)p[3]<<24);
    *hi = (uint32_t)p[4] | ((uint32_t)p[5]<<8) | ((uint32_t)p[6]<<16) | ((uint32_t)p[7]<<24);
}

static inline void _store64(uint8_t *p, uint32_t hi, uint32_t lo) {
    p[0]=(uint8_t)lo; p[1]=(uint8_t)(lo>>8); p[2]=(uint8_t)(lo>>16); p[3]=(uint8_t)(lo>>24);
    p[4]=(uint8_t)hi; p[5]=(uint8_t)(hi>>8); p[6]=(uint8_t)(hi>>16); p[7]=(uint8_t)(hi>>24);
}

static inline void _ror8(uint32_t *hi, uint32_t *lo) {
    uint32_t t = *lo & 0xFF;
    *lo = (*lo >> 8) | (*hi << 24);
    *hi = (*hi >> 8) | (t  << 24);
}

static inline void _rol3(uint32_t *hi, uint32_t *lo) {
    uint32_t t = *hi >> 29;
    *hi = (*hi << 3) | (*lo >> 29);
    *lo = (*lo << 3) | t;
}

static void _key_expand(const uint8_t key[16],
                         uint32_t rk_hi[KSF_SPECK_ROUNDS],
                         uint32_t rk_lo[KSF_SPECK_ROUNDS])
{
    uint32_t khi, klo, lhi, llo;
    _load64(key,     &khi, &klo);
    _load64(key + 8, &lhi, &llo);

    rk_hi[0] = khi;
    rk_lo[0] = klo;

    for (uint8_t i = 0; i < KSF_SPECK_ROUNDS - 1; i++) {
        _ror8(&lhi, &llo);
        uint32_t s_lo = llo + klo;
        uint32_t s_hi = lhi + khi + (s_lo < llo ? 1 : 0);
        lhi = s_hi;
        llo = s_lo ^ (uint32_t)i;
        _rol3(&khi, &klo);
        khi ^= lhi;
        klo ^= llo;
        rk_hi[i + 1] = khi;
        rk_lo[i + 1] = klo;
    }
}

static void _encrypt_block(const uint32_t rk_hi[KSF_SPECK_ROUNDS],
                            const uint32_t rk_lo[KSF_SPECK_ROUNDS],
                            uint8_t block[16])
{
    uint32_t xhi, xlo, yhi, ylo;
    _load64(block + 8, &xhi, &xlo);
    _load64(block + 0, &yhi, &ylo);

    for (uint8_t i = 0; i < KSF_SPECK_ROUNDS; i++) {
        _ror8(&xhi, &xlo);
        uint32_t s_lo = xlo + ylo;
        uint32_t s_hi = xhi + yhi + (s_lo < xlo ? 1 : 0);
        xhi = s_hi ^ rk_hi[i];
        xlo = s_lo ^ rk_lo[i];
        _rol3(&yhi, &ylo);
        yhi ^= xhi;
        ylo ^= xlo;
    }

    _store64(block + 8, xhi, xlo);
    _store64(block + 0, yhi, ylo);
}

static void _ksf_ctr(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len)
{
    uint32_t rk_hi[KSF_SPECK_ROUNDS], rk_lo[KSF_SPECK_ROUNDS];
    _key_expand(key, rk_hi, rk_lo);

    uint8_t ctr[KSF_BLOCK_SIZE];
    uint8_t done  = 0;
    uint8_t blk_i = 0;

    while (done < len) {
        memset(ctr, 0, KSF_BLOCK_SIZE);
        ctr[KSF_BLOCK_SIZE - 1] = blk_i;
        _encrypt_block(rk_hi, rk_lo, ctr);

        uint8_t chunk = len - done;
        if (chunk > KSF_BLOCK_SIZE) chunk = KSF_BLOCK_SIZE;

        for (uint8_t i = 0; i < chunk; i++)
            data[done + i] ^= ctr[i];

        done  += chunk;
        blk_i += 1;
    }
}

void ksf_encrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len)
{
    _ksf_ctr(key, data, len);
}

void ksf_decrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len)
{
    _ksf_ctr(key, data, len);
}
