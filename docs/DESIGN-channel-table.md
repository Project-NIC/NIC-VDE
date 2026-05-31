# Channel conversion table — DONE (implemented upstream in NIC-MLA)

> Status: **IMPLEMENTED.** This started as a VDE-side proposal; the feature was
> ultimately designed and built **in the MLA format itself** (the right home —
> it travels in the file), and VDE now just *reads* it. The final binary layout
> differs from the early VDE drafts in this file's history; the authoritative
> definition lives in `third_party/nic_mla/tools/mla_schema.py` and
> `DESIGN-MLA.md`. This page documents what shipped and how VDE consumes it.

## The problem (recap)

The MLA kernel deliberately doesn't know what a payload means. That meaning —
which bytes are temperature, humidity, etc., and how to scale them — has to live
*somewhere*, or every reader is reduced to guessing from payload length. VDE's
old `decode_value` did exactly that guess, and CSV/SQL inherited it.

## The solution that shipped: a self-describing schema table

The station's processor writes a small **schema table** into the prefix free
space at format time, covered by the prefix CRC16. The file is then
self-describing: any reader (VDE, a future remote viewer, …) decodes raw packed
payloads into real values + units with **no prior knowledge**.

### Binary layout (authoritative: `tools/mla_schema.py`)

```
Prefix offset 34:
  [34] tbl_ver  1 B   = 1   (0x00 / 0xFF → no schema, fall back to a guess)
  [35] n_log    1 B   number of LOG-header fields
  [36] n_data   1 B   number of DATA-payload fields
  [37 ..]             (n_log + n_data) × 14 B field descriptors

Field descriptor (14 B):
  [+0] width   1 B   bytes on the wire (1 / 2 / 4)
  [+1] unit    1 B   code from the universal UNIT vocabulary
  [+2] exp10   1 B   signed exponent
  [+3] flags   1 B   bit0 = signed value
  [+4] offset  2 B   i16 LE, additive calibration term
  [+6] name    8 B   UTF-8, NUL-padded — the column name, carried in the file

value:  physical = (raw + offset) * 10**exp10
```

Two key model points that differ from the original VDE draft:

- **Positional, not keyed.** The schema describes the *layout* of the LOG header
  and the DATA payload. A measurement record's payload is **all data fields
  packed back-to-back** (one full sensor row per record), decoded by position —
  not a `(station, channel)` lookup. (The log field once called `channel` is now
  `region`.)
- **Per-field width + power-of-ten scale.** Each field picks its own width
  (1/2/4 B) and an `exp10` scale; the conversion is `(raw + offset) * 10**exp10`.

## How VDE uses it (`volkov_core/mla.py`)

1. **On open** — `_read_schema()` pulls the table out of the prefix via
   `mla_schema.read_schema(prefix)`. No table → `(None, None)`.
2. **F4 Values / info** — a measurement payload is decoded into all of its named
   columns (`temp=23.5 degC  humidity=60 pct …`). Text/event records and any
   payload whose width doesn't match the schema fall through to the old guess.
3. **Export (CSV / SQL)** — *with* a schema: one row per record, a column per
   data field; `raw=False` (default) writes the decoded physical values,
   `raw=True` writes the on-wire integers. *Without* a schema: the historical
   flat single-`value` shape. Text/event rows keep their metadata and leave the
   data columns blank.

The universal UNIT vocabulary (`degC`, `pct`, `hPa`, `V`, …) is spec-wide, so it
doesn't travel in the file — only the per-field composition does.

## Not done here (future, optional)

- **F4 as an in-place schema editor** inside an open `.mla` (rewrite the table +
  recompute the prefix CRC). MLA already supports writing a schema at
  `format()`; an in-place edit would build on that.
- **Index rebuild on F2 Repair** (spec §5.3) — unrelated to the schema, still
  report-only today.
