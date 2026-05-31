# Volkov Data

[Česká dokumentace zde](README_cs.md) | [Документация на русском здесь](README_ru.md)

A cross-platform, two-pane file manager in the style of **Volkov Commander**,
written in Python on **prompt_toolkit**. It browses the local filesystem and
steps *inside* **NIC-MLA** containers, showing each logged record as a file.

> Status: **working** — two-pane browsing, file operations, file/record viewer,
> and an MLA backend that browses records. When a container carries a
> **self-describing schema table** (written by the station), the backend decodes
> each packed payload into real values + units; CSV/SQL export follows suit. All
> logic lives in `volkov_core/` (GUI-free) so it can be reused headless.

## Run

```bash
pip install -r requirements.txt
python3 volkov_data.py [left_dir] [right_dir]
```

**Keys**

| Key | Action |
|---|---|
| `Tab` | switch panel |
| `↑/↓ PgUp/PgDn Home/End` | move cursor |
| `Enter` | open dir / step into `.mla` / go up (`..`) |
| `F1` | info about the selected item / record |
| `F2` | repair / check an `.mla` container (flags damaged records) |
| `F3` | view file or record payload (text/hex) |
| `F4` | view a record with its decoded value(s) + units (schema-aware) |
| `F5` | copy selected file to the other panel |
| `F6` | rename or move — inside an `.mla`, export all records to CSV |
| `F7` | make directory |
| `F8` | delete (with confirmation) |
| `F9` | pull-down menu (sorting, language, export to SQL, …) |
| `F10` / `q` / `Ctrl-Q` | quit · `Esc` closes any overlay |

Press `Enter` on `samples/weather.mla` to step inside and browse its records.

## Tests

The `volkov_core/` logic is GUI-free, so it is covered by a stdlib `unittest`
suite (no extra dependencies):

```bash
python3 -m unittest discover -s tests
```

The tests build throwaway MLA containers on the fly and also smoke-test the
committed `samples/weather.mla`.

## Layout

```
volkov_data.py           prompt_toolkit GUI (thin shell over volkov_core)
volkov_core/             GUI-free logic — reusable headless
  backend.py               storage-backend abstraction (Entry / Backend)
  local.py                 LocalBackend — host filesystem
  mla.py                   MlaBackend — records inside an .mla as "files",
                           schema-aware value decoding + CSV/SQL export
samples/make_sample.py   generator for a self-describing sample datalogger file
samples/weather.mla      committed sample (packed rows + schema) to develop against
tests/                   stdlib unittest suite for volkov_core (GUI-free)
third_party/nic_mla/     vendored NIC-MLA — canonical data format (Python + C + spec)
  tools/mla_schema.py      host-only schema-table builder + reader (VDE links this)
docs/vc-reference/       original Volkov Commander sources (BSD-2) as a UI reference
```

The desktop reads the logger format through MLA's Python reference
(`third_party/nic_mla/nic_mla.py`) and decodes payloads via the host-only schema
reader (`tools/mla_schema.py`), both kept byte-identical to the C core. The
Volkov Commander sources are a **behavior reference only** — not ported code.
