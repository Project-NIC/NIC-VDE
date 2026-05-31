# NIC-MLA — Návrhová specifikace formátu

> **Stav:** v1.0 · **Datum:** 2026-05-31
> **MLA** = *Matroshka Logging Archive* — univerzální jednosouborový kontejner
> (data + log v jednom souboru, à la Matroska / tar / DriveSpace).
>
> Tento dokument definuje formát v1.0. **Stav implementace:** Python reference
> (`nic_mla.py`, `nic_mla_archive.py`, `tools/mla_schema.py`) i C knihovny
> (`c/` — write-only pro ATmega + kompletní pro ARM/PC) jsou hotové a bajtově
> shodné (ověřeno cross-compat testem C↔Python).
>
> **Princip návrhu — hloupý kontejner.** MLA jen ukládá bajty: 16 B log záznam
> krytý CRC + datový blok, plus dvě sebepopisné tabulky v prefixu (názvy/jednotky
> polí a index stanice → skutečné číslo). Veškerá inteligence — komprese,
> šifrování, překlad čísel stanic, transport — žije v samostatné glue vrstvě.

---

## 1. Účel a rozsah

NIC-MLA je **univerzální kontejner pro záznam dat** z měřicích stanic
(meteostanice, elektroměr, …). Cílem je jeden přenosný soubor, který nese
**data i log** pohromadě a je čitelný napříč platformami.

**Proč to vzniká:** konec bordelu s milionem formátů. Místo hromady souborů a
nástrojů → **jedna tabulka**, do které jde „bastlit" cokoli. Vytáhneš kartu ze
zařízení, strčíš do počítače a **jeden triviální prohlížeč** složí strukturu
z vnitřních registrů, které jsou tak dokonale popsané, že to zvládne i dítě.
Cíl není být „extra" — cíl je **jednoduchý, intuitivní a triviální proces**,
který ušetří čas i peníze. (Že části tohoto přístupu existují i jinde, nevadí —
hodnota je v tom mít je **pohromadě a samopopisně**.)

### Cílové platformy a role

| Platforma | Role | Co dělá |
|---|---|---|
| **ATmega328** (8-bit) | **WRITE-ONLY** | jen připojuje záznamy (append), à ~15 min; žádné hledání ani editace na čipu |
| Arduino 32/64-bit, STM, ESP | zápis + případně čtení | jako ATmega + lokální čtení |
| **Host** (PC / Raspberry) | čtení, hledání, editace | načte celý log do RAM, filtruje, exportuje |

**Klíčový princip:** zápis je triviální a robustní (kvůli ATmega), zatímco
veškerá inteligence (hledání, dotazy, editace) běží na hostu, kde se log
načte naráz do RAM. **Na disku proto NENÍ žádný strom/AVL — stačí plochý log**,
který host sekvenčně proskenuje. Pole logu jsou navržena tak, aby toto
filtrování bylo rychlé (čas, index stanice, typ).

### Mimo rozsah

- **Editace** záznamů → samostatný budoucí projekt *Volkov Data Editor*.
- **Komprese** → volitelná, řeší ji **samostatná metoda**; kontejner komprimovaná
  data pouze **nese a typuje** přes `rec_type` (delta / keyframe / raw), sám
  kompresi nedefinuje (viz §4).
- **Přímý raw SPI-NOR/NAND** → **experimentální a zamrzlé** (viz
  `experimental/`). Cílové úložiště je **SD/flash karta** — vlastní řadič karty
  řeší wear-leveling, ECC i remapování. Raw NOR jsme opustili kvůli riziku
  lockdownu některých čipů při partial-page/partial-block zápisech a kvůli
  vendor-specifičnosti. NOR simulátor zůstává jen jako doklad univerzálnosti
  formátu (jádro je vůči médiu nezávislé přes HAL), ne jako podporovaná cesta.

---

## 2. Rozložení souboru

Zachováváme osvědčený fyzický model — **dva proudy rostoucí proti sobě**
v souboru pevné velikosti:

```
offset 0                                                            EOF
┌──────────────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX            │ DATA  proud  →    │  volné  0xFF   │  ← LOG proud  │
│ 1–255 sektorů     │ (roste nahoru)    │               │ (roste dolů)  │
└──────────────────┴──────────────────┴───────────────┴──────────────┘
                    ▲ data_base         ▲ top_ptr   bot_ptr ▲  region_end
```

