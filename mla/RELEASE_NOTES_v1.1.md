# NIC-MLA v1.1 — high-rate sampling & a leaner record

A compatibility-breaking but simple **upgrade** of the log record. The project
has essentially no users yet, so the format change breaks nobody; the internal
prefix `version` byte stays **1**. Frame it as a feature release.

## What's new

- **Sub-second timestamps.** The log record now carries a `subsec` field
  (sub-second tick / sample index within the second), so MLA handles sampling
  well above 1 Hz — e.g. a MEMS seismograph. Previously time resolved only to
  whole Unix seconds, which suited slow telemetry. Set `subsec = 0` for
  whole-second logging.
- **Compact, consolidated 16-byte record.** The old `rec_type`/class byte and
  the `reserved` padding byte are gone. Encoding state is now one packed `flags`
  byte: **bit 7 = compressed**, **bits 0–6 = `kf_back`** (distance back to the
  owning keyframe; 0 = this record *is* a keyframe). This frees room for
  `subsec` without growing the record.
  - MLA stays **codec-agnostic**: the `compressed` bit only means "hand the
    payload to the codec layer". *Which* codec / keyframe / variant lives in the
    data block's own header (NIC-DMD already does this), never in MLA.
  - Files are **homogeneous** — what a payload means comes from the SCHEMA
    table, not a per-record type tag.
- **Robust file rotation.** Each rotated file is independently mountable:
  - it inherits the previous file's tables/format params on reopen (no more
    rotating into a file with empty tables);
  - MLA now **surfaces the rotation event** (`MlaArchive.append` returns whether
    it rotated; `will_rotate()` predicts it; an `on_rotate` callback fires) so a
    compressed stream's glue can force a keyframe at the start of each file. For
    RAW data this is moot.
- **Prefix resilience.** A byte-identical **mirror copy** of the self-describing
  prefix is written at the tail of the file; `mount()` falls back to it if the
  primary copy at offset 0 fails its CRC. A single bad sector at the head no
  longer blinds the whole file. (The log region now ends at `region_end =
  file_size − prefix size`.)

## Compatibility

- On-disk format changes (record layout + tail mirror). v1.0 files are not
  read by v1.1 and vice-versa. The prefix `version` byte remains 1.
- Byte-exact across the Python reference and the C libraries (AVR / ARM / PC) —
  verified by `c/cross_check.py`.

## Verification

- Python suite: **87/87 PASS**
- C suite (write-only + complete libs): **36/36 PASS**
- C↔Python byte-exact cross-check: **10/10 PASS**

---

# NIC-MLA v1.1 — rychlé vzorkování & štíhlejší záznam (CZ)

Jednoduchý **upgrade** log záznamu (mění formát, ale projekt zatím nemá uživatele,
takže nikomu nic nerozbije). Interní `version` bajt v prefixu zůstává **1**.

- **Sub-sekundové časování** — záznam nese nové pole `subsec` (tik / číslo vzorku
  v rámci sekundy), takže MLA zvládá vzorkování výrazně nad 1 Hz (např. MEMS
  seismograf). Dřív čas šel jen na celé sekundy. Pro pomalou telemetrii `subsec = 0`.
- **Kompaktní 16B záznam** — zrušen `rec_type`/třída i `reserved` bajt. Kódování
  je teď jeden `flags` bajt: **bit 7 = compressed**, **bity 0–6 = `kf_back`**
  (vzdálenost k vlastnímu keyframu; 0 = tento záznam JE keyframe). MLA zůstává
  kodek-agnostický — *který* kodek žije v hlavičce datového bloku, ne v MLA.
  Význam dat plyne ze SCHEMA tabulky, ne z typu záznamu.
- **Robustní rotace souborů** — každý rotovaný soubor je samostatně čitelný:
  při znovuotevření zdědí tabulky/parametry; MLA navíc **signalizuje rotaci**
  (`append` vrací zda rotoval, `will_rotate()`, callback `on_rotate`), takže
  lepidlo komprimovaného streamu vynutí keyframe na začátku každého souboru.
- **Odolnost prefixu** — na konci souboru je **zrcadlová kopie** prefixu;
  `mount()` na ni přepne, když primární kopii na offsetu 0 nesedí CRC. Jeden
  vadný sektor v hlavičce už nezničí celý soubor.

Ověřeno: Python 87/87 · C 36/36 · C↔Python byte-exact 10/10.
