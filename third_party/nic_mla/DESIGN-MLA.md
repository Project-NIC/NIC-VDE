# NIC-MLA — Format Design Specification

> **Status:** v1.0 · **Date:** 2026-05-31
> **MLA** = *Matroshka Logging Archive* — universal single-file container
> (data + log in one file, like Matroska / tar / DriveSpace).
>
> This document defines the v1.0 format. **Implementation status:** Python reference
> (`nic_mla.py`, `nic_mla_archive.py`, `tools/mla_schema.py`) and C libraries
> (`c/` — write-only for ATmega + complete for ARM/PC) are ready and
> byte-identical (verified via cross-compat test C↔Python).
>
> **Design principle — a dumb container.** MLA only stores bytes: a 16 B
> CRC-covered log record + a data block, plus two self-describing tables in the
> prefix (field names/units, and station index → real number). Everything smart
> — compression, encryption, station-number translation, transport — lives in a
> separate glue layer.

---

## 1. Purpose and Scope

NIC-MLA is a **universal container for data recording** from measuring stations
(weather station, electricity meter, …). The goal is a single portable file that carries
**data and log together** and is readable across platforms.

**Why it exists:** end the mess with a million formats. Instead of piles of files and
tools → **one table** into which you can "hack" anything. You pull the card from the
device, stick it into a computer, and **one trivial viewer** assembles the structure
from internal registers that are so well-described that even a child can do it.
The goal is not to be "fancy" — the goal is a **simple, intuitive and trivial process**
that saves time and money. (That parts of this approach exist elsewhere doesn't matter —
the value is in having them **together and self-describing.**).

### Target platforms and roles

| Platform | Role | What it does |
|---|---|---|
| **ATmega328** (8-bit) | **WRITE-ONLY** | only appends records (append), at ~15 min intervals; no searching or editing on the chip |
| Arduino 32/64-bit, STM, ESP | write + optionally read | like ATmega + local reading |
| **Host** (PC / Raspberry) | read, search, edit | loads entire log into RAM, filters, exports |

**Key principle:** writing is trivial and robust (because of ATmega), while all intelligence (searching, querying, editing) runs on the host, where the log is loaded into RAM at once. **There is NO tree/AVL on disk — just a flat log**, which the host scans sequentially. Log fields are designed to make this filtering fast (time, station index, type).

### Out of scope

- **Record editing** → separate future project *Volkov Data Editor*.
- **Compression** → optional, handled by a **separate method**; the container merely **carries and types** compressed
  data via `rec_type` (delta / keyframe / raw), it does not define compression itself (see §4).
- **Direct raw SPI-NOR/NAND** → **experimental and frozen** (see
  `experimental/`). Target storage is **SD/flash card** — the card's own controller
  handles wear-leveling, ECC and remapping. We abandoned raw NOR due to lockdown risk on some chips during partial-page/partial-block writes and due to vendor-specificity. The NOR simulator remains only as proof of format universality (the kernel is storage-independent via HAL), not as a supported path.

---

## 2. File Layout

We maintain the proven physical model — **two streams growing toward each other**
in a fixed-size file:

```
offset 0                                                            EOF
┌──────────────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX            │ DATA  stream  →   │   free  0xFF   │  ← LOG stream │
│ 1–255 sectors     │ (grows up)        │               │ (grows down)  │
└──────────────────┴──────────────────┴───────────────┴──────────────┘
                    ▲ data_base         ▲ top_ptr   bot_ptr ▲  region_end
```

- **DATA** grows up from `data_base` (`top_ptr` = where next block goes).
- **LOG** grows down from EOF (`bot_ptr`; next record goes
  at `bot_ptr − log_rec_size`).
- Between them is free space filled with `0xFF`.
- **PREFIX** is one 512 B sector by default; it only grows (in whole sectors, up
  to 255) when the SCHEMA + STATION tables don't fit. `data_base` = prefix size.
  There is no separate index region — there is no on-disk search tree at all.

### Why fixed, pre-allocated size

The file is **pre-allocated entirely** during `format()` and filled with `0xFF` (exactly like today's
`MlaPosixHAL.create`). On FAT/SD this is the right choice:

- FAT cluster chain is allocated upfront → file doesn't grow, doesn't fragment,
- all **logical offsets remain stable** throughout the file's lifetime,
- the "two pointers against each other" model fits a fixed region without FS conflicts.

### "Full" condition

```
top_ptr + next_block_size  >  bot_ptr − log_rec_size
```

