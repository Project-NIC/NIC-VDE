# NIC-GLUE-IN

**Connection layer between the NIC libraries — DMD, KSF, MLA, VDE — on the ingest / write side.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   sensor row / wire packet ──▶ [ optional NIC-DMD ] ──▶ NIC-MLA container
                                                                 │
                                                                 ▼
                                            NIC-VDE  (read-only viewer / export)
```

> **Read this first.** The glue is *yours to shape.* There are many right ways to
> wire these libraries together, and the best one depends on your device, your
> link, and what you want to do with the data. This repo is therefore **a worked
> example plus a catalogue of options** — not a framework you must adopt. The
> lasting value here is the **[library alignment reference](#library-alignment-reference)**:
> the small set of seams where the libraries have to agree, written down once so
> everyone can find their way around them. The reading / exporting direction is
> the sibling project **NIC-GLUE-OUT**; **NIC-VDE** is the viewer.

---

## Library alignment reference

The NIC libraries are deliberately *dumb and independent*: MLA stores opaque
bytes, DMD codes fixed-width packets, VDE views files, KSF transforms bytes.
None of them knows about the others. A glue layer is whatever code lines up
these seams. There are only a handful, and getting them right is the whole job:

| Seam | What each side exposes | How they line up |
|---|---|---|
| **Compressed bit + `kf_back`** | MLA v1.1 log carries a 1-byte `flags` (bit 7 = `compressed`, bits 0–6 = `kf_back`) but never interprets it; *which* codec lives in the data block's own header (DMD byte 0), never in MLA | glue sets the `compressed` bit (`False` for verbatim rows, `True` for DMD output) and `kf_back`; record kinds are **raw** (not compressed), **keyframe** (compressed, `kf_back == 0`), **delta** (compressed, `kf_back > 0`) |
| **Keyframe** | DMD keyframe = sample number `0` (3-bit field; value `7` reserved for protocol version) | glue reads it back off the DMD blob (`blob[0] & 0x07 == 0` ⇒ keyframe = DMD sample 0) and tags the record accordingly |
| **Keyframe distance** | MLA log has a `kf_back` field it only carries; readers need to find the owning keyframe | glue sets `kf_back` = records back to the owning keyframe (`0` on the keyframe) |
| **Keyframe cadence hint** | MLA prefix has `keyframe_intv` (metadata only); DMD cadence is internal (`DMD_KEYFRAME_EVERY`) | base library default `0`; glue seeds DMD's cadence so the caller never types it (overridable) |
| **`subsec` (two opaque bytes)** | MLA log carries a `subsec` field — two opaque bytes the glue owns (sub-**sec**ond time *and/or* sub-**sec**tion / rotation); MLA gives it no meaning | glue passes `subsec` through unchanged on `log_raw` / `CompressedChannel.log` (the caller composes the 16-bit value or the two bytes) |
| **Packet width** | DMD requires every packet in a stream to be the *same* width (delta) | width belongs to the **channel** (4..255 B), enforced on every `log()`; different channels may differ |
| **Stream identity** | a stream's identity in the file *is* its MLA station index; MLA needs no other per-record tag | the reader tells streams apart by station and reads `kf_back` to find each stream's keyframe; one stateless DMD compressor + N tiny per-stream contexts (`ChannelBank`) keep the deltas straight |
| **Rotation → keyframe** | MLA v1.1 (2b) surfaces a rotation event + `will_rotate()` so each rotated file can be independently decodable | `GlueArchiveLogger` + `ChannelBank` wire this end to end: the stream that *triggers* the rollover checks `will_rotate(pkt_len+1)` **before** compressing and resets so that record is a keyframe (a delta never crosses a file boundary); every *other* stream is reset by `on_rotate` → `reset_all()`. So the first record of each stream in every file is a keyframe (moot for RAW data) |
| **Station** | MLA log stores a 1-byte station *index* (1..255), real numbers live in the prefix station table | glue/`MlaStationTable` owns the index ↔ region/number mapping |
| **Time** | MLA log has a dedicated 4-byte `timestamp`; the schema's `log("datetime")` (preset `4 B unix_s`) *describes* it | time lives in the log header, **not** duplicated in the data block — see [time options](#1-where-the-timestamp-comes-from) |
| **Field layout** | the schema splits `log(...)` fields (header) from `data(...)` fields (payload); `mla_decode_payload` unpacks the block | the log-vs-data split *is* the map of "what goes in the header" vs "what stays in the block" |
| **Integrity** | MLA covers the log record (and optionally the data block) with CRC16 | pick `MLA_CRC_FULL` (recommended), `MLA_CRC_DATA`, or `MLA_CRC_NONE` at format time |

If your own glue respects this table, your files round-trip through NIC-VDE and
NIC-GLUE-OUT regardless of how you structure the rest.

---

## What the example provides

A deliberately small datalogger over a single MLA container:

- **`GlueLogger`** — `log_raw()` / `log_event()`: take a row, store a row, into a
  **single** MLA container. The everyday case; works for any number of stations.
- **`GlueArchiveLogger`** — same write API as `GlueLogger`, but over a **rotating**
  `MlaArchive` (`MLA00000.MLA`, `MLA00001.MLA`, …). It wires the rotation→keyframe
  seam end to end, so **each file is independently decodable** (see below); the
  schema/station tables are written into every file's prefix too.
- **`CompressedChannel`** — `open_compressed_channel(station, pkt_len)` then
  `.log(ts, row)`: optional NIC-DMD compression for **one fixed-width stream**,
  with the `compressed` bit / `kf_back` filled in automatically.
- **`ChannelBank`** — `open()` / `log()` / `reset_all()` / `on_rotate()`: one
  stateless DMD compressor + N tiny per-stream contexts, one `CompressedChannel`
  per MLA station index. Construct it over a `GlueArchiveLogger` and the rotation
  seam wires itself; the first record of each stream in every file is a keyframe.

```python
from nic_glue_in import GlueLogger, MlaSchemaBuilder, MlaStationTable

