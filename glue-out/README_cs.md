# NIC-GLUE-OUT

**Spojovací vrstva mezi knihovnami NIC — DMD, KSF, MLA, VDE — na výstupní (čtecí / exportní) straně.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   kontejner NIC-MLA ──▶ dekód schématu ──▶ řádky ──▶ CSV / SQLite
```

> **Přečti si nejdřív tohle.** Tohle je sourozenec **NIC-GLUE-IN**, stejný nápad
> otočený opačně: GLUE-IN zapojí řádek dat *do* kontejneru; GLUE-OUT projde
> hotový kontejner *zpátky ven* do tabulky. Stejně jako sourozenec je to
> **funkční příklad plus malý katalog možností**, ne framework. Trvalá hodnota
> je zase **[reference srovnání knihoven](#reference-srovnání-knihoven)** — švy
> čtené z druhého konce. Příklad dělá tu nejjednodušší užitečnou věc: otevři
> kontejner, dekóduj každý RAW záznam přes jeho samopopisné schéma a vyexportuj
> vše do **CSV nebo SQLite**. **Kompresi NIC-DMD tahle čtečka dekomprimuje
> automaticky**; mimo rozsah zůstává jen šifrování (NIC-KSF) — viz
> [níže](#použití-nic-dmd-a-nic-ksf). **NIC-VDE** je interaktivní
> prohlížeč týchž souborů; GLUE-OUT je bezhlavá exportní cesta.

---

## Reference srovnání knihoven

Knihovny NIC jsou záměrně *hloupé a nezávislé*: MLA ukládá neprůhledné bajty,
VDE prohlíží soubory, KSF transformuje bajty, DMD kóduje pakety. Žádná o
ostatních neví. Lepidlo je jakýkoliv kód, který tyhle švy srovná. GLUE-IN je
*zapisuje*; celá práce čtečky je *číst ty samé švy zpátky*. Ty, které tahle
jednoduchá čtečka používá:

| Šev | Co kontejner nese | Co s tím čtečka dělá |
|---|---|---|
| **Druh záznamu** | MLA nenese žádný typový bajt; záznam má jen bit `compressed` + `kf_back` (0 = keyframe). Soubory jsou homogenní — žádný typ TEXT/EVENT/CLASS už není, význam dává SCHÉMA | z toho odvodí *druh* `raw` / `keyframe` / `delta`, pak dekóduje na pojmenované hodnoty: raw rovnou ze schématu, komprimované přes DMD (viz níže); řádek je prázdný jen bez shody se schématem |
| **Stanice** | MLA log ukládá 1bajtový *index* stanice; reálná čísla žijí v tabulce stanic v prefixu | přeloží index → region/číslo, ať exportované řádky nesou reálná čísla |
| **Čas** | MLA log má vyhrazené 4bajtové `timestamp`; pole schématu `log("datetime")` ho popisuje | čas jde rovnou z hlavičky logu — nikdy se nedoluje z datového bloku |
| **Rozložení polí** | schéma odděluje pole `log(...)` (hlavička) od polí `data(...)` (payload) | `mla_decode_payload` rozdělí zabalený blok zpátky na pojmenované, škálované hodnoty |
| **Integrita** | MLA pokrývá log záznam (a volitelně datový blok) přes CRC16 | sloty se špatným CRC MLA jádro při mountu přeskočí — čtečka vidí jen potvrzené záznamy |

Pokud soubor tuhle tabulku respektuje na cestě dovnitř (každý GLUE-IN ano),
přečte se rovnou zpátky tady i v NIC-VDE, bez ohledu na to, jak byl udělaný
zbytek.

---

## Co příklad obsahuje

Záměrně malá čtečka / exportér nad jedním MLA kontejnerem:

- **`GlueReader`** — otevři kontejner, pak ho iteruj nebo vyexportuj. Přečte
  samopopisné tabulky schématu/stanic z prefixu, dekóduje každý RAW měřicí
  payload na pojmenované, škálované hodnoty, přeloží index stanice na reálný
  region/číslo a vše serializuje do **CSV** nebo **SQLite**.

```python
from nic_glue_out import GlueReader

with GlueReader("weather.mla") as r:
    for rec in r:                          # dekódované záznamy, od nejstaršího (raw + komprimované)
        if rec.values is not None:         # dekódované hodnoty (raw nebo rozbalené z DMD)
            print(rec.timestamp, rec.station_label,
                  {n: v for n, _u, v in rec.values})
        else:                              # bez shody se schématem — vypiš surové bajty
            print(rec.timestamp, rec.kind, rec.block.hex())

    r.write_csv("weather.csv")             # → idx,time,unix,sta_idx,region,number,kind,length,<pole…>
    r.write_sqlite("weather.db")           # → SQLite databáze s jednou tabulkou
