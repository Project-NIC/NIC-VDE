# NIC-MLA — Návrhová specifikace formátu

> **Stav:** koncept — otevřené otázky vyřešeny · **Verze dokumentu:** 0.6 · **Datum:** 2026-05-30
> **MLA** = *Matroshka Logging Archive* — univerzální jednosouborový kontejner
> (data + log v jednom souboru, à la Matroska / tar / DriveSpace).
>
> Tento dokument definuje formát v1.0. **Stav implementace:** Python reference
> (`nic_mla.py`, `nic_mla_archive.py`) i C knihovny (`c/` — write-only pro
> ATmega + kompletní pro ARM/PC) jsou hotové a bajtově shodné (ověřeno
> cross-compat testem C↔Python). Otevřené body k rozhodnutí jsou v sekci 9.

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
filtrování bylo rychlé (čas, stanice, kanál, typ).

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
offset 0                                                              EOF
┌────────┬───────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX │ INDEX     │ DATA  proud  →    │   volné  0xFF  │   ← LOG proud │
│ 512 B  │ (volit.)  │ (roste nahoru)    │               │ (roste dolů)  │
└────────┴───────────┴──────────────────┴───────────────┴──────────────┘
         512         ▲ data_base         ▲ top_ptr   bot_ptr ▲  region_end
```

- **DATA** rostou nahoru od `data_base` (`top_ptr` = kam přijde příští blok).
- **LOG** roste dolů od EOF (`bot_ptr`; příští záznam jde
  na `bot_ptr − log_rec_size`).
- Mezi nimi je volné místo vyplněné `0xFF`.
- **INDEX** (volitelný, §5.2) je pevný region mezi prefixem a daty,
  `[512, data_base)`. Při `index_kb=0` (výchozí) je prázdný a `data_base=512` —
  formát je pak **bajtově shodný** s variantou bez indexu.

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

## 3. Prefix (512 B)

Prefix zůstává **přesně 512 B** a končí CRC16 přes bajty `[0..509]`. Verze se
zvedá na **2**. Všechna nová pole se vejdou do dnešního nulového paddingu, takže
schéma serializace i CRC zůstává stejné.

```
[0]   magic[4]        b"MLA\0"                                      (beze změny)
[4]   version         1 B   = 1
[5]   cluster_shift   1 B   8=256B · 10=1KB · 11=2KB · 12=4KB · 13=8KB
                            · 14=16KB · 15=32KB
[6]   log_rec_size    1 B   24 (výchozí) nebo 32 (víc stanic / delší popis) — §10.1
[7]   flags           1 B   viz níže
[8]   file_size       4 B   uint32 LE                              (beze změny)
[12]  phys_addr       8 B   uint64 LE  (báze na médiu; 0 pro FAT/POSIX)
── nová pole (dříve padding) ──
[20]  container_kind  1 B   0=single · 1=rotace · 2=kruhový
[21]  file_seq        2 B   uint16 LE  pořadí souboru v rotaci
[23]  keyframe_intv   1 B   interval keyframe u komprese (výchozí 8; 0 = N/A)
[24]  enc_caps        1 B   bitmaska kódování, která tento soubor smí nést
[25]  data_base       4 B   uint32 LE  = 512 + index_kb·1024 (začátek DATA; §5.2)
[29]  region_end      4 B   uint32 LE  = file_size  (konec LOG proudu)
[33]  checkpoint_shift 1 B  interval kontrolního bodu = 2^hodnota záznamů
                            (0 = vypnuto); výchozí 8 → 256 — viz §5.1 / §10.2
