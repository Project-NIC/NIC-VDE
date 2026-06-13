<p align="center">
  <img src="NICDMD.svg" width="200"/>
</p>

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

★ N.I.C. ★

# NIC DMD — Delta Markov Duda

## Kompresní protokol pro embedded zařízení

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

## Co je DMD?

DMD je multiplatformní kompresní protokol pro malé pakety dat z meteostanic, elektroměrů, GPS trackerů a dalších embedded zařízení. Je navržen pro přenos přes technologie s omezenou šířkou pásma, jako je LoRa.

Protokol je plně funkční na kontroléru ATmega328 a nevyžaduje žádné velké slovníky ani vyhledávací tabulky v paměti. Každý paket je komprimován nezávisle — adaptivním výběrem nejlepší metody z pěti kandidátů.

---

## Proč DMD?

Existující kompresní knihovny pro embedded zařízení buď vyžadují stovky bajtů RAM navíc (Heatshrink), nebo potřebují přenášet Huffmanovu tabulku spolu s daty. DMD volí jinou cestu — kombinuje několik jednoduchých metod s heuristickou analýzou a vybírá nejlepší výsledek pro každý paket zvlášť.

**Hlavní výhody:**
- Pevná Huffmanova tabulka pouze v ROM (64B), žádná RAM navíc
- Adaptivní výběr metody pro každý paket — až 5 kandidátů
- Plně deterministická dekomprese — žádné ztráty dat
- Maximální expanze dat o 1 bajt (záhlaví) v nejhorším případě
- Implementace v Pythonu i C (ATmega328 / Arduino)

---

## Kdy se DMD nevyplatí

DMD je navržen pro data která se v čase mění pomalu a předvídatelně — senzorové hodnoty, GPS souřadnice, průmyslová telemetrie. Pokud jsou vstupní data náhodná, šifrovaná nebo již komprimovaná, DMD přidá pouze 1 bajt záhlaví a odešle je jako RAW. To je správné chování — žádná ztrátová komprese, žádné zhoršení.

---

## Kompatibilita

**Python:** 3.10 nebo novější (používá typové anotace `bytes | None`).

**C:** C99 nebo novější. Testováno s GCC na PC (Linux/Windows) a AVR-GCC pro ATmega328. Bez závislostí na standardní knihovně kromě `<string.h>`. Interní buffery jsou dimenzovány pomocí C99 VLA podle skutečné délky paketu.

**Arduino:** Zkopíruj `c/nic_dmd.c` a `c/nic_dmd.h` do složky projektu. Kompatibilní s Arduino IDE 1.8+ a 2.x (AVR-GCC podporuje C99 VLA).

**Poznámka pro jiné překladače:** IAR, Keil a MSVC C++ VLA nepodporují. Pro tyto toolchainy lze při kompilaci definovat `-DDMD_PKT_MAX_BUILD=N` (např. 32 nebo 64) a buffery budou fixní.

**Závislosti pro fetch/benchmark:** `pip install requests`

**Délka paketu:** Minimální technické omezení je 1B, ale pod 16B se komprese prakticky nevyplatí — overhead záhlaví (1B) a ANS state (2B) sežere většinu případné úspory. Doporučené minimum je **16B**. Maximum je **255B**. Pro LoRa přenos je praktický limit payload 51–64B podle spreading factoru a regionu. Nejlepších výsledků dosahuje DMD na datech kde se sousední pakety mění pomalu — typicky 16–64B senzorová telemetrie.

---

## Validace a integrita dat

V zájmu dosažení maximálního výkonu a absolutní minimalizace zátěže procesoru knihovna neprovádí žádné dodatečné kontroly záhlaví ani validaci délky předaných dat.

Návrh protokolu striktně předpokládá, že kontrolu integrity (např. hardwarové CRC) a zahození poškozených či prázdných paketů řeší nižší transportní vrstva nebo hlavní program (typicky samotný rádiový modul, logika sběru dat apod.). Uživatel knihovny musí na aplikační úrovni zajistit, že do kompresních a dekompresních funkcí vstupují pouze strukturálně korektní data. Tímto delegováním odpovědnosti bylo dosaženo nízké paměťové režie bez plýtvání hodinovými cykly procesoru.