- **DATA** rostou nahoru od `data_base` (`top_ptr` = kam přijde příští blok).
- **LOG** roste dolů od EOF (`bot_ptr`; příští záznam jde
  na `bot_ptr − log_rec_size`).
- Mezi nimi je volné místo vyplněné `0xFF`.
- **PREFIX** je standardně jeden 512 B sektor; roste (po celých sektorech, max
  255) jen když se tabulky SCHEMA + STATION nevejdou. `data_base` = velikost
  prefixu. Žádný samostatný index region — na disku není žádný strom hledání.

### Proč pevná, předalokovaná velikost

Soubor se při `format()` **předalokuje celý** a vyplní `0xFF` (přesně jako dnešní
`MlaPosixHAL.create`). Na FAT/SD je to správná volba:

- řetězec clusterů FAT je alokovaný předem → soubor neroste, nefragmentuje se,
- všechny **logické offsety jsou stabilní** po celou životnost souboru,
- model „dva ukazatele proti sobě“ sedí na pevnou oblast bez konfliktu s FS.

### Podmínka „plno“

```
top_ptr + velikost_příštího_bloku  >  bot_ptr − log_rec_size
```

### Režimy zaplnění (`container_kind` v prefixu)

| Hodnota | Režim | Chování | Doporučení |
|---|---|---|---|
| 0 | **Hard stop** | při zaplnění `RuntimeError` | jednoduché |
| 1 | **Rotace souborů** | po zaplnění se otevře další soubor `NIC0001.MLA`, `NIC0002.MLA`, … ; každý prefix nese `file_seq` | **doporučeno pro FAT/SD** |
| 2 | **Kruhový buffer** | DATA se zalomí zpět nahoru, nejstarší sektor se uvolní (`sector_erase`) a odpovídající LOG sloty se označí jako opuštěné | jen RAW/NOR / experiment |

Rotace je preferovaná: každý soubor je nezávisle mountovatelný a crash-safe,
karty jsou obří, takže zaplnění je vzácné. Kruhový buffer komplikuje obnovu a
odkládá se (viz §9).

### Rozhodnutí: velikost kontejneru a předalokace

- **Volné místo = `0xFF`** (jako čerstvá NOR po erase) zůstává — **žádný superblok**.
  Je to nejjednodušší pro MCU: čip jen zapisuje, `mount()` najde hranici
  skenováním `0xFF`. Žádná persistence ukazatelů v prefixu.
- **Výchozí velikost kontejneru ~1 MB.** Předalokace 1 MB (vyplnění `0xFF`) je
  na MCU jednorázově rychlá; pro velké objemy se **rotuje víc 1MB souborů**.
- **Velké filesystémy** = mnoho 1MB souborů na obří kartě. **Agregaci a čtení
  napříč soubory dělá PC** (`MlaArchive`) — výkonný procesor „sežere všechno",
  takže pomalejší předalokace ani plný sken nejsou problém. MCU drží jen jeden
  otevřený 1MB soubor.
- 32-bit adresace → jeden soubor max **4 GB**; nad to (i pod tím) rotace.
  Pokud by vadil počet souborů, lze `file_size` zvednout (např. 16–64 MB) —
  je to jen volba ve `format()`.

---

## 3. Prefix (1–255 sektorů po 512 B)

Prefix je 34 B strukturovaná hlavička, za ní dvě sebepopisné tabulky (SCHEMA +
STATION), a končí CRC16 přes vše před ním. Standardně je to **jeden 512 B
sektor**; když se tabulky nevejdou, roste po celých 512 B sektorech (max
**255 ≈ 127 KB**) a CRC se přesune na poslední 2 bajty prefixu.

> Limit 255 sektorů je tvrdý strop, ne cíl — existuje jen proto, že počet je
> jeden bajt. **Doporučené maximum je 16 sektorů (8 KB)**; s automaticky
> dimenzovanými tabulkami SCHEMA/STATION se k němu reálná stanice nikdy nepřiblíží.

