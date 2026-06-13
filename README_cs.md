★ N.I.C. ★

# NIC-Arduino — rodina NIC Arduino

**Datová strana NIC na jednom místě: formát logu MLA, jeho volitelné doplňky, lepidlo, seismo export a prohlížeč VDE.**

*[English](README.md) · [Čeština](README_cs.md) · [Русский](README_ru.md)*

---

[![License: MIT](https://img.shields.io/badge/License-MIT-red.svg)](https://opensource.org/licenses/MIT)

---

Tohle je deštník nad celou **Arduino rodinou** NIC — softwarová/datová strana, co
vyrostla kolem Arduina. Každá součást si drží svůj název i svoje README a zůstává
použitelná samostatně (drop-in, ve stylu Arduino knihovny) — jen bydlí pohromadě
tady, aby bylo jasné, co kam patří.

```
NIC-Arduino  (Arduino rodina)
├── mla/         základ — kontejner/formát logu NIC-MLA
├── dmd/         volitelné: komprese NIC-DMD
├── ksf/         volitelné: šifrování NIC-KSF
├── glue-in/     lepidlo: zápis dat do MLA logu
├── glue-out/    lepidlo: čtení / export MLA logu (CSV, SQLite, …)
├── mseed/       seismo export: MLA log → miniSEED (mezinárodní standard)
└── vde/         prohlížeč VDE (Volkov Data) — procházení a export MLA logů
```

## Jak to do sebe zapadá

**MLA je základ.** NIC node loguje vzorky do NIC-MLA kontejneru — to je srdce
datové strany a stojí samo o sobě. Začínalo to jako triviální pár řádků a i po
tom, co to dostalo bohatší výbavu, zůstal výsledek stejně triviální na použití;
jen možností je teď o trošku víc.

Všechno ostatní je **volitelné, navrstvené nad MLA — bonus, ne podmínka:**

- **dmd/** — když chceš vzorky uložené *komprimovaně*, MLA je umí zapsat přes
  NIC-DMD. Čisté MLA se bez toho v pohodě obejde.
- **ksf/** — když chceš vzorky uložené *šifrovaně*, dělá to NIC-KSF. Opět
  volitelné.
- **glue-in/ · glue-out/** — lepidlo, které zapisuje do MLA logu a čte/exportuje
  z něj. Knihovny jsou díly; lepidlo je propojuje podle použití.
- **mseed/** — seismo export. Seismograf NIC-Quake / NIC-Station ukládá do MLA;
  když chceš *mezinárodní* seismologický formát, mseed ten MLA log převede do
  miniSEED (ObsPy / SeisComp / FDSN). Vznikl pro seismo platformu; že to může
  použít i někdo jiný, je bonus.
- **vde/** — prohlížeč VDE (Volkov Data): desktopová aplikace na procházení a
  export MLA logů.

## Licence

MIT License — Copyright (c) 2026 NIC — Native Intellect Community

★ Viva La Resistánce ★