---

## Výsledky

Testováno na více než 50 000 vzorcích z 20 reálných a syntetických zdrojů dat (meteostanice, GPS, elektroměry, průmyslové senzory, seizmika, kvalita ovzduší). Chyby round-trip: **0 ve všech datasetech**.

Sloupec **výstup B/pkt** je průměrná skutečná velikost odesílaného paketu po kompresi (včetně 1B záhlaví). Právě tento údaj je rozhodující pro dimenzování přenosového okna LoRa.

### Tabulka 1 — uniformní int16 (fetch_plus.py)

Všechna pole uložena jako `int16` s ×100 škálováním, pakety doplněny na pevnou délku nulami. Forecast datasety mají 384 vzorků (16 dní × 24 hodin), ostatní 8 000–10 000 vzorků.

```
===================================================================================
  Dataset                    | Pkty  | Vstup | Výstup  | Úspora | Dominantní metoda
------------------------------|-------|-------|---------|--------|------------------
NOAA San Francisco (přílivy)  |  8184 |  16 B |   6.4 B |  62.2% | DELTA1+ZZ+FLAG
NOAA New York (přílivy)       |  8184 |  16 B |   6.7 B |  60.6% | DELTA1+ZZ+FLAG
DWD Fichtelberg (meteo)       | 10000 |  16 B |   8.1 B |  52.6% | DELTA1+ZZ+FLAG 75%
DWD Helgoland (meteo)         | 10000 |  16 B |   8.5 B |  49.8% | DELTA1+ZZ+FLAG 74%
DWD Zugspitze (meteo)         | 10000 |  16 B |   8.6 B |  49.2% | DELTA1+ZZ+FLAG 73%
GPS Trek                      | 10000 |  16 B |   8.6 B |  49.3% | DELTA1+ZZ+FLAG 53%
Komplexní stanice             | 10000 |  64 B |  38.6 B |  40.7% | DELTA1+ZZ+HUF  84%
AirQuality Brno               |   168 |  16 B |  10.3 B |  39.7% | FLAG + D1+ZZ+FLAG
AirQuality Ostrava            |   168 |  16 B |  10.5 B |  38.4% | FLAG + D1+ZZ+FLAG
Elektroměry                   | 10000 |  16 B |  10.6 B |  37.7% | DELTA1+ZZ+HUF  52%
AirQuality Praha              |   168 |  16 B |  10.6 B |  37.6% | FLAG + D1+ZZ+FLAG
Forecast Praha (32B)          |   384 |  32 B |  22.0 B |  33.2% | DELTA1+ZZ+FLAG 58%
Forecast Brno (32B)           |   384 |  32 B |  22.4 B |  32.1% | DELTA1+ZZ+FLAG 55%
IoT budova                    | 10000 |  16 B |  11.7 B |  31.3% | DELTA1+ZZ+HUF  84%
Průmyslový senzor             | 10000 | 128 B |  89.3 B |  30.7% | DELTA1+ZZ+HUF  80%
Forecast Ostrava (16B)        |   384 |  16 B |  12.3 B |  27.3% | DELTA1+ZZ+FLAG 42%
Forecast Praha (16B)          |   384 |  16 B |  12.4 B |  26.8% | DELTA1+ZZ+FLAG 41%
Forecast Brno (16B)           |   384 |  16 B |  12.5 B |  26.5% | DELTA1+ZZ+FLAG 43%
Forecast Bratislava (16B)     |   384 |  16 B |  12.7 B |  25.3% | DELTA1+ZZ+FLAG 38%
USGS seizmika                 | 10000 |  16 B |  13.9 B |  18.2% | FLAG 29% (chaot.)
===================================================================================
  Rozsah: 18 % – 62 %   |   Chyby: 0
===================================================================================
```

