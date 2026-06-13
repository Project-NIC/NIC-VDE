# NIC-GLUE-OUT

**Connection layer between the NIC libraries — DMD, KSF, MLA, VDE — on the read / export side.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   NIC-MLA container ──▶ schema decode ──▶ rows ──▶ CSV / SQLite
```

> **Read this first.** This is the sibling of **NIC-GLUE-IN**, the same idea
> pointed the other way: GLUE-IN wires a data row *into* a container; GLUE-OUT
> walks a finished container *back out* into a table. Like its sibling it is
> **a worked example plus a small catalogue of options**, not a framework. The
> lasting value is again the **[library alignment reference](#library-alignment-reference)**
> — the seams, read from the other end. The example does the simplest useful
> thing: open a container, decode every RAW record through its self-describing
> schema, and export the lot to **CSV or SQLite**. **NIC-DMD compression is
> decompressed automatically** by this reader; only encryption (NIC-KSF) stays
> out of scope — see [below](#using-nic-dmd-and-nic-ksf).
> **NIC-VDE** is the interactive viewer for the same files; GLUE-OUT is the
> headless export path.

---

## Library alignment reference

The NIC libraries are deliberately *dumb and independent*: MLA stores opaque
bytes, VDE views files, KSF transforms bytes, DMD codes packets. None of them
knows about the others. The glue is whatever code lines up these seams. GLUE-IN
*writes* them; a reader's whole job is to *read the same seams back*. The ones
this simple reader uses:

| Seam | What the container carries | What the reader does with it |
|---|---|---|
| **Record kind** | MLA carries no type byte; a record only has a `compressed` bit + `kf_back` (0 = keyframe). Files are homogeneous — there is no TEXT/EVENT/CLASS type, meaning comes from the SCHEMA | derive the *kind* `raw` / `keyframe` / `delta` from those, then decode to named values: raw straight from the schema, compressed via DMD (see below); a row is blank only with no schema match |
| **Station** | MLA log stores a 1-byte station *index*; real numbers live in the prefix station table | resolve index → region/number so exported rows carry real numbers |
| **Time** | MLA log has a dedicated 4-byte `timestamp`; the schema's `log("datetime")` describes it | time comes straight from the log header — never dug out of the data block |
| **Field layout** | the schema splits `log(...)` header fields from `data(...)` payload fields | `mla_decode_payload` splits the packed block back into named, scaled values |
| **Integrity** | MLA covers the log record (and optionally the data block) with CRC16 | bad-CRC slots are skipped by the MLA core on mount — the reader only sees committed records |

If a file respects this table on the way in (any GLUE-IN does), it reads straight
back out here and in NIC-VDE, regardless of how the rest was structured.

---

## What the example provides

A deliberately small reader/exporter over a single MLA container:

- **`GlueReader`** — open a container, then iterate it or export it. It reads the
  self-describing schema/station tables out of the prefix, decodes every RAW
  measurement payload into named, scaled values, resolves the station index to
  its real region/number, and serialises everything to **CSV** or **SQLite**.

```python
from nic_glue_out import GlueReader

with GlueReader("weather.mla") as r:
    for rec in r:                          # decoded records, oldest first (raw + compressed)
        if rec.values is not None:         # decoded values (raw or DMD-decompressed)
            print(rec.timestamp, rec.station_label,
                  {n: v for n, _u, v in rec.values})
        else:                              # no schema match — show the raw bytes
            print(rec.timestamp, rec.kind, rec.block.hex())

    r.write_csv("weather.csv")             # → idx,time,unix,sta_idx,region,number,kind,length,<fields…>
    r.write_sqlite("weather.db")           # → a one-table SQLite database
```

```bash
python3 examples/weather_export.py          # builds a sample, then exports weather.csv + weather.db
python3 tests/test_glue.py                  # or: pytest tests/
```

---

## Design options & how-to

These are *possibilities*, not requirements — pick what fits. The example
implements the simplest useful read+export; the rest is a short list of knobs.

### 1. Export targets — CSV or SQLite

Both fall out of the same assembled rows; the `export` module is a dumb
serialiser (it knows nothing about MLA). `to_csv()` returns UTF-8 bytes;
`to_sqlite()` returns a one-table database as bytes. Pass `raw=True` to keep the
on-the-wire integers instead of the schema's scaled physical values. Add your
own target (Parquet, JSON Lines, a socket) by writing one more `to_…` that
consumes the same `(name, sql_decl)` columns + row tuples.

### 2. Where the timestamp comes from

The reader never guesses time: MLA's log record has a dedicated 4-byte
`timestamp`, separate from the data block, and the schema's `log("datetime")`
field merely *describes* it. So the reader takes it straight from the log header
(`rec.timestamp`) — the data block is pure sensor payload. The exact inverse of
GLUE-IN's "where the timestamp goes" seam: time lives in the header, never
duplicated in the data, on the way in *and* out.

### 3. Filtering

The reader loads the whole container into RAM (the documented host model) and
filters host-side: `records(station=…, time_from=…, time_to=…)`. There is no
on-disk index — it is a flat scan, the same result as filtering every record.

### 4. Schemaless files

A container written without a schema still reads: every record falls back to a
single `value` column (text as text, tiny payloads as an integer, otherwise hex).
A file *with* a schema gets one named column per data field instead.

### NIC-DMD (built in) and NIC-KSF

- **NIC-DMD (compression).** If a writer used GLUE-IN's compressed channel, those
  records carry the `compressed` bit (kind `keyframe` / `delta`). This reader
  **decompresses them automatically**: it replays each station's stream through a
  per-station `DmdDecoder(width)` in order (`width` = the schema's total data
  width) and then runs the result through the same schema decode the raw path
  uses — so compressed and raw rows export identically. A record only shows blank
  cells if there is no schema mapping for it at all. **NIC-VDE** also browses
  such files.
- **NIC-KSF (encryption).** KSF lives on the *transport* path, never at rest:
  the sender encrypts before transmitting, the receiver decrypts *before* the
  bytes are stored — so the container holds cleartext and this reader needs no
  key. Add it on the receive side (`recv → ksf_decrypt → … → store`), the mirror
  of GLUE-IN's send side. See NIC-GLUE-IN.

---

## Layout

```
nic_glue_out/       the glue example (GlueReader) + the dumb CSV/SQLite exporter
examples/           runnable, self-contained weather reader/exporter
tests/              read-back + decode + export tests
third_party/        vendored copy of NIC-MLA (see VENDORED.md)
tools/              sync_vendor.py — refresh third_party/ from canonical NIC-MLA/NIC-DMD
```

Pure Python 3.10+, no external packages — the dependency is vendored, the
exporter is stdlib `sqlite3`.

---

## Datalogger (multi-profile)

Export a datalogger `.mla` (several station profiles in one file) to CSV / SQLite — one table per profile: `is_datalogger()` + `dl_export_csv()` / `dl_export_sqlite()`. See `tests/test_datalogger.py`; full spec in NIC-MLA `DESIGN-MLA-datalogger.md`.

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgements

To my brother for advice during the development of this project.
For technical assistance with code optimisation, to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
