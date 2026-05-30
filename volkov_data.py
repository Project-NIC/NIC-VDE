#!/usr/bin/env python3
"""
Volkov Data — two-pane file manager (Volkov Commander look).

Renders the classic VC/NC screen: a top menu bar with a clock, two
double-bordered cyan-on-blue panels (path in the frame title, a "Name" header,
a bottom info line with the selected item's date/time), a command line, and the
F1-F10 function-key bar.

Navigate the local filesystem: Tab switches panel, arrows/PgUp/PgDn/Home/End
move, Enter descends. File operations and the MLA codec are not wired yet.

Run:  python3 volkov_data.py [left_dir] [right_dir]
Quit: F10 / q / Ctrl-Q
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import UIControl, UIContent
from prompt_toolkit.styles import Style

# Box-drawing (double frame + single divider tee)
TL, TR, BL, BR, H, V = "╔", "╗", "╚", "╝", "═", "║"
DL, DR, DH = "╟", "╢", "─"

Fragments = list[tuple[str, str]]


def fit(s: str, w: str | int) -> str:
    """Truncate or right-pad a string to exactly ``w`` columns."""
    w = int(w)
    if w <= 0:
        return ""
    return s[:w] if len(s) >= w else s + " " * (w - len(s))


@dataclass
class Panel:
    """One file-browser pane: a directory and a cursor over its entries."""

    path: str
    selected: int = 0
    scroll: int = 0
    entries: list[tuple[str, bool]] = field(default_factory=list)  # (name, is_dir)

    def load(self) -> None:
        """Read the directory: '..' first, then dirs, then files (each sorted)."""
        self.path = os.path.abspath(self.path)
        dirs: list[str] = []
        files: list[str] = []
        try:
            with os.scandir(self.path) as it:
                for e in it:
                    try:
                        (dirs if e.is_dir() else files).append(e.name)
                    except OSError:
                        files.append(e.name)
        except OSError:
            pass  # unreadable dir → just show '..'
        entries: list[tuple[str, bool]] = []
        if os.path.dirname(self.path) != self.path:
            entries.append(("..", True))
        entries += [(n, True) for n in sorted(dirs, key=str.lower)]
        entries += [(n, False) for n in sorted(files, key=str.lower)]
        self.entries = entries
        self.selected = max(0, min(self.selected, len(entries) - 1))
        self.scroll = 0

    @property
    def current(self) -> tuple[str, bool] | None:
        if 0 <= self.selected < len(self.entries):
            return self.entries[self.selected]
        return None

    def move(self, delta: int) -> None:
        if self.entries:
            self.selected = max(0, min(self.selected + delta, len(self.entries) - 1))

    def enter(self) -> None:
        """Descend into the selected directory (or '..')."""
        cur = self.current
        if cur is None or not cur[1]:
            return
        name = cur[0]
        prev = os.path.basename(self.path)
        self.path = os.path.abspath(os.path.join(self.path, name))
        self.load()
        if name == "..":  # stepping up → land on the directory we came from
            for i, (n, _) in enumerate(self.entries):
                if n == prev:
                    self.selected = i
                    break


class VCControl(UIControl):
    """Draws the whole VC screen; knows the terminal size via create_content."""

    def __init__(self, app: "VolkovData"):
        self.app = app

    def create_content(self, width: int, height: int) -> UIContent:
        lines = self.app.render(width, height)
        return UIContent(
            get_line=lambda i: lines[i],
            line_count=len(lines),
            show_cursor=False,
        )


class VolkovData:
    def __init__(self, left: str, right: str):
        self.panels = [Panel(left), Panel(right)]
        self.active = 0
        for p in self.panels:
            p.load()
        self.app = self._build_app()

    # ── full-screen render ─────────────────────────────────────────────────
    def render(self, width: int, height: int) -> list[Fragments]:
        panels_h = max(6, height - 3)  # menu + cmdline + fkey bar take 3 rows
        lw = width // 2
        rw = width - lw
        left = self._panel(self.panels[0], self.active == 0, lw, panels_h)
        right = self._panel(self.panels[1], self.active == 1, rw, panels_h)
        body = [l + r for l, r in zip(left, right)]
        return [self._menubar(width), *body, self._cmdline(width), self._fkeybar(width)]

    def _panel(self, p: Panel, active: bool, w: int, h: int) -> list[Fragments]:
        inner = w - 2
        list_h = h - 5  # top, header, divider, info, bottom borders
        # keep the cursor inside the viewport
        if p.selected < p.scroll:
            p.scroll = p.selected
        elif p.selected >= p.scroll + list_h:
            p.scroll = p.selected - list_h + 1
        p.scroll = max(0, min(p.scroll, max(0, len(p.entries) - list_h)))

        bd = "class:border-act" if active else "class:border"
        lines: list[Fragments] = []

        # top border with the path as a centered title
        title = p.path
        if len(title) > inner - 4:
            title = "…" + title[-(inner - 5):]
        disp = fit(" " + title + " ", min(len(title) + 2, inner))
        fill = inner - len(disp)
        lpad, rpad = fill // 2, fill - fill // 2
        tstyle = "class:title-act" if active else "class:title"
        lines.append([(bd, TL + H * lpad), (tstyle, disp), (bd, H * rpad + TR)])

        # header
        lines.append([(bd, V), ("class:header", fit(" Name", inner)), (bd, V)])

        # file rows
        for row in range(list_h):
            i = p.scroll + row
            if i < len(p.entries):
                name, is_dir = p.entries[i]
                text = fit(" " + name, inner)
                if active and i == p.selected:
                    style = "class:sel"
                else:
                    style = "class:dir" if is_dir else "class:file"
            else:
                style, text = "class:file", " " * inner
            lines.append([(bd, V), (style, text), (bd, V)])

        # divider + info line (selected item's name + date/time)
        lines.append([(bd, DL + DH * inner + DR)])
        lines.append([(bd, V), ("class:info", self._info(p, inner)), (bd, V)])
        # bottom border
        lines.append([(bd, BL + H * inner + BR)])
        return lines

    def _info(self, p: Panel, inner: int) -> str:
        cur = p.current
        if not cur:
            return " " * inner
        name, is_dir = cur
        right = ""
        try:
            st = os.stat(os.path.join(p.path, name))
            right = datetime.fromtimestamp(st.st_mtime).strftime("%d.%m.%y %H:%M")
        except OSError:
            pass
        left = "▶UP--DIR◀" if name == ".." else name
        field_w = inner - 2 - len(right) - 1
        return " " + fit(left, field_w) + " " + right + " "

    def _menubar(self, width: int) -> Fragments:
        items = ["Left", "Files", "Commands", "Options", "Right"]
        frags: Fragments = []
        used = 0
        for name in items:
            seg = " " + name + " "
            frags.append(("class:menu", seg))
            used += len(seg)
        clock = datetime.now().strftime("%H:%M")
        fill = max(0, width - used - len(clock) - 1)
        frags.append(("class:menu", " " * fill))
        frags.append(("class:menu", clock + " "))
        return frags

    def _cmdline(self, width: int) -> Fragments:
        prompt = self.panels[self.active].path + ">"
        return [("class:cmdline", fit(prompt, width))]

    def _fkeybar(self, width: int) -> Fragments:
        labels = [
            ("1", "Help"), ("2", "Menu"), ("3", "View"), ("4", "Edit"),
            ("5", "Copy"), ("6", "RenMov"), ("7", "Mkdir"), ("8", "Delete"),
            ("9", "PullDn"), ("10", "Quit"),
        ]
        n = len(labels)
        edge_gap = 1   # black gap before each cell + one trailing (n+1 narrow cells)
        num_gap = 2    # black spaces between the number and its cyan box
        nums_len = sum(len(num) for num, _ in labels)
        # remaining width is split equally across the 10 cyan label boxes
        fixed = nums_len + n * num_gap + (n + 1) * edge_gap
        box_w = max(1, (width - fixed) // n)
        rem = max(0, width - fixed - box_w * n)  # leftover columns → widen first boxes

        frags: Fragments = []
        for i, (num, label) in enumerate(labels):
            w = box_w + (1 if i < rem else 0)
            frags.append(("class:fkey-gap", " " * edge_gap))   # kick off the number
            frags.append(("class:fkey-num", num))
            frags.append(("class:fkey-gap", " " * num_gap))    # two before the box
            frags.append(("class:fkey-label", fit(" " + label, w)))
        frags.append(("class:fkey-gap", " " * edge_gap))       # trailing narrow cell
        return frags

    # ── app wiring ─────────────────────────────────────────────────────────
    def _build_app(self) -> Application:
        style = Style.from_dict({
            "menu": "bg:#00aaaa #000000",
            "border": "bg:#0000aa #00cccc",
            "border-act": "bg:#0000aa #ffffff bold",
            "title": "bg:#0000aa #00cccc",
            "title-act": "bg:#00aaaa #000000 bold",
            "header": "bg:#0000aa #ffff55 bold",
            "file": "bg:#0000aa #00cccc",
            "dir": "bg:#0000aa #ffffff bold",
            "sel": "bg:#00aaaa #000000 bold",
            "info": "bg:#0000aa #00cccc",
            "cmdline": "bg:#000000 #cccccc",
            "fkey-num": "bg:#000000 #ffffff",
            "fkey-label": "bg:#00aaaa #000000",
            "fkey-gap": "bg:#000000",
        })
        return Application(
            layout=Layout(Window(content=VCControl(self))),
            key_bindings=self._keys(),
            style=style,
            full_screen=True,
            mouse_support=False,
            refresh_interval=1.0,  # tick the clock
        )

    def _keys(self) -> KeyBindings:
        kb = KeyBindings()
        panel = lambda: self.panels[self.active]

        @kb.add("tab")
        def _(e): self.active ^= 1

        @kb.add("up")
        def _(e): panel().move(-1)

        @kb.add("down")
        def _(e): panel().move(1)

        @kb.add("pageup")
        def _(e): panel().move(-10)

        @kb.add("pagedown")
        def _(e): panel().move(10)

        @kb.add("home")
        def _(e): panel().selected = 0

        @kb.add("end")
        def _(e): panel().selected = max(0, len(panel().entries) - 1)

        @kb.add("enter")
        def _(e): panel().enter()

        @kb.add("q")
        @kb.add("c-q")
        @kb.add("f10")
        @kb.add("c-c")
        def _(e): e.app.exit()

        return kb

    def run(self) -> None:
        self.app.run()


def main() -> None:
    left = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    right = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~")
    VolkovData(left, right).run()


if __name__ == "__main__":
    main()