### Fill modes (`container_kind` in prefix)

| Value | Mode | Behavior | Recommendation |
|---|---|---|---|
| 0 | **Hard stop** | RuntimeError on full | simple |
| 1 | **File rotation** | on full, next file opens `NIC0001.MLA`, `NIC0002.MLA`, … ; each prefix carries `file_seq` | **recommended for FAT/SD** |
| 2 | **Circular buffer** | DATA wraps back up, oldest sector is freed (`sector_erase`) and corresponding LOG slots marked as abandoned | RAW/NOR only / experiment |

Rotation is preferred: each file is independently mountable and crash-safe,
cards are huge, so full condition is rare. Circular buffer complicates recovery and
is deferred (see §9).

### Decision: container size and pre-allocation

- **Free space = `0xFF`** (like fresh NOR after erase) remains — **no superblock**.
  It's simplest for MCU: chip just writes, `mount()` finds the boundary
  by scanning for `0xFF`. No pointer persistence in prefix.
- **Default container size ~1 MB.** Pre-allocating 1 MB (filling with `0xFF`) is
  fast one-time on MCU; for large volumes **rotate multiple 1 MB files**.
- **Large filesystems** = many 1 MB files on huge card. **Aggregation and cross-file reading
  is done by PC** (`MlaArchive`) — powerful processor "consumes everything",
  so slower pre-allocation and full scan aren't a problem. MCU keeps only one
  open 1 MB file.
- 32-bit addressing → one file max **4 GB**; above that (and below) rotation.
  If file count bothers you, you can raise `file_size` (e.g., 16–64 MB) —
  it's just a choice in `format()`.

---

## 3. Prefix (1–255 sectors of 512 B)

The prefix is a 34 B structured header followed by two self-describing tables
(SCHEMA + STATION), ending with a CRC16 over everything before it. Normally it
is **one 512 B sector**; if the tables don't fit it grows in whole 512 B sectors
(up to **255 ≈ 127 KB**) and the CRC moves to the prefix's last 2 bytes.

> The 255-sector limit is a hard ceiling, not a target — it exists only because
> the count is one byte. The **recommended maximum is 16 sectors (8 KB)**; with
> the auto-sized SCHEMA/STATION tables a real station never comes near it.

```
[0]   magic[4]        b"MLA\0"
[4]   version         1 B   = 1
[5]   cluster_shift   1 B   8=256B · 10=1KB · 12=4KB · … · 15=32KB
[6]   log_rec_size    1 B   = 16
[7]   flags           1 B   CRC mode (bits 0-1): 0=NONE · 1=DATA · 2=FULL
[8]   file_size       4 B   uint32 LE
[12]  reserved        8 B   0
[20]  container_kind  1 B   0=single · 1=rotation
[21]  file_seq        2 B   uint16 LE  file order in rotation
[23]  keyframe_intv   1 B   keyframe interval for compression (default 8; 0 = N/A)
[24]  enc_caps        1 B   bitmask of encodings this file may carry
[25]  data_base       4 B   uint32 LE  = prefix size (first DATA byte)
[29]  region_end      4 B   uint32 LE  = file_size  (LOG stream end)
[33]  reserved        1 B   0
[34]  SCHEMA table    …     §3.1
[..]  STATION table   …     §3.2
[end-2] crc16         2 B   LE  — over everything before it
```

### 3.1 SCHEMA table — field names/units for CSV/SQL

Built/read by `tools/mla_schema.py`. Lets any reader export records to CSV/SQL
with **no prior knowledge** — the station carries its own column descriptions.

```
[0] tbl_ver  1 B  = 1
[1] n_log    1 B  number of LOG fields (describe the timestamp etc.)
[2] n_data   1 B  number of DATA fields (the packed payload columns)
[3 ..]       (n_log + n_data) × 14 B field descriptors:
   width 1 B · unit 1 B · exp10 1 B (i8) · flags 1 B (bit0=signed) ·
   offset 2 B (i16 LE) · name 8 B (UTF-8, NUL-padded)
   physical = (raw + offset) × 10^exp10
```

The unit vocabulary is universal (spec-wide); only the field *composition*
(which sensors, scale, width, **8-char name**) is device-specific and travels
in the file.

### 3.2 STATION table — index → real station

```
[0] sta_ver  1 B  = 0x53
[1] n        1 B  number of stations (1..255)
[2 ..]       n × 6 raw bytes (index i in the log → record i-1)
```

