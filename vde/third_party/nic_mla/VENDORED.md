# Vendored: NIC-MLA (Python reference)

Vendored copy of the **NIC-MLA** Python reference — the single-file container
format (Matroshka Logging Archive) that the data logger writes and that Volkov
Data reads/browses.

- **Origin:** [Project-NIC/NIC-MLA](https://github.com/Project-NIC/NIC-MLA).
- **Vendored at:** upstream `main` (**library v1.2**, on-disk **format v1.1**).
- **License:** MIT (see upstream / file headers).

## What VDE links (the only files vendored here)

- `nic_mla.py` — `MlaCore` (format / mount / append / read_record / iterate /
  scan / recover) + `MlaLog` (16 B) + `MlaPrefix`.
- `nic_mla_archive.py` — `MlaArchive` file-rotation helper.
- `tools/mla_datalogger.py` — datalogger (profile-ref) format: many station profiles in one file.
- `tools/mla_schema.py` — host-only schema/station readers & builders:
  `mla_read_schema`, `mla_decode_value`, `mla_decode_payload`, `mla_read_stations`,
  `mla_split_station`, `MlaSchemaBuilder`, `MlaStationTable`, and the `UNITS` vocabulary.

Only the Python runtime is vendored. The C core, the spec/docs and the
experimental HAL live upstream and are intentionally **not** carried here — less
to drift, fewer errors.

## subsec (library v1.2)

The 16-byte log record carries a `flags` byte (bit 7 = `compressed`, bits 0–6 =
`kf_back`; 0 = keyframe) and a 2-byte `subsec`. As of v1.2 `subsec` is **two
opaque bytes the glue owns** (`subsec_lo` / `subsec_hi`); the wire layout is
unchanged, so v1.1 and v1.2 files stay byte-compatible.

## Refresh

Run `python3 tools/sync_vendor.py` (mapping in `tools/vendor_manifest.txt`);
never edit vendored files by hand. CI (`vendor-sync-check`) fails on drift.
