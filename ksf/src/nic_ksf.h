/*
 * NIC-KSF — Kolmogorov Shannon Feistel
 * SPECK-128 CTR encryption library
 *
 * MIT License
 * Copyright (c) 2026 NIC — Native Intellect Community
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.
 *
 * ★ Viva La Resistánce ★
 */

#ifndef NIC_KSF_H
#define NIC_KSF_H

#include <stdint.h>

#define KSF_VERSION_MAJOR  1
#define KSF_VERSION_MINOR  2
#define KSF_VERSION        "1.2"

#define KSF_KEY_SIZE   16   /* Key:   128 bits = 16 bytes */
#define KSF_BLOCK_SIZE 16   /* Block: 128 bits = 16 bytes */
#define KSF_MAX_DATA  255   /* Max data length (1 byte)   */

/* Arduino sketches (.ino) and other C++ code compile as C++. Without extern "C"
 * the linker would not find the .c symbols because of name mangling. */
#ifdef __cplusplus
extern "C" {
#endif

/*
 * Encrypt / decrypt — in-place, CTR mode.
 * In CTR mode both operations are identical (XOR with the keystream).
 *
 * key  : 16 bytes — provided by the calling layer
 * data : data buffer (overwritten with the result)
 * len  : length in bytes (1–255)
 *
 * Every call must receive a unique key.
 * Ensuring uniqueness is the calling layer's responsibility.
 */
void ksf_encrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len);
void ksf_decrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len);

#ifdef __cplusplus
}
#endif

#endif /* NIC_KSF_H */