[34]  padding          0x00 … až byte 509
[510] crc16           2 B   LE  — přes bajty 0..509
```

### `flags` (1 B)

| Bity | Význam |
|---|---|
| 0–1 | režim integrity CRC: `0=NONE` · `1=DATA` · `2=FULL` |
| 2–7 | rezerva (0) |

> **Pozn.:** bufferovaný / cluster-aligned režim zápisu byl z návrhu **vypuštěn**.
> Důvod: na ATmega328 (2 KB RAM) se buffer celého clusteru (4–32 KB) fyzicky
> nevejde a při kadenci vzorků (sekundy až minuty) je write-amplifikace
> bezvýznamná. Zápis je tedy vždy **bajtově přesný** (viz §6).

### `enc_caps` (1 B) — deklarace, jaká kódování payloadu se v souboru vyskytují

bit 0 = raw · bit 1 = delta · bit 2 = keyframe · bit 3 = text/JSON · 4–7 rezerva.
Host podle ní pozná, zda potřebuje dekompresor, ještě než začne číst data.

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

## 5. LOG záznam: 16 B → 24 B (pevná délka)

```
[0]  timestamp  4 B  u32   Unix sekundy (dodá volající: RTC/GPS)
[4]  offset     4 B  u32   logický offset datového bloku
[8]  station    2 B  u16
[10] region    2 B  u16
[12] seq        2 B  u16   monotónní pořadí v souboru — hostové hledání
                           + seskupení keyframe u komprese
[14] rec_type   1 B        typ dat — rychlý filtr na hostu (viz výčet níže)
[15] length     2 B  u16   délka payloadu 1..65535
[17] kf_back    2 B  u16   vzdálenost (v seq) zpět k vlastnímu keyframe
                           (0 = tento záznam JE keyframe)