```
[0]   magic[4]        b"MLA\0"
[4]   version         1 B   = 1
[5]   cluster_shift   1 B   8=256B · 10=1KB · 12=4KB · … · 15=32KB
[6]   log_rec_size    1 B   = 16
[7]   flags           1 B   režim CRC (bity 0-1): 0=NONE · 1=DATA · 2=FULL
[8]   file_size       4 B   uint32 LE
[12]  reserved        8 B   0
[20]  container_kind  1 B   0=single · 1=rotace
[21]  file_seq        2 B   uint16 LE  pořadí souboru v rotaci
[23]  keyframe_intv   1 B   interval keyframe u komprese (výchozí 8; 0 = N/A)
[24]  enc_caps        1 B   bitmaska kódování, která tento soubor smí nést
[25]  data_base       4 B   uint32 LE  = velikost prefixu (první bajt DATA)
[29]  region_end      4 B   uint32 LE  = file_size  (konec LOG proudu)
[33]  reserved        1 B   0
[34]  SCHEMA tabulka  …     §3.1
[..]  STATION tabulka …     §3.2
[konec-2] crc16       2 B   LE  — přes vše před ním
```

### 3.1 SCHEMA tabulka — názvy/jednotky polí pro CSV/SQL

Staví/čte ji `tools/mla_schema.py`. Umožní libovolné čtečce export do CSV/SQL
**bez předchozí znalosti** — stanice nese vlastní popis sloupců.

```
[0] tbl_ver  1 B  = 1
[1] n_log    1 B  počet LOG polí (popisují timestamp atd.)
[2] n_data   1 B  počet DATA polí (sloupce napakovaného payloadu)
[3 ..]       (n_log + n_data) × 14 B deskriptorů pole:
   width 1 B · unit 1 B · exp10 1 B (i8) · flags 1 B (bit0=signed) ·
   offset 2 B (i16 LE) · name 8 B (UTF-8, doplněno NUL)
   fyzikální = (raw + offset) × 10^exp10
```

Slovník jednotek je univerzální (sdílí ho spec); jen *složení* polí (které
senzory, škála, šířka, **8znakový název**) je specifické pro zařízení a cestuje
v souboru.

### 3.2 STATION tabulka — index → skutečná stanice

```
[0] sta_ver  1 B  = 0x53
[1] n        1 B  počet stanic (1..255)
[2 ..]       n × 6 syrových bajtů (index i v logu → záznam i-1)
```

Těch 6 bajtů je pro MLA **neprůhledných**. Časté dělení je `region(2) +
číslo(2) + reserved(2)`, ale rozhoduje glue (lepidlo); klidně `město/číslo/kraj`
nebo jedno velké číslo. Lidé čísla stanic přidělují s mezerami — glue je mapuje
na kompaktní 1bajtové indexy a zpět.

> **Hloupý kontejner.** Obě tabulky se zapisují verbatim shora a C/MCU cesta je
> nikdy nečte. Komprese, šifrování, překlad čísel stanic i transport žijí
> v samostatné glue vrstvě.

---

## 4. Datový blok

Zůstává minimální — datový blok nese **jen ryzí data**, žádný prefix,
žádný typ:

```
┌────────────┬─────────────────────┬──────────┐
│ MAGIC 2 B  │ payload  1..65535 B │ CRC16 2 B│
│ 0xAB 0xCD  │                     │          │
└────────────┴─────────────────────┴──────────┘
```

- **Typ dat ani délka se v bloku NEukládají** — obojí je v LOG záznamu (`rec_type`
  a `length`, §5). Tím se typ nedubluje a zápisová cesta na ATmega je nejmenší
  možná.
- Oddělovací značka `MAGIC` slouží k nalezení bloků při nouzové obnově.
- **CRC16** kryje payload.
- Konec bloku: `block_end = offset + 2 (MAGIC) + length + 2 (CRC)`.

**Vazba keyframe** (které delta-bloky patří k jakému keyframe) se drží
v **LOG záznamu** (pole `kf_back`, §5). Samotná kompresní metoda je vůči
kontejneru neprůhledná; kontejner ji jen rámuje a typuje přes `rec_type`. Keyframe
každých `keyframe_intv` (výchozí 8) balíčků omezuje ztrátu dat, když některý
delta-balíček vypadne při přenosu. Pokud se komprese nevyplatí, blok se uloží
prostě jako `raw` (kontejner to nese bez rozdílu).

