# NIC-MSEED

**A standalone NIC data library — turn a NIC-MLA log into miniSEED (Steim-1 / Steim-2).**

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   .mla  ──▶  [NIC-DMD decode if compressed]  ──▶  per-channel int counts  ──▶  miniSEED
```

> **What this is.** One of the standalone NIC data libraries (alongside NIC-MLA,
> NIC-DMD, NIC-KSF). A NIC node logs its samples into a NIC-MLA container; **miniSEED**
> is the lingua franca of seismology, dropping straight into **ObsPy, SeisComp, SWARM**
> and the FDSN toolchain. NIC-MSEED is the bridge — it reads a `.mla`, decompresses
> NIC-DMD blobs, pulls the raw integer counts out per SCHEMA channel, and writes
> standard miniSEED records. Whoever has a seismic MLA log (e.g. from **NIC-Quake** /
> **NIC-Station**) and needs SEED uses it — **a worked library, not a framework.** (For
> ad-hoc CSV / SQLite inspection of any MLA log, use NIC-GLUE-OUT; miniSEED is the
> seismo path.)

## Two implementations

- **Python** (`nic_mseed/`) — the reference: pure Python 3.10+, no external packages.
- **C** (`c/`) — the same Steim-1/2 codec + miniSEED writer in portable C, for
  on-device / embedded export. Both are host-tested and round-trip against each other's
  vectors.

## Two layers

- **`steim` / `mseed`** — a container-agnostic core: integers → Steim-1/2 frames
  → miniSEED records, and back (a minimal reader for round-trip tests). No deps.
- **`from_mla`** — the converter that wires **NIC-MLA + NIC-DMD** to that core:
  per-station DMD replay, schema-driven channel split, SEED code mapping.

## Quick start

```python
from nic_mseed import MseedExporter, STEIM2

stats = MseedExporter(
    sample_rate_hz=100.0,        # device ODR — miniSEED needs the rate; MLA doesn't store it
    network="NQ",                # SEED network code
    version=STEIM2,              # or STEIM1
    channel_map={"z": "HHZ", "n": "HHN", "e": "HHE"},   # SCHEMA field → SEED channel
).export("quake.mla", "quake.mseed")
print(stats)   # {channels, samples, records, bytes, out}
```

```bash
python3 examples/mla_to_mseed.py            # builds a sample .mla, converts, prints stats
python3 tests/test_steim.py                 # Steim-1/2 codec round-trip
python3 tests/test_mseed.py                 # miniSEED writer (+ ObsPy gold-standard if installed)
python3 tests/test_from_mla.py              # end-to-end MLA(+DMD) → miniSEED round-trip
```

## How MLA maps to miniSEED

| miniSEED needs | comes from |
|---|---|
| start time (BTIME) | MLA `timestamp` (u32 s) + `subsec` (u16) of the first record |
| sample rate | **you supply it** (`sample_rate_hz` = device ODR); `subsec` only pins the sub-second phase |
| integer counts | MLA payload split per SCHEMA field (raw, or NIC-DMD-decompressed) — *raw* counts, not the scaled physical value (calibration belongs in StationXML) |
| network/station/location | the MLA STATION table (or `station_map`) |
| channel code | the SCHEMA field name (or `channel_map`) |

Each `(station, field)` becomes one miniSEED channel. The converter assumes an
evenly-sampled, contiguous series per channel (true for synchronised acquisition,
e.g. **NIC-Quake**); gap-splitting is left to a later pass.

## Validation

The codec and writer round-trip through this package's own minimal reader. The
miniSEED test additionally validates against **ObsPy** when it is installed — run
`python3 tests/test_mseed.py` on a machine with ObsPy for gold-standard proof of
spec-compliance.

## Layout

```
nic_mseed/          Python: steim (codec) + mseed (record writer) + from_mla (converter)
c/                  C: portable Steim-1/2 codec + miniSEED writer (+ tests, CMake)
examples/           runnable MLA → miniSEED demo
tests/              codec round-trip, writer, and end-to-end converter tests
third_party/        vendored NIC-MLA + NIC-DMD (see VENDORED.md)
```

The Python reference is pure Python 3.10+, no external packages (ObsPy is an optional
*test-only* check). The C build is host-testable with CMake:

```bash
cmake -S c -B c/build && cmake --build c/build && ctest --test-dir c/build --output-on-failure
```

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Acknowledgements

To my brother for advice during the development of this project.
For technical assistance with code optimisation, to AI assistants Claude (Anthropic) and Gemini (Google).

★ Viva La Resistánce ★
