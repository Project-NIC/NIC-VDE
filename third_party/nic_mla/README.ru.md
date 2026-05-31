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
- **Тупой контейнер.** MLA только хранит байты. Вся логика (сжатие, шифрование,
  перевод номеров станций, LoRa/Wi-Fi) — в отдельном слое-клее (glue); MLA
  остаётся маленьким и не мешает.
- **Крошечная запись журнала 16 Б, целиком под CRC.** Никакого трюка «flags вне
  CRC»: запись отменяется перезаписью нулями — её CRC перестаёт совпадать, и
  читатель её пропускает.
- **Устойчивость к сбоям.** Протокол фиксации «LOCK first, DATA second» + CRC16
  (CCITT-FALSE). После сброса последняя запись либо проходит проверку (продолжаем),
  либо обнуляется и место освобождается. На диске нет дерева поиска, которое
  могло бы повредиться.
- **Самоописание.** Префикс несёт таблицу SCHEMA (8-символьные имена полей +
  единицы → готово к экспорту в CSV/SQL без предварительного знания) и таблицу
  STATION (1-байтовый индекс станции в каждой записи → реальный номер станции).
- **Компактность для микроконтроллера.** ATmega328 (2 КБ ОЗУ) только пишет;
  никакого динамического выделения памяти, самый большой буфер — 32 Б. Поиск и
  чтение выполняются на хосте.
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
┌──────────────────┬──────────────────┬───────────────┬──────────────┐
│ PREFIX           │ поток ДАННЫХ  →   │ свободно 0xFF  │  ← поток ЖУРН.│
│ 1–255 секторов   │ (растёт вверх)    │               │ (растёт вниз) │
│ (по 512 Б)       │                   │               │               │
└──────────────────┴──────────────────┴───────────────┴──────────────┘
```

- **Префикс:** заголовок 34 Б + таблицы SCHEMA и STATION, покрытые CRC16 в
  последних 2 байтах. Обычно один сектор 512 Б; растёт целыми секторами
  (до 255 ≈ 127 КБ) только если таблицам это нужно.
- **Блок данных:** `MAGIC(2) + payload(1..65535) + CRC16(2)`
- **Запись журнала (16 Б), целиком под CRC:** offset, timestamp, length,
  rec_type, kf_back, station (1-байтовый индекс), reserved, CRC16.

## Структура репозитория

| Путь | Содержимое |
|---|---|
| `nic_mla.py` | Эталонное ядро на Python (format / mount / append / read / recover) |
| `nic_mla_archive.py` | Python: ротация файлов (`MlaArchive`) + запрос на хосте (`query`) |
| `tools/mla_schema.py` | Сборка/чтение таблиц SCHEMA + STATION; декодирование payload для CSV/SQL |
| `nic_mla_test.py` | Набор тестов (Python) |
| `c/` | Библиотеки на C: только запись (МК) + полная (ARM/ПК) + адаптеры HAL |
| `DESIGN-MLA.md` | Проектная спецификация формата |

## Быстрый старт — Python

```python
from nic_mla import MlaCore, MlaPosixHAL

# Первый запуск (создаёт файл 1 МБ, предзаполненный 0xFF)
hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format()
    mla.append(timestamp, station=1, data=b"\x01\x02\x03")   # station = индекс в таблице

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
    arch.append(ts, station=1, data=payload)
for rec, data in query(MlaArchive("/data"), station=1, time_from=t0, time_to=t1):
    ...
```

Самоописывающийся файл (таблицы schema + station → экспорт в CSV/SQL):

```python
from mla_schema import SchemaBuilder, StationTable, read_schema, \
                       read_stations, decode_payload, split_station

sb = SchemaBuilder()
sb.data("temp", unit="degC", width=2, exp10=-1, signed=True)
st = StationTable(); st.station(region=55, number=25000)   # индекс 1 → эта станция

hal = MlaPosixHAL.create("log.mla")
with hal:
    mla = MlaCore(hal)
    mla.format(schema_table=sb.table(), station_table=st.table())
    mla.append(ts, station=1, data=temp.to_bytes(2, "little", signed=True))

# Любой читатель восстановит имена, единицы и реальный номер станции — без знаний:
with MlaPosixHAL("log.mla") as hal:
    mla = MlaCore(hal); mla.mount()
    pfx = mla._prefix.to_bytes()
    _, fields = read_schema(pfx); stations = read_stations(pfx)
    for rec, data in mla:
        region, number, _ = split_station(stations[rec.station - 1])
        cols = decode_payload(fields, data)   # [(имя, единица, значение), …]
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

## Заметки для интеграторов

- **Имён станций в файле нет.** Таблица STATION хранит лишь 6 сырых байт на
  станцию; что они значат (регион / номер / город / …) решает ваш слой-клей,
  который держит собственное отображение «6 байт → смысл». Лог несёт только
  1-байтовый индекс — перевод его в реальный номер станции это задача клея, а не
  контейнера.
- **Байт `reserved` в записи лога — это паддинг**, выравнивающий запись до 16 Б
  (степень двойки, так что она никогда не пересекает сектор). Он внутри CRC и
  сейчас всегда 0 — считайте его свободным местом под будущее поле, а не чем-то,
  что что-то значит сегодня.

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