schema = MlaSchemaBuilder(); schema.log("datetime")          # describes the log timestamp
for n in ("temp", "humidity"): schema.data(n, unit="raw", width=2)
stations = MlaStationTable(); stations.station(region=55, number=25000)

with GlueLogger("out.mla", schema_table=schema.table(),
                station_table=stations.table()) as log:
    log.log_raw(ts, station=1, data=row_bytes)            # classic path (raw)
    log.log_event(ts, station=1, text="PING")             # just an uncompressed record

    ch = log.open_compressed_channel(station=1, pkt_len=4) # optional compression
    ch.log(ts, row_bytes)                                  # → compressed, kf_back (keyframe/delta)
```

Rotating, with each file independently decodable:

```python
from nic_glue_in import GlueArchiveLogger, ChannelBank

with GlueArchiveLogger("/data", schema_table=schema.table(),
                       station_table=stations.table()) as log:
    bank = ChannelBank(log)                       # auto-wires the rotation seam
    for ts, row in stream:
        bank.log(station=1, pkt_len=4, timestamp=ts, row=row)   # rotates + keyframes itself
```

```bash
python3 examples/weather_datalogger.py     # writes weather_raw.mla + weather_dmd.mla
python3 tests/test_glue.py                  # or: pytest tests/
```

---

## Design options & how-to

These are *possibilities*, not requirements — pick what fits. The example
implements the simplest of each; the rest is sketched so you can extend it.

### 1. Where the timestamp comes from

MLA's log record has a dedicated 4-byte `timestamp`, separate from the opaque
data block, and the schema's `log("datetime")` field describes it. So time
belongs **in the log header**, never duplicated in the data. How it gets there
is your choice:

- **(a) Glue's own clock (RTC / receive time).** The simplest: the glue stamps
  each record with the time it *received / logged* it, from the device RTC. The
  packet carries only sensor data. This is what the example does — `timestamp`
  is an argument to `log_raw` / `Channel.log`.
- **(b) Extracted from the packet header.** The wire packet itself carries the
  time as a header (e.g. `[datetime 4 B unix_s][sensors …]`). On ingest the glue
  slices off the header — the **schema's log-field widths tell it where** — writes
  it into `log.timestamp`, and stores the remaining sensor bytes as the block.
  The "header moves into the log." DMD knows nothing about time; the *schema* is
  the thing that knows the offset.
- **(c) Supplied by the caller.** Whatever upstream layer already knows the
  authoritative time passes it in directly.

> Procedure for (b), wire ingest:
> `recv(blob)` → `DmdDecoder.decompress(blob)` → `packet` →
> `t = int.from_bytes(packet[:4], "little")` → `data = packet[4:]` →
> `MlaCore.append(t, station, data, compressed=…, kf_back=…)`.

### 2. Compressed at rest, or only on the wire?

DMD's 1-byte header and "never expands by more than 1 B, never loses data"
property make it safe to store compressed. Two stances:

- **Store RAW (decompressed).** If you receive a compressed packet, decompress
  it on ingest and store the sensor bytes verbatim (a **raw** record — the
  `compressed` bit stays clear). Readers need no codec; VDE decodes straight from
  the schema. Costs disk, buys simplicity.
- **Store compressed (a **keyframe** then **delta** records).** Keep the DMD blob
  in the data block (the `compressed` bit set; `kf_back == 0` marks the keyframe,
  `kf_back > 0` a delta). Smaller files. The cost is **random access**: because each delta
  packet is relative to the previous one, to open record *i* you must replay the
  stream from its keyframe forward — which is exactly what `kf_back` is for (it
  tells the reader how far back the keyframe sits). For one channel this is one
  small "previous-sample" buffer and a walk from the keyframe.

### 3. Multiple streams

A `CompressedChannel` is one DMD stream = one station index + one fixed width.
The model is **one stateless DMD compressor + N tiny per-stream contexts**, which
is exactly what `ChannelBank` provides: it manages several `CompressedChannel`s,
one per MLA station index (`open` / `log` / `reset_all` / `on_rotate`). You may
open many (up to 255), but the delta only buys anything *within* a stream, so
compressing dozens of independent stations mostly just costs RAM (one
previous-sample buffer each).

On file rotation (NIC-MLA 2b) wire `MlaArchive(dir, on_rotate=bank.on_rotate)`
(or check `arch.will_rotate(n)` before encoding): `ChannelBank.on_rotate` calls
`reset_all()`, so the first record of each stream in the new file is a keyframe
and every rotated file is independently decodable. The example compresses a
single station to show it works; everything else logs raw.

### 4. Encryption (NIC-KSF)

KSF is intentionally **not** in the at-rest path — storing ciphertext in the
container is the wrong layer (leave confidentiality at rest to a trusted
platform). Its place is the **transport** path: the sender encrypts the
(optionally compressed) packet before transmitting, the receiver decrypts before
ingest. Both ends own the key; the container never sees it.

> Wire order (sender): `pack row → [DMD compress] → [KSF encrypt] → transmit`.
> Receiver mirrors it: `recv → [KSF decrypt] → [DMD decompress] → store`.
> Note DMD treats encrypted bytes as random and stores them RAW (+1 B), so
> **compress before encrypt**, never after.

---

## Layout

```
nic_glue_in/        the glue example (GlueLogger, CompressedChannel, ChannelBank)
examples/           runnable weather datalogger
tests/              round-trip + port-mapping tests
third_party/        vendored copies of NIC-DMD and NIC-MLA (see VENDORED.md)
tools/              sync_vendor.py — refresh third_party/ from canonical NIC-MLA/NIC-DMD
```

Pure Python 3.10+, no external packages — the dependencies are vendored.

---

## Datalogger (multi-profile)

Write several station types into one `.mla` (different column layouts): pass the datalogger tables as `schema_table` and use `log_raw(station, data)`. See `DataloggerBuilder` and `tests/test_datalogger.py`; full spec in NIC-MLA `DESIGN-MLA-datalogger.md`.

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgements

To my brother for advice during the development of this project.
For technical assistance with code optimisation, to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
