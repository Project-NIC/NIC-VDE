# Vendored: NIC-MLA

This directory is a vendored copy of the **NIC-MLA** project (Matroshka Logging
Archive) — the single-file container format that the data logger writes and that
Volkov Data reads/browses.

- **Origin:** [Project-NIC/NIC-MLA](https://github.com/Project-NIC/NIC-MLA).
- **Vendored at:** upstream `main`, commit `2a60e9d` (**format v1.0**).
- **License:** MIT (see file headers / upstream).
- **Why vendored:** the desktop links MLA directly via the Python reference
  (`nic_mla.py`) and the host-only tooling (`tools/mla_schema.py`), kept
  byte-identical to the C core (`c/`). One source of truth lives next to the app.

## The "dumb container" model (v1.0)

MLA is deliberately **dumb**: it stores bytes and never interprets their meaning.
Two self-describing tables travel in the prefix (covered by its CRC); the host
*glue* turns them into meaning:

- **SCHEMA table** (offset 34) — names/units/scale of the LOG and DATA fields, so
  a packed payload decodes to real values + units. `physical = (raw+offset)*10^exp10`.
- **STATION table** — the 16 B log record carries a **1-byte station index**; the
  real numbers are `n × 6` raw bytes here, one record per station. MLA never reads
  those 6 bytes — splitting them into region/number is entirely the host's job.

The prefix grows in whole 512 B sectors to fit the tables.

## What VDE links

- `nic_mla.py` — `MlaCore` (format / mount / append / read_record / iterate /
  scan / recover) + `MlaLog` (16 B) + `MlaPrefix`.
- `tools/mla_schema.py` — host-only builders **and readers**:
  `read_schema(prefix)`, `decode_value`, `decode_payload`, `read_stations(prefix)`,
  `split_station(record)`, plus `SchemaBuilder` / `StationTable` and the universal
  `UNITS` vocabulary.

## Updating

Re-copy from upstream and re-run `python3 nic_mla_test.py` (should report all PASS).
Do not edit vendored files locally — fix upstream and re-vendor to avoid drift.