### Tabulka 2 — schema-aware tight packing (fetch_small.py)

Každé pole uloženo v nejmenším potřebném typu (uint8/int16) s ×10 škálováním, bez nulového paddingu.

```
===================================================================================
  Dataset                    | Pkty  | Vstup | Výstup  | Úspora | Dominantní metoda
------------------------------|-------|-------|---------|--------|------------------
Forecast Praha (27B)          |   384 |  27 B |  17.1 B |  39.0% | DELTA1+ZZ+HUF  63%
Forecast Brno (27B)           |   384 |  27 B |  17.2 B |  38.5% | DELTA1+ZZ+HUF  61%
AirQuality Brno (12B)         |   168 |  12 B |   8.3 B |  35.9% | DELTA1+ZZ+HUF  49%
AirQuality Ostrava (12B)      |   168 |  12 B |   8.5 B |  34.8% | DELTA1+ZZ+HUF  47%
AirQuality Praha (12B)        |   168 |  12 B |   8.6 B |  34.2% | DELTA1+ZZ+HUF  44%
Forecast Ostrava (13B)        |   384 |  13 B |   9.3 B |  33.6% | DELTA1+ZZ+HUF  67%
Forecast Brno (13B)           |   384 |  13 B |   9.3 B |  33.3% | DELTA1+ZZ+HUF  69%
Forecast Praha (13B)          |   384 |  13 B |   9.3 B |  33.3% | DELTA1+ZZ+HUF  70%
Forecast Bratislava (13B)     |   384 |  13 B |   9.4 B |  32.5% | DELTA1+ZZ+HUF  72%
DWD Fichtelberg (9B)          | 10000 |   9 B |   6.3 B |  37.0% | D1+ZZ+ANS  49%
DWD Helgoland (9B)            | 10000 |   9 B |   6.4 B |  36.0% | D1+ZZ+ANS  42%
DWD Zugspitze (9B)            | 10000 |   9 B |   6.4 B |  35.6% | D1+ZZ+ANS  42%
USGS seizmika (8B)            | 10000 |   8 B |   8.6 B |   3.9% | RAW 79% ⚠ expanze
NOAA New York (3B)            |  8184 |   3 B |   4.0 B |   0.0% | RAW 100% ⚠ expanze
NOAA San Francisco (3B)       |  8184 |   3 B |   4.0 B |   0.0% | RAW 100% ⚠ expanze
===================================================================================
  Rozsah: 0 % – 39 %   |   Chyby: 0
  ⚠ U paketů < 8B výstup větší než vstup — header overhead (1B) převáží úsporu.
===================================================================================
```

### Tabulka 3 — surový text JSON/CSV (fetch_raw_text.py)

Data přesně jak přicházejí ze zdrojů — bez binárního packingu, text jako bajty, doplněno nulami na délku prvního záznamu.

```
===================================================================================
  Dataset                    | Pkty  | Vstup  | Výstup  | Úspora | Dom. metoda
------------------------------|-------|--------|---------|--------|---------------
DWD Helgoland (raw CSV)       | 10000 |  72 B  |  21.2 B |  71.0% | D1+ZZ+ANS 69%
DWD Zugspitze (raw CSV)       | 10000 |  72 B  |  21.3 B |  70.9% | D1+ZZ+ANS 68%
DWD Fichtelberg (raw CSV)     | 10000 |  72 B  |  21.4 B |  70.7% | D1+ZZ+ANS 67%
NOAA San Francisco (raw JSON) |  8448 |  72 B  |  26.7 B |  63.4% | D1+ZZ+FLAG 38%
NOAA New York (raw JSON)      |  8448 |  72 B  |  27.3 B |  62.6% | D1+ZZ+FLAG 37%
Forecast Bratislava (raw JSON)|   384 | 200 B  |  73.2 B |  63.6% | D1+ZZ+ANS  40%
Forecast Ostrava (raw JSON)   |   384 | 200 B  |  76.1 B |  62.2% | D1+ZZ+ANS  40%
Forecast Praha (raw JSON)     |   384 | 200 B  |  76.7 B |  61.9% | D1+ZZ+ANS  41%
Forecast Brno (raw JSON)      |   384 | 200 B  |  77.1 B |  61.6% | D1+ZZ+ANS  37%
USGS seizmika (raw CSV)       | 10000 | 216 B  |  96.5 B |  55.5% | D1+ZZ+FLAG 34%
===================================================================================
  Rozsah: 56 % – 71 %   |   Chyby: 0
===================================================================================
```

