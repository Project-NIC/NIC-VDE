<p align="center">
  <img src="NICDMD.svg" width="200"/>
</p>

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

★ N.I.C. ★

# NIC DMD — Delta Markov Duda

## Compression Protocol for Embedded Devices

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

## What is DMD?

DMD is a multiplatform compression protocol for small data packets from weather stations, electricity meters, GPS trackers, and other embedded devices. It is designed for transmission over bandwidth-limited technologies such as LoRa.

The protocol is fully functional on the ATmega328 controller and requires no large dictionaries or lookup tables in memory. Each packet is compressed independently — using adaptive selection of the best method from five candidates.

---

## Why DMD?

Existing compression libraries for embedded devices either require hundreds of extra bytes of RAM (Heatshrink) or need to transmit the Huffman table along with the data. DMD takes a different approach — it combines several simple methods with heuristic analysis and selects the best result for each packet individually.

**Main advantages:**
- Fixed Huffman table in ROM only (64B), no extra RAM
- Adaptive method selection for each packet — up to 5 candidates
- Fully deterministic decompression — no data loss
- Maximum data expansion of 1 byte (header) in the worst case
- Implementations in both Python and C (ATmega328 / Arduino)

---

## When DMD is Not Worth It

DMD is designed for data that changes slowly and predictably over time — sensor values, GPS coordinates, industrial telemetry. If the input data is random, encrypted, or already compressed, DMD adds only 1 byte of header and sends it as RAW. This is the correct behavior — no lossy compression, no degradation.

---

## Compatibility

**Python:** 3.10 or newer (uses type annotations `bytes | None`).

**C:** C99 or newer. Tested with GCC on PC (Linux/Windows) and AVR-GCC for ATmega328. No dependencies on standard library except `<string.h>`. Internal buffers are dimensioned using C99 VLA according to the actual packet length.

**Arduino:** Copy `c/nic_dmd.c` and `c/nic_dmd.h` into your project folder. Compatible with Arduino IDE 1.8+ and 2.x (AVR-GCC supports C99 VLA).

**Note for other compilers:** IAR, Keil, and MSVC C++ do not support VLA. For these toolchains, you can define `-DDMD_PKT_MAX_BUILD=N` during compilation (e.g., 32 or 64) and the buffers will be fixed-size.

**Dependencies for fetch/benchmark:** `pip install requests`

**Packet length:** The minimum technical limit is 1B, but below 16B compression is practically worthless — header overhead (1B) and ANS state (2B) consume most of the potential savings. The recommended minimum is **16B**. Maximum is **255B**. For LoRa transmission, the practical payload limit is 51–64B depending on spreading factor and region. DMD achieves the best results on data where neighboring packets change slowly — typically 16–64B sensor telemetry.

---

## Data Validation and Integrity

In pursuit of maximum performance and absolute minimization of processor load, the library performs no additional header checks or validation of input data length.

The protocol design strictly assumes that integrity checks (e.g., hardware CRC) and discarding of corrupted or empty packets are handled by the lower transport layer or the main program (typically the radio module itself, data collection logic, etc.). Users of the library must ensure at the application level that only structurally correct data is passed to the compression and decompression functions. By delegating this responsibility, low memory overhead has been achieved without wasting processor cycles.

---

## Results

Tested on over 50,000 samples from 20 real and synthetic data sources (weather stations, GPS, electricity meters, industrial sensors, seismology, air quality). Round-trip errors: **0 in all datasets**.

The **output B/pkt** column is the average actual size of the transmitted packet after compression (including 1B header). This is the decisive figure for sizing the LoRa transmission window.

### Table 1 — uniform int16 (fetch_plus.py)

All fields stored as `int16` with ×100 scaling, packets padded to fixed length with zeros. Forecast datasets have 384 samples (16 days × 24 hours), others 8,000–10,000 samples.