The 6 bytes are **opaque to MLA**. A common split is `region(2) + number(2) +
reserved(2)`, but the host glue decides; it can also be `city/number/region` or
one big number. People assign station numbers with gaps — the glue maps them to
compact 1-byte indices and back.

> **Dumb container.** Both tables are written verbatim from above and never
> interpreted by the C/MCU path. Compression, encryption, station-number
> translation and transport all live in a separate glue layer.

---

## 4. Data Types and Encoding (`rec_type`)

### 4.1 Registry

The `rec_type` field (byte 7 of LOG record, §5) identifies the **semantic type** of the payload. Core types:

| Hex | Type | Meaning | Payload semantics |
|---|---|---|---|
| 0x00 | **RAW** | untyped binary | as-is |
| 0x01 | **MEASUREMENT** | sensor sample | T°C / °F, pressure, humidity, … (columns described by the SCHEMA table) |
| 0x02 | **DIAGNOSTIC** | status / counters | CPU temp, uptime, error count, … |
| 0x03 | **CONFIG** | configuration blob | persisted settings, thresholds, names |
| 0x04 | **DELTA** | delta-encoded measurement | diff from previous sample (compression via keyframe) |
| 0x05 | **KEYFRAME** | uncompressed anchor | every N-th raw sample in delta stream |
| 0x06 | **AGGREGATED** | pre-computed stats | min/max/mean over interval |
| 0x10–0xFE | reserved / user | — | future / custom per project |

**Note:** The kernel does not interpret these. A `MEASUREMENT` record is still just bytes — *meaning* (which byte is temperature, which is humidity) is determined by the **SCHEMA table** (§3.1), not by the type byte. The type is a **hint to tools** (export filters, viewers, compression schemes) and to the app itself (recovery uses `RAW` for torn blocks, not from the original type).

### 4.2 Compression (optional, per `rec_type`)

The format **carries** compression state but **does not define methods**. Tools outside this spec can implement delta/keyframe/deflate/etc. The container ensures:

- `rec_type` marks the encoding (e.g., `DELTA` vs. `KEYFRAME` vs. `MEASUREMENT`).
- `keyframe_intv` (prefix byte 23) hints at the compression window.
- Each record is **self-contained** (CRC, length) — torn writes don't corrupt unrelated records.

**Practical example:**
1. App encodes: sample 1 = 24.5°C (raw), samples 2–7 = deltas, sample 8 = keyframe (raw).
2. Container sees `rec_type` = `DELTA` or `KEYFRAME` — no interpretation, just typing.
3. On read, decompressor (not the kernel) knows: every 8th record is a keyframe, apply deltas backward.

### 4.3 Encoding capabilities (`enc_caps`)

Prefix byte 24 is a **bitmask** of encodings the file is declared to use:

```
bit 0: RAW       (0x01)
bit 1: DELTA     (0x02)
bit 2: KEYFRAME  (0x04)
bit 3: DEFLATE   (0x08)  [example: if using external compression]
bits 4–7: reserved
```

On format: set bits for all `rec_type`s you'll write. On read: tools can skip files outside their decompression capability. (This is a **hint**, not a constraint — reader must still handle unknown types gracefully.)

---

## 5. Log Record (16 bytes)

The log record lives in the LOG stream (growing down from EOF). It is a fixed
**16 bytes** and the **whole record is covered by the CRC** — there is no
"flags outside the CRC" field.

```
[0]  offset      4 B  uint32 LE  byte offset of the data block in DATA
[4]  timestamp   4 B  uint32 LE  Unix seconds (from the caller's RTC/GPS)
[8]  length      2 B  uint16 LE  payload size (1..65535 B)
[10] rec_type    1 B  encoding + class (see §4)
[11] kf_back     1 B  uint8      records back to the owning keyframe (0 = is one)
[12] station     1 B  uint8      index 1..255 into the prefix station table (0 = none)
[13] reserved    1 B  uint8      0
[14] crc16       2 B  LE  — CRC16 over [0..13]
```

Why 16 B: it is a power of two, so a record never straddles a 512 B sector and
slot addressing is a shift, not a multiply — the friendliest size for an MCU.
The `reserved` byte is the padding that achieves this; it is inside the CRC and
always 0 — a free slot for a future field, not meaningful today.

### 5.1 Record states (no flags field)

A slot is interpreted purely from its bytes:

| State | Bytes | Detected by |
|---|---|---|
| **Free** | all `0xFF` | fresh / erased medium |
| **Live** | data + matching CRC | `crc16(body) == stored CRC` |
| **Abandoned** | all `0x00` | CRC fails (a zeroed body does **not** hash to `0x0000`) |

