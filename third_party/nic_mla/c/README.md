# NIC-MLA — C knihovny

Dvě C implementace formátu **NIC-MLA**, sdílející jednu definici
(`nic_mla_format.h`). Binárně **shodné s Python referencí** (ověřeno cross-compat
testem: C zápis ↔ Python čtení).

| Soubor(y) | Knihovna | Pro koho |
|---|---|---|
| `nic_mla_format.h` | sdílené (header-only): konstanty, CRC16, LE pomocníci, build/parse log+prefix, `mla_hal_t` | obě |
| `nic_mla_write.{h,c}` | **WRITE-ONLY** — `format` / `mount` / `append` | ATmega328 a malá Arduina (write-only stanice) |
| `nic_mla.{h,c}` | **KOMPLETNÍ** — + `read_record` / `foreach`+filtr / `recover` | ARM Arduino (SAMD/STM32/Teensy/ESP), PC |

> **Proč dvě?** ATmega jen zapisuje (à minuty/15 min) a má 2 KB RAM. Write-only
> knihovna nemá žádnou velkou alokaci (největší buffer na zásobníku = 24 B, prefix
> se streamuje), takže se vejde i na šváb. Hledání/čtení/obnova běží až na hostu
> nebo na výkonnějším čipu přes kompletní knihovnu.

## HAL adaptéry (`hal/`) — souborový systém si vybíráš POD HALem

Jádro mluví jen přes 4 funkce (`mla_hal_t`). Co je pod nimi za FS knihovnu, je
**volba podle platformy — jádro se nemění**. Hotové adaptéry:

| Platforma | „Pod HALem" (FS) | Adaptér | NIC-MLA |
|---|---|---|---|
| **Raspberry Pi / PC** (SSD, SD, USB) | OS: ext4 / exFAT / NTFS / FAT32 / FAT16 | `hal/nic_mla_hal_posix.{h,c}` | Python **nebo** C kompletní |
| **Arduino AVR / ESP / STM32duino** | **SdFat** | `examples/atmega_sd_writeonly.ino` (glue) | C write-only / kompletní |
| **STM32 bare-metal** (CubeIDE/HAL) | **FatFs** (ChaN) | `hal/nic_mla_hal_fatfs.{h,c}` | C write-only / kompletní |

- **POSIX adaptér** (`nic_mla_hal_posix`) — `mla_posix_create/open/hal/close`;
  funguje na čemkoli s OS (Malina + SSD, PC). FS řeší OS pod tím.
- **FatFs adaptér** (`nic_mla_hal_fatfs`) — ~30 řádků nad `f_read/f_write/
  f_lseek/f_sync`. Kompiluje se v projektu s FatFs (`ff.h`).
- **SdFat** je C++ — napojení viz Arduino příklad; tentýž princip (4 funkce).

Napsat adaptér na další FS = pár desítek řádků; formát ani jádro se nemění.

## HAL — co musíš dodat

Obě knihovny pracují přes 4 funkce (žádný `malloc`, žádný filesystém uvnitř):

```c
typedef struct {
    int      (*read)(void *ctx, uint32_t off, void *buf, uint16_t n);   /* 0 = OK */
    int      (*write)(void *ctx, uint32_t off, const void *buf, uint16_t n);
    void     (*sync)(void *ctx);
    uint32_t (*size)(void *ctx);
    void     *ctx;
} mla_hal_t;
```

Na Arduinu se `read/write` typicky napojí na **SdFat** (`file.seek/read/write`)
nebo na SPI NOR flash. Offsety jsou logické (0 .. file_size-1).

## Minimální použití (write-only, ATmega)

```c
#include "nic_mla_write.h"

mla_writer_t w;
mla_hal_t hal = my_sd_hal();           /* tvůj HAL nad SdFat/NOR */

/* první spuštění: */
mla_w_format(&w, hal, 1UL<<20, MLA_CRC_FULL, 12, /*ckpt*/8, /*kf*/8);
/* další spuštění (po restartu): */
mla_w_mount(&w, hal);

uint8_t sample[5] = { temp_lo, temp_hi, hum, 0, batt };
mla_w_append(&w, unix_time(), /*station*/1, /*channel*/0, sample, 5, MLA_ENC_RAW, 0);
```

## Čtení / dotaz (kompletní knihovna, host nebo ARM)

```c
#include "nic_mla.h"

mla_t m; mla_mount(&m, hal);

mla_log_t rec; uint8_t buf[256]; uint16_t len;
mla_read_record(&m, 0, &rec, buf, sizeof(buf), &len);

/* filtr: jen stanice 1, kanál 0, časové okno */
mla_filter_t f = {0};
f.has_station = 1; f.station = 1;
f.has_channel = 1; f.channel = 0;
f.has_time = 1; f.time_from = t0; f.time_to = t1;
mla_foreach(&m, &f, my_callback, my_user_ptr);
```

## Test (na PC)

```sh
cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c \
   hal/nic_mla_hal_posix.c -o mlatest
./mlatest /tmp/mla_c_out.bin       # 19/19 PASS; zapíše soubor pro Python cross-check
python3 ../nic_mla.py              # Python umí týž soubor přečíst
```

## Poznámky

- Vše little-endian, serializace po bajtech → nezávislé na endianness/padding.
- Crash-safety (LOCK first, DATA second + torn-write detekce) je v obou knihovnách.
- `phys_addr` se podporuje jen v dolních 32 bitech (pro FAT/POSIX = 0).
- Rotace souborů a komprese jsou mimo toto jádro (rotace = platformní lepidlo nad
  FS; komprese = samostatná metoda, kontejner nese přes `rec_type`).

★ Viva La Resistánce ★
