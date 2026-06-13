# experimental/ — zamrzlé, čistě teoretické

> **Tento adresář je experimentální a dál se nerozvíjí.** Obsah tu zůstává jako
> referenční/teoretická možnost, ne jako cílová cesta projektu.

## `nic_mla_hal_nor.py` — raw SPI-NOR HAL (simulátor)

Simulátor NOR flash třídy W25Q v RAM (`MlaNorSimHAL`) — modeluje omezení raw
NOR: Page Program po 256 B, zápis jako AND (jen 1→0), erase po sektorech na 0xFF.
Slouží k testu, že se jádro chová korektně i na médiu s erase-before-write
sémantikou.

### Proč je to zamrzlé (rozhodnutí)

Přímý raw SPI-NOR/NAND jsme **opustili** ve prospěch **SD/flash karty**:

- **Riziko zablokování čipu.** Některé NOR/NAND integráče mají bezpečnostní /
  lockdown mechanismy; partial-page nebo partial-block zápisy (když nezapíšeš
  celý blok) je po pár desítkách bloků můžou dostat do chybového stavu.
- **Vendor-specifické.** Dělat to pořádně by znamenalo psát to pro konkrétní
  řady (Winbond W25Q apod.) — malá univerzálnost, velká údržba.
- **SD má vlastní řadič.** Karta si **wear-leveling, ECC i remapování bloků**
  obstará sama. Pro stanici à 15 min je to spolehlivější a jednodušší — i na
  Arduinu jedeme přes kartu.

### Důsledky

- **Žádný reálný SPI-NOR HAL se nepíše** (ani v C). C knihovny cílí na SD (SdFat).
- Simulátor a jeho testy zůstávají funkční (jádro je vůči médiu nezávislé přes
  HAL), ale jde o **doklad univerzálnosti**, ne o podporovaný scénář.
- Pokud bys to někdy oživoval, počítej s tím, že commit protokol (LOCK first,
  flags mimo CRC) je na NOR navržený schválně — ale lockdown rizika konkrétních
  čipů si musíš ověřit v datasheetu.

★ Viva La Resistánce ★
