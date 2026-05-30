#!/usr/bin/env python3
"""
Volkov Data — minimal two-pane file manager prototype.

A first, deliberately tiny step: two panels, Tab to switch, arrows + Enter to
navigate the local filesystem. No file operations wired yet — the goal is just
to get the Volkov Commander shape on screen so we can decide what to build next.

Run:  python3 volkov_data.py [left_dir] [right_dir]
Quit: F10 / q / Ctrl-Q
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, VSplit, Window, WindowAlign
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.data_structures import Point
from prompt_toolkit.styles import Style


@dataclass
class Panel:
    """One file-browser pane: a directory and a cursor over its entries."""

    path: str
    selected: int = 0
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
        target = os.path.abspath(os.path.join(self.path, name))
        self.path = target
        self.load()
        # When stepping up, land the cursor on the directory we came from.
        if name == "..":
            for i, (n, _) in enumerate(self.entries):
                if n == prev:
                    self.selected = i
                    break


class VolkovData:
    def __init__(self, left: str, right: str):
        self.panels = [Panel(left), Panel(right)]
        self.active = 0
        for p in self.panels:
            p.load()
        self.app = self._build_app()

    # ── rendering ─────────────────────────────────────────────────────────
    def _panel_text(self, idx: int):
        panel = self.panels[idx]
        is_active = idx == self.active
        lines = []
        for i, (name, is_dir) in enumerate(panel.entries):
            label = name + "/" if is_dir and name != ".." else name
            if i == panel.selected:
                style = "class:sel" if is_active else "class:sel-inactive"
            else:
                style = "class:dir" if is_dir else ""
            lines.append((style, label.ljust(40)[:40] + "\n"))
        return lines or [("", "<empty>\n")]

    def _panel_cursor(self, idx: int) -> Point:
        return Point(x=0, y=self.panels[idx].selected)

    def _title_text(self, idx: int):
        panel = self.panels[idx]
        style = "class:title-active" if idx == self.active else "class:title"
        path = panel.path
        return [(style, (" " + path).ljust(42)[:42])]

    def _statusbar(self):
        cur = self.panels[self.active].current
        info = ""
        if cur:
            name, is_dir = cur
            full = os.path.join(self.panels[self.active].path, name)
            kind = "DIR" if is_dir else "FILE"
            try:
                size = "" if is_dir else f"  {os.path.getsize(full)} B"
            except OSError:
                size = ""
            info = f"  {kind}  {name}{size}"
        return [("class:status", info.ljust(90)[:90])]

    def _funcbar(self):
        keys = [
            ("1", "Help"), ("2", "Menu"), ("3", "View"), ("4", "Edit"),
            ("5", "Copy"), ("6", "Move"), ("7", "MkDir"), ("8", "Del"),
            ("9", "PullDn"), ("10", "Quit"),
        ]
        out = []
        for num, label in keys:
            out.append(("class:fkey-num", num))
            out.append(("class:fkey-label", label.ljust(7)))
        return out

    # ── app ───────────────────────────────────────────────────────────────
    def _build_app(self) -> Application:
        def panel_window(idx: int) -> HSplit:
            return HSplit([
                Window(
                    content=FormattedTextControl(lambda i=idx: self._title_text(i)),
                    height=1,
                ),
                Window(
                    content=FormattedTextControl(
                        lambda i=idx: self._panel_text(i),
                        get_cursor_position=lambda i=idx: self._panel_cursor(i),
                    ),
                    wrap_lines=False,
                ),
            ])

        body = VSplit([
            panel_window(0),
            Window(width=1, char="│"),  # vertical separator
            panel_window(1),
        ])

        root = HSplit([
            Window(
                content=FormattedTextControl(
                    [("class:header", " Volkov Data — two-pane prototype")]
                ),
                height=1,
                align=WindowAlign.LEFT,
            ),
            body,
            Window(content=FormattedTextControl(self._statusbar), height=1),
            Window(content=FormattedTextControl(self._funcbar), height=1),
        ])

        style = Style.from_dict({
            "header": "bg:#000080 #ffffff bold",
            "title": "#888888",
            "title-active": "#ffffff bold",
            "dir": "#ffffff bold",
            "sel": "reverse",
            "sel-inactive": "#444444 bg:#aaaaaa",
            "status": "bg:#000080 #ffffff",
            "fkey-num": "#ffffff",
            "fkey-label": "bg:#008080 #ffffff",
        })

        return Application(
            layout=Layout(root),
            key_bindings=self._keys(),
            style=style,
            full_screen=True,
            mouse_support=False,
        )

    def _keys(self) -> KeyBindings:
        kb = KeyBindings()
        panel = lambda: self.panels[self.active]

        @kb.add("tab")
        def _(event):
            self.active ^= 1

        @kb.add("up")
        def _(event):
            panel().move(-1)

        @kb.add("down")
        def _(event):
            panel().move(1)

        @kb.add("pageup")
        def _(event):
            panel().move(-10)

        @kb.add("pagedown")
        def _(event):
            panel().move(10)

        @kb.add("home")
        def _(event):
            panel().selected = 0

        @kb.add("end")
        def _(event):
            panel().selected = max(0, len(panel().entries) - 1)

        @kb.add("enter")
        def _(event):
            panel().enter()

        @kb.add("q")
        @kb.add("c-q")
        @kb.add("f10")
        @kb.add("c-c")
        def _(event):
            event.app.exit()

        return kb

    def run(self) -> None:
        self.app.run()


def main() -> None:
    left = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    right = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~")
    VolkovData(left, right).run()


if __name__ == "__main__":
    main()