```
===================================================================================
  Dataset                    | Pkts  | Input | Output  | Saving | Dominant method
------------------------------|-------|-------|---------|--------|------------------
NOAA San Francisco (tides)    |  8184 |  16 B |   6.4 B |  62.2% | DELTA1+ZZ+FLAG
NOAA New York (tides)         |  8184 |  16 B |   6.7 B |  60.6% | DELTA1+ZZ+FLAG
DWD Fichtelberg (meteo)       | 10000 |  16 B |   8.1 B |  52.6% | DELTA1+ZZ+FLAG 75%
DWD Helgoland (meteo)         | 10000 |  16 B |   8.5 B |  49.8% | DELTA1+ZZ+FLAG 74%
DWD Zugspitze (meteo)         | 10000 |  16 B |   8.6 B |  49.2% | DELTA1+ZZ+FLAG 73%
GPS Trek                      | 10000 |  16 B |   8.6 B |  49.3% | DELTA1+ZZ+FLAG 53%
Complex station               | 10000 |  64 B |  38.6 B |  40.7% | DELTA1+ZZ+HUF  84%
AirQuality Brno               |   168 |  16 B |  10.3 B |  39.7% | FLAG + D1+ZZ+FLAG
AirQuality Ostrava            |   168 |  16 B |  10.5 B |  38.4% | FLAG + D1+ZZ+FLAG
Electricity meters            | 10000 |  16 B |  10.6 B |  37.7% | DELTA1+ZZ+HUF  52%
AirQuality Prague             |   168 |  16 B |  10.6 B |  37.6% | FLAG + D1+ZZ+FLAG
Forecast Prague (32B)         |   384 |  32 B |  22.0 B |  33.2% | DELTA1+ZZ+FLAG 58%
Forecast Brno (32B)           |   384 |  32 B |  22.4 B |  32.1% | DELTA1+ZZ+FLAG 55%
IoT building                  | 10000 |  16 B |  11.7 B |  31.3% | DELTA1+ZZ+HUF  84%
Industrial sensor             | 10000 | 128 B |  89.3 B |  30.7% | DELTA1+ZZ+HUF  80%
Forecast Ostrava (16B)        |   384 |  16 B |  12.3 B |  27.3% | DELTA1+ZZ+FLAG 42%
Forecast Prague (16B)         |   384 |  16 B |  12.4 B |  26.8% | DELTA1+ZZ+FLAG 41%
Forecast Brno (16B)           |   384 |  16 B |  12.5 B |  26.5% | DELTA1+ZZ+FLAG 43%
Forecast Bratislava (16B)     |   384 |  16 B |  12.7 B |  25.3% | DELTA1+ZZ+FLAG 38%
USGS seismology               | 10000 |  16 B |  13.9 B |  18.2% | FLAG 29% (chaotic)
===================================================================================
  Range: 18 % – 62 %   |   Errors: 0
===================================================================================
```

### Table 2 — schema-aware tight packing (fetch_small.py)

Each field stored in the smallest required type (uint8/int16) with ×10 scaling, without zero padding.

```
===================================================================================
  Dataset                    | Pkts  | Input | Output  | Saving | Dominant method
------------------------------|-------|-------|---------|--------|------------------
Forecast Prague (27B)         |   384 |  27 B |  17.1 B |  39.0% | DELTA1+ZZ+HUF  63%
Forecast Brno (27B)           |   384 |  27 B |  17.2 B |  38.5% | DELTA1+ZZ+HUF  61%
AirQuality Brno (12B)         |   168 |  12 B |   8.3 B |  35.9% | DELTA1+ZZ+HUF  49%
AirQuality Ostrava (12B)      |   168 |  12 B |   8.5 B |  34.8% | DELTA1+ZZ+HUF  47%
AirQuality Prague (12B)       |   168 |  12 B |   8.6 B |  34.2% | DELTA1+ZZ+HUF  44%
Forecast Ostrava (13B)        |   384 |  13 B |   9.3 B |  33.6% | DELTA1+ZZ+HUF  67%
Forecast Brno (13B)           |   384 |  13 B |   9.3 B |  33.3% | DELTA1+ZZ+HUF  69%
Forecast Prague (13B)         |   384 |  13 B |   9.3 B |  33.3% | DELTA1+ZZ+HUF  70%
Forecast Bratislava (13B)     |   384 |  13 B |   9.4 B |  32.5% | DELTA1+ZZ+HUF  72%
DWD Fichtelberg (9B)          | 10000 |   9 B |   6.3 B |  37.0% | D1+ZZ+ANS  49%
DWD Helgoland (9B)            | 10000 |   9 B |   6.4 B |  36.0% | D1+ZZ+ANS  42%
DWD Zugspitze (9B)            | 10000 |   9 B |   6.4 B |  35.6% | D1+ZZ+ANS  42%
USGS seismology (8B)          | 10000 |   8 B |   8.6 B |   3.9% | RAW 79% ⚠ expansion
NOAA New York (3B)            |  8184 |   3 B |   4.0 B |   0.0% | RAW 100% ⚠ expansion
NOAA San Francisco (3B)       |  8184 |   3 B |   4.0 B |   0.0% | RAW 100% ⚠ expansion
===================================================================================
  Range: 0 % – 39 %   |   Errors: 0
  ⚠ For packets < 8B, output is larger than input — header overhead (1B) outweighs savings.
===================================================================================
```

