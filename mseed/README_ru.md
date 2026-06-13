# NIC-MSEED

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

**Самостоятельная библиотека данных NIC — преобразование лога NIC-MLA в miniSEED (Steim-1 / Steim-2).**

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

```
   .mla  ──▶  [декод NIC-DMD, если сжато]  ──▶  целочисленные counts по каналам  ──▶  miniSEED
```

> **Что это.** Одна из самостоятельных библиотек данных NIC (наряду с NIC-MLA,
> NIC-DMD, NIC-KSF). NIC-узел логирует отсчёты в контейнер NIC-MLA; **miniSEED** —
> это lingua franca сейсмологии: он ложится прямо в **ObsPy, SeisComp, SWARM** и
> инструментарий FDSN. NIC-MSEED — это мост: читает `.mla`, распаковывает блобы
> NIC-DMD, достаёт сырые целочисленные counts по каналам SCHEMA и пишет стандартные
> записи miniSEED. У кого есть сейсмический MLA-лог (напр. от **NIC-Quake** /
> **NIC-Station**) и нужен SEED — использует это: **рабочая библиотека, а не
> фреймворк.** (Для ad-hoc проверки любого MLA-лога в CSV / SQLite служит
> NIC-GLUE-OUT; miniSEED — сейсмо-путь.)

## Две реализации

- **Python** (`nic_mseed/`) — эталон: чистый Python 3.10+, без внешних пакетов.
- **C** (`c/`) — тот же кодек Steim-1/2 + писатель miniSEED на переносимом C, для
  on-device / встраиваемого экспорта. Обе host-тестируются и round-trip’ятся против
  векторов друг друга.

## Два слоя

- **`steim` / `mseed`** — ядро, не зависящее от контейнера: целые числа → кадры
  Steim-1/2 → записи miniSEED, и обратно (минимальный ридер для round-trip тестов). Без зависимостей.
- **`from_mla`** — конвертер, связывающий **NIC-MLA + NIC-DMD** с этим ядром:
  по-станционный DMD-реплей, разбиение каналов по схеме, маппинг SEED-кодов.

## Быстрый старт

```python
from nic_mseed import MseedExporter, STEIM2

stats = MseedExporter(
    sample_rate_hz=100.0,        # ODR устройства — miniSEED нужна частота дискретизации; MLA её не хранит
    network="NQ",                # SEED-код сети
    version=STEIM2,              # или STEIM1
    channel_map={"z": "HHZ", "n": "HHN", "e": "HHE"},   # поле SCHEMA → SEED-канал
).export("quake.mla", "quake.mseed")
print(stats)   # {channels, samples, records, bytes, out}
```

```bash
python3 examples/mla_to_mseed.py            # строит образец .mla, конвертирует, печатает статистику
python3 tests/test_steim.py                 # round-trip кодека Steim-1/2
python3 tests/test_mseed.py                 # запись miniSEED (+ эталон ObsPy, если установлен)
python3 tests/test_from_mla.py              # end-to-end MLA(+DMD) → miniSEED round-trip
```

## Как MLA отображается в miniSEED

| miniSEED требует | берётся из |
|---|---|
| время начала (BTIME) | MLA `timestamp` (u32 с) + `subsec` (u16) первой записи |
| частота дискретизации | **задаёшь ты** (`sample_rate_hz` = ODR устройства); `subsec` лишь фиксирует суб-секундную фазу |
| целочисленные counts | полезная нагрузка MLA, разбитая по полям SCHEMA (сырая или распакованная NIC-DMD) — *сырые* counts, а не масштабированное физическое значение (калибровка — в StationXML) |
| network/station/location | таблица STATION в MLA (или `station_map`) |
| код канала | имя поля SCHEMA (или `channel_map`) |

Каждая пара `(станция, поле)` становится одним каналом miniSEED. Конвертер
предполагает равномерно дискретизированный, непрерывный ряд на канал (верно для
синхронизированного сбора, напр. **NIC-Quake**); разбиение на пропуски (gaps)
оставлено на более поздний проход.

## Валидация

Кодек и писатель проходят round-trip через собственный минимальный ридер этого
пакета. Тест miniSEED дополнительно проверяет против **ObsPy**, если он установлен —
запусти `python3 tests/test_mseed.py` на машине с ObsPy для эталонного
доказательства соответствия спецификации.

## Структура

```
nic_mseed/          Python: steim (кодек) + mseed (писатель) + from_mla (конвертер)
c/                  C: переносимый кодек Steim-1/2 + писатель miniSEED (+ тесты, CMake)
examples/           запускаемое демо MLA → miniSEED
tests/              round-trip кодека, писатель и end-to-end тесты конвертера
third_party/        вендоренные NIC-MLA + NIC-DMD (см. VENDORED.md)
```

Python-эталон — чистый Python 3.10+, без внешних пакетов (ObsPy — опциональная
проверка *только для тестов*). C-сборка host-тестируется через CMake:

```bash
cmake -S c -B c/build && cmake --build c/build && ctest --test-dir c/build --output-on-failure
```

## Лицензия

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

---

## Благодарности

Брату за советы при создании этого проекта.
За техническую помощь с оптимизацией кода — ИИ-ассистентам Claude (Anthropic) и Gemini (Google).

★ Viva La Resistánce ★