---

## Jak formát kódování ovlivňuje kompresi

### Škálování ×10 vs ×100 a vliv nulového paddingu

Při násobení ×100 s uniformním int16 packingem (Tabulka 1) dosahuje DMD 49–53 % úspory u DWD dat. Při těsném schema-aware packingu s ×10 škálováním (Tabulka 2) je výsledek jen 35–37 %. Paradox: hrubší škálování s větším paketem dává lepší kompresi. Důvod je strukturální — v 16B paketu s ×100 škálováním bývá horní byte každého int16 po delta+ZigZag blízko nuly, takže FLAG metoda může celý byte reprezentovat jedním bitem v bitmapě. Těsný packing ×10 do uint8/uint16 tuto strukturu odstraní a přepne kompresi na HUF nebo ANS.

NOAA data jsou nejlepší příklad vlivu záměrných nulových polí: ve variantě 16B se 6 nulovými poli je výstup **6.4–6.7 B** (úspora 61–62 %). Ve variantě 3B bez paddingu je výstup **4.0 B** — to je ale horší než vstup (3B), protože povinný 1B header převáží jakoukoli úsporu. Záměrné nulové pole tedy není plýtvání bajty — aktivně pomáhá kompresi.

### Absolutní výsledné velikosti — co skutečně odešleš

Přestože procento úspory vypadá u surového textu nejlépe, z hlediska skutečně odesílaných bajtů je binární packing jasně výhodnější:

```
  DWD data — srovnání absolutní výsledné velikosti:
  ┌─────────────────────────────────────────────────────┐
  │ Formát         │ Vstup │ Výstup │ Metoda            │
  │─────────────────────────────────────────────────────│
  │ 9B  schema-aw. │   9 B │  6.4 B │ D1+ZZ+ANS         │
  │ 16B uniform.   │  16 B │  8.4 B │ D1+ZZ+FLAG        │
  │ 72B raw CSV    │  72 B │ 21.3 B │ D1+ZZ+ANS         │
  └─────────────────────────────────────────────────────┘

  Forecast data — srovnání absolutní výsledné velikosti:
  ┌─────────────────────────────────────────────────────┐
  │ Formát         │ Vstup │ Výstup │ Metoda            │
  │─────────────────────────────────────────────────────│
  │ 13B schema-aw. │  13 B │  9.3 B │ D1+ZZ+HUF         │
  │ 16B uniform.   │  16 B │ 12.4 B │ D1+ZZ+FLAG        │
  │ 27B schema-aw. │  27 B │ 17.1 B │ D1+ZZ+HUF         │
  │ 32B uniform.   │  32 B │ 22.2 B │ D1+ZZ+FLAG        │
  │ 200B raw JSON  │ 200 B │ 76.7 B │ D1+ZZ+ANS         │
  └─────────────────────────────────────────────────────┘
```

Pro DWD meteorologická data vychází 16B uniformní int16 a 9B schema-aware na podobný výsledný paket (8.4B vs 6.4B — rozdíl jen 2B), ale 16B varianta nevyžaduje vlastní schema, snáze se rozšiřuje o další proměnné a lépe těží z nulového paddingu při FLAG kompresi.

Schema-aware packing dává smysl výhradně tam, kde každý bajt rozhoduje ještě před kompresí — typicky při přenosu bez DMD nebo na extrémně omezených linkách.

### Charakter dat a dominantní metoda

