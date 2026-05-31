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
- **Dumb container.** MLA only stores bytes. All the brains (compression,
  encryption, station-number translation, LoRa/Wi-Fi) live in a separate glue
  layer — MLA stays small and never gets in the way.
- **Tiny 16 B log record, fully CRC-protected.** No "flags outside the CRC"
  trick: abandon a record by overwriting it with zeros — its CRC then fails and
  readers skip it.
- **Crash-safe.** "LOCK first, DATA second" commit protocol + CRC16 (CCITT-FALSE).
  After a reset the last record either verifies (carry on) or is zeroed and the
  space reclaimed. No on-disk search tree to corrupt.
- **Self-describing.** The prefix carries a SCHEMA table (8-char field names +
  units → ready for CSV/SQL export with no prior knowledge) and a STATION table
  (the 1-byte station index in each log record → the real station number).
- **Small for a microcontroller.** The ATmega328 (2 KB RAM) only writes; no
  dynamic allocation, largest buffer 32 B. Searching and reading happen on the host.
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
┌──────────────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX           │ DATA  stream  →   │   free  0xFF   │   ← LOG stream│
│ 1–255 sectors    │ (grows up)        │               │ (grows down)  │
│ (512 B each)     │                   │               │               │
└──────────────────┴──────────────────┴───────────────┴──────────────┘
```

- **Prefix:** a 34 B header + the SCHEMA and STATION tables, covered by a CRC16
  in its last 2 bytes. Normally one 512 B sector; it grows in whole sectors
  (up to 255 ≈ 127 KB) only if the tables need it.
- **Data block:** `MAGIC(2) + payload(1..65535) + CRC16(2)`
- **Log record (16 B), all CRC-covered:** offset, timestamp, length, rec_type,
  kf_back, station (1-byte index), reserved, CRC16.

## Repository structure

| Path | Contents |
|---|---|
| `nic_mla.py` | Python reference core (format / mount / append / read / scan / recover) |
| `nic_mla_archive.py` | Python: file rotation (`MlaArchive`) + host-side query (`query`) |
| `tools/mla_schema.py` | Build/read the SCHEMA + STATION tables; decode payloads for CSV/SQL |
| `nic_mla_test.py` | Test suite (Python) |
| `c/` | C libraries: write-only (MCU) + complete (ARM/PC) + HAL adapters |
| `DESIGN-MLA.md` | Format design specification |

## Quick start — Python

```python
from nic_mla import MlaCore, MlaPosixHAL

# First run (creates a 1 MB file pre-filled with 0xFF)
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format()
    mla.append(timestamp, station=1, data=b"\x01\x02\x03")   # station = table index

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
    arch.append(ts, station=1, data=payload)
for rec, data in query(MlaArchive("/data"), station=1, time_from=t0, time_to=t1):
    ...
```

Self-describing file (schema + station tables → ready for CSV/SQL export):

```python
from mla_schema import SchemaBuilder, StationTable, read_schema, \
                       read_stations, decode_payload, split_station

sb = SchemaBuilder()
sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
sb.data("hum",  unit="pct",  width=2, exp10=-1)
st = StationTable()
st.station(region=55, number=25000)          # log index 1 → this station

hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format(schema_table=sb.table(), station_table=st.table())
    mla.append(ts, station=1, data=temp.to_bytes(2,"little",signed=True)+hum.to_bytes(2,"little"))

# Any reader recovers names, units and the real station number — no prior knowledge:
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    pfx = mla._prefix.to_bytes()
    _, fields = read_schema(pfx); stations = read_stations(pfx)
    for rec, data in mla:
        region, number, _ = split_station(stations[rec.station - 1])
        cols = decode_payload(fields, data)   # [(name, unit, value), …]
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

## Notes for integrators

- **Station names are not in the file.** The STATION table stores only 6 raw
  bytes per station; what they mean (region / number / city / …) is decided by
  your glue layer, which keeps its own mapping "6 bytes → meaning". The log
  carries just a 1-byte index — translating it to a real station number is the
  glue's job, not the container's.
- **The `reserved` byte in the log record is padding** that rounds the record up
  to 16 B (a power of two, so it never straddles a sector). It is inside the CRC
  and currently always 0 — treat it as a free slot for a future field, not as
  something that carries meaning today.

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
