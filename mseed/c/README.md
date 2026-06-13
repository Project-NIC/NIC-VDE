# NIC-MSEED — C codec core

**Steim-1/2 + miniSEED 2.x record writer/reader in portable C11, no deps.**

A faithful, **byte-exact** port of the Python codec core (`../nic_mseed/steim.py`
+ `mseed.py`) — the "container-agnostic" layer: integers → Steim frames →
miniSEED records, and back. It lets the **data station (ESP32) write miniSEED in
C**, not only the host PC, and is embeddable like the rest of the NIC C ecosystem
(no `malloc`, no globals — the caller owns every buffer).

The MLA + NIC-DMD wiring (`from_mla`) stays in the Python tool; a C converter
would need C MLA/DMD readers — a separate follow-up. This is the **codec**, the
piece worth having on-device.

## API (`include/nic_mseed.h`)

```c
/* integers -> one Steim record (frames_per_record * 64 B) */
int  nic_steim_encode_record(const int32_t *samples, size_t nsamp, int version,
                             int frames_per_record, int32_t prev,
                             uint8_t *out, size_t *used_samples);
int  nic_steim_decode_record(const uint8_t *frames, size_t len,
                             size_t n_samples, int version, int32_t *out);

/* one channel series -> concatenated fixed-length miniSEED records */
long nic_mseed_write_stream(const nic_mseed_params_t *p, const int32_t *samples,
                            size_t nsamp, int64_t start_unix, double start_frac,
                            uint32_t seq_start, uint8_t *out, size_t out_cap);
int  nic_mseed_write_record(/* one record at a time, no malloc */ ...);
int  nic_mseed_read_record (const uint8_t *rec, size_t cap,
                            nic_mseed_rechdr_t *hdr, int32_t *out, size_t out_cap);
```

`STEIM2` → encoding 11, `STEIM1` → encoding 10; record length is any power of two
≥ 128 (512 default → 7 frames). Big-endian throughout, FSDH + Blockette 1000 only
— the universal subset libmseed / ObsPy / SeisComP read.

## Build & test

```sh
cmake -S c -B c/build && cmake --build c/build
ctest --test-dir c/build --output-on-failure
```

Two suites: `test_steim` and `test_mseed`. They check internal round-trips **and**
compare against reference vectors in `test/vectors.h` — generated from the Python
(`c/tools/gen_vectors.py`), which is itself validated against ObsPy. So "C matches
the vectors" ⇒ "C matches ObsPy-grade miniSEED." Regenerate the vectors with:

```sh
python3 c/tools/gen_vectors.py     # run from the nic-mseed/ package root
```

## License

MIT — Copyright (c) 2026 NIC — Native Intellect Community