```
  Pomalé změny + nulový padding (NOAA, AQ 16B)  → FLAG
  Pomalé meteo změny (DWD, Forecast)             → DELTA1+ZZ+FLAG
  Syntetická data bez nul (IoT, průmysl)         → DELTA1+ZZ+HUF
  Surový text JSON/CSV                            → DELTA1+ZZ+ANS
  Náhodná data (USGS malé pakety)                → RAW (žádná úspora)
```

DELTA1 (1-bajtová delta) dominuje ve všech kategoriích — přes 70 % použití napříč datasety. DELTA2 a DELTA_FULL nastupují okrajově (do 10 %) pouze u dat s korelací přes bajtové hranice.

---

## Spotřeba RAM

Pracovní buffery při kompresi i dekompresi leží na zásobníku (stack) a existují jen po dobu volání funkce. Trvale v paměti zůstávají pouze struktury enkodéru a dekodéru. Hodnoty v tabulce platí pro build dimenzovaný přesně na délku paketu `N` (viz poznámka pod tabulkou):

```
================================================================================
  Délka paketu | Stack komprese | dmd_encoder_t | dmd_decoder_t | Celkem
---------------+----------------+---------------+---------------+---------------
       16B     |      62B       |      18B      |     17B       |      80B
       32B     |      96B       |      34B      |     33B       |     130B
       64B     |     164B       |      66B      |     65B       |     230B
      128B     |     300B       |     130B      |    129B       |     430B
      255B     |     569B       |     257B      |    256B       |     826B
================================================================================
```

Peak RAM při volání `dmd_compress` = Stack komprese + dmd_encoder_t.

**Jak to přeložit (důležité pro RAM):**

- **Default (bez přepínače):** pracovní buffery se dělají přes C99 VLA — za běhu se nafouknou přesně na délku zpracovávaného paketu. Trvalý buffer `previous[]` ve strukturách je ale napevno **255 B** (enkodér 257 B + dekodér 256 B), ať posíláš jakkoli krátké pakety. Tahle varianta je univerzální — jedna binárka zvládne libovolnou délku do 255 B — a hodí se na PC a pro testování.
- **S `-DDMD_PKT_MAX_BUILD=N`:** všechny buffery (včetně `previous[]`) se zafixují přesně na `N` a **žádné VLA se nepoužije**. Teprve tohle ti dá ta malá čísla z tabulky výše a kód běží i na překladačích bez podpory VLA (IAR, Keil, SDCC…). **Pro nasazení na MCU (ATmega328 apod.) je tohle doporučená a nejčistší volba** — stačí `N` nastavit na svoji maximální délku paketu.

Pro typické použití s LoRa (16–64B pakety, přeloženo s `-DDMD_PKT_MAX_BUILD=N`) je peak **80–230 B** — bez problémů na ATmega328 (2KB RAM). V defaultním buildu počítej navíc s ~513 B trvale obsazenými strukturami (255B `previous` v každé).

---

## Jak to funguje

```
+-------------------------------------------------------------------------------+
|                          START: Vstupní paket dat                             |
+-------------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------------+
|  Krok 1: Delta + ZigZag (keyframe přeskočí)                                   |
|  Testuj DELTA_1B / DELTA_2B / DELTA_FULL — vyber nejmenší počet jedniček      |
+-------------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------------+
|  Krok 2: Zkus kompresní kandidáty (každý s early exit limitem)                |
|                                                                               |
|   (a) µANS     — jen pokud zero_ratio >= 45%                                  |
|   (b) Huffman  — nibble Huffman s pevnou tabulkou v ROM se spouští vždy       |
|   (c) FLAG     — mapa nulových bajtů se spouští vždy                          |
|   (d) FLAG+HUF — FLAG odstraní nuly a Huffman případně zkomprimuje zbytek     |
+-------------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------------+
|  Krok 3: Vyber nejmenší výsledek                                              |
|  Pokud nic nepomůže → RAW záchrana (delta_type = NONE, original data)         |
+-------------------------------------------------------------------------------+
                                    |
                                    v
+-------------------------------------------------------------------------------+
|  Krok 4: Sestav záhlaví (1B) + payload → odešli                               |
+-------------------------------------------------------------------------------+
```

