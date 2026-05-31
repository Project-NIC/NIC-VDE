<p align="center">
  <img src="NICMLA.svg" width="200"/>
</p>

[Pro dokumentaci v češtině klikněte zde](README.cs.md) | [Для документации на русском языке нажмите здесь](README.ru.md)

---

# NIC-MLA


[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

**Matroshka Logging Archive** — a universal single-file container for logging
data from measurement stations. Both the data and the log live in **one portable
file**, readable across platforms from an 8-bit microcontroller to a PC.

One file, one format, one way to read it — pull the card out of the device, plug
it into a computer, and you have everything. No zoo of formats.

> Full format specification: **[`DESIGN-MLA.md`](DESIGN-MLA.md)**

## Key features

- **One file = data + log.** Two streams grow toward each other: data from the
  top, the log from the bottom.
- **Crash-safe.** "LOCK first, DATA second" commit protocol + CRC16 (CCITT-FALSE).
  An interrupted write is safely detected and cleaned up at startup.
- **Small for a microcontroller.** The ATmega328 (2 KB RAM) only writes; no
  dynamic allocation, largest buffer 24 B. Searching and reading happen on the host.
- **Checkpoint.** A periodic anchor point speeds up startup and recovery.
- **Optional index region.** A small host-side time/station skip-table for fast
  queries (configurable; off by default — the write-only/MCU path never fills it).
- **File rotation.** When one file fills up, the next is started; large volumes =
  many smaller files, the host reads them as a whole.
- **32-bit addressing** → a single file up to 4 GB (beyond that, rotation).
- **Optional compression.** The container carries and types compressed data
  (`rec_type`: raw / delta / keyframe); it does not define the compression method
  itself.
- **Filesystem-independent.** Access through a thin HAL (4 functions);
  FAT16 / FAT32 / exFAT / NTFS / ext4 are handled by the layer beneath it
  (the OS, SdFat or FatFs).

## File layout

```
offset 0                                                              EOF
┌────────┬───────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX │ INDEX     │ DATA  stream  →   │   free  0xFF   │   ← LOG stream│
│ 512 B  │ (optional)│ (grows up)        │               │ (grows down)  │
└────────┴───────────┴──────────────────┴───────────────┴──────────────┘
```

- **Data block:** `MAGIC(2) + payload(1..65535) + CRC16(2)`
- **Log record (24 B):** timestamp, offset, station, channel, seq, rec_type,
  length, kf_back, flags (outside the CRC), CRC16
- **Index** (optional): a flat array of 12 B anchors (timestamp + log slot +
  station); empty when disabled.

## Repository structure

| Path | Contents |
|---|---|
| `nic_mla.py` | Python reference core (format / mount / append / read / scan / recover) |
| `nic_mla_archive.py` | Python: file rotation (`MlaArchive`) + host-side query (`query`) |
| `nic_mla_test.py` | Test suite (Python) |
| `c/` | C libraries: write-only (MCU) + complete (ARM/PC) + HAL adapters |
| `experimental/` | Frozen / purely theoretical (raw SPI-NOR simulator) |
| `DESIGN-MLA.md` | Format design specification |

## Quick start — Python

```python
from nic_mla import MlaCore, MlaPosixHAL

# First run (creates a 1 MB file pre-filled with 0xFF)
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format()
    mla.append(timestamp, station=1, channel=0, data=b"\x01\x02\x03")

# Later runs: mount() restores the state; iteration reads records
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    for rec, payload in mla:
        ...
```

Rotation across multiple files and filtering:

```python
from nic_mla_archive import MlaArchive, query
with MlaArchive("/data") as arch:          # MLA00000.MLA, MLA00001.MLA, …
    arch.append(ts, 1, 0, payload)
for rec, data in query(MlaArchive("/data"), station=1, time_from=t0, time_to=t1):
    ...
```

Accelerated query with an index region:

```python
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format(index_kb=4)                  # reserve a 4 KB time/station skip-table
    ...
    # later, on the host:
    for rec, data in mla.scan(time_from=t0, time_to=t1, station=1):
        ...
```

Tests:

```sh
python3 nic_mla_test.py
```

## Quick start — C

Two libraries share one format definition (`c/nic_mla_format.h`):

- **write-only** (`c/nic_mla_write.{h,c}`) — for the ATmega / small Arduinos,
- **complete** (`c/nic_mla.{h,c}`) — for ARM Arduino / PC (+ read, query, recover).

You wire the HAL (4 functions) to your filesystem. Ready-made adapters in `c/hal/`:

| Platform | "Beneath the HAL" | Adapter |
|---|---|---|
| Raspberry Pi / PC (SSD, SD, USB) | OS: ext4 / exFAT / NTFS / FAT32 / FAT16 | `hal/nic_mla_hal_posix.{h,c}` |
| Arduino AVR / ESP / STM32duino | SdFat | `examples/atmega_sd_writeonly.ino` |
| STM32 bare-metal (CubeIDE/HAL) | FatFs (ChaN) | `hal/nic_mla_hal_fatfs.{h,c}` |

Build and test on a PC:

```sh
cd c
cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c \
   hal/nic_mla_hal_posix.c -o mlatest
./mlatest
```

See **[`c/README.md`](c/README.md)**.

## Data transport (LoRa / network)

**Out of scope** — the container is storage, not transport. Each record is
self-contained (type + length + CRC), so sending it over LoRa/network means
"take the record's bytes and send them". The project leaves the transport choice
to the user.

## Status

Both the Python and C references are complete, tested, and **byte-for-byte
identical** (a file written by the C library is read by Python and vice versa).

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgements

To my brother for advice during the development of this project.
For technical assistance with code optimisation, to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
