# NIC-MLA — Format Design Specification

> **Status:** concept — open questions resolved · **Document version:** 0.6 · **Date:** 2026-05-30
> **MLA** = *Matroshka Logging Archive* — universal single-file container
> (data + log in one file, like Matroska / tar / DriveSpace).
>
> This document defines the v1.0 format. **Implementation status:** Python reference
> (`nic_mla.py`, `nic_mla_archive.py`) and C libraries (`c/` — write-only for
> ATmega + complete for ARM/PC) are ready and byte-identical (verified
> via cross-compat test C↔Python). Open points requiring decision are in section 9.

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

**Key principle:** writing is trivial and robust (because of ATmega), while all intelligence (searching, querying, editing) runs on the host, where the log is loaded into RAM at once. **There is NO tree/AVL on disk — just a flat log**, which the host scans sequentially. Log fields are designed to make this filtering fast (time, station, region, type).

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
offset 0                                                              EOF
┌────────┬───────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX │ INDEX     │ DATA  stream  →   │   free  0xFF   │   ← LOG stream │
│ 512 B  │ (optional)│ (grows up)        │               │ (grows down)   │
└────────┴───────────┴──────────────────┴───────────────┴──────────────┘
         512         ▲ data_base         ▲ top_ptr   bot_ptr ▲  region_end
```

- **DATA** grows up from `data_base` (`top_ptr` = where next block goes).
- **LOG** grows down from EOF (`bot_ptr`; next record goes
  at `bot_ptr − log_rec_size`).
- Between them is free space filled with `0xFF`.
- **INDEX** (optional, §5.2) is a fixed region between prefix and data,
  `[512, data_base)`. When `index_kb=0` (default) it's empty and `data_base=512` —
  format is then **byte-identical** with the indexless variant.

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

## 3. Prefix (512 B)

The prefix remains **exactly 512 B** and ends with CRC16 over bytes `[0..509]`. Version is
bumped to **2**. All new fields fit into today's zero padding, so
the serialization and CRC scheme remain identical.

```
[0]   magic[4]        b"MLA\0"                                      (unchanged)
[4]   version         1 B   = 1
[5]   cluster_shift   1 B   8=256B · 10=1KB · 11=2KB · 12=4KB · 13=8KB
                            · 14=16KB · 15=32KB
[6]   log_rec_size    1 B   24 (default) or 32 (more stations / longer desc) — §10.1
[7]   flags           1 B   see below
[8]   file_size       4 B   uint32 LE                              (unchanged)
[12]  phys_addr       8 B   uint64 LE  (base on medium; 0 for FAT/POSIX)
── new fields (previously padding) ──
[20]  container_kind  1 B   0=single · 1=rotation · 2=circular
[21]  file_seq        2 B   uint16 LE  file order in rotation
[23]  keyframe_intv   1 B   keyframe interval for compression (default 8; 0 = N/A)
[24]  enc_caps        1 B   bitmask of encodings this file may carry
[25]  data_base       4 B   uint32 LE  = 512 + index_kb·1024 (DATA start; §5.2)
[29]  region_end      4 B   uint32 LE  = file_size  (LOG stream end)
[33]  checkpoint_shift 1 B  checkpoint interval = 2^value records
                            (0 = disabled); default 8 → 256 — see §5.1 / §10.2