### Záhlaví (1 bajt)

Každý komprimovaný paket začíná jedním bajtem záhlaví:

```
MSB                    LSB
 7    6    5    4    3    2    1    0
[huf][ans][flg][dlt][dlt][vzo][vzo][vzo]
```

```
=======================================================================
| Bity |        Význam                                                |
|------|--------------------------------------------------------------|
|   7  | nibble Huffman komprese    1 = ON                            |
|   6  | µANS komprese              1 = ON                            |
|   5  | Flagování nulových bajtů   1 = ON                            |
|  4-3 | Typ delty: 00=žádná, 01=1B, 10=2B, 11=FULL (big-int+carry)   |
|  2-0 | Číslo vzorku (0–7)         0 = keyframe / start frame        |
=======================================================================

Kombinace bit 7 + bit 5 = FLAG+HUF (mapa nul + Huffman na nenulových)
```

Pokud žádná metoda nedokáže zkomprimovat data lépe než RAW, pošlou se původní data s bity 3-7 záhlaví nastavenými na 0. Přijímač pozná RAW protože záhlaví nepoužívá žádná nastavení.

### Vrstvy komprese

**1. Delta — rozdílová metoda**

Porovnání dvou po sobě jdoucích paketů. Tam kde se data mění pomalu (teplota, tlak, GPS souřadnice) vznikají po odečtení řetězce nulových nebo velmi malých hodnot. Protokol testuje tři typy delty a vybere ten s nejlepším výsledkem podle heuristiky (počet jedničkových bitů).

Podporované typy:
- **1B delta** — bajt po bajtu (uint8_t aritmetika)
- **2B delta** — po 16-bit slovech big-endian (uint16_t aritmetika)
- **FULL delta** — celý paket jako jedno velké číslo s carry propagací napříč všemi bajty. Vyhrává na čítačích a GPS souřadnicích kde hodnota přetéká přes bajtové hranice.

**2. ZigZag kódování**

Po aplikaci delty se data převedou ZigZag kódováním. Záporné rozdíly se zobrazí jako malá lichá čísla, kladné jako malá sudá čísla. Výsledkem jsou data s vysokým počtem nulových bitů, která lépe reagují na následující kompresní metody.

ZigZag se nepoužije pokud delta = žádná (včetně keyframe). Delta a ZigZag probíhají v jednom průchodu daty.

**3. Flagování nulových bajtů (FLAG)**

Každý nulový bajt se nahradí jedním bitem v bitové mapě. Před mapou je uložena délka paketu (1B). Nenulové bajty následují v původním pořadí.

Příklad pro 16B paket s 12 nulami:
```
Původní:  [0, 0, 5, 0, 0, 0, 3, 0, 0, 0, 0, 0, 7, 0, 0, 2]  (16B)
Payload:  [16][11011101 11110110][5, 3, 7, 2]
           1B délka + 2B mapa + 4B nenulové = 7B
Výsledek: 8B místo 16B (1B záhlaví + 7B payload)
```

**4. µANS komprese**

Asymetrické číselné systémy (ANS) pracují na úrovni bitů se dvěma váhami: nulový bit je vysoce pravděpodobný (29/32), jedničkový méně (3/32). Pro data s převahou nul po deltě+ZigZag dosahuje výrazné komprese bez tabulky.

ANS payload obsahuje délku dat (1B), stav (2B — uint16_t) a zakódované bajty. Spouští se pouze pokud podíl nulových bajtů >= 45% (heuristika). Enkodér i dekodér mají early exit — pokud výsledek přeroste limit, výpočet se okamžitě zastaví.

**5. Nibble Huffman (HUF)**