---

## 5. LOG záznam (16 B)

LOG záznam žije v LOG proudu (roste dolů od EOF). Má pevných **16 bajtů** a
**celý je krytý CRC** — žádné pole „flags mimo CRC".

```
[0]  offset     4 B  u32   logický offset datového bloku v DATA
[4]  timestamp  4 B  u32   Unix sekundy (dodá volající: RTC/GPS)
[8]  length     2 B  u16   délka payloadu 1..65535
[10] rec_type   1 B        kódování + třída (viz §4)
[11] kf_back    1 B  u8    záznamů zpět k vlastnímu keyframe (0 = tento JE keyframe)
[12] station    1 B  u8    index 1..255 do tabulky stanic v prefixu (0 = žádná)
[13] reserved   1 B  u8    0
[14] crc16      2 B        CRC16 přes [0..13]
```

Proč 16 B: je to mocnina dvojky, takže záznam nikdy nepřesahuje 512 B sektor a
adresace slotu je posun, ne násobení — nejpřívětivější velikost pro MCU.
Bajt `reserved` je výplň, která to zajišťuje; je uvnitř CRC a vždy 0 — volné
místo pro budoucí pole, dnes bez významu.

### 5.1 Stavy záznamu (žádné pole flags)

Slot se interpretuje čistě z jeho bajtů:

| Stav | Bajty | Pozná se |
|---|---|---|
| **Volný** | samé `0xFF` | čerstvé / smazané médium |
| **Živý** | data + sedící CRC | `crc16(tělo) == uložené CRC` |
| **Zahozený** | samé `0x00` | CRC nesedí (vynulované tělo se **nehashuje** na `0x0000`) |

Zahození záznamu = **přepiš 16 B nulami**. Jeho CRC pak nesedí, takže ho každá
čtečka přeskočí. Tím odpadá starý trik „přepni jeden bajt flags mimo CRC" a celý
záznam je chráněný kontrolním součtem.

### 5.2 `station` je index, ne číslo

`station` je **1bajtový index** (1..255; 0 = žádná) do tabulky STATION v prefixu
(§3.2). Skutečná čísla stanice/regionu — která si lidé a nástroje přidělují jak
chtějí, s mezerami — žijí v té tabulce; kontejner index nikdy neinterpretuje.
Překlad index ↔ skutečné číslo je práce hostové glue vrstvy.

> Žádné kontrolní body. Velikost souboru je pevná a log má pevný krok, takže
> `mount()` najde hranici binárním hledáním a z nejnovějšího platného záznamu
> přečte `offset + length` → obnoví `top_ptr`. Není co ukládat, takže žádný
> kontrolní záznam neexistuje.

---

## 6. Zarovnání clusteru / sektoru

**Zápis je vždy bajtově přesný** — data jdou přesně tam, kam patří.
Žádný bufferovaný ani cluster-aligned režim:

- Na **ATmega328 (2 KB RAM)** se buffer celého clusteru (4–32 KB) fyzicky nevejde.
- Při kadenci vzorků (sekundy až minuty, reálně i à 15 min) je write-amplifikace
  na SD/flash **bezvýznamná**.

Velikost clusteru `cluster_shift` v prefixu je jen **informativní metadata**,
ne mód zápisu.

### 6.1 Přístup k médiu a souborové systémy

**Kontejner nikdy nemluví s FAT/souborovým systémem přímo.** HAL dělá jen
*„skoč na bajtový offset, přečti/zapiš N bajtů"* v jednom otevřeném souboru;
samotný FS leží **pod HALem**:

```
PC:   kód → MlaPosixHAL → open(path,"r+b")/seek/read/write
                        → OS (Linux/Windows) → FAT16/FAT32/exFAT/NTFS/ext4 → SD
MCU:  kód → mla_hal_t  → SdFat (file.seek/read/write)
                        → SdFat: FAT16/FAT32/exFAT → SPI → SD
```

Důsledky:

- **FAT16 i FAT32 (i exFAT/NTFS/ext4)** fungují **bez práce navíc** — nezávisíme
  na vnitřnostech FS. Malá karta (do 2 GB) klidně na **FAT16**; 1MB kontejner se
  vejde levou zadní. Není nutné mít všude FAT32.
