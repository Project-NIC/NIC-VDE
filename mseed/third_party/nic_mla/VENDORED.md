# Vendored: NIC-MLA (Python reference)

Copy of the Python reference from
[`Project-NIC/NIC-MLA`](https://github.com/Project-NIC/NIC-MLA) (`main`, format v1.1).

Files:
- `nic_mla.py` — container core (from repo root)
- `nic_mla_archive.py` — file rotation + host queries (from repo root)
- `mla_schema.py` — self-describing schema/station tables + value encode/decode
  (from `tools/mla_schema.py`)

**Format v1.1:** the 16-byte log record carries a `subsec` field and a single
`flags` byte (bit 7 = `compressed`, bits 0–6 = `kf_back`; 0 = keyframe). NIC-MSEED
reads the schema to split each record's payload into raw integer counts per
channel. Re-copy from upstream to refresh; do not edit vendored files locally.