[19] reserved   1 B        0x00, rezerva pro budoucí pole hledání (UVNITŘ CRC)
[20] flags      1 B        MIMO CRC: 0xFF=LIVE / 0x00=ABANDONED
[21] pad        1 B        0xFF, mimo CRC
[22] crc16      2 B        přes bajty [0..19]
```

- **`rec_type` (1 B) — výčet typů dat** (zde výchozí návrh, finální doladí majitel):
  - dolní nibble = kódování: `0`=raw · `1`=delta · `2`=keyframe ·
    `3`=text/JSON · (4–15 rezerva)
  - horní nibble = třída: `0`=měření · `1`=událost · `2`=konfigurace ·
    `0xF`=kontrolní bod (§5.1) · (ostatní rezerva)
- **Log má vlastní CRC16** (bajty [22..23], přes [0..19]) — každý log záznam je
  tedy chráněný kontrolním součtem stejně jako data. To je základ obnovy v §7.
- **Pole pro hostové hledání:** `timestamp`, `station`, `region`, `rec_type`,
  `seq` umožní rychlé filtrování v RAM (časový rozsah, stanice, kanál, typ) bez
  jakéhokoli stromu na disku — přesně dle principu z §1.
- **`length` je 2 B** (max 65535 B) — pokryje záznamy nad 255 B (např. ~280 B).
- **Trik flags-mimo-CRC zachován:** opuštění záznamu je jediný zápis `0xFF→0x00`,
  platný i na NOR bez erase, a CRC zůstane nedotčený.
- **24 B** je zarovnané na slovo a beze zbytku dělí všechny velikosti clusteru
  (512 B – 32 KB). Velikost je **konfigurovatelná** přes `log_rec_size` v prefixu
  — pro víc stanic / delší popis lze zvolit **32 B** (8 B navíc). Viz §10.1.

### 5.1 Kontrolní bod (checkpoint / registr)

Aby se po výpadku nebo při mountu nemusel skenovat celý log, zapisuje se každých
`2^checkpoint_shift` záznamů (výchozí shift **8 → 256**, prefix byte [33];
0 = vypnuto) **kontrolní bod**. Je to obyčejný LOG záznam s `rec_type` třídou
`0xF`, který místo odkazu na data nese **aktuální stav zaplnění**. Využívá
stejných 24 B polí:

```
timestamp = čas zápisu kontrolního bodu
offset    = top_ptr   (konec DATA proudu)
station   = horní 2 B počtu živých záznamů
region   = dolní 2 B počtu živých záznamů
seq       = seq v okamžiku zápisu
rec_type  = 0xF0  (třída „kontrolní bod", kódování raw)
length    = 0     (kontrolní bod nemá datový blok)
```

**K šířce adresy (tvůj postřeh):** pro 1MB soubor je bajtová adresa 20 bitů
(3 B), po sektorech/clusterech 2 B. My adresu řešit zvlášť nemusíme — pole
`offset` má **4 B**, takže ukazuje bajtově přesně až do 4 GB souboru. Kontrolní
bod tedy nese rovnou byte-přesný `top_ptr`, plus **čas** jako index (`timestamp`)
a počet záznamů — přesně ty složky, cos vyjmenoval, jen úhledně zabalené do
existujícího 24 B záznamu (vlastní oddělovací hlavička = `rec_type` 0xF0 + CRC).

**Použití při mountu:** najdi nejnovější kontrolní bod s platným CRC → odtud znáš
`top_ptr`, `bot_ptr` (= jeho vlastní adresa) i počet a stačí dohledat jen
posledních ≤ jeden interval záznamů. Pokud chybí (starý soubor, vypnuto),
použije se původní binární hledání z §7 — kontrolní bod je **čistě urychlení,
ne podmínka**.

Místa zabere zanedbatelně: 24 B na 256 záznamů ≈ 0,09 B na záznam. Interval je
dvousečný (hustší = větší rejstřík + víc zápisů pro ATmega; řidší = víc skenování
na hostu) → je **konfigurovatelný**, výchozí řidší. Viz §10.2.

### 5.2 Index region — hostová časová/stanicová skip-table (volitelné)

Kontrolní bod (§5.1) urychluje **mount/obnovu**. Tahle sekce řeší druhou polovinu:
**rychlé vyhledávání podle času a stanice**. Je to **volitelná** nadstavba pro
výkonnější platformy (STM32/ESP/PC) — na ATmega se nepoužívá (ta jen zapisuje).

**Kde leží:** pevný region mezi prefixem a daty, `[512, data_base)`. Velikost se
volí při `format()` parametrem `index_kb` (KB); `0` = vypnuto a `data_base = 512`
(formát bajtově shodný s variantou bez indexu). Posun `data_base` je sebepopisný
přes prefix, takže i write-only knihovna (ATmega) takový soubor správně namountuje
— jen do indexu nikdy nezapisuje.

**Co obsahuje:** ploché, append-only pole **12 B kotev** (LE):

```
[0]  timestamp 4 B  uint32  čas měření v daném záznamu (kotva)
[4]  slot      4 B  uint32  index LOG slotu, na který se má skočit
[8]  station   2 B  uint16  stanice v daném záznamu (nápověda k filtru)
[10] status    1 B  uint8   0xFF=nepoužito · 0xA5=platná · 0x00=zneplatněná
[11] reserved  1 B  uint8   0xFF (budoucí: region / flags)
```

Jedna kotva se zapíše při každém kontrolním bodu (tj. à `2^checkpoint_shift`
záznamů). `status` je **mimo jakýkoli CRC**, aby šel na NORu přepínat
(`0xFF→0xA5` zápis, `0xA5→0x00` zneplatnění — obojí jen 1→0). Na SD/FAT se o
přepis dotčeného sektoru postará vrstva pod HALem (read-modify-write v ovladači
FS + řadič karty), náš kód žádné RMW neřeší.

**Vyhledávání (na hostu):** přečti celý (malý) index do RAM → najdi nejnovější
kotvu s `timestamp < time_from` → skoč na její `slot` → odtud čti LOG dopředu a
filtruj (čas, stanice, …). Pro víc stanic: časová kotva zúží okno na jeden
„bucket“ (≤ jeden interval záznamů) a stanice se **dofiltruje za běhu** —
stanice se proto v kotvě drží jen jako nápověda, ne jako klíč.

**Bezpečnost a meze:**
- Je to **čistě urychlení**. Když index chybí / je plný / nese poškozenou kotvu,
  host se vrátí k plnému skenu (výsledek je identický, jen pomalejší).
- Kotvy nemají vlastní CRC (kvůli 12 B a NOR-flipu). Případnou torn poslední
  kotvu host pozná tak, že `slot` ukazuje za platný konec logu → takovou kotvu
  ignoruje (příliš nízký start jen zpomalí, příliš vysoký by minul data).
- Po `recover()` se kotvy zahodí (ukazují na stará čísla slotů) → host skenuje.

**Velikost vs. pokrytí:** 12 B/kotva, jedna na `2^checkpoint_shift` záznamů.
Např. 4 KB region ≈ 340 kotev → při intervalu 256 pokryje ~87 000 záznamů.
Region je dvousečný stejně jako interval (víc kotev = větší rejstřík, méně = větší
buckety) → necháno na uživateli, typicky 2–4 KB. Viz §10.5.

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

1. **Torn lock write** (přerušení během zápisu LOG záznamu) → nejnovější slot má
   špatný CRC → při mountu se přeskočí. Binární hledání hranice v `mount()`
   funguje dál (jen krokuje po 24 místo 16).
2. **Torn data write** (LOG OK, ale datový blok se nedopsal) → chybí `MAGIC` →
   slot se opustí (`flags 0xFF→0x00`) a `top_ptr` se vrátí na `rec.offset`.
   To je klíčová obnovovací cesta; bajt flags je na offsetu 20.
3. `recover()`: hledá `MAGIC`, pak zkouší délky 1..65535 dokud `CRC16(payload)`
   nesedí. Typ dat se z bloku nedá zjistit (je v logu), takže obnovené záznamy
   dostanou `rec_type = raw`. Větší rozsah délek dělá nouzový sken pomalejší, ale
   běží jen při poškození celého logu.
4. **Kontrolní bod (§5.1)** dává mountu i obnově rychlý záchytný bod — od něj
   stačí dohledat posledních ≤ jeden interval záznamů místo celého souboru.

---

## 8. Mapa znovupoužití kódu (pro navazující refaktor)

**Beze změny**
- `crc16` (+ self-test `crc16(b"123456789") == 0x29B1`)
- `MlaHAL` (ABC se 4 funkcemi), `MlaPosixHAL`, `MlaNorSimHAL` — kontrakt HAL stejný
- tvar commit protokolu, strategie binárního hledání v `mount`, řízení
  `__iter__` / `read_record`, aritmetika volného místa / „plno“

**Úprava**
- `MlaPrefix` — nová pole, `version=1`, `index_rec_size` → `log_rec_size=24`;
  serializace i CRC schéma identické
- `MlaIndex` (→ `MlaLog`) — layout 24 B; `length` na 2 B; nová pole `seq`,
  `rec_type`, `kf_back`; `flags` na bajt 20; CRC přes `[0..19]`
- `MlaCore.append` — naplnění `seq`/`rec_type`/`kf_back`, upravené velikosti záznamu
- `MlaCore.mount` — velikost záznamu 24, parsování nových polí, offset opuštění 20
- `MlaCore._build_block` / `_read_data` — `length` 2 B; CRC přes payload
  (datový blok zůstává `MAGIC + data + CRC`, bez TYPE bajtu)
- `MlaCore.recover` — sken s délkou 1..65535, `rec_type = raw`

**Nové** (✓ = hotovo v Python referenci)
- ✓ konstanty `rec_type` + malý registr typů dat
- ✓ **kontrolní bod (§5.1)** — zápis každých `2^checkpoint_shift` záznamů + mount
- ✓ **správce rotace souborů** `MlaArchive` (`nic_mla_archive.py`) — další
  `NICnnnn.MLA` při zaplnění, sebepopisné přes `file_seq`
- ✓ **hostový helper** `query()` (`nic_mla_archive.py`) — ploché filtrování
  (čas / stanice / kanál / typ), jen na PC; čip zůstává štíhlý
- ✓ **index region (§5.2)** — volitelná hostová skip-table (`index_kb`);
  `MlaCore.scan()` + `read_index()` v Pythonu, `mla_scan()` v C; write-only
  cesta ji neplní, jen respektuje `data_base`
- kruhový buffer (NOR/experiment) — **odloženo na později**
- ~~bufferovaný / cluster-aligned režim~~ — **vypuštěno** (ATmega 2 KB RAM, §6)

**Testy** (`nic_mla_test.py`) — aktualizovat torn-write, full, recovery a
round-trip na 24 B záznam a přítomnost TYPE bajtu.

---

## 9. Rozhodnutí (uzavřeno s majitelem)

| # | Otázka | Rozhodnutí |
|---|---|---|
| 1 | Šířka `timestamp` | **4 B u32, Unix sekundy** — pro meteostanici dostačuje |
| 2 | Délka záznamu `length` | **2 B (max 65535 B)** — pokryje záznamy nad 255 B (~280 B) |
| 3 | Šířka `seq` | **2 B** — na 1MB soubor stačí; minimální záznam má desítky B |
| 4 | Registr typů dat | majitel doladí; **základní výčet navrhne tento dokument** (§4) |
| 5 | Kontrolní bod / registr | **ano** — speciální log záznam, **konfigurovatelný interval** (§5.1) |
| 6 | Rotace souborů | **sebepopisná** přes `file_seq`, bez zvláštního manifestu |
| 7 | Kruhový buffer (přepis od konce) | **odloženo na později** |
| 8 | Zarovnání clusteru | **bajtově přesné** (jednoduchá varianta) jako výchozí |
| 9 | Typ dat v datovém bloku | **zrušen** — typ je jen v logu (`rec_type`), blok je `MAGIC+data+CRC` |
| 10 | CRC u logu | **ano, už je** — každý log záznam má vlastní CRC16 (§5) |
| 11 | Umístění hlavičky | **prefix na offsetu 0, u logu — NE u dat** (jinak skákání mezi logem a daty při hledání) |
| 12 | Velikost log záznamu | **konfigurovatelná přes `log_rec_size`** — 24 B nebo 32 B per-soubor (§5.2) |

---

## 10. Konfigurovatelné parametry („praxe ukáže")

> Tři věci, u kterých je rozhodnutí dvousečné, a tak je **nezadrátujeme natvrdo**
> — stanou se z nich parametry v prefixu. Každé nasazení si zvolí svoje a praxe
> rozhodne, aniž by se měnil formát.

### 10.1 Velikost log záznamu — 24 B vs 32 B (`log_rec_size`)

Pole `log_rec_size` v prefixu (byte [6]) určuje velikost log záznamu pro daný
soubor. Podporujeme dvě:

- **24 B (výchozí)** — kompaktní, ideál pro jednu stanici / meteostanici.
- **32 B** — 8 B navíc na **popis stanice** (širší identifikace, druhý časový
  index, příznaky). Hodí se, až se formát použije jako **datalogger pro víc
  stanic** stejného i různého typu — víc místa na popis záznamu.

Je to dvousečné (víc místa vs. víc režie), proto je to volba, ne dogma.

**Hostová hlavička až 256 znaků (klíčový princip):** na disku držíme co nejmíň —
Unixový čas jsou jen 4 B, i když jako text „2026-05-30 08:14:00" je to ~19 znaků.
Kompaktní binární log (≤ 24/32 B) se **až na PC rozbalí** do **čitelné hlavičky
záznamu** (až ~256 znaků volného popisu, jako název souboru). Tato bohatá
hlavička je výhradně hostová (export/zobrazení) — na čipu zůstává jen kompaktní
binární log. Per-stanici popis lze uložit do konfiguračního záznamu
(`rec_type` třída „konfigurace") nebo do 8 B navíc u 32 B varianty.

### 10.2 Interval kontrolního bodu (`checkpoint_shift`)

Dvousečné: **hustší** rejstřík (malý interval) = větší rejstřík + víc zápisů
(zlé pro ATmega); **řidší** (velký interval) = víc skenování v souboru (ale to
dělá výkonný procesor, ne ATmega). Protože **ATmega hlavně zapisuje** a hledání
běží na hostu (a používá se vzácně):

- **Default: řidší** (shift 8 → **256**) — menší režie zápisu pro ATmega.
- Uloženo jako **1 bajt** = mocnina dvojky (`2^checkpoint_shift`), stejný idiom
  jako `cluster_shift`; 0 = vypnuto. (Dřív zbytečně 2 B — díky postřehu majitele.)

### 10.3 (Vyřešeno) Umístění hlavičky

Hlavička/prefix zůstává **na offsetu 0, na straně logu — ne u dat**. Kdyby byla
u dat, musel by se při hledání skákat mezi logem a daty. (Viz §9, řádek 11.)

### 10.4 Souhrn — co se dá měnit (vše v prefixu, nastaví se při `format()`)

| Parametr | Kde | Volby | Default |
|---|---|---|---|
| `cluster_shift` | byte [5] | 8…15 (256 B … 32 KB) | 12 (4 KB) |
| `log_rec_size` | byte [6] | 24 / 32 B | 24 |
| `flags` (CRC) | byte [7] b0–1 | NONE / DATA / FULL | FULL |
| `flags` (zarovnání) | byte [7] b2–3 | ALIGN_DATA / BUFFERED | vyp. |
| `container_kind` | byte [20] | single / rotace / kruhový | single |
| `keyframe_intv` | byte [23] | 0…255 | 8 |
| `enc_caps` | byte [24] | bitmaska kódování | dle použití |
| `checkpoint_shift` | byte [33] | 0 (vyp.) / 1…N (2^N) | 8 (→256) |
| `data_base` | byte [25] | 512 + `index_kb`·1024 | 512 (index vyp.) |

`data_base` se nenastavuje přímo — odvodí se z `index_kb` (§5.2) předaného do
`format()`. `index_kb=0` → `data_base=512` → žádný index region.

### 10.5 Velikost index regionu (`index_kb`)

Volitelná hostová skip-table z §5.2. Jako u `checkpoint_shift` je to kompromis:

- **Default: vypnuto** (`index_kb=0`) — write-only/ATmega ji nepotřebuje a formát
  zůstává bajtově shodný s variantou bez indexu.
- **Datalogger (STM32/ESP/PC): typicky 2–4 KB.** 12 B/kotva, jedna na
  `2^checkpoint_shift` záznamů; 4 KB ≈ 340 kotev → ~87 000 záznamů při intervalu 256.
- Region se vyhradí jednorázově při `format()` (posune `data_base`); za běhu se do
  něj jen připisují kotvy, na SD/FAT to řeší RMW vrstva pod HALem.

### 10.6 Kandidáti na úsporu bajtů (k revizi v kroku 2)

> Praxe ukáže; postupně se dá ještě hodně oříznout.

- **`seq` na 1 bajt** — pokud je `seq` relativní k poslednímu kontrolnímu bodu
  (okno ≤ 256 záznamů), stačí 0…255 = 1 bajt místo 2. Pozor: dotýká se vazby
  keyframe komprese (`kf_back`), proto rozhodnout až při implementaci kroku 2.
- **`station` / `region`** — pokud meteostanice používá jen pár kanálů, lze
  zvážit 1 bajt místo 2 (zatím ponecháno 2 B kvůli dataloggeru s víc stanicemi).
- Obecně: každé pole projít a zúžit na skutečně potřebnou šířku.

---

*★ Viva La Resistánce ★*
