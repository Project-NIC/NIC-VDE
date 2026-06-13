★ N.I.C. ★

# NIC-Arduino — the NIC Arduino Family

**The data side of NIC in one place: the MLA log format, its optional add-ons, the glue, the seismo export, and the VDE viewer.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

This is the umbrella for the whole NIC **Arduino family** — the software/data side
that grew up around Arduino. Each component keeps its own name and its own README
and stays usable on its own (drop-in, Arduino-library style) — they just live
together here so it is clear what belongs to what.

```
NIC-Arduino  (the Arduino family)
├── mla/         the base — the NIC-MLA log container/format
├── dmd/         optional: NIC-DMD compression
├── ksf/         optional: NIC-KSF encryption
├── glue-in/     glue: write data into an MLA log
├── glue-out/    glue: read / export an MLA log (CSV, SQLite, …)
├── mseed/       seismo export: an MLA log → miniSEED (international standard)
└── vde/         the VDE viewer (Volkov Data) — browse & export MLA logs
```

## How it fits together

**MLA is the base.** A NIC node logs its samples into a NIC-MLA container — that
is the heart of the data side, and it stands on its own. It started life as a
trivial few-line container and, even after growing a richer feature set, the
result stayed just as simple to use; there are only a few more options now.

Everything else is **optional, layered on top of MLA — a bonus, not a
requirement:**

- **dmd/** — if you want the samples stored *compressed*, MLA can write them
  through NIC-DMD. Plain MLA works perfectly well without it.
- **ksf/** — if you want the samples stored *encrypted*, NIC-KSF does that.
  Again optional.
- **glue-in/ · glue-out/** — the glue that writes into and reads/exports out of
  an MLA log. The libraries are the parts; the glue wires them per use.
- **mseed/** — the seismo export. A NIC-Quake / NIC-Station seismograph stores
  to MLA; when you want the *international* seismology format, mseed turns that
  MLA log into miniSEED (ObsPy / SeisComp / FDSN). It exists for the seismo
  platform; that anyone else can reuse it is a bonus.
- **vde/** — the VDE (Volkov Data) viewer: a desktop app that browses and
  exports MLA logs.

## Build & test

Each component builds and tests on its own; see its folder. In short:

```bash
# vde — the viewer (Python)
cd vde && python3 -m unittest discover -s tests

# mla (Python reference + C cross-check)        cd mla   && python3 nic_mla_test.py
# dmd (C + Python)                              cd dmd   && make test
# ksf (C 32/64-bit)                             cd ksf   && make
# mseed (Python + C)                            cd mseed && python3 tests/test_mseed.py
# glue-in / glue-out (Python)                   cd glue-in && python3 tests/test_glue.py
```

## License

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

★ Viva La Resistánce ★
