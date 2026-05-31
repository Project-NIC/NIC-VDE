# Volkov Data

[English documentation here](README.md) | [Документация на русском здесь](README_ru.md)

Multiplatformní dvoupanelový souborový manažer ve stylu **Volkov Commanderu**,
napsaný v Pythonu nad knihovnou **prompt_toolkit**. Prochází místní souborový
systém a umí *vstoupit dovnitř* kontejnerů **NIC-MLA**, kde každý zalogovaný
záznam zobrazí jako soubor.

> Stav: **funkční** — dvoupanelové procházení, souborové operace, prohlížeč
> souborů i záznamů a MLA backend, který prochází záznamy. Veškerá logika žije
> v `volkov_core/` (bez GUI), takže ji lze použít i bez grafického rozhraní.

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
| `F2` | oprava / kontrola kontejneru `.mla` (označí poškozené záznamy) |
| `F3` | zobrazit obsah souboru nebo záznamu (text/hex) |
| `F4` | zobrazit záznam i s dekódovanou hodnotou |
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
  mla.py                   MlaBackend — záznamy v .mla jako „soubory“
samples/make_sample.py   generátor vzorového souboru meteostanice
samples/weather.mla      přiložený vzorek (~549 záznamů) pro vývoj
tests/                   sada testů unittest pro volkov_core (bez GUI)
third_party/nic_mla/     přibalená NIC-MLA — kanonický datový formát (Python + C + specifikace)
docs/vc-reference/       původní zdrojáky Volkov Commanderu (BSD-2) jako vzor UI
```

Aplikace čte formát loggeru přes referenční Python implementaci MLA
(`third_party/nic_mla/nic_mla.py`), udržovanou bajt po bajtu shodnou s jejím
C jádrem. Zdrojáky Volkov Commanderu slouží **jen jako vzor chování** — nejde
o přepsaný kód.
