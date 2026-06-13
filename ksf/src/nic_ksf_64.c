/*
 * NIC-KSF — Kolmogorov Shannon Feistel
 * SPECK-128 CTR encryption library
 * Variant: 64-bit (uint64_t) — for newer avr-gcc / PC
 *
 * MIT License
 * Copyright (c) 2026 NIC — Native Intellect Community
 *
 * ★ Viva La Resistánce ★
 */

#include "nic_ksf.h"
#include <string.h>

#define KSF_SPECK_ROUNDS 32

static inline uint64_t _load64(const uint8_t *p) {
    uint64_t v = 0;
    for (uint8_t i = 0; i < 8; i++)
        v |= ((uint64_t)p[i] << (i * 8));
    return v;
}

static inline void _store64(uint8_t *p, uint64_t v) {
    for (uint8_t i = 0; i < 8; i++)
        p[i] = (uint8_t)(v >> (i * 8));
}

static inline uint64_t _ror64(uint64_t x, uint8_t r) { return (x >> r) | (x << (64 - r)); }
static inline uint64_t _rol64(uint64_t x, uint8_t r) { return (x << r) | (x >> (64 - r)); }

static void _key_expand(const uint8_t key[16], uint64_t rk[KSF_SPECK_ROUNDS])
{
    uint64_t k = _load64(key);
    uint64_t l = _load64(key + 8);

    rk[0] = k;

    for (uint8_t i = 0; i < KSF_SPECK_ROUNDS - 1; i++) {
        l = (_ror64(l, 8) + k) ^ i;
        k = _rol64(k, 3) ^ l;
        rk[i + 1] = k;
    }
}

static void _encrypt_block(const uint64_t rk[KSF_SPECK_ROUNDS], uint8_t block[16])
{
    uint64_t x = _load64(block + 8);
    uint64_t y = _load64(block + 0);

    for (uint8_t i = 0; i < KSF_SPECK_ROUNDS; i++) {
        x = (_ror64(x, 8) + y) ^ rk[i];
        y = _rol64(y, 3) ^ x;
    }

    _store64(block + 8, x);
    _store64(block + 0, y);
}

static void _ksf_ctr(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len)
{
    uint64_t rk[KSF_SPECK_ROUNDS];
    _key_expand(key, rk);

    uint8_t ctr[KSF_BLOCK_SIZE];
    uint8_t done  = 0;
    uint8_t blk_i = 0;

    while (done < len) {
        memset(ctr, 0, KSF_BLOCK_SIZE);
        ctr[KSF_BLOCK_SIZE - 1] = blk_i;
        _encrypt_block(rk, ctr);

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
