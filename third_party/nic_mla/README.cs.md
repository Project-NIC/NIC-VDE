<p align="center">
  <img src="NICMLA.svg" width="200"/>
</p>

[For documentation in English click here](README.md) | [Для документации на русском языке нажмите здесь](README.ru.md)

---
# NIC-MLA

**Matroshka Logging Archive** — univerzální jednosouborový kontejner pro záznam
dat z měřicích stanic. Data i log jsou v **jednom přenosném souboru**, čitelném
napříč platformami od 8bitového mikrokontroléru po PC.

Jeden soubor, jeden formát, jeden způsob čtení — vytáhneš kartu ze zařízení,
strčíš do počítače a máš všechno. Žádný zoo formátů.

> Plná specifikace formátu: **[`DESIGN-MLA.md`](DESIGN-MLA.md)**

## Hlavní vlastnosti

- **Jeden soubor = data + log.** Dva proudy rostou proti sobě: data shora,
  log zdola.
- **Hloupý kontejner.** MLA jen ukládá bajty. Veškerá inteligence (komprese,
  šifrování, překlad čísel stanic, LoRa/Wi-Fi) je v samostatné „glue" (lepidlo)
  vrstvě — MLA zůstává malé a nepřekáží.
- **Drobný 16 B log záznam, celý chráněný CRC.** Žádný trik „flags mimo CRC":
  záznam se zahodí přepsáním na nuly — jeho CRC pak nesedí a čtečka ho přeskočí.
- **Crash-safe.** Commit protokol „LOCK first, DATA second" + CRC16 (CCITT-FALSE).
  Po resetu poslední záznam buď sedí (jede se dál), nebo se vynuluje a místo
  uvolní. Žádný strom hledání na disku, který by se mohl rozbít.
- **Sebepopisný.** Prefix nese SCHEMA tabulku (8znakové názvy polí + jednotky →
  připraveno na export do CSV/SQL bez předchozí znalosti) a STATION tabulku
  (1bajtový index stanice v každém log záznamu → skutečné číslo stanice).
- **Malý pro mikrokontrolér.** ATmega328 (2 KB RAM) jen zapisuje; žádná dynamická
  alokace, největší buffer 32 B. Hledání a čtení běží až na hostu.
- **Rotace souborů.** Po zaplnění se založí další soubor; velké objemy = víc
  menších souborů, host je čte jako celek.
- **32-bit adresace** → jeden soubor až 4 GB (nad to rotace).
- **Volitelná komprese.** Kontejner komprimovaná data nese a typuje (`rec_type`:
  raw / delta / keyframe); samotnou kompresní metodu nedefinuje.
- **Nezávislé na souborovém systému.** Přístup přes tenký HAL (4 funkce);
  FAT16 / FAT32 / exFAT / NTFS / ext4 řeší vrstva pod ním (OS, SdFat nebo FatFs).

## Rozložení souboru

```
offset 0                                                        EOF
┌──────────────────┬──────────────────┬──────────────┬──────────────┐
│ PREFIX           │ DATA  proud  →    │  volné  0xFF  │  ← LOG proud  │
│ 1–255 sektorů    │ (roste nahoru)    │              │ (roste dolů)  │
│ (po 512 B)       │                   │              │               │
└──────────────────┴──────────────────┴──────────────┴──────────────┘
```

- **Prefix:** 34 B hlavička + tabulky SCHEMA a STATION, kryté CRC16 v posledních
  2 bajtech. Standardně jeden 512 B sektor; roste po celých sektorech (max 255 ≈
  127 KB) jen když to tabulky potřebují.
- **Datový blok:** `MAGIC(2) + payload(1..65535) + CRC16(2)`
- **Log záznam (16 B), celý v CRC:** offset, timestamp, length, rec_type,
  kf_back, station (1bajtový index), reserved, CRC16.

## Struktura repozitáře

| Cesta | Obsah |
|---|---|
| `nic_mla.py` | Python referenční jádro (format / mount / append / read / recover) |
| `nic_mla_archive.py` | Python: rotace souborů (`MlaArchive`) + hostový dotaz (`query`) |
| `tools/mla_schema.py` | Stavba/čtení tabulek SCHEMA + STATION; dekód payloadu pro CSV/SQL |
| `nic_mla_test.py` | Testovací sada (Python) |
| `c/` | C knihovny: write-only (MCU) + kompletní (ARM/PC) + HAL adaptéry |
| `DESIGN-MLA.md` | Návrhová specifikace formátu |