### Table 3 — raw text JSON/CSV (fetch_raw_text.py)

Data exactly as received from sources — without binary packing, text as bytes, padded with zeros to the length of the first record.

```
===================================================================================
  Dataset                    | Pkts  | Input  | Output  | Saving | Dom. method
------------------------------|-------|--------|---------|--------|---------------
DWD Helgoland (raw CSV)       | 10000 |  72 B  |  21.2 B |  71.0% | D1+ZZ+ANS 69%
DWD Zugspitze (raw CSV)       | 10000 |  72 B  |  21.3 B |  70.9% | D1+ZZ+ANS 68%
DWD Fichtelberg (raw CSV)     | 10000 |  72 B  |  21.4 B |  70.7% | D1+ZZ+ANS 67%
NOAA San Francisco (raw JSON) |  8448 |  72 B  |  26.7 B |  63.4% | D1+ZZ+FLAG 38%
NOAA New York (raw JSON)      |  8448 |  72 B  |  27.3 B |  62.6% | D1+ZZ+FLAG 37%
Forecast Bratislava (raw JSON)|   384 | 200 B  |  73.2 B |  63.6% | D1+ZZ+ANS  40%
===================================================================================
  Range: 63 % – 71 %   |   Errors: 0
===================================================================================
```

---

## Compression Methods

**1. Delta Encoding + ZigZag (DELTA1)**

The encoder subtracts each value from the previous one (forming differences). The decoder adds them back — fully reversible, zero losses. ZigZag transforms signed integers (both positive and negative) into small unsigned integers for better entropy. This method is dominant in about 70% of test cases because natural sensor data changes slowly.

**2. FLAG — zero elimination**

Replaces each sequence of zero bytes with a bit in a bitmap. Payload: `[1B length][bitmap][non-zero bytes]`. Trivial to decode — no floating point, no state. Enabled when >= 30% of bytes are zero. Very effective on sparse data (many zeros interspersed between real values).

**3. ANS — Asymmetric Numeral Systems**

Arithmetic coding for bytes with variable-length codes. The encoder builds a frequency table on the fly and encodes bytes as a stream. The decoder uses the inverse state machine — no dictionary needed. Fast on small packets, especially text and CSV data.

ANS payload contains data length (1B), state (2B — uint16_t), and encoded bytes. Kicks in only if the ratio of zero bytes >= 45% (heuristic). Both encoder and decoder have early exit — if the result exceeds the limit, computation stops immediately.

**4. Nibble Huffman (HUF)**

Fixed Huffman table trained on combined weather and GPS data after delta+ZigZag. Encodes each byte as two nibble codes (hi and lo). The table is stored in ROM (32B PROGMEM on ATmega), no extra RAM.

Maximum code length is 6 bits, average ~3.2 bits per byte. Wins especially on IoT, industrial, and complex data where zeros are rare but nibble distribution fits the table.

**5. FLAG+HUF combination**

FLAG first removes zero bytes into a bitmap, Huffman then compresses the remaining non-zero bytes. Payload: `[1B length][bitmap][1B valid bits HUF][HUF stream]`. Best of both worlds — deterministic elimination of zeros + entropy compression of the rest.

**Keyframe and start frame**

The sample with number 0 is a keyframe. Since there is no previous packet to calculate the delta, the difference method and ZigZag are skipped. Data is processed directly by FLAG, HUF, FLAG+HUF, or ANS methods. A keyframe occurs automatically every 7 packets or after device reset.

---

## Usage

### Python

```python
from nic_dmd import DmdEncoder, DmdDecoder

PKT_LEN = 16
enc = DmdEncoder(PKT_LEN)
dec = DmdDecoder(PKT_LEN)

data = bytes([0xFC, 0x18, 0x21, 0x34, 0x01, 0x81,
              0x04, 0xCE, 0x00, 0x00, 0xFC, 0x7C,
              0xFC, 0xA8, 0x00, 0x00])

compressed   = enc.compress(data)
decompressed = dec.decompress(compressed)

print(f"Compressed: {PKT_LEN}B → {len(compressed)}B")
assert decompressed == data
```

### C (ATmega328 / Arduino)