- **Linux i Windows** — `open()` je multiplatformní; klíčový je **binární režim
  `"rb"/"r+b"`** (Windows pak nepřekládá konce řádků → soubor se nepoškodí).
  Liší se jen cesta (`/media/.../MLA00000.MLA` vs `E:\MLA00000.MLA`).
- Velikost clusteru (FAT16 vs FAT32) **neovlivní** čtení/zápis — adresujeme
  bajtově, přepočet bajt→cluster dělá OS / SdFat.

---

## 7. Crash-safety

Protokol — **LOCK první, DATA druhé**:

1. **Torn lock write** (přerušení během zápisu LOG záznamu) → ten slot má špatný
   CRC → při mountu se přeskočí. Binární hledání hranice pokračuje.
2. **Torn data write** (LOG OK, ale datový blok se nedopsal) → chybí `MAGIC` →
   při mountu se zámek **vynuluje** (celých 16 B přepsáno `0x00`, takže jeho CRC
   nesedí) a `top_ptr` se vrátí na `rec.offset`.
3. **Zahození** libovolného záznamu funguje stejně — přepiš ho nulami; CRC pak
   nesedí a čtečky ho přeskočí. Žádný bajt flags, nic mimo CRC.
4. `recover()`: hledá `MAGIC`, zkouší délky 1..65535 dokud `CRC16(payload)`
   nesedí; obnovené záznamy dostanou `rec_type = raw` (typ není v bloku).
5. Žádné kontrolní body: velikost souboru je pevná a log má pevný krok, takže
   binární hledání + přečtení nejnovějšího záznamu obnoví stav přímo. Sken se dá
   rozjet hrubým krokem (např. 256, u velkého souboru 2000) a při nárazu na
   `0xFF` couvnout o jeden — na disku není co opravovat.

---

## 8. Konfigurovatelné parametry (nastaví se při `format()`, uloží v prefixu)

| Parametr | Kde | Volby | Výchozí |
|---|---|---|---|
| `cluster_shift` | byte 5 | 8…15 (256 B … 32 KB) | 12 (4 KB) |
| `flags` (CRC) | byte 7, bity 0-1 | NONE / DATA / FULL | FULL |
| `container_kind` | byte 20 | single / rotace | single |
| `file_seq` | byte 21 | 0…65535 | 0 |
| `keyframe_intv` | byte 23 | 0…255 | 8 |
| `enc_caps` | byte 24 | bitmaska kódování | dle použití |
| `schema_table` | [34..) | z `tools/mla_schema.py` | prázdná |
| `station_table` | za schématem | z `tools/mla_schema.py` | prázdná |

`log_rec_size` je pevně **16** a `data_base` se dopočítá (= velikost prefixu,
což je 512 B, pokud se tabulky nevejdou do víc sektorů).

## 9. Mimo rozsah (žije v glue vrstvě, ne v MLA)

MLA je hloupý kontejner; následující záměrně **není** jeho práce:

- **Překlad čísel stanic** — log ukládá 1bajtový index; mapování na skutečné,
  klidně děravé číslo stanice je věc glue (přes tabulku STATION, kterou zapsala).
- **Komprese** — MLA ji jen nese a typuje (`rec_type`: raw / delta / keyframe;
  `kf_back` váže záznam k jeho keyframu). Kodek je samostatný.
- **Šifrování** — totéž: samostatná knihovna; MLA uloží jakékoli bajty dostane.
- **Transport (LoRa / Wi-Fi / síť)** — každý záznam je samostatný (typ + délka +
  CRC), takže „pošli záznam" = pošli jeho bajty. Volba transportu je na glue.
- **Rotace souborů** přes víc souborů — platformní glue nad souborovým systémem
  (`MlaArchive` v Pythonu); každý soubor je samostatně mountovatelný přes `file_seq`.

Tahle separace drží MLA dost malé pro ATmega (jen zápis, 16 B log, jeden 512 B
sektor prefixu), a přitom dovolí výkonnému hostu postavit nad ním libovolně
chytrý systém.

*★ Viva La Resistánce ★*