Abandoning a record = **overwrite the 16 B with zeros**. Its CRC then no longer
matches, so every reader skips it. This replaces the old "flip one flags byte
outside the CRC" trick and lets the entire record be checksummed.

### 5.2 `station` is an index, not a number

`station` is a **1-byte index** (1..255; 0 = none) into the STATION table in the
prefix (§3). The real station/region numbers — which people and tools assign
however they like, with gaps — live in that table; the container never
interprets the index. Translation index ↔ real number is the host glue's job.

> No checkpoints. The file size is fixed and the log is fixed-stride, so
> `mount()` finds the boundary by binary search and reads the newest valid
> record's `offset + length` to restore `top_ptr` — there is nothing to
> persist, so no checkpoint record exists.

---

## 6. Data Block (variable)

Data payload written to the DATA stream:

```
[0]       magic       2 B  0xAB 0xCD  (sync word)
[2]       <payload>   N B  app data (1 to 65535 B)
[2+N]     crc16       2 B  LE  — CRC16 over the payload (0xFFFF if CRC mode = NONE)
```

**Why no type byte?** Type is in the log record (`rec_type`). This keeps the
data stream **purely app-driven**. With a schema in the prefix, the payload is
the sensor columns packed back-to-back (§3.1); `decode_payload()` splits and
scales them into `(name, unit, value)` for CSV/SQL.

---

## 7. Crash-safety

Protocol — **LOCK first, DATA second**:

1. **Torn lock write** (interrupted during the LOG record write) → that slot has
   a bad CRC → skipped at mount. Binary-search boundary finding continues.
2. **Torn data write** (LOG OK, but the data block is incomplete) → its `MAGIC`
   is missing → on mount the lock is **zeroed** (the whole 16 B overwritten with
   `0x00`, so its CRC fails) and `top_ptr` reverts to `rec.offset`.
3. **Abandon** any record the same way — overwrite it with zeros; the CRC then
   fails and readers skip it. There is no flags byte and nothing outside the CRC.
4. `recover()`: finds `MAGIC`, tries lengths 1..65535 until `CRC16(payload)`
   matches; recovered records get `rec_type = raw` (type isn't in the block).
5. No checkpoints: the file size is fixed and the log is fixed-stride, so a
   binary search plus reading the newest record restores the state directly.
   Start the scan with a coarse stride (e.g. 256, or 2000 for a big file) and
   step back one when you hit `0xFF` — nothing on disk needs repairing.

---

## 8. Configurable parameters (set at `format()`, stored in the prefix)

| Parameter | Where | Choices | Default |
|---|---|---|---|
| `cluster_shift` | byte 5 | 8…15 (256 B … 32 KB) | 12 (4 KB) |
| `flags` (CRC) | byte 7, bits 0-1 | NONE / DATA / FULL | FULL |
| `container_kind` | byte 20 | single / rotation | single |
| `file_seq` | byte 21 | 0…65535 | 0 |
| `keyframe_intv` | byte 23 | 0…255 | 8 |
| `enc_caps` | byte 24 | encoding bitmask | per use |
| `schema_table` | [34..) | from `tools/mla_schema.py` | empty |
| `station_table` | after schema | from `tools/mla_schema.py` | empty |

`log_rec_size` is fixed at **16** and `data_base` is derived (= prefix size,
which is 512 B unless the tables overflow into more sectors).

## 9. Out of scope (lives in the glue layer, not in MLA)

MLA is a dumb container; the following are deliberately **not** its job:

- **Station-number translation** — the log stores a 1-byte index; mapping it to
  a real, possibly gap-ridden station number is the glue's (via the STATION
  table it wrote).
- **Compression** — MLA only carries and types it (`rec_type`: raw / delta /
  keyframe; `kf_back` links a record to its keyframe). The codec is separate.
- **Encryption** — same: a separate library; MLA stores whatever bytes it gets.
- **Transport (LoRa / Wi-Fi / network)** — each record is self-contained
  (type + length + CRC), so "send a record" = send its bytes. The transport is
  the glue's choice.
- **File rotation** across many files — platform glue over the filesystem
  (`MlaArchive` in Python); each file is independently mountable via `file_seq`.

This separation keeps MLA small enough for an ATmega (write-only, 16 B log, one
512 B prefix sector) while letting a capable host build an arbitrarily smart
system on top.

*★ Viva La Resistánce ★*
