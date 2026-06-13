# Vendored: NIC-DMD (Python reference)

Copy of the Python reference from
[`Project-NIC/NIC-DMD`](https://github.com/Project-NIC/NIC-DMD) (`main`).

File:
- `nic_dmd.py` — adaptive lossless compressor: `DmdEncoder` / `DmdDecoder`
  (+ `dmd_compress` / `dmd_decompress`) and `DMD_KEYFRAME_EVERY = 7`.

NIC-MSEED uses `DmdDecoder` to decompress compressed MLA records (replaying each
station's stream in order) so the integer counts reach the Steim encoder.
Re-copy from upstream to refresh; do not edit vendored files locally.
