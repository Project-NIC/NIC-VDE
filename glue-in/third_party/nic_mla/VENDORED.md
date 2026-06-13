# Vendored: NIC-MLA (Python reference)

Copy of the Python reference from
[`Project-NIC/NIC-MLA`](https://github.com/Project-NIC/NIC-MLA) (`main`).

Files:
- `nic_mla.py` — container core (from repo root)
- `nic_mla_archive.py` — file rotation + host queries (from repo root)
- `mla_schema.py` — self-describing schema/station tables (from `tools/mla_schema.py`)
- `mla_datalogger.py` — datalogger (profile-ref) format: many station profiles in one file (from `tools/mla_datalogger.py`)
- `LICENSE`

**Library v1.2 (on-disk format v1.1):** the 16-byte log record carries a single
`flags` byte (bit 7 = `compressed`, bits 0–6 = `kf_back`; 0 = keyframe) plus a
2-byte `subsec`. As of v1.2 `subsec` is **two opaque bytes the glue owns** — MLA
gives them no meaning (`subsec_lo` / `subsec_hi`). The wire layout did not change,
so v1.1 and v1.2 files stay byte-compatible. The old `rec_type`/class byte and the
`reserved` byte are gone — a record is just a *compressed* bit + a keyframe
distance, and meaning comes from the SCHEMA. The glue sets `compressed`/`kf_back`
from the DMD blob.

**Refresh:** run `python3 tools/sync_vendor.py` (mapping in
`tools/vendor_manifest.txt`); never edit vendored files by hand. CI
(`vendor-sync-check`) fails if they drift from upstream.

`MlaCore.format(keyframe_intv=...)` defaults to `0` upstream now (keyframe-
agnostic base); the glue seeds DMD's cadence and passes `keyframe_intv`
explicitly, so it never depends on the default.
