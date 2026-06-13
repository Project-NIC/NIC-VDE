# NIC-GLUE-IN

**Spojovací vrstva mezi knihovnami NIC — DMD, KSF, MLA, VDE — na vstupní (zápisové) straně.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   řádek senzoru / paket z drátu ──▶ [ volitelně NIC-DMD ] ──▶ kontejner NIC-MLA
                                                                       │
                                                                       ▼
                                          NIC-VDE  (prohlížeč / export, jen pro čtení)
```

> **Přečti si nejdřív tohle.** Lepidlo je *tvoje, utvař si ho.* Knihovny jde
> propojit mnoha správnými způsoby a ten nejlepší závisí na tvém zařízení, lince
> a na tom, co chceš s daty dělat. Tenhle repozitář je proto **funkční příklad
> plus katalog možností** — ne framework, který musíš převzít. Trvalá hodnota
> je tady **[reference srovnání knihoven](#reference-srovnání-knihoven)**: malá
> množina švů, kde se knihovny musí shodnout, sepsaná jednou, ať se v tom každý
> vyzná. Směr čtení / exportu je sesterský projekt **NIC-GLUE-OUT**;
> **NIC-VDE** je prohlížeč.

---

## Reference srovnání knihoven

Knihovny NIC jsou záměrně *hloupé a nezávislé*: MLA ukládá neprůhledné bajty,
DMD kóduje pakety pevné šířky, VDE prohlíží soubory, KSF transformuje bajty.
Žádná o ostatních neví. Lepidlo je jakýkoliv kód, který tyhle švy srovná. Je
jich jen hrstka a správně je zapojit je celá ta práce:

| Šev | Co každá strana vystavuje | Jak se to srovná |
|---|---|---|
| **Bit `compressed` + `kf_back`** | MLA log v1.1 nese 1bajtový `flags` (bit 7 = `compressed`, bity 0–6 = `kf_back`), ale neinterpretuje ho; *který* kodek se použil, žije v hlavičce datového bloku (DMD bajt 0), nikdy v MLA | lepidlo nastaví bit `compressed` (`False` pro syrové řádky, `True` pro výstup DMD) a `kf_back`; druhy záznamu jsou **raw** (nekomprimovaný), **keyframe** (komprimovaný, `kf_back == 0`), **delta** (komprimovaný, `kf_back > 0`) |
| **Keyframe** | keyframe DMD = číslo vzorku `0` (3bitové pole; hodnota `7` rezervovaná pro verzi protokolu) | lepidlo to přečte zpět z DMD blobu (`blob[0] & 0x07 == 0` ⇒ keyframe = DMD vzorek 0) a podle toho záznam označí |
| **Vzdálenost ke keyframe** | MLA log má pole `kf_back`, které jen nese; čtečka potřebuje najít vlastní keyframe | lepidlo nastaví `kf_back` = počet záznamů zpět k vlastnímu keyframe (`0` na keyframe) |
| **Nápověda kadence keyframe** | MLA prefix má `keyframe_intv` (jen metadata); kadence DMD je interní (`DMD_KEYFRAME_EVERY`) | default knihovny `0`; lepidlo dosadí kadenci DMD, ať to volající nikdy nepíše ručně (přepsatelné) |
| **Subsekundový tik** | MLA log nese pole `subsec` (subsekundový tik / index vzorku) vedle 4bajtového `timestamp` | lepidlo `subsec` propustí v `log_raw` / `CompressedChannel.log` |
| **Šířka paketu** | DMD vyžaduje, aby každý paket ve streamu měl *stejnou* šířku (delta) | šířka patří **kanálu** (4..255 B), vynucená při každém `log()`; různé kanály se mohou lišit |
| **Identita streamu** | identita streamu v souboru *je* jeho index stanice MLA; MLA nepotřebuje žádný další tag na záznam | čtečka rozliší streamy podle stanice a přečte `kf_back`, aby našla keyframe každého streamu; jeden bezstavový DMD kompresor + N drobných kontextů na stream (`ChannelBank`) drží delty pohromadě |
| **Rotace → keyframe** | MLA v1.1 (2b) vynáší rotační událost, ať je každý rotovaný soubor nezávisle dekódovatelný | lepidlo si při přetočení vynutí keyframe: `MlaArchive(dir, on_rotate=bank.on_rotate)` (nebo zkontroluj `arch.will_rotate(n)` před kódováním) → `ChannelBank.on_rotate` zavolá `reset_all()`, takže první záznam každého streamu v novém souboru je keyframe (pro RAW data bezpředmětné) |
| **Stanice** | MLA log ukládá 1bajtový *index* stanice (1..255), reálná čísla žijí v tabulce stanic v prefixu | lepidlo / `MlaStationTable` vlastní mapování index ↔ region/číslo |
| **Čas** | MLA log má vyhrazené 4bajtové `timestamp`; pole schématu `log("datetime")` (preset `4 B unix_s`) ho *popisuje* | čas žije v hlavičce logu, **ne** zaduplikovaný v datovém bloku — viz [možnosti času](#1-odkud-se-bere-časové-razítko) |
| **Rozložení polí** | schéma odděluje pole `log(...)` (hlavička) od polí `data(...)` (payload); `mla_decode_payload` rozbalí blok | rozdělení log vs. data *je* ta mapa „co jde do hlavičky" vs. „co zůstává v bloku" |
| **Integrita** | MLA pokrývá log záznam (a volitelně datový blok) přes CRC16 | vyber `MLA_CRC_FULL` (doporučeno), `MLA_CRC_DATA`, nebo `MLA_CRC_NONE` při formátování |

Pokud tvoje vlastní lepidlo tuhle tabulku respektuje, tvoje soubory projdou tam
i zpět přes NIC-VDE a NIC-GLUE-OUT bez ohledu na to, jak si uděláš zbytek.

---

## Co příklad obsahuje

Záměrně malý datalogger nad jedním MLA kontejnerem:

- **`GlueLogger`** — `log_raw()` / `log_event()`: vezmi řádek, ulož řádek. Běžný
  případ; funguje pro libovolný počet stanic.
- **`CompressedChannel`** — `open_compressed_channel(station, pkt_len)` a pak
  `.log(ts, row)`: volitelná komprese NIC-DMD pro **jeden stream pevné šířky**,
  s automaticky doplněným bitem `compressed` / `kf_back`.
- **`ChannelBank`** — `open()` / `log()` / `reset_all()` / `on_rotate()`: jeden
  bezstavový DMD kompresor + N drobných kontextů na stream, jeden
  `CompressedChannel` na index stanice MLA. Zapoj `MlaArchive(dir, on_rotate=bank.on_rotate)`,
  ať každý rotovaný soubor začíná každý stream keyframe-em.

```python
from nic_glue_in import GlueLogger, MlaSchemaBuilder, MlaStationTable

