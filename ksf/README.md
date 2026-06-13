<p align="center">
  <img src="NICKSF.svg" width="200"/>
</p>

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

# NIC-KSF — Kolmogorov Shannon Feistel

## Encryption library for embedded devices

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

## What is KSF?

NIC-KSF is a lightweight symmetric encryption library built on the **SPECK-128** block cipher operating in **CTR (Counter) mode**. It is designed for resource-constrained microcontrollers such as the ATmega328, where RAM and Flash are scarce and computational overhead must be minimal.

The library has a single, clear responsibility: **encrypt or decrypt a block of data using a 128-bit key**. All key management, key derivation, session handling, and protocol logic are intentionally delegated to higher-level layers.

---

## Features

- SPECK-128/128 block cipher
- CTR mode — encrypt and decrypt are identical operations (XOR keystream)
- In-place operation — no additional buffer required
- Supports any payload length from 1 to 255 bytes
- No dynamic memory allocation (`malloc`)
- No dependencies beyond `<stdint.h>` and `<string.h>`
- Compatible with AVR (ATmega328) and standard C99 compilers
- Two implementation variants — see below

---

## Implementation Variants

Both variants share the same interface (`nic_ksf.h`) and produce identical results.

| File | Description |
|---|---|
| `nic_ksf_32.c` | Manual 32-bit arithmetic — for older `avr-gcc` |
| `nic_ksf_64.c` | Native `uint64_t` — for newer `avr-gcc` and PC |

Newer versions of `avr-gcc` (Arduino IDE) can translate native 64-bit operations into a very efficient sequence of `add` / `adc` instructions. We recommend testing both variants and choosing the one that produces smaller or faster code for your specific project.

---

## Security Model

NIC-KSF is a **pure cryptographic primitive**. It does not manage keys, sessions, or packet counters.

**The caller is responsible for ensuring that each call to `ksf_encrypt` receives a unique 128-bit key.**

If the same key were used for two different packets, an attacker could XOR the captured ciphertexts and the keystream would cancel out. Ensuring key uniqueness is the full responsibility of the calling layer.

---

## API

```c
#include "nic_ksf.h"

/* Encrypts data in-place using a 128-bit key.
 * key  : 16 bytes (128 bits) — provided by the upper layer
 * data : pointer to buffer (overwritten with encrypted result)
 * len  : number of bytes (1–255)
 */
void ksf_encrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len);

/* Decrypts data in-place. Identical to ksf_encrypt in CTR mode. */
void ksf_decrypt(const uint8_t key[KSF_KEY_SIZE], uint8_t *data, uint8_t len);
```

---

## Usage Example

```c
#include "nic_ksf.h"

/* 128-bit key prepared by the calling layer */
uint8_t key[16] = { /* ... 16 bytes ... */ };

/* Data to encrypt */
uint8_t payload[20] = { /* ... data ... */ };

/* Encrypt in-place */
ksf_encrypt(key, payload, sizeof(payload));

/* ... transmit ... */

/* Decrypt on the receiver side */
ksf_decrypt(key, payload, sizeof(payload));
```

---

## Building

### PC / Linux

```bash
# 32-bit variant
gcc -std=c99 -Wall -Isrc -o test_ksf tests/test_ksf.c src/nic_ksf_32.c

# 64-bit variant
gcc -std=c99 -Wall -Isrc -o test_ksf tests/test_ksf.c src/nic_ksf_64.c

./test_ksf
```

Or simply run `make` (builds and runs both variants).

### AVR / ATmega328

```bash
avr-gcc -std=c99 -mmcu=atmega328p -Os -Isrc -o nic_ksf.elf src/nic_ksf_32.c
```

---

## Project structure

| Path | Description |
|---|---|
| `src/nic_ksf.h` | Public interface and constants |
| `src/nic_ksf_32.c` | SPECK-128 CTR implementation — 32-bit variant |
| `src/nic_ksf_64.c` | SPECK-128 CTR implementation — 64-bit variant |
| `python/nic_ksf.py` | Python reference implementation (testing) |
| `python/ksf_demo.py` | End-to-end demo script |
| `tests/test_ksf.c` | C test suite |
| `tests/test_ksf.py` | Python test suite |
| `Makefile` | Build for PC and AVR |
| `README_cs.md`, `README_ru.md` | Translated documentation (cs, ru) |
---

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgements

To my brother for advice during the development of this project.
For technical assistance with code optimisation, to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
