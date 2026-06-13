# Vendored: NIC-DMD (Python reference)

Copy of the Python reference from
[`Project-NIC/NIC-DMD`](https://github.com/Project-NIC/NIC-DMD) (`main`).

Files:
- `nic_dmd.py` — adaptive lossless compressor: `DmdEncoder` / `DmdDecoder`
  (+ `dmd_compress` / `dmd_decompress`) and `DMD_KEYFRAME_EVERY = 7`.
- `LICENSE`

VDE uses `DmdDecoder` to decompress compressed MLA records when browsing or
exporting a container (replaying each station's stream in order to rebuild the
named values).

**Refresh:** run `python3 tools/sync_vendor.py`; never edit vendored files by
hand. CI (`vendor-sync-check`) fails if they drift from upstream.
