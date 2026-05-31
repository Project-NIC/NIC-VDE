# Volkov Data

[English documentation here](README.md) | [Документация на русском здесь](README_ru.md)

Multiplatformní dvoupanelový souborový manažer ve stylu **Volkov Commanderu**,
napsaný v Pythonu nad knihovnou **prompt_toolkit**. Prochází místní souborový
systém a umí *vstoupit dovnitř* kontejnerů **NIC-MLA**, kde každý zalogovaný
záznam zobrazí jako soubor.

> Stav: **1.0** — postaveno nad **NIC-MLA v1.0**. Dvoupanelové procházení,
> souborové operace, prohlížeč souborů i záznamů a MLA backend, který prochází
> záznamy. MLA je záměrně *hloupý* kontejner, takže se backend opírá o jeho
> self-describing tabulky: **schema tabulka** dekóduje každý packed payload na
> reálné hodnoty + jednotky a **station tabulka** přeloží 1bajtový index stanice
> z logu na reálný region/number — obojí jde rovnou do CSV/SQL exportu.
>
> Návrh zrcadlí filozofii samotného MLA — **hloupé knihovny + tenké lepidlo**:
> dole znovupoužitelné knihovny (`export` plus přibalené MLA i jeho reader),
> backendy jsou nad nimi jen tenké adaptéry. Celé `volkov_core/` je bez GUI,
> takže ho lze použít i samostatně.

## Spuštění

```bash
pip install -r requirements.txt
python3 volkov_data.py [levý_adresář] [pravý_adresář]
```

**Klávesy**

| Klávesa | Akce |
|---|---|
| `Tab` | přepnout panel |
| `↑/↓ PgUp/PgDn Home/End` | pohyb kurzoru |
| `Enter` | otevřít adresář / vstoupit do `.mla` / o úroveň výš (`..`) |
| `F1` | informace o vybrané položce / záznamu |
| `F2` | kontrola kontejneru `.mla` — počet platných / mrtvých / poškozených slotů |
| `F3` | zobrazit obsah souboru nebo záznamu (text/hex) |
| `F4` | zobrazit záznam i s dekódovanými hodnotami a jednotkami (dle schématu) |
| `F5` | kopírovat vybraný soubor do druhého panelu |
| `F6` | přejmenovat nebo přesunout — uvnitř `.mla` export všech záznamů do CSV |
| `F7` | vytvořit adresář |
| `F8` | smazat (s potvrzením) |
| `F9` | rozbalovací menu (třídění, jazyk, export do SQL, …) |
| `F10` / `q` / `Ctrl-Q` | konec · `Esc` zavře jakékoli překryvné okno |

Stiskem `Enter` na `samples/weather.mla` vstoupíš dovnitř a procházíš jeho záznamy.

## Testy

Logika v `volkov_core/` je bez GUI, takže ji pokrývá sada testů ve standardní
knihovně `unittest` (bez dalších závislostí):

```bash
python3 -m unittest discover -s tests
```

Testy si za běhu vytvářejí dočasné MLA kontejnery a navíc otestují i přiložený
vzorek `samples/weather.mla`.

## Struktura

```
volkov_data.py           GUI v prompt_toolkit (tenká slupka nad volkov_core)
volkov_core/             logika bez GUI — použitelná samostatně
  backend.py               abstrakce úložného backendu (Entry / Backend)
  local.py                 LocalBackend — souborový systém hostitele
  mla.py                   MlaBackend — tenký adaptér: záznamy jako „soubory“,
                           dekódování dle schématu + překlad stanice + export
  export.py                hloupá knihovna — generické řádky → CSV / SQLite
  stations.py              lepidlo — index stanice → reálný region/number
samples/make_sample.py   generátor self-describing vzorku meteostanice
samples/weather.mla      přiložený vzorek (packed řádky + schéma + stanice)
tests/                   sada testů unittest pro volkov_core (bez GUI)
third_party/nic_mla/     přibalená NIC-MLA v1.0 — kanonický datový formát (Python + C)
  tools/mla_schema.py      host-only buildery + readery schématu/stanic (VDE je linkuje)
docs/vc-reference/       jeden archiv Volkov Commanderu (BSD-2) — záložní vzor UI
```

Aplikace čte formát loggeru přes referenční Python implementaci MLA
(`third_party/nic_mla/nic_mla.py`) a dekóduje payloady i stanice přes host-only
reader (`tools/mla_schema.py`), obojí udržované bajt po bajtu shodné s C jádrem.
Zdrojáky Volkov Commanderu slouží **jen jako vzor chování** — nejde o přepsaný kód.