schema = MlaSchemaBuilder(); schema.log("datetime")          # popisuje časové razítko logu
for n in ("temp", "humidity"): schema.data(n, unit="raw", width=2)
stations = MlaStationTable(); stations.station(region=55, number=25000)

with GlueLogger("out.mla", schema_table=schema.table(),
                station_table=stations.table()) as log:
    log.log_raw(ts, station=1, data=row_bytes)            # klasická cesta (raw)
    log.log_event(ts, station=1, text="PING")             # jen nekomprimovaný záznam

    ch = log.open_compressed_channel(station=1, pkt_len=4) # volitelná komprese
    ch.log(ts, row_bytes)                                  # → compressed, kf_back (keyframe/delta)
```

```bash
python3 examples/weather_datalogger.py     # zapíše weather_raw.mla + weather_dmd.mla
python3 tests/test_glue.py                  # nebo: pytest tests/
```

---

## Možnosti návrhu & jak na to

Tohle jsou *možnosti*, ne povinnosti — vyber, co se hodí. Příklad implementuje
tu nejjednodušší z každé; zbytek je naskicovaný, ať si to rozšíříš.

### 1. Odkud se bere časové razítko

MLA log záznam má vyhrazené 4bajtové `timestamp`, oddělené od neprůhledného
datového bloku, a pole schématu `log("datetime")` ho popisuje. Takže čas patří
**do hlavičky logu**, nikdy zaduplikovaný v datech. Jak se tam dostane, je na
tobě:

- **(a) Vlastní hodiny lepidla (RTC / čas příjmu).** Nejjednodušší: lepidlo
  orazítkuje každý záznam časem, kdy ho *přijalo / zalogovalo*, z RTC zařízení.
  Paket nese jen senzorová data. To dělá příklad — `timestamp` je argument
  `log_raw` / `Channel.log`.
- **(b) Vytažené z hlavičky paketu.** Paket z drátu sám nese čas jako hlavičku
  (např. `[datetime 4 B unix_s][senzory …]`). Při příjmu lepidlo hlavičku
  uřízne — **šířky log-polí ve schématu řeknou, kde je** — zapíše ji do
  `log.timestamp` a zbylé senzorové bajty uloží jako blok. „Hlavička se přesune
  do logu." DMD o čase nic neví; ten, kdo zná offset, je *schéma*.
- **(c) Dodané volajícím.** Vrstva nad tím, která už autoritativní čas zná, ho
  předá rovnou.

> Postup pro (b), příjem z drátu:
> `recv(blob)` → `DmdDecoder.decompress(blob)` → `packet` →
> `t = int.from_bytes(packet[:4], "little")` → `data = packet[4:]` →
> `MlaCore.append(t, station, data, compressed=…, kf_back=…)`.

### 2. Komprimovaně v úložišti, nebo jen na drátě?

1bajtová hlavička DMD a vlastnost „nikdy nezvětší o víc než 1 B, nikdy neztratí
data" dělají z ukládání komprimovaně bezpečnou volbu. Dva přístupy:

- **Ulož RAW (rozbalené).** Když přijmeš komprimovaný paket, při příjmu ho
  rozbal a senzorové bajty ulož doslovně (záznam typu **raw** — bit `compressed`
  zůstane nulový). Čtečka nepotřebuje žádný kodek; VDE dekóduje rovnou ze
  schématu. Stojí místo, kupuje jednoduchost.
- **Ulož komprimovaně (záznam **keyframe** a pak **delta**).** Nech DMD blob
  v datovém bloku (bit `compressed` nastavený; `kf_back == 0` značí keyframe,
  `kf_back > 0` deltu). Menší soubory. Cena je **náhodný přístup**: protože každý
  delta paket je relativní vůči předchozímu, k otevření záznamu *i* musíš
  přehrát stream od jeho keyframe dopředu — přesně k tomu je `kf_back` (řekne
  čtečce, jak daleko zpět keyframe sedí). Pro jeden kanál je to jeden malý
  buffer „předchozího vzorku" a procházka od keyframe.

### 3. Více streamů

`CompressedChannel` je jeden DMD stream = jeden index stanice + jedna pevná
šířka. Model je **jeden bezstavový DMD kompresor + N drobných kontextů na
stream**, což je přesně to, co poskytuje `ChannelBank`: spravuje několik
`CompressedChannel`ů, jeden na index stanice MLA (`open` / `log` / `reset_all` /
`on_rotate`). Můžeš jich otevřít víc (až 255), ale delta cokoliv získá jen
*uvnitř* streamu, takže komprese desítek nezávislých stanic většinou jen stojí
RAM (jeden buffer předchozího vzorku na každou).

Při rotaci souboru (NIC-MLA 2b) zapoj `MlaArchive(dir, on_rotate=bank.on_rotate)`
(nebo zkontroluj `arch.will_rotate(n)` před kódováním): `ChannelBank.on_rotate`
zavolá `reset_all()`, takže první záznam každého streamu v novém souboru je
keyframe a každý rotovaný soubor je nezávisle dekódovatelný. Příklad komprimuje
jednu stanici, aby ukázal, že to jde; vše ostatní loguje raw.

### 4. Šifrování (NIC-KSF)

KSF záměrně **není** v úložné cestě — ukládat šifrový text do kontejneru je
špatná vrstva (důvěrnost v úložišti nech na trusted platformě). Jeho místo je
**transportní** cesta: odesílatel zašifruje (volitelně zkomprimovaný) paket
před odesláním, příjemce ho dešifruje před uložením. Klíč vlastní oba konce;
kontejner ho nikdy nevidí.

> Pořadí na drátě (odesílatel): `zabal řádek → [DMD komprese] → [KSF šifrování] → odešli`.
> Příjemce to zrcadlí: `příjem → [KSF dešifrování] → [DMD dekomprese] → ulož`.
> Pozor: DMD bere šifrovaná data jako náhodná a uloží je RAW (+1 B), takže
> **komprimuj před šifrováním**, nikdy naopak.

---

## Rozložení

```
nic_glue_in/        samotné lepidlo (GlueLogger, CompressedChannel, ChannelBank)
examples/           spustitelný weather datalogger
tests/              testy round-trip + mapování portů
third_party/        vendorované kopie NIC-DMD a NIC-MLA (viz VENDORED.md)
tools/              sync_vendor.py — přesync third_party/ z kanonického NIC-MLA/NIC-DMD
```

Čistý Python 3.10+, žádné externí balíčky — závislosti jsou vendorované.

---

## Datalogger (více profilů)

Zapiš víc typů stanic do jednoho `.mla` (různé layouty sloupců): předej datalogger tabulky jako `schema_table` a použij `log_raw(station, data)`. Viz `DataloggerBuilder` a `tests/test_datalogger.py`; specifikace v NIC-MLA `DESIGN-MLA-datalogger.cs.md`.

## Licence

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Poděkování

Bratrovi za rady při tvorbě tohoto projektu.
Za technickou asistenci s optimalizací kódu AI asistentům Claude (Anthropic) a Gemini (Google).

★ Viva La Resistánce ★
