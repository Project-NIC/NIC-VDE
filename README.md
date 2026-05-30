# Volkov Data

A cross-platform, two-pane file manager in the style of **Volkov Commander**,
written in Python on **prompt_toolkit**. It browses and (later) edits data logged
in the **NIC-MLA** container format.

> Status: **early prototype** — a minimal two-pane browser is on screen; file
> operations and the MLA codec are not wired yet.

## Run

```bash
pip install -r requirements.txt
python3 volkov_data.py [left_dir] [right_dir]
```

**Keys:** `Tab` switch panel · `↑/↓ PgUp/PgDn Home/End` move · `Enter` open dir ·
`F10` / `q` / `Ctrl-Q` quit. (The F1–F9 bar is shown but not yet wired.)

## Layout

```
volkov_data.py          minimal two-pane prototype (single file, for now)
third_party/nic_mla/     vendored NIC-MLA — canonical data format (Python + C + spec)
docs/vc-reference/       original Volkov Commander sources (BSD-2) as a UI reference
```

The desktop reads the logger format through MLA's Python reference
(`third_party/nic_mla/nic_mla.py`), kept byte-identical to its C core. The
Volkov Commander sources are a **behavior reference only** — not ported code.
