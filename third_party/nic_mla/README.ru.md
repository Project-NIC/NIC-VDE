<p align="center">
  <img src="NICMLA.svg" width="200"/>
</p>

[Pro dokumentaci v češtině klikněte zde](README.cs.md) | [For documentation in English click here](README.md)

---
# NIC-MLA

[![Лицензия: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

**Matroshka Logging Archive** — универсальный однофайловый контейнер для записи
данных с измерительных станций. И данные, и журнал хранятся в **одном переносимом
файле**, читаемом на разных платформах — от 8-битного микроконтроллера до ПК.

Один файл, один формат, один способ чтения — вынул карту из устройства, вставил
в компьютер и получил всё. Никакого зоопарка форматов.

> Полная спецификация формата: **[`DESIGN-MLA.md`](DESIGN-MLA.md)**

## Ключевые особенности

- **Один файл = данные + журнал.** Два потока растут навстречу друг другу:
  данные сверху, журнал снизу.
- **Устойчивость к сбоям.** Протокол фиксации «LOCK first, DATA second» + CRC16
  (CCITT-FALSE). Прерванная запись безопасно обнаруживается и устраняется при
  запуске.
- **Компактность для микроконтроллера.** ATmega328 (2 КБ ОЗУ) только пишет;
  никакого динамического выделения памяти, самый большой буфер — 24 Б. Поиск и
  чтение выполняются на хосте.
- **Контрольная точка (checkpoint).** Периодический якорь ускоряет запуск и
  восстановление.
- **Опциональная индексная область.** Небольшая таблица-указатель по времени и
  станции на стороне хоста для быстрых запросов (настраивается; по умолчанию
  выключена — путь только для записи / МК её никогда не заполняет).
- **Ротация файлов.** Когда один файл заполняется, создаётся следующий; большие
  объёмы = много файлов поменьше, хост читает их как единое целое.
- **32-битная адресация** → один файл до 4 ГБ (сверх того — ротация).
- **Опциональное сжатие.** Контейнер несёт и типизирует сжатые данные
  (`rec_type`: raw / delta / keyframe); сам метод сжатия он не определяет.
- **Независимость от файловой системы.** Доступ через тонкий HAL (4 функции);
  FAT16 / FAT32 / exFAT / NTFS / ext4 обслуживает слой под ним (ОС, SdFat или FatFs).

## Структура файла

```
смещение 0                                                            EOF
┌────────┬───────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX │ INDEX     │ поток ДАННЫХ  →   │ свободно 0xFF  │  ← поток ЖУРН.│
│ 512 Б  │ (опцион.) │ (растёт вверх)    │               │ (растёт вниз) │
└────────┴───────────┴──────────────────┴───────────────┴──────────────┘
```

- **Блок данных:** `MAGIC(2) + payload(1..65535) + CRC16(2)`
- **Запись журнала (24 Б):** timestamp, offset, station, channel, seq, rec_type,
  length, kf_back, flags (вне CRC), CRC16
- **Индекс** (опционально): плоский массив якорей по 12 Б (timestamp + слот
  журнала + станция); пуст, когда отключён.

## Структура репозитория

| Путь | Содержимое |
|---|---|
| `nic_mla.py` | Эталонное ядро на Python (format / mount / append / read / scan / recover) |
| `nic_mla_archive.py` | Python: ротация файлов (`MlaArchive`) + запрос на хосте (`query`) |
| `nic_mla_test.py` | Набор тестов (Python) |
| `c/` | Библиотеки на C: только запись (МК) + полная (ARM/ПК) + адаптеры HAL |
| `experimental/` | Заморожено / чисто теоретическое (симулятор raw SPI-NOR) |
| `DESIGN-MLA.md` | Проектная спецификация формата |

## Быстрый старт — Python

```python
from nic_mla import MlaCore, MlaPosixHAL

# Первый запуск (создаёт файл 1 МБ, предзаполненный 0xFF)
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format()
    mla.append(timestamp, station=1, channel=0, data=b"\x01\x02\x03")

# Последующие запуски: mount() восстанавливает состояние; итерация читает записи
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    for rec, payload in mla:
        ...
```

Ротация по нескольким файлам и фильтрация:

```python
from nic_mla_archive import MlaArchive, query
with MlaArchive("/data") as arch:          # MLA00000.MLA, MLA00001.MLA, …
    arch.append(ts, 1, 0, payload)
for rec, data in query(MlaArchive("/data"), station=1, time_from=t0, time_to=t1):
    ...
```

Ускоренный запрос с индексной областью:

```python
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format(index_kb=4)                  # зарезервировать таблицу 4 КБ по времени/станции
    ...
    # позже, на хосте:
    for rec, data in mla.scan(time_from=t0, time_to=t1, station=1):
        ...
```

Тесты:

```sh
python3 nic_mla_test.py
```

## Быстрый старт — C

Две библиотеки используют одно определение формата (`c/nic_mla_format.h`):

- **только запись** (`c/nic_mla_write.{h,c}`) — для ATmega / небольших Arduino,
- **полная** (`c/nic_mla.{h,c}`) — для ARM Arduino / ПК (+ чтение, запрос, recover).

HAL (4 функции) вы подключаете к своей файловой системе. Готовые адаптеры в `c/hal/`:

| Платформа | «Под HAL» | Адаптер |
|---|---|---|
| Raspberry Pi / ПК (SSD, SD, USB) | ОС: ext4 / exFAT / NTFS / FAT32 / FAT16 | `hal/nic_mla_hal_posix.{h,c}` |
| Arduino AVR / ESP / STM32duino | SdFat | `examples/atmega_sd_writeonly.ino` |
| STM32 bare-metal (CubeIDE/HAL) | FatFs (ChaN) | `hal/nic_mla_hal_fatfs.{h,c}` |

Сборка и тест на ПК:

```sh
cd c
cc -std=c99 -Wall -Wextra -O2 nic_mla_test.c nic_mla.c nic_mla_write.c \
   hal/nic_mla_hal_posix.c -o mlatest
./mlatest
```

См. **[`c/README.md`](c/README.md)**.

## Передача данных (LoRa / сеть)

**Вне области проекта** — контейнер это хранилище, а не транспорт. Каждая запись
самодостаточна (тип + длина + CRC), поэтому отправить её по LoRa/сети означает
«взять байты записи и отправить их». Выбор транспорта проект оставляет
пользователю.

## Статус

Эталонные реализации на Python и C завершены, протестированы и **побайтово
идентичны** (файл, записанный библиотекой на C, читается Python и наоборот).

## Лицензия

---

## Благодарности

Брату за советы при разработке этого проекта.
За техническую помощь в оптимизации кода — ИИ-ассистентам Claude (Anthropic) и Gemini (Google).

★ Viva La Resistánce ★