```c
#include "nic_dmd.h"

dmd_encoder_t enc;
dmd_decoder_t dec;

void setup() {
    dmd_encoder_init(&enc, 16);   // packet length — must match on both sides
    dmd_decoder_init(&dec, 16);
}

void loop() {
    uint8_t data[16]          = { /* sensor data */ };
    uint8_t compressed[DMD_OUT_MAX];   // DMD_OUT_MAX = packet length + 1 (up to 256B)
    uint8_t decompressed[16];

    uint16_t comp_len = dmd_compress(&enc, data, compressed);
    lora.send(compressed, comp_len);

    // On the receiver:
    int res = dmd_decompress(&dec, compressed, comp_len, decompressed);
    if (res != 0) {
        // res < 0 → packet is corrupted, see return value table below
    }
}
```

### Return Values and Error Codes

Each function returns a number when it finishes. This number is the only way the library tells you how it went — no prints, no logging (to save memory and performance). The higher program (the one using the library) must read this number and act accordingly.

**`dmd_compress(...)` — compression**

Returns **output length in bytes** (type `uint16_t`, i.e., 16-bit number):

| Returned value | Meaning | What to do |
|---|---|---|
| 2 to 256 | Number of bytes to send (1B header + compressed data) | Send exactly that many bytes from `output` |

Compression **never fails** and has no error code — you always get a valid length. In the worst case (255B packet that cannot be compressed, e.g., random or encrypted data), the result is **256 B**, i.e., 1 byte more than the input. This is called maximum expansion of 1B (that 1 byte is the mandatory header). Therefore, the return type is 16-bit — to fit the number 256. The output buffer must therefore have size `DMD_OUT_MAX` (= packet length + 1).

**`dmd_decompress(...)` — decompression**

Returns **status code** (type `int`). Decompressed data is found in `output` only when the code is `0`:

| Returned value | Meaning | What to do |
|---|---|---|
| `0` | OK — everything went fine, `output` contains the original data | Use `output` |
| `-1` | Corrupted or invalid input (empty packet, or payload length mismatch) | Discard packet, data is unusable |
| `-3` | Reserved protocol version (the header has `sample_num = 7`) | This packet does not belong to this library version — discard it |

A negative number always means "something is wrong, do not use the data". The library itself does not perform integrity checks (CRC, etc.) — it assumes that corrupted packets are caught by the lower layer (the radio module). Codes `-1` and `-3` are just a last safeguard against obviously nonsensical input.

> **The Python version behaves identically.** `DmdEncoder.compress()` returns the same-length output (including that 256 B in the worst case) and `DmdDecoder.decompress()` with corrupted or invalid input raises an **exception** instead of a negative code — this is Python's equivalent of a C error (reserved protocol version specifically raises `ValueError`). Handle it with `try/except`. The same input otherwise produces **byte-identical output** in both C and Python, so you can compress on a C device and decompress on a server/Raspberry Pi in Python (and vice versa).

---

### Compilation for Other Compilers (without VLA)

If your compiler does not support C99 VLA (IAR, Keil, MSVC C++), define the maximum packet length at compilation:

```
gcc -DDMD_PKT_MAX_BUILD=32 c/nic_dmd.c ...
```

Buffers will be compiled to a fixed size of 32B. For projects with one fixed packet length (typical Arduino use case), this variant is ideal.

---

## Files

The repository is organized into three directories by role:

```
c/        C implementation (embedded, ATmega328 / AVR-GCC)
python/   Python reference implementation + its tests
bench/    Benchmarks, data fetching and analysis tooling
makefile  Builds the C library and runs both C and Python tests
```

| File                    | Description                                         |
| ----------------------- | --------------------------------------------------- |
| `python/nic_dmd.py`     | Python implementation — reference, for testing      |
| `c/nic_dmd.c`           | C implementation for ATmega328                      |
| `c/nic_dmd.h`           | Header file                                          |
| `bench/nic_dmd_utils.py`| Helper functions — analysis and results output      |
| `makefile`              | Compilation and testing                             |

### Testing and Benchmarking

| File                      | Description                                                            |
| ------------------------- | ---------------------------------------------------------------------- |
| `python/nic_dmd_test.py`  | Python tests — round-trip, weather, keyframe                           |
| `c/nic_dmd_test.c`        | C tests — round-trip, all-zeros, weather                               |
| `bench/fetch_plus.py`     | Benchmark — real + synthetic data, uniform int16 (20 sources)          |
| `bench/fetch_small.py`    | Benchmark — same sources, schema-aware tight packing                   |
| `bench/fetch_raw_text.py` | Benchmark — raw JSON/CSV text as bytes                                 |
| `bench/benchmark.py`      | Comparison of DMD vs Huffman vs Heatshrink                             |

---

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgments

Brother for advice during the creation of this project.
For technical assistance with code optimization to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