Pevná Huffmanova tabulka natrénovaná na kombinovaných meteo a GPS datech po deltě+ZigZag. Kóduje každý bajt jako dva nibble kódy (hi a lo). Tabulka je uložena v ROM (32B PROGMEM na ATmega), žádná RAM navíc.

Maximální délka kódu je 6 bitů, průměr ~3.2 bitu na bajt. Vyhrává zejména na IoT, průmyslových a komplexních datech kde jsou nuly vzácné ale distribuce nibblů sedí na tabulku.

**6. FLAG+HUF kombinace**

FLAG nejprve odstraní nulové bajty do bitové mapy, Huffman pak zkomprimuje zbývající nenulové bajty. Payload: `[1B délka][mapa][1B valid bits HUF][HUF stream]`. Nejlepší z obou světů — deterministická eliminace nul + entropická komprese zbytku.

**Keyframe a start frame**

Vzorek s číslem 0 je keyframe. Protože neexistuje předchozí paket pro výpočet delty, přeskočí se rozdílová metoda a ZigZag. Data jsou zpracována přímo metodami FLAG, HUF, FLAG+HUF nebo ANS. Keyframe nastane automaticky každých 7 paketů nebo po resetu zařízení.

---

## Použití

### Python

```python
from nic_dmd import DmdEncoder, DmdDecoder

PKT_LEN = 16
enc = DmdEncoder(PKT_LEN)
dec = DmdDecoder(PKT_LEN)

data = bytes([0xFC, 0x18, 0x21, 0x34, 0x01, 0x81,
              0x04, 0xCE, 0x00, 0x00, 0xFC, 0x7C,
              0xFC, 0xA8, 0x00, 0x00])

compressed   = enc.compress(data)
decompressed = dec.decompress(compressed)

print(f"Zkomprimováno: {PKT_LEN}B → {len(compressed)}B")
assert decompressed == data
```

### C (ATmega328 / Arduino)

```c
#include "nic_dmd.h"

dmd_encoder_t enc;
dmd_decoder_t dec;

void setup() {
    dmd_encoder_init(&enc, 16);   // délka paketu — musí sedět na obou stranách
    dmd_decoder_init(&dec, 16);
}

void loop() {
    uint8_t data[16]          = { /* senzorová data */ };
    uint8_t compressed[DMD_OUT_MAX];   // DMD_OUT_MAX = délka paketu + 1 (až 256B)
    uint8_t decompressed[16];

    uint16_t comp_len = dmd_compress(&enc, data, compressed);
    lora.send(compressed, comp_len);

    // Na přijímači:
    int res = dmd_decompress(&dec, compressed, comp_len, decompressed);
    if (res != 0) {
        // res < 0 → paket je vadný, viz tabulka návratových hodnot níže
    }
}
```

### Návratové hodnoty a chybové kódy

Každá funkce ti po doběhnutí „vrátí" jedno číslo. To číslo je jediný způsob, jak ti knihovna řekne, jak dopadla — žádné výpisy, žádné logování (kvůli úspoře paměti a výkonu). Vyšší program (to, co knihovnu používá) si toto číslo musí přečíst a zařídit se podle něj.

**`dmd_compress(...)` — komprese**

Vrací **délku výstupu v bajtech** (typ `uint16_t`, tj. 16bitové číslo):

| Vrácená hodnota | Co znamená | Co s tím |
|---|---|---|
| 2 až 256 | Počet bajtů, které máš odeslat (1B hlavička + komprimovaná data) | Pošli přesně tolik bajtů z `output` |

Komprese **nikdy neselže** a nemá chybový kód — vždy dostaneš platnou délku. V nejhorším případě (255B paket, který se nedá zkomprimovat, např. náhodná nebo šifrovaná data) vyleze **256 B**, tj. o 1 bajt víc než vstup. Tomu se říká maximální expanze o 1B (ten 1 bajt je povinná hlavička). Proto je návratový typ 16bitový — aby se číslo 256 vešlo. Výstupní buffer proto musí mít velikost `DMD_OUT_MAX` (= délka paketu + 1).

**`dmd_decompress(...)` — dekomprese**

