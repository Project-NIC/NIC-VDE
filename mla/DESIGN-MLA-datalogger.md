# NIC-MLA — Datalogger format (profile-ref)

> **Status:** implemented (Python reference + 33 tests). **Additive** to the
> v1.2 single-schema format — the 16-byte log record is unchanged and v1.2 files
> keep working. Reference: `tools/mla_datalogger.py`, tests
> `tools/mla_datalogger_test.py`. Date: 2026-06-06

## Why
A datalogger / LoRa repeater receives from **several station types** (meteo,
electricity, bee-hive, …) and must log them into **one** `.mla`. v1.2 carries a
single schema per file (one layout + many station identities); the datalogger
format lets **each station carry its own column layout** while still sharing a
layout when stations are identical.

## Model — profile-ref
- **PROFILE** = a column layout (its own data-field descriptors).
- **STATION** = an 8-byte opaque identity + a 1-byte reference to a profile.
- The 16-byte log record's 1-byte **station index** selects a station →
  `{ identity, profile_ref }` → the profile → decode the payload.

```
log record.index → STATION (identity + profile_ref) → PROFILE (column layout) → values
```

This shares the *layout* across identical stations (8 meteo stations → 1 profile
+ 8 station rows) yet allows *different* layouts in one file (meteo + electricity).

## Binary layout (carried in the prefix `schema_table` slot, after the 34 B header)
Each section is tagged and self-sizing; a reader walks them in order. The whole
blob is covered by the prefix CRC, exactly like the v1.2 schema table.

```
LOG       : 0x4C  n_log         n_log × 14B descriptor      (describes the fixed 16B record)
PROFILES  : 0x50  n_profiles    [ n_data(1B)  n_data × 14B ] × n_profiles
STATIONS  : 0x54  n_stations    [ identity(8B)  profile_ref(1B) ] × n_stations
```

The 14-byte field descriptor and `physical = (raw + offset) × 10^exp10` are the
**same** as v1.2 (`width 1/2/4 · unit · exp10 i8 · flags · offset i16 · name 8B`).
The tags (0x4C/0x50/0x54) differ from the v1.2 schema tag (0x01) so the core
(`_schema_byte_len` in `nic_mla.py`) sizes either format transparently — `MlaCore`
just carries the bytes.

## Station identity (8 B, opaque)
MLA gives the 8 bytes no meaning; the glue does. Builder encoders:
- `dl_gps(lat, lon)` — 2× i32 (degrees ×10⁷, ~1 cm)
- `dl_ident(number, region, kind, reserved)` — hierarchical (4× u16)
- `dl_raw(8 bytes)` — anything
> Four electricity meters in one box → 4 stations with the **same GPS, different
> `number`, same profile_ref** (the layout is stored once).

## Usage
```python
from mla_schema import MlaField
from mla_datalogger import DataloggerBuilder, DataloggerTables, dl_gps, export_csv
from nic_mla import MlaCore, MlaPosixHAL

# 1) describe profiles + stations
b = DataloggerBuilder()
b.log("datetime")
meteo = b.profile([MlaField("temp", 2, "degC", -2, signed=True),
                   MlaField("hum",  2, "pct",  -1)])
elec  = b.profile([MlaField("power", 2, "W"), MlaField("energy", 4, "kWh")])
b.station(dl_gps(50.0875, 14.4213), meteo)   # station 1
b.station(dl_gps(49.1951, 16.6068), meteo)   # station 2 (same layout)
b.station(dl_gps(50.0875, 14.4213), elec)    # station 3 (different layout)
blob = b.serialize()

# 2) write a real .mla (the tables ride in the schema_table slot)
hal = MlaPosixHAL.create("weather.mla", 64 * 1024)
with hal:
    m = MlaCore(hal); m.format(file_size=64 * 1024, schema_table=blob)
    t = DataloggerTables.parse(blob)
    m.append(1700000000, station=1, data=t.encode(1, {"temp": 25.45, "hum": 60.0}))
    m.append(1700000060, station=3, data=t.encode(3, {"power": 1500, "energy": 12345}))

# 3) export → one CSV / one SQL table per profile
export_csv("weather.mla", "out/")
```

## Limits
- ≤ 255 profiles, ≤ 255 stations (1-byte counts / index), ≤ 255 columns per profile.
- Data payload per record ≤ 65535 B (log `length` is u16); DMD-compressed rows ≤ 255 B.
- A file is v1.2-schema **or** datalogger, told apart by the tag at offset 34.
  The container, CRC, crash-safety, rotation and compression are identical to v1.2.

## What links it
- `tools/mla_datalogger.py` — builder, reader, decode-by-profile, identity encoders, CSV/SQL export.
- `nic_mla.py` — `_schema_byte_len` sizes the datalogger blob (additive).
- Mixed-profile decode/export verified end-to-end against a real `.mla` in the tests.
