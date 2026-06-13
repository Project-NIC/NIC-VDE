# NIC-MLA — Datalogger formát (profile-ref)

> **Stav:** implementováno (Python referenční + 33 testů, C reader + dekodér hodnot).
> **Přírůstkové** k formátu v1.2 (jedna schema) — 16bajtový log paket se nemění a
> v1.2 soubory dál fungují. Reference: `tools/mla_datalogger.py`, testy
> `tools/mla_datalogger_test.py`, C `c/datalogger_test.c` + `c/mla_decode.c`.
> Datum: 2026-06-06

## Proč
Datalogger / LoRa repeater přijímá z **víc typů stanic** (meteo, elektřina, úl, …)
a musí je zalogovat do **jednoho** `.mla`. v1.2 nese jednu schemu na soubor (jeden
layout + víc identit stanic); datalogger formát nechá **každou stanici nést svůj
vlastní layout sloupců**, a přitom layout sdílet, když jsou stanice stejné.

## Model — profile-ref
- **PROFIL** = layout sloupců (vlastní descriptory datových polí).
- **STANICE** = 8bajtová neprůhledná identita + 1bajtový odkaz na profil.
- 1bajtový **index stanice** v log paketu vybere stanici →
  `{ identita, odkaz na profil }` → profil → dekód payloadu.

```
log paket.index → STANICE (identita + odkaz) → PROFIL (layout) → hodnoty
```

Sdílí *layout* mezi stejnými stanicemi (8 meteostanic → 1 profil + 8 řádků
stanic), a přitom dovolí *různé* layouty v jednom souboru (meteo + elektřina).

## Binární rozložení (v prefixu, ve slotu `schema_table`, za 34B hlavičkou)
Každá sekce je otagovaná a sebe-měřitelná; čtečka je projde po pořadí. Celý blok
je krytý CRC prefixu, přesně jako v1.2 schema tabulka.

```
LOG       : 0x4C  n_log         n_log × 14B descriptor      (popisuje pevný 16B paket)
PROFILY   : 0x50  n_profilů     [ n_data(1B)  n_data × 14B ] × n_profilů
STANICE   : 0x54  n_stanic      [ identita(8B)  odkaz(1B) ] × n_stanic
```

14bajtový descriptor pole a `fyzikální = (raw + offset) × 10^exp10` jsou **stejné**
jako ve v1.2 (`width 1/2/4 · unit · exp10 i8 · flags · offset i16 · name 8B`).
Tagy (0x4C/0x50/0x54) se liší od v1.2 schema tagu (0x01), takže jádro
(`_schema_byte_len` v `nic_mla.py`, resp. `mla_datalogger_size` v `nic_mla.c`)
spočítá délku obou formátů — `MlaCore` jen veze bajty.

## Identita stanice (8 B, neprůhledná)
MLA jí nepřiřazuje význam; dělá to glue. Enkodéry v builderu:
- `dl_gps(lat, lon)` — 2× i32 (stupně ×10⁷, ~1 cm)
- `dl_ident(number, region, kind, reserved)` — hierarchická (4× u16)
- `dl_raw(8 bajtů)` — cokoliv
> Čtyři elektroměry v jedné krabici → 4 stanice se **stejnou GPS, různým `number`,
> stejným odkazem na profil** (layout uložený jednou).

## Použití
```python
from mla_schema import MlaField
from mla_datalogger import DataloggerBuilder, DataloggerTables, dl_gps, export_csv
from nic_mla import MlaCore, MlaPosixHAL

# 1) popiš profily + stanice
b = DataloggerBuilder()
b.log("datetime")
meteo = b.profile([MlaField("temp", 2, "degC", -2, signed=True),
                   MlaField("hum",  2, "pct",  -1)])
elec  = b.profile([MlaField("power", 2, "W"), MlaField("energy", 4, "kWh")])
b.station(dl_gps(50.0875, 14.4213), meteo)   # stanice 1
b.station(dl_gps(49.1951, 16.6068), meteo)   # stanice 2 (stejný layout)
b.station(dl_gps(50.0875, 14.4213), elec)    # stanice 3 (jiný layout)
blob = b.serialize()

# 2) zapiš reálný .mla (tabulky jedou ve slotu schema_table)
hal = MlaPosixHAL.create("weather.mla", 64 * 1024)
with hal:
    m = MlaCore(hal); m.format(file_size=64 * 1024, schema_table=blob)
    t = DataloggerTables.parse(blob)
    m.append(1700000000, station=1, data=t.encode(1, {"temp": 25.45, "hum": 60.0}))
    m.append(1700000060, station=3, data=t.encode(3, {"power": 1500, "energy": 12345}))

# 3) export → jedna CSV / SQL tabulka na profil
export_csv("weather.mla", "out/")
```

## Limity
- ≤ 255 profilů, ≤ 255 stanic (1bajtové počty / index), ≤ 255 sloupců na profil.
- Payload na záznam ≤ 65535 B (log `length` je u16); DMD-komprimovaný řádek ≤ 255 B.
- Soubor je buď v1.2-schema **nebo** datalogger, rozliší se tagem na offsetu 34.
  Kontejner, CRC, crash-safety, rotace i komprese jsou shodné s v1.2.

## Co to linkuje
- `tools/mla_datalogger.py` — builder, čtečka, dekód podle profilu, enkodéry identity, CSV/SQL export.
- `nic_mla.py` / `c/nic_mla.c` — sizing datalogger bloku (přírůstkově).
- `c/mla_decode.c` — C dekodér hodnot, byte-identický s Pythonem (i pro v1.2).
- Míchání profilů ověřeno end-to-end proti reálnému `.mla` v testech (Python i C).