Vrací **stavový kód** (typ `int`). Rozkomprimovaná data najdeš v `output` jen tehdy, když je kód `0`:

| Vrácená hodnota | Co znamená | Co s tím |
|---|---|---|
| `0` | OK — vše proběhlo v pořádku, `output` obsahuje původní data | Použij `output` |
| `-1` | Poškozený nebo nevalidní vstup (prázdný paket, nebo nesedí délka payloadu) | Zahoď paket, data jsou nepoužitelná |
| `-3` | Rezervovaná verze protokolu (v hlavičce je `sample_num = 7`) | Tento paket nepatří této verzi knihovny — zahoď ho |

Záporné číslo tedy vždy znamená „něco je špatně, data nepoužívej". Knihovna sama o sobě nedělá kontrolu integrity (CRC apod.) — předpokládá, že porušené pakety odchytí už nižší vrstva (rádiový modul). Kódy `-1` a `-3` jsou jen poslední pojistka proti zjevně nesmyslnému vstupu.

> **Python verze se chová identicky.** `DmdEncoder.compress()` vrátí stejně dlouhý výstup (i těch 256 B u nejhoršího případu) a `DmdDecoder.decompress()` při poškozeném nebo nevalidním vstupu vyhodí **výjimku** místo záporného kódu — to je pythonovský ekvivalent chyby z C (rezervovaná verze protokolu konkrétně vyhodí `ValueError`). Ošetři ji přes `try/except`. Stejný vstup jinak v C i v Pythonu vyprodukuje **bajtově totožný výstup**, takže můžeš komprimovat na zařízení v C a rozkomprimovat na serveru/Raspberry Pi v Pythonu (a naopak).

---

### Překlad pro jiné překladače (bez VLA)

Pokud tvůj překladač nepodporuje C99 VLA (IAR, Keil, MSVC C++), definuj maximální délku paketu při kompilaci:

```
gcc -DDMD_PKT_MAX_BUILD=32 c/nic_dmd.c ...
```

Buffery se zkompilují na pevnou velikost 32B. Pro projekty s jednou pevnou délkou paketu (typický Arduino use case) je tato varianta ideální.

---

## Soubory

Repozitář je rozdělený do tří adresářů podle role:

```
c/        C implementace (embedded, ATmega328 / AVR-GCC)
python/   Python referenční implementace + její testy
bench/    Benchmarky, stahování dat a nástroje pro analýzu
makefile  Sestaví C knihovnu a spustí C i Python testy
```

| Soubor                  | Popis                                              |
| ----------------------- | -------------------------------------------------- |
| `python/nic_dmd.py`     | Python implementace — referenční, pro testování    |
| `c/nic_dmd.c`           | C implementace pro ATmega328                       |
| `c/nic_dmd.h`           | Hlavičkový soubor                                  |
| `bench/nic_dmd_utils.py`| Pomocné funkce — analýza a výpis výsledků          |
| `makefile`              | Kompilace a testování                              |

### Testování a benchmark

| Soubor                    | Popis                                                              |
| ------------------------- | ------------------------------------------------------------------ |
| `python/nic_dmd_test.py`  | Python testy — round-trip, meteo, keyframe                         |
| `c/nic_dmd_test.c`        | C testy — round-trip, all-zeros, meteo                             |
| `bench/fetch_plus.py`     | Benchmark — reálná + syntetická data, uniformní int16 (20 zdrojů)  |
| `bench/fetch_small.py`    | Benchmark — stejné zdroje, schema-aware tight packing              |
| `bench/fetch_raw_text.py` | Benchmark — surový JSON/CSV text jako bajty                        |
| `bench/benchmark.py`      | Srovnání DMD vs Huffman vs Heatshrink                              |

---

## Licence

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Poděkování

Bratrovi za rady při tvorbě tohoto projektu.
Za technickou asistenci s optimalizací kódu AI asistentům Claude (Anthropic) a Gemini (Google).

★ Viva La Resistánce ★