[34]  padding          0x00 … to byte 509
[510] crc16           2 B   LE  — over bytes 0..509
```

### `flags` (1 B)

| Bits | Meaning |
|---|---|
| 0–1 | CRC integrity mode: `0=NONE` · `1=DATA` · `2=FULL` |
| 2–7 | reserved (0) |

> **Note:** buffered / cluster-aligned write mode was **dropped from the design**.
> Reason: on ATmega328 (2 KB RAM) a buffer for an entire cluster (4–32 KB) physically won't fit
> and at sample cadence (seconds to minutes) write amplification is negligible. Writing is thus always
> **byte-precise** (see §6).

### Roles in prefix (summary; §1–§10 detail each)

- `cluster_shift`, `flags`: unchanged behavior
- `log_rec_size`: chooses record size 24 or 32 B per-file (§10.1)
- `data_base`, `index_kb`: determine if there's an optional skip-table (§5.2)
- `container_kind`, `file_seq`: for file rotation (§2, §10.3)
- `keyframe_intv`: compression hint (§4.2)
- `enc_caps`: which encodings are used (§4.3)
- `checkpoint_shift`: checkpointing interval for fast recovery (§5.1, §10.2)

---

## 4. Data Types and Encoding (`rec_type`)

### 4.1 Registry

The `rec_type` field (byte 7 of LOG record, §5) identifies the **semantic type** of the payload. Core types:

| Hex | Type | Meaning | Payload semantics |
|---|---|---|---|
| 0x00 | **RAW** | untyped binary | as-is |
| 0x01 | **MEASUREMENT** | sensor sample | T°C / °F, pressure, humidity, … (interpretation per `station` + `region`) |
| 0x02 | **DIAGNOSTIC** | status / counters | CPU temp, uptime, error count, … |
| 0x03 | **CONFIG** | configuration blob | persisted settings, thresholds, names |
| 0x04 | **DELTA** | delta-encoded measurement | diff from previous sample (compression via keyframe) |
| 0x05 | **KEYFRAME** | uncompressed anchor | every N-th raw sample in delta stream |
| 0x06 | **AGGREGATED** | pre-computed stats | min/max/mean over interval |
| 0x10–0xFE | reserved / user | — | future / custom per project |

**Note:** The kernel does not interpret these. A `MEASUREMENT` record is still just bytes — *meaning* (which byte is temperature, which is humidity) is determined by the **station+region metadata**, not by the type byte. The type is a **hint to tools** (export filters, viewers, compression schemes) and to the app itself (recovery uses `RAW` for torn blocks, not from the original type).

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

## 5. Log Record (24 or 32 bytes)

The log record lives in the LOG stream (growing down from EOF). Two sizes per file:

### 5.1 Size 24 B (default) — compact

```
[0]  timestamp   4 B  uint32 LE  Unix seconds
[4]  flags       1 B  see below
[5]  rec_type    1 B  encoding (see §4)
[6]  seq         2 B  uint16 LE  sequence in file (for keyframe backrefs)
[8]  station     2 B  uint16 LE  station ID
[10] region     2 B  uint16 LE  region within station
[12] offset      4 B  uint32 LE  byte offset in DATA
[16] length      2 B  uint16 LE  payload size (max 65535 B)
[18] kf_back     2 B  uint16 LE  seq distance to keyframe (0 = is keyframe)
[20] crc16       2 B  LE  — CRC16 over [0..19]
```

### 5.2 Size 32 B (optional) — extended

Same as 24 B, plus 8 extra bytes:

```
[0..19] — same as 24 B variant
[20] user_field  8 B  app-defined (e.g., secondary timestamp, flags, desc ID)
[28] crc16       2 B  LE  — CRC16 over [0..27]
```

The size is **fixed per file** and set at format time via `log_rec_size` parameter. On mount, read from prefix byte 6.

### 5.3 `flags` (byte 4)

| Bit | Meaning |
|---|---|
| 0 | reserved (0) |
| 1 | reserved (0) |
| 2 | reserved (0) |
| 3 | reserved (0) |
| 4 | reserved (0) |
| 5 | reserved (0) |
| 6 | reserved (0) |
| 7 | reserved (0) |

**Currently unused** (all 0). Reserved for future per-record flags (e.g., "deleted", "marked for audit", etc.). On torn write, the CRC will fail → record is skipped.

### 5.4 Checkpoint Record (special; `rec_type=0xFF`)

A **sparse, optional** record inserted every `2^checkpoint_shift` records (default: every 256). Used to accelerate `mount()` and recovery:

```
[0]  timestamp      4 B  (copy of last real record's timestamp)
[4]  flags          1 B  0xFF (marker)
[5]  rec_type       1 B  0xFF (marker)
[6]  seq            2 B  (same as last real record)
[8..19]            (payload not used)
[20] crc16          2 B  (calculated like other records)
```

- On mount: binary search finds last valid checkpoint.
- On recovery: full scan starts from last checkpoint instead of file start.
- Storage overhead: ~1 checkpoint per 256 records at 24 B = ~9% (acceptable).

**Note:** Checkpoints are **optional** (if `checkpoint_shift=0` in prefix, skip writing them). They are backwards-compatible: files without checkpoints mount just fine (scan from start, slower but correct).

---

## 6. Data Block (variable)

Data payload written to the DATA stream:

```
[0]       magic       4 B  0x4D4C4144 ("MLAD" LE)
[4]       <payload>   N B  app data (1 to 65535 B)
[4+N]     crc16       2 B  LE  — CRC16 over [4..4+N-1] (payload only)
```

**Why no type byte?** Type is in the log record (`rec_type`). This saves 1 byte and keeps the data stream **purely app-driven** — no format overhead. (Torn-write recovery synthesizes `type=RAW` for salvaged blocks anyway.)

**Crash safety:** If the write is torn (e.g., MAGIC is partial), CRC over payload fails → log slot is marked abandoned, `top_ptr` reverts to the block's start. Subsequent reads of that slot skip the record.

---

## 7. Crash-safety

Protocol — **LOCK first, DATA second**:

1. **Torn lock write** (interrupted during LOG record write) → newest slot has bad CRC → skipped at mount. Binary search boundary-finding continues (just steps by 24 instead of 16).
2. **Torn data write** (LOG OK, but data block incomplete) → missing `MAGIC` →
   slot is abandoned (`flags 0xFF→0x00`) and `top_ptr` reverts to `rec.offset`.
   This is the key recovery path; the flags byte sits at offset 20.
3. `recover()`: finds `MAGIC`, tries lengths 1..65535 until `CRC16(payload)` matches. Type is not in the block (it's in the log), so recovered records get `rec_type = raw`. Larger length range makes emergency scan slower, but it runs only on full log corruption.
4. **Checkpoint (§5.1)** gives mount and recovery a fast catchpoint — from it, only ≤ one interval of records need to be scanned instead of the entire file.

---

## 8. Code Reuse Map (for upcoming refactor)

**Unchanged**
- `crc16` (+ self-test `crc16(b"123456789") == 0x29B1`)
- `MlaHAL` (ABC with 4 functions), `MlaPosixHAL`, `MlaNorSimHAL` — HAL contract is the same
- commit protocol shape, binary search strategy in `mount`, `__iter__` / `read_record` control, free space / "full" arithmetic

**Modified**
- `MlaPrefix` — new fields, `version=1`, `index_rec_size` → `log_rec_size=24`;
  serialization and CRC schema identical
- `MlaIndex` (→ `MlaLog`) — layout 24 B; `length` to 2 B; new fields `seq`,
  `rec_type`, `kf_back`; `flags` to byte 20; CRC over `[0..19]`
- `MlaCore.append` — populate `seq`/`rec_type`/`kf_back`, adjusted record sizes
- `MlaCore.mount` — record size 24, parse new fields, abandonment offset 20
- `MlaCore._build_block` / `_read_data` — `length` 2 B; CRC over payload
  (data block stays `MAGIC + data + CRC`, no TYPE byte)
- `MlaCore.recover` — scan with length 1..65535, `rec_type = raw`

**New** (✓ = done in Python reference)
- ✓ `rec_type` constants + small data type registry
- ✓ **checkpointing (§5.1)** — write every `2^checkpoint_shift` records + mount
- ✓ **file rotation manager** `MlaArchive` (`nic_mla_archive.py`) — next
  `NICnnnn.MLA` on full, self-describing via `file_seq`
- ✓ **host helper** `query()` (`nic_mla_archive.py`) — flat filtering
  (time / station / region / type), PC-only; chip stays lean
- ✓ **index region (§5.2)** — optional host skip-table (`index_kb`);
  `MlaCore.scan()` + `read_index()` in Python, `mla_scan()` in C; write-only path doesn't fill it, just respects `data_base`
- circular buffer (NOR/experiment) — **deferred to later**
- ~~buffered / cluster-aligned mode~~ — **dropped** (ATmega 2 KB RAM, §6)

**Tests** (`nic_mla_test.py`) — update for 24 B records, no TYPE byte, and torn-write/full/recovery scenarios.

---

## 9. Decisions (closed with owner)

| # | Question | Decision |
|---|---|---|
| 1 | `timestamp` width | **4 B u32, Unix seconds** — sufficient for weather station |
| 2 | `length` width | **2 B (max 65535 B)** — covers records beyond 255 B (~280 B) |
| 3 | `seq` width | **2 B** — 1 MB file is enough; minimal record is tens of B |
| 4 | Data type registry | owner to refine; **this doc proposes base set** (§4) |
| 5 | Checkpoint / registry | **yes** — special log record, **configurable interval** (§5.1) |
| 6 | File rotation | **self-describing** via `file_seq`, no separate manifest |
| 7 | Circular buffer (wrap) | **deferred to later** |
| 8 | Cluster alignment | **byte-precise** (simple variant) as default |
| 9 | Type in data block | **removed** — type is log-only (`rec_type`), block is `MAGIC+data+CRC` |
| 10 | CRC in log | **yes, already there** — each log record has its own CRC16 (§5) |
| 11 | Header placement | **prefix at offset 0, near log — NOT near data** (else jumping between log and data on search) |
| 12 | Log record size | **configurable via `log_rec_size`** — 24 B or 32 B per-file (§5.2) |

---

## 10. Configurable Parameters ("practice will tell")

> Three things where the decision is double-edged, so we **don't hard-code them** —
> they become parameters in the prefix. Each deployment chooses its own, and practice decides,
> without format change.

### 10.1 Log Record Size — 24 B vs 32 B (`log_rec_size`)

The `log_rec_size` field in the prefix (byte 6) determines the log record size for that file. We support two:

- **24 B (default)** — compact, ideal for single station / weather station.
- **32 B** — 8 B extra for **station description** (wider identification, second time index, flags). Good when format is used as a **datalogger for multiple stations** of same or different type — more room for record description.

It's double-edged (more space vs. more overhead), so it's a choice, not dogma.

**Host header up to 256 chars (key principle):** we keep the minimum on disk —
Unix time is just 4 B, even though as text "2026-05-30 08:14:00" is ~19 chars.
Compact binary log (≤ 24/32 B) is **expanded on PC to a readable record header** (up to ~256 chars of free description, like a filename). This rich header is host-only (export/display) — on the chip stays only compact binary. Per-station description can be stored in a config record (`rec_type` "config" class) or in the 8 extra B of the 32 B variant.

### 10.2 Checkpoint Interval (`checkpoint_shift`)

Double-edged: **denser** index (small interval) = larger index + more writes
(bad for ATmega); **sparser** (large interval) = more scanning in file (but that's on a powerful processor, not ATmega). Since **ATmega mainly writes** and searching runs on host (and is rare):

- **Default: sparse** (shift 8 → **256** records) — less write overhead for ATmega.
- Stored as **1 byte** = power of two (`2^checkpoint_shift`), same idiom
  as `cluster_shift`; 0 = disabled. (Previously 2 B unnecessarily — thanks to owner's insight.)

### 10.3 (Resolved) Header Placement

Header/prefix stays **at offset 0, near log — not near data**. If near data, search would jump between log and data. (See §9, row 11.)

### 10.4 Summary — What Can Be Changed (all in prefix, set at `format()`)

| Parameter | Where | Choices | Default |
|---|---|---|---|
| `cluster_shift` | byte 5 | 8…15 (256 B … 32 KB) | 12 (4 KB) |
| `log_rec_size` | byte 6 | 24 / 32 B | 24 |
| `flags` (CRC) | byte 7 b0–1 | NONE / DATA / FULL | FULL |
| `flags` (align) | byte 7 b2–3 | ALIGN_DATA / BUFFERED | disabled |
| `container_kind` | byte 20 | single / rotation / circular | single |
| `keyframe_intv` | byte 23 | 0…255 | 8 |
| `enc_caps` | byte 24 | encoding bitmask | per use |
| `checkpoint_shift` | byte 33 | 0 (disabled) / 1…N (2^N) | 8 (→256) |
| `data_base` | byte 25 | 512 + `index_kb`·1024 | 512 (index disabled) |

`data_base` is not set directly — it's derived from `index_kb` (§5.2) passed to `format()`. `index_kb=0` → `data_base=512` → no index region.

### 10.5 Index Region Size (`index_kb`)

Optional host skip-table from §5.2. Like `checkpoint_shift`, a compromise:

- **Default: disabled** (`index_kb=0`) — write-only/ATmega doesn't need it, and format
  stays byte-identical with the indexless variant.
- **Datalogger (STM32/ESP/PC): typically 2–4 KB.** 12 B/anchor, one per
  `2^checkpoint_shift` records; 4 KB ≈ 340 anchors → ~87,000 records at interval 256.
- Region is reserved one-time at `format()` (shifts `data_base`); at runtime only anchors are appended, handled by RMW layer under HAL on SD/FAT.

### 10.6 Candidates for byte savings (to review in step 2)

> Practice will tell; there's still a lot to trim.

- **`seq` to 1 byte** — if `seq` is relative to last checkpoint
  (window ≤ 256 records), 0…255 = 1 byte instead of 2. Caveat: affects keyframe compression (`kf_back`), so decide at step 2 implementation.
- **`station` / `region`** — if weather station uses only a few channels, consider 1 byte instead of 2 (kept 2 B for multi-station datalogger).
- Generally: review each field and shrink to actual need.

---

*★ Viva La Resistánce ★*