## Rychlý start — Python

```python
from nic_mla import MlaCore, MlaPosixHAL

# První spuštění (vytvoří 1MB soubor předvyplněný 0xFF)
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format()
    mla.append(timestamp, station=1, data=b"\x01\x02\x03")   # station = index do tabulky

# Další spuštění: mount() obnoví stav; iterace čte záznamy
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    for rec, payload in mla:
        ...
```

Rotace přes víc souborů a filtrování:

```python
from nic_mla_archive import MlaArchive, query
with MlaArchive("/data") as arch:          # MLA00000.MLA, MLA00001.MLA, …
    arch.append(ts, station=1, data=payload)
for rec, data in query(MlaArchive("/data"), station=1, time_from=t0, time_to=t1):
    ...
```

Sebepopisný soubor (tabulky schema + stanice → export do CSV/SQL):

```python
from mla_schema import SchemaBuilder, StationTable, read_schema, \
                       read_stations, decode_payload, split_station

sb = SchemaBuilder()
sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
st = StationTable(); st.station(region=55, number=25000)   # index 1 → tato stanice

hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format(schema_table=sb.table(), station_table=st.table())
    mla.append(ts, station=1, data=teplota.to_bytes(2, "little", signed=True))

# Libovolná čtečka obnoví názvy, jednotky i skutečné číslo stanice — bez znalosti:
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    pfx = mla._prefix.to_bytes()
    _, fields = read_schema(pfx); stations = read_stations(pfx)
    for rec, data in mla:
        region, number, _ = split_station(stations[rec.station - 1])
        cols = decode_payload(fields, data)   # [(název, jednotka, hodnota), …]
```

Testy:

```sh
python3 nic_mla_test.py
```

## Rychlý start — C

Dvě knihovny sdílí jednu definici formátu (`c/nic_mla_format.h`):

- **write-only** (`c/nic_mla_write.{h,c}`) — pro ATmega / malá Arduina,
- **kompletní** (`c/nic_mla.{h,c}`) — pro ARM Arduino / PC (+ čtení, dotaz, recover).

HAL (4 funkce) napojíš na svůj souborový systém. Hotové adaptéry v `c/hal/`:

| Platforma | „Pod HALem" | Adaptér |
|---|---|---|
| Raspberry Pi / PC (SSD, SD, USB) | OS: ext4 / exFAT / NTFS / FAT32 / FAT16 | `hal/nic_mla_hal_posix.{h,c}` |
| Arduino AVR / ESP / STM32duino | SdFat | `examples/atmega_sd_writeonly.ino` |
| STM32 bare-metal (CubeIDE/HAL) | FatFs (ChaN) | `hal/nic_mla_hal_fatfs.{h,c}` |

Build a test na PC:

```sh
cd c
cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c \
   hal/nic_mla_hal_posix.c -o mlatest
./mlatest
```

Viz **[`c/README.md`](c/README.md)**.

## Poznámky pro integrátory

- **Názvy stanic nejsou v souboru.** Tabulka STATION nese jen 6 syrových bajtů
  na stanici; co znamenají (region / číslo / město / …) určuje tvoje glue (lepidlo)
  vrstva, která si drží vlastní mapování „6 bajtů → význam". Log nese jen 1bajtový
  index — překlad na skutečné číslo stanice je práce glue, ne kontejneru.
- **Bajt `reserved` v log záznamu je výplň**, která zarovnává záznam na 16 B
  (mocnina dvojky, takže nikdy nepřesahuje sektor). Je uvnitř CRC a teď je vždy 0
  — ber ho jako volné místo pro budoucí pole, ne jako něco, co dnes něco znamená.

## Přenos dat (LoRa / síť)

**Mimo rozsah** — kontejner je úložiště, ne transport. Každý záznam je
samostatný (typ + délka + CRC), takže poslat ho po LoRa/síti znamená „vzít bajty
záznamu a odeslat je". Volbu transportu nechává projekt na uživateli.

## Stav

Python i C reference jsou hotové, otestované a **bajtově shodné** (soubor zapsaný
C knihovnou přečte Python a naopak).

## Licence

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Poděkování

Bratrovi za rady při tvorbě tohoto projektu.
Za technickou asistenci s optimalizací kódu AI asistentům Claude (Anthropic) a Gemini (Google).

★ Viva La Resistánce ★