```

```bash
python3 examples/weather_export.py          # postaví vzorek, pak vyexportuje weather.csv + weather.db
python3 tests/test_glue.py                  # nebo: pytest tests/
```

---

## Možnosti návrhu & jak na to

Tohle jsou *možnosti*, ne povinnosti — vyber, co se hodí. Příklad implementuje
nejjednodušší užitečné čtení + export; zbytek je krátký seznam pák.

### 1. Cíle exportu — CSV nebo SQLite

Oba vypadnou ze stejných sestavených řádků; modul `export` je hloupý serializér
(o MLA nic neví). `to_csv()` vrátí UTF-8 bajty; `to_sqlite()` vrátí databázi
s jednou tabulkou jako bajty. Předej `raw=True`, ať místo škálovaných fyzikálních
hodnot necháš celá čísla z drátu. Přidej si vlastní cíl (Parquet, JSON Lines,
socket) napsáním dalšího `to_…`, který bere stejné sloupce `(name, sql_decl)` +
n-tice řádků.

### 2. Odkud se bere časové razítko

Čtečka čas nikdy nehádá: MLA log záznam má vyhrazené 4bajtové `timestamp`,
oddělené od datového bloku, a pole schématu `log("datetime")` ho jen *popisuje*.
Takže čtečka ho bere rovnou z hlavičky logu (`rec.timestamp`) — datový blok je
čistý senzorový payload. Přesný opak švu GLUE-IN „kam jde časové razítko": čas
žije v hlavičce, nikdy zaduplikovaný v datech, na cestě dovnitř *i* ven.

### 3. Filtrování

Čtečka načte celý kontejner do RAM (dokumentovaný host model) a filtruje
host-side: `records(station=…, time_from=…, time_to=…)`. Žádný on-disk index
není — je to lineární průchod, stejný výsledek jako filtrovat každý záznam.

### 4. Soubory bez schématu

Kontejner zapsaný bez schématu se taky přečte: každý záznam spadne do jednoho
sloupce `value` (text jako text, malé payloady jako celé číslo, jinak hex).
Soubor *se* schématem dostane místo toho jeden pojmenovaný sloupec na datové pole.

### Použití NIC-DMD a NIC-KSF

- **NIC-DMD (zabudováno).** Pokud zapisovač použil komprimovaný kanál GLUE-IN, ty
  záznamy nesou bit `compressed` (druh `keyframe` / `delta`). Tahle čtečka je
  **dekomprimuje automaticky**: přehraje stream každé stanice v pořadí přes
  per-station `DmdDecoder(šířka)` (`šířka` = celková datová šířka schématu) a
  výsledek pak prožene stejným dekódem schématu jako RAW cestu — takže
  komprimované i RAW řádky se exportují identicky. Řádek ukáže prázdné buňky jen
  tehdy, když pro něj vůbec není shoda se schématem. **NIC-VDE** takové soubory
  taky prohlíží.
- **NIC-KSF (šifrování).** KSF žije na *transportní* cestě, nikdy v úložišti:
  odesílatel zašifruje před odesláním, příjemce dešifruje *předtím*, než se bajty
  uloží — takže kontejner drží čistý text a tahle čtečka klíč nepotřebuje. Přidej
  ho na straně příjmu (`příjem → ksf_decrypt → … → ulož`), zrcadlo odesílací
  strany GLUE-IN. Viz NIC-GLUE-IN.

---

## Rozložení

```
nic_glue_out/       samotné lepidlo (GlueReader) + hloupý CSV/SQLite exportér
examples/           spustitelná weather čtečka / exportér
tests/              testy zpětného čtení + dekódu + exportu
third_party/        vendorovaná kopie NIC-MLA (viz VENDORED.md)
tools/              sync_vendor.py — přesync third_party/ z kanonického NIC-MLA/NIC-DMD
```

Čistý Python 3.10+, žádné externí balíčky — závislost je vendorovaná, exportér
je stdlib `sqlite3`.

---

## Datalogger (více profilů)

Export datalogger `.mla` (víc profilů stanic v jednom souboru) do CSV / SQLite — tabulka na profil: `is_datalogger()` + `dl_export_csv()` / `dl_export_sqlite()`. Viz `tests/test_datalogger.py`; specifikace v NIC-MLA `DESIGN-MLA-datalogger.cs.md`.

## Licence

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Poděkování

Bratrovi za rady při tvorbě tohoto projektu.
Za technickou asistenci s optimalizací kódu AI asistentům Claude (Anthropic) a Gemini (Google).

★ Viva La Resistánce ★
