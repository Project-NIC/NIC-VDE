#!/usr/bin/env python3
"""
Volkov Data — two-pane file manager (Volkov Commander look).

A thin prompt_toolkit shell over volkov_core: two bordered panels browse storage
backends (the local filesystem, or the records inside an .mla container). All the
real logic lives in volkov_core/, so it can be reused headless.

Keys
  Tab            switch active panel
  ↑/↓ PgUp/PgDn Home/End   move cursor
  Enter          open dir / step into .mla / go up via ".."
  F1 Info        details about the selected item
  F2 Info        (record info inside MLA — same as F1 there)
  F3 View        view file / record payload (text or hex), ESC to close
  F5 Copy        copy selected file to the other panel
  F6 RenMov      rename the selected item
  F7 Mkdir       create a directory
  F8 Delete      delete the selected item (with confirmation)
  F9 Menu        pull-down menu bar (←/→ switch column, ↑/↓ move, Enter pick)
                 Left/Right → sort the panel (Name/Extension/Time/Size/…),
                 Files → the F-key actions, Commands → swap/re-read, …
  F10/q/Ctrl-Q   quit
  Esc            close any overlay/dialog (or the open menu)

Run:  python3 volkov_data.py [left_dir] [right_dir]
"""
from __future__ import annotations

import os
import shutil
import sys
from datetime import datetime

from prompt_toolkit import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import UIContent, UIControl
from prompt_toolkit.styles import Style

import volkov_core as vc

# Box-drawing (double frame + single divider tee)
TL, TR, BL, BR, H, V = "╔", "╗", "╚", "╝", "═", "║"
DL, DR, DH = "╟", "╢", "─"

Fragments = list[tuple[str, str]]


def fit(s: str, w) -> str:
    """Truncate or right-pad a string to exactly ``w`` columns."""
    w = int(w)
    if w <= 0:
        return ""
    return s[:w] if len(s) >= w else s + " " * (w - len(s))


class Panel:
    """One pane: a storage backend + a cursor over its entries."""

    # sort keys for each mode (".." and group order are handled separately)
    SORT_KEYS = {
        "name": lambda e: e.name.lower(),
        "ext": lambda e: (os.path.splitext(e.name)[1].lower(), e.name.lower()),
        "time": lambda e: e.mtime or 0,
        "size": lambda e: e.size,
    }

    def __init__(self, backend: vc.Backend):
        self.backend = backend
        self.entries: list[vc.Entry] = []
        self.selected = 0
        self.scroll = 0
        self.error = ""
        self.sort_mode = "name"     # name | ext | time | size | unsorted
        self.sort_reverse = False
        self.reload()

    def reload(self) -> None:
        try:
            self.entries = self._sorted(self.backend.list())
            self.error = ""
        except vc.BackendError as exc:
            self.entries = [vc.Entry("..", True, kind="updir")]
            self.error = str(exc)
        self.selected = max(0, min(self.selected, len(self.entries) - 1))
        self.scroll = 0

    def set_sort(self, mode: str) -> None:
        """Switch sort mode, remembering which entry the cursor is on."""
        keep = self.current.name if self.current else None
        self.sort_mode = mode
        self.reload()
        if keep is not None:  # try to keep the cursor on the same item
            for i, e in enumerate(self.entries):
                if e.name == keep:
                    self.selected = i
                    break

    def _sorted(self, entries: list[vc.Entry]) -> list[vc.Entry]:
        """Order entries per the panel's sort mode, '..' first, dirs before files."""
        updir = [e for e in entries if e.name == ".."]
        rest = [e for e in entries if e.name != ".."]
        if self.sort_mode != "unsorted":
            key = self.SORT_KEYS.get(self.sort_mode, self.SORT_KEYS["name"])
            rest.sort(key=lambda e: (0 if e.is_container else 1, key(e)))
        if self.sort_reverse:
            # flip within each group so dirs stay on top (VC behaviour)
            cont = [e for e in rest if e.is_container][::-1]
            files = [e for e in rest if not e.is_container][::-1]
            rest = cont + files
        return updir + rest

    @property
    def current(self) -> vc.Entry | None:
        if 0 <= self.selected < len(self.entries):
            return self.entries[self.selected]
        return None

    def move(self, delta: int) -> None:
        if self.entries:
            self.selected = max(0, min(self.selected + delta, len(self.entries) - 1))

    def enter(self) -> None:
        cur = self.current
        if cur is None or not cur.is_container:
            return
        try:
            nxt = self.backend.enter(cur)
        except vc.BackendError as exc:
            self.error = str(exc)
            return
        if nxt is None:
            return
        prev_label = self.backend.label
        if nxt is not self.backend:
            self.backend.close()
        self.backend = nxt
        self.reload()
        if cur.name == "..":  # land on the item we came from
            for i, e in enumerate(self.entries):
                if e.name == prev_label:
                    self.selected = i
                    break


class VCControl(UIControl):
    """Draws the whole VC screen; learns the terminal size in create_content."""

    def __init__(self, app: "VolkovData"):
        self.app = app

    def create_content(self, width: int, height: int) -> UIContent:
        lines = self.app.render(width, height)
        return UIContent(get_line=lambda i: lines[i],
                         line_count=len(lines), show_cursor=False)


class VolkovData:
    def __init__(self, left: str, right: str):
        self.panels = [Panel(vc.LocalBackend(left)), Panel(vc.LocalBackend(right))]
        self.active = 0
        # overlay state: None | ("info", rows) | ("view", title, lines, scroll)
        #                | ("input", title, prompt, buffer, action)
        #                | ("confirm", title, message, action)
        #                | ("message", title, message)
        self.overlay = None
        # pull-down menu state: None when closed, else [col, row] of the cursor
        self.menu = None
        self.app = self._build_app()

    # ── pull-down menu (top bar) ───────────────────────────────────────────
    MENU_TITLES = ["Left", "Files", "Commands", "Options", "Right"]

    def _menu_items(self, col: int) -> list:
        """Items for one top-bar column: (label, action|None) or None=separator.

        A None action means "recognised but not implemented yet" — it still
        shows in the menu (greyed) so the layout matches real VC.
        """
        if col in (0, 4):  # Left / Right — operate on that panel
            pi = 0 if col == 0 else 1
            p = self.panels[pi]
            tick = lambda m: "• " if p.sort_mode == m else "  "
            return [
                (tick("name") + "Name", lambda: self.panels[pi].set_sort("name")),
                (tick("ext") + "Extension", lambda: self.panels[pi].set_sort("ext")),
                (tick("time") + "Time", lambda: self.panels[pi].set_sort("time")),
                (tick("size") + "Size", lambda: self.panels[pi].set_sort("size")),
                (tick("unsorted") + "Unsorted", lambda: self.panels[pi].set_sort("unsorted")),
                None,
                (("• " if p.sort_reverse else "  ") + "Reverse", lambda: self._toggle_reverse(pi)),
                None,
                ("  Re-read", lambda: self.panels[pi].reload()),
            ]
        if col == 1:  # Files — the F-key actions
            return [
                ("Info", self._do_info),
                ("Repair / check", self._do_repair),
                ("View", self._do_view),
                ("Values", lambda: self._do_view(with_values=True)),
                ("Copy", self._do_copy),
                ("Rename or move", self._do_f6),
                ("Make directory", self._do_mkdir),
                ("Delete", self._do_delete),
                None,
                ("Quit", lambda: self.app.exit()),
            ]
        if col == 2:  # Commands
            return [
                ("Swap panels", self._swap_panels),
                ("Re-read both", self._reread_both),
                None,
                ("Find file", None),
                ("History", None),
                ("Compare directories", None),
            ]
        # col == 3 — Options (not wired yet, shown for parity with VC)
        return [
            ("General...", None),
            ("Interface...", None),
            ("Panels...", None),
            None,
            ("Save setup", None),
        ]

    def _toggle_reverse(self, pi: int) -> None:
        self.panels[pi].sort_reverse = not self.panels[pi].sort_reverse
        self.panels[pi].reload()

    def _swap_panels(self) -> None:
        self.panels.reverse()
        self.active ^= 1

    def _reread_both(self) -> None:
        for p in self.panels:
            p.reload()

    def _open_menu(self) -> None:
        self.menu = [self.active * 4, 0]  # Left bar over left panel, Right over right

    def _menu_move_col(self, d: int) -> None:
        self.menu[0] = (self.menu[0] + d) % len(self.MENU_TITLES)
        self.menu[1] = 0

    def _menu_move_row(self, d: int) -> None:
        items = self._menu_items(self.menu[0])
        r = self.menu[1]
        for _ in range(len(items)):           # step over separators
            r = (r + d) % len(items)
            if items[r] is not None:
                break
        self.menu[1] = r

    def _menu_activate(self) -> None:
        items = self._menu_items(self.menu[0])
        row = items[self.menu[1]] if 0 <= self.menu[1] < len(items) else None
        self.menu = None
        if row is None:
            return
        _label, action = row
        if action is None:
            self.overlay = ("message", "Menu", "This command is not implemented yet.")
        else:
            action()

    @property
    def panel(self) -> Panel:
        return self.panels[self.active]

    @property
    def other(self) -> Panel:
        return self.panels[self.active ^ 1]

    # minimum usable terminal size (below this the layout can't be drawn)
    MIN_W = 100
    MIN_H = 15

    # ── full-screen render ─────────────────────────────────────────────────
    def render(self, width: int, height: int) -> list[Fragments]:
        if width < self.MIN_W or height < self.MIN_H:
            return self._too_small(width, height)
        panels_h = max(6, height - 3)
        lw = width // 2
        rw = width - lw
        left = self._panel(self.panels[0], self.active == 0, lw, panels_h)
        right = self._panel(self.panels[1], self.active == 1, rw, panels_h)
        body = [l + r for l, r in zip(left, right)]
        screen = [self._menubar(width), *body,
                  self._cmdline(width), self._fkeybar(width)]
        if self.menu is not None:
            self._draw_menu(screen, width)
        if self.overlay:
            self._draw_overlay(screen, width, height)
        return screen

    def _too_small(self, width: int, height: int) -> list[Fragments]:
        """Shown when the terminal is below the minimum usable size."""
        msg = f"Terminal too small — need at least {self.MIN_W}x{self.MIN_H}"
        rows: list[Fragments] = []
        for y in range(max(1, height)):
            if y == height // 2:
                rows.append([("class:cmdline", fit(msg.center(width), width))])
            else:
                rows.append([("class:cmdline", " " * max(0, width))])
        return rows

    def _panel(self, p: Panel, active: bool, w: int, h: int) -> list[Fragments]:
        inner = w - 2
        list_h = h - 5
        if p.selected < p.scroll:
            p.scroll = p.selected
        elif p.selected >= p.scroll + list_h:
            p.scroll = p.selected - list_h + 1
        p.scroll = max(0, min(p.scroll, max(0, len(p.entries) - list_h)))

        bd = "class:border-act" if active else "class:border"
        lines: list[Fragments] = []

        title = p.backend.location
        if len(title) > inner - 4:
            title = "…" + title[-(inner - 5):]
        disp = fit(" " + title + " ", min(len(title) + 2, inner))
        fill = inner - len(disp)
        lp, rp = fill // 2, fill - fill // 2
        ts = "class:title-act" if active else "class:title"
        lines.append([(bd, TL + H * lp), (ts, disp), (bd, H * rp + TR)])
        lines.append([(bd, V), ("class:header", fit(" Name", inner)), (bd, V)])

        for row in range(list_h):
            i = p.scroll + row
            if i < len(p.entries):
                e = p.entries[i]
                mark = "/" if e.is_container and e.name != ".." else ""
                text = fit(" " + e.name + mark, inner)
                if active and i == p.selected:
                    style = "class:sel"
                elif e.kind == "record" and not e.meta.get("healthy", True):
                    style = "class:bad"   # damaged record flagged with '*'
                elif e.kind in ("dir", "updir", "mla"):
                    style = "class:dir"
                elif e.kind == "record":
                    style = "class:rec"
                else:
                    style = "class:file"
            else:
                style, text = "class:file", " " * inner
            lines.append([(bd, V), (style, text), (bd, V)])

        lines.append([(bd, DL + DH * inner + DR)])
        lines.append([(bd, V), ("class:info", self._info_line(p, inner)), (bd, V)])
        lines.append([(bd, BL + H * inner + BR)])
        return lines

    def _info_line(self, p: Panel, inner: int) -> str:
        if p.error:
            return fit(" ! " + p.error, inner)
        cur = p.current
        if not cur:
            return " " * inner
        right = ""
        if cur.mtime:
            right = datetime.fromtimestamp(cur.mtime).strftime("%d.%m.%y %H:%M")
        left = "▶UP--DIR◀" if cur.name == ".." else cur.name
        field_w = inner - 2 - len(right) - 1
        return " " + fit(left, field_w) + " " + right + " "

    def _menu_col_x(self) -> list[int]:
        """Left column where each top-bar title's box starts (for the dropdown)."""
        xs, used = [], 0
        for name in self.MENU_TITLES:
            xs.append(used)
            used += len(" " + name + " ")
        return xs

    def _menubar(self, width: int) -> Fragments:
        frags: Fragments = []
        used = 0
        for i, name in enumerate(self.MENU_TITLES):
            seg = " " + name + " "
            opened = self.menu is not None and self.menu[0] == i
            frags.append(("class:menu-sel" if opened else "class:menu", seg))
            used += len(seg)
        clock = datetime.now().strftime("%H:%M")
        frags.append(("class:menu", " " * max(0, width - used - len(clock) - 1)))
        frags.append(("class:menu", clock + " "))
        return frags

    def _cmdline(self, width: int) -> Fragments:
        return [("class:cmdline", fit(self.panel.backend.location + ">", width))]

    def _fkeybar(self, width: int) -> Fragments:
        labels = [("1", "Info"), ("2", "Repair"), ("3", "View"), ("4", "Values"),
                  ("5", "Copy"), ("6", "CSV/Mv"), ("7", "Mkdir"), ("8", "Delete"),
                  ("9", "Menu"), ("10", "Quit")]
        n = len(labels)
        edge_gap, num_gap = 2, 1
        nums_len = sum(len(num) for num, _ in labels)
        fixed = nums_len + n * num_gap + (n + 1) * edge_gap
        box_w = max(1, (width - fixed) // n)  # every label box is the SAME width
        rem = max(0, width - fixed - box_w * n)  # leftover columns → trailing gap
        frags: Fragments = []
        for num, label in labels:
            frags.append(("class:fkey-gap", " " * edge_gap))
            frags.append(("class:fkey-num", num))
            frags.append(("class:fkey-gap", " " * num_gap))
            frags.append(("class:fkey-label", fit(" " + label, box_w)))
        frags.append(("class:fkey-gap", " " * (edge_gap + rem)))  # absorb remainder
        return frags

    # ── pull-down menu drawing ──────────────────────────────────────────────
    def _draw_menu(self, screen: list[Fragments], width: int) -> None:
        col, cursor = self.menu
        items = self._menu_items(col)
        labels = [("─" if it is None else it[0]) for it in items]
        bw = max(len(s) for s in labels) + 2
        x = self._menu_col_x()[col]
        if x + bw + 2 >= width:            # keep the box on-screen
            x = max(0, width - bw - 2)
        bd = "class:menu-border"
        self._overlay_row(screen, 1, x, [(bd, TL + H * bw + TR)], width)
        for j, it in enumerate(items):
            if it is None:
                self._overlay_row(screen, 2 + j, x,
                                  [(bd, DL + DH * bw + DR)], width)
                continue
            st = "class:menu-item-sel" if j == cursor else "class:menu-item"
            self._overlay_row(screen, 2 + j, x,
                              [(bd, V), (st, fit(" " + it[0], bw)), (bd, V)], width)
        self._overlay_row(screen, 2 + len(items), x,
                          [(bd, BL + H * bw + BR)], width)
        # drop shadow under the box
        sh = "class:shadow"
        for r in range(2, 3 + len(items)):
            if x + bw + 2 < width:
                self._overlay_row(screen, r, x + bw + 2, [(sh, " ")], width)

    # ── overlays ────────────────────────────────────────────────────────────
    def _draw_overlay(self, screen: list[Fragments], width: int, height: int) -> None:
        kind = self.overlay[0]
        if kind == "view":
            box_lines = self._render_view(width, height)
            for i, ln in enumerate(box_lines):
                if i < len(screen):
                    screen[i] = ln
            return
        # centered dialog box for info/input/confirm/message
        body = self._dialog_body()
        bw = min(width - 4, max(40, max((len(s) for s, _ in body), default=40) + 4))
        bh = len(body) + 2
        top = max(0, (height - bh) // 2)
        left = max(0, (width - bw) // 2)
        bd = "class:dlg-border"
        # top border with title
        title = " " + self.overlay[1] + " "
        fillw = bw - 2 - len(title)
        lp = fillw // 2
        self._overlay_row(screen, top, left,
                          [(bd, TL + H * lp), ("class:dlg-title", title),
                           (bd, H * (fillw - lp) + TR)], width)
        for j, (text, st) in enumerate(body):
            self._overlay_row(screen, top + 1 + j, left,
                              [(bd, V), (st, fit(" " + text, bw - 2)), (bd, V)], width)
        self._overlay_row(screen, top + bh - 1, left,
                          [(bd, BL + H * (bw - 2) + BR)], width)
        # drop shadow: one column down the right edge + a row under the box
        sh = "class:shadow"
        for r in range(1, bh):
            if left + bw < width:
                self._overlay_row(screen, top + r, left + bw, [(sh, " ")], width)
        self._overlay_row(screen, top + bh, left + 1, [(sh, " " * bw)], width)

    def _dialog_body(self) -> list[tuple[str, str]]:
        kind = self.overlay[0]
        if kind == "info":
            rows = self.overlay[2]
            return [(f"{k}: {v}", "class:dlg") for k, v in rows] + \
                   [("", "class:dlg"), ("[ Esc / Enter to close ]", "class:dlg-dim")]
        if kind == "input":
            _, _title, prompt, buf, _action = self.overlay
            return [(prompt, "class:dlg"),
                    ("> " + buf + "_", "class:dlg-edit"),
                    ("", "class:dlg"),
                    ("[ Enter = OK   Esc = Cancel ]", "class:dlg-dim")]
        if kind == "confirm":
            body = [(ln, "class:dlg") for ln in self.overlay[2].split("\n")]
            return body + [("", "class:dlg"),
                           ("[ Y = Yes   N / Esc = No ]", "class:dlg-dim")]
        if kind == "message":
            body = [(ln, "class:dlg") for ln in self.overlay[2].split("\n")]
            return body + [("", "class:dlg"),
                           ("[ Esc / Enter to close ]", "class:dlg-dim")]
        return []

    def _overlay_row(self, screen, y, x, frags, width) -> None:
        if not (0 <= y < len(screen)):
            return
        w = sum(len(t) for _, t in frags)
        # build the row: keep left part of original, place box, keep right part
        left_part = self._slice(screen[y], 0, x)
        right_part = self._slice(screen[y], x + w, width)
        screen[y] = left_part + frags + right_part

    @staticmethod
    def _slice(frags: Fragments, start: int, end: int) -> Fragments:
        """Return the sub-fragments covering columns [start, end)."""
        out: Fragments = []
        col = 0
        for style, text in frags:
            seg_start, seg_end = col, col + len(text)
            col = seg_end
            a, b = max(seg_start, start), min(seg_end, end)
            if a < b:
                out.append((style, text[a - seg_start:b - seg_start]))
        # pad if the row was shorter than 'end'
        if col < end and start <= col:
            out.append(("", " " * (end - max(col, start))))
        return out

    def _render_view(self, width: int, height: int) -> list[Fragments]:
        _, title, vlines, scroll = self.overlay
        bd = "class:view-border"
        out: list[Fragments] = []
        t = " " + title + " "
        fillw = width - 2 - len(t)
        out.append([(bd, TL + H * (fillw // 2)), ("class:view-title", t),
                    (bd, H * (fillw - fillw // 2) + TR)])
        view_h = height - 2
        for i in range(view_h):
            idx = scroll + i
            line = vlines[idx] if idx < len(vlines) else ""
            out.append([(bd, V), ("class:view", fit(" " + line, width - 2)), (bd, V)])
        hint = " ↑/↓ PgUp/PgDn scroll   Esc close "
        fillw = width - 2 - len(hint)
        out.append([(bd, BL + H * (fillw // 2)), ("class:view-title", hint),
                    (bd, H * (fillw - fillw // 2) + BR)])
        return out

    # ── actions ───────────────────────────────────────────────────────────
    def _do_view(self, with_values: bool = False) -> None:
        cur = self.panel.current
        if cur is None or (cur.is_container and cur.kind != "mla"):
            return
        try:
            data = self.panel.backend.read(cur)
        except vc.BackendError as exc:
            self.overlay = ("message", "Error", str(exc))
            return
        lines: list[str] = []
        if cur.kind == "record":  # show the log metadata (time/station/…) above the payload
            try:
                for k, v in self.panel.backend.info(cur):
                    lines.append(f"{k}: {v}")
            except vc.BackendError:
                pass
            if with_values:  # F4: decoded value via the conversion table
                try:
                    lines.append(f"Value: {self.panel.backend.decode_value(cur)}")
                except Exception:
                    pass
            lines.append("─" * 40)
        lines += self._format_view(data)
        title = ("Values: " if with_values else "") + cur.name
        self.overlay = ("view", title, lines, 0)

    @staticmethod
    def _format_view(data: bytes) -> list[str]:
        # text if mostly printable, else hex dump
        sample = data[:4096]
        printable = sum(1 for b in sample if 9 <= b <= 13 or 32 <= b <= 126)
        if sample and printable / len(sample) > 0.85:
            try:
                return data.decode("utf-8", "replace").splitlines() or ["<empty>"]
            except Exception:
                pass
        lines = []
        for off in range(0, len(data), 16):
            chunk = data[off:off + 16]
            hexs = " ".join(f"{b:02x}" for b in chunk)
            text = "".join(chr(b) if 32 <= b <= 126 else "." for b in chunk)
            lines.append(f"{off:08x}  {hexs:<47}  {text}")
        return lines or ["<empty>"]

    def _do_info(self) -> None:
        cur = self.panel.current
        if cur is None:
            return
        try:
            rows = self.panel.backend.info(cur)
        except vc.BackendError as exc:
            self.overlay = ("message", "Error", str(exc))
            return
        self.overlay = ("info", "Info", rows)

    def _do_repair(self) -> None:
        """F2: check an MLA container and report damaged slots.

        Works both inside a container (check the open file) and from the
        filesystem with the cursor on an .mla file (check it in place).
        On anything that isn't an MLA there is nothing to repair → show info.
        """
        be = self.panel.backend
        if isinstance(be, vc.MlaBackend):
            self.overlay = ("info", "Repair / check", be.repair_info())
            return
        cur = self.panel.current
        if cur is not None and cur.kind == "mla":
            try:
                probe = be.enter(cur)  # opens the .mla as a fresh MlaBackend
                rows = probe.repair_info()
                probe.close()
            except (vc.BackendError, AttributeError) as exc:
                self.overlay = ("message", "Error", str(exc))
                return
            self.overlay = ("info", "Repair / check", rows)
            return
        self._do_info()

    def _do_f6(self) -> None:
        """F6: inside MLA → export whole container to CSV; else → rename."""
        be = self.panel.backend
        if isinstance(be, vc.MlaBackend):
            name = be.csv_name()
            exists = self.other.backend.exists(name)
            msg = (f"Export all records to CSV\n  to  {self.other.backend.location}"
                   f"\n  as  '{name}'")
            if exists:
                msg += f"\n\n! '{name}' exists and will be OVERWRITTEN."
            self.overlay = ("confirm", "Export CSV", msg, "csv")
        else:
            self._do_rename()

    def _do_mkdir(self) -> None:
        self.overlay = ("input", "Make directory", "New directory name:", "", "mkdir")

    def _do_rename(self) -> None:
        cur = self.panel.current
        if cur is None or cur.name == "..":
            return
        # VC "RenMov": default destination is the other panel, so a bare Enter
        # MOVES the file there; edit it down to just a name to rename in place.
        if isinstance(self.other.backend, vc.LocalBackend):
            dest = os.path.join(self.other.backend.location, cur.name)
        else:
            dest = cur.name
        self.overlay = ("input", "Rename / move", "New name or path:", dest, "renmov")

    def _renmov(self, dest: str) -> None:
        """F6: a bare name renames in place; a path moves to the host filesystem."""
        be = self.panel.backend
        cur = self.panel.current
        if cur is None or cur.name == "..":
            return
        has_path = os.sep in dest or (os.altsep and os.altsep in dest)
        if not has_path:
            be.rename(cur, dest)            # no path → plain rename
            self.panel.reload()
            return
        if not isinstance(be, vc.LocalBackend):
            raise vc.BackendError("Move is only supported on the filesystem.")
        src = os.path.join(be.location, cur.name)
        dst = os.path.join(dest, cur.name) if os.path.isdir(dest) else dest
        try:
            shutil.move(src, dst)
        except OSError as exc:
            raise vc.BackendError(f"move failed: {exc}") from exc
        self.panel.reload()
        self.other.reload()

    def _do_delete(self) -> None:
        cur = self.panel.current
        if cur is None or cur.name == "..":
            return
        self.overlay = ("confirm", "Delete", f"Delete '{cur.name}' ?", "delete")

    def _copyable(self, e) -> bool:
        """A leaf file, or an .mla (enterable but still a real file on disk)."""
        return e is not None and e.name != ".." and (not e.is_container or e.kind == "mla")

    def _do_copy(self) -> None:
        cur = self.panel.current
        if not self._copyable(cur):
            self.overlay = ("message", "Copy", "Select a file to copy.")
            return
        dest_name = cur.meta.get("export_name", cur.name)
        # VC-style copy dialog: confirm source → destination before doing it
        msg = f"Copy  '{cur.name}'\n  to  {self.other.backend.location}\n  as  '{dest_name}'"
        if self.other.backend.exists(dest_name):
            msg += f"\n\n! '{dest_name}' already exists and will be OVERWRITTEN."
        self.overlay = ("confirm", "Copy", msg, "copy")

    def _copy_now(self) -> None:
        """Perform the actual copy of the selected item to the other panel."""
        cur = self.panel.current
        if cur is None:
            return
        dest_name = cur.meta.get("export_name", cur.name)
        try:
            data = self.panel.backend.read(cur)
            self.other.backend.put_file(dest_name, data)
            self.other.reload()
        except vc.BackendError as exc:
            self.overlay = ("message", "Error", str(exc))
            return
        self.overlay = None  # silent success — panel just refreshes

    def _commit_input(self) -> None:
        _, _t, _p, buf, action = self.overlay
        buf = buf.strip()
        self.overlay = None
        if not buf:
            return
        try:
            if action == "mkdir":
                self.panel.backend.mkdir(buf)
                self.panel.reload()
            elif action == "renmov":
                self._renmov(buf)
        except vc.BackendError as exc:
            self.overlay = ("message", "Error", str(exc))

    def _commit_confirm(self) -> None:
        action = self.overlay[3]
        self.overlay = None
        try:
            if action == "delete":
                self.panel.backend.delete(self.panel.current)
                self.panel.reload()
            elif action == "copy":
                self._copy_now()
            elif action == "csv":
                be = self.panel.backend
                self.other.backend.put_file(be.csv_name(), be.to_csv())
                self.other.reload()
        except vc.BackendError as exc:
            self.overlay = ("message", "Error", str(exc))

    # ── app wiring ─────────────────────────────────────────────────────────
    def _build_app(self) -> Application:
        style = Style.from_dict({
            "menu": "bg:#00aaaa #000000",
            "menu-sel": "bg:#000000 #00ffff bold",
            "menu-border": "bg:#00aaaa #000000",
            "menu-item": "bg:#00aaaa #000000",
            "menu-item-sel": "bg:#000000 #00ffff bold",
            "border": "bg:#0000aa #00cccc",
            "border-act": "bg:#0000aa #ffffff bold",
            "title": "bg:#0000aa #00cccc",
            "title-act": "bg:#00aaaa #000000 bold",
            "header": "bg:#0000aa #ffff55 bold",
            "file": "bg:#0000aa #00cccc",
            "dir": "bg:#0000aa #ffffff bold",
            "rec": "bg:#0000aa #55ff55",
            "bad": "bg:#0000aa #ff5555 bold",
            "sel": "bg:#00aaaa #000000 bold",
            "info": "bg:#0000aa #00cccc",
            "cmdline": "bg:#000000 #cccccc",
            "fkey-num": "bg:#000000 #ffffff",
            "fkey-label": "bg:#00aaaa #000000",
            "fkey-gap": "bg:#000000",
            # overlays
            "dlg-border": "bg:#aaaaaa #000000",
            "dlg-title": "bg:#aaaaaa #aa0000 bold",
            "dlg": "bg:#aaaaaa #000000",
            "dlg-dim": "bg:#aaaaaa #555555",
            "dlg-edit": "bg:#000088 #ffffff",
            "shadow": "bg:#000000",
            "view-border": "bg:#0000aa #ffffff bold",
            "view-title": "bg:#0000aa #ffff55 bold",
            "view": "bg:#0000aa #cccccc",
        })
        return Application(layout=Layout(Window(content=VCControl(self))),
                           key_bindings=self._keys(), style=style,
                           full_screen=True, mouse_support=False,
                           refresh_interval=1.0)

    def _keys(self) -> KeyBindings:
        kb = KeyBindings()
        from prompt_toolkit.filters import Condition
        in_overlay = Condition(lambda: self.overlay is not None)
        in_menu = Condition(lambda: self.menu is not None)
        # "browsing": panel has focus — no overlay and no open pull-down menu
        no_overlay = ~in_overlay & ~in_menu
        typing = Condition(lambda: self.overlay is not None and self.overlay[0] == "input")
        viewing = Condition(lambda: self.overlay is not None and self.overlay[0] == "view")

        # ── navigation (only when no overlay) ──
        @kb.add("tab", filter=no_overlay)
        def _(e): self.active ^= 1

        @kb.add("up", filter=no_overlay)
        def _(e): self.panel.move(-1)

        @kb.add("down", filter=no_overlay)
        def _(e): self.panel.move(1)

        @kb.add("pageup", filter=no_overlay)
        def _(e): self.panel.move(-15)

        @kb.add("pagedown", filter=no_overlay)
        def _(e): self.panel.move(15)

        @kb.add("home", filter=no_overlay)
        def _(e): self.panel.selected = 0

        @kb.add("end", filter=no_overlay)
        def _(e): self.panel.selected = max(0, len(self.panel.entries) - 1)

        @kb.add("enter", filter=no_overlay)
        def _(e): self.panel.enter()

        # ── function keys ──
        @kb.add("f1", filter=no_overlay)
        def _(e): self._do_info()

        @kb.add("f2", filter=no_overlay)
        def _(e): self._do_repair()

        @kb.add("f3", filter=no_overlay)
        def _(e): self._do_view()

        @kb.add("f4", filter=no_overlay)
        def _(e): self._do_view(with_values=True)

        @kb.add("f5", filter=no_overlay)
        def _(e): self._do_copy()

        @kb.add("f6", filter=no_overlay)
        def _(e): self._do_f6()

        @kb.add("f7", filter=no_overlay)
        def _(e): self._do_mkdir()

        @kb.add("f8", filter=no_overlay)
        def _(e): self._do_delete()

        @kb.add("f9", filter=no_overlay)
        def _(e): self._open_menu()

        # ── pull-down menu navigation ──
        @kb.add("left", filter=in_menu)
        def _(e): self._menu_move_col(-1)

        @kb.add("right", filter=in_menu)
        def _(e): self._menu_move_col(1)

        @kb.add("up", filter=in_menu)
        def _(e): self._menu_move_row(-1)

        @kb.add("down", filter=in_menu)
        def _(e): self._menu_move_row(1)

        @kb.add("enter", filter=in_menu)
        def _(e): self._menu_activate()

        @kb.add("escape", filter=in_menu)
        def _(e): self.menu = None

        @kb.add("f9", filter=in_menu)
        def _(e): self.menu = None

        @kb.add("q", filter=no_overlay)
        @kb.add("c-q")
        @kb.add("f10", filter=no_overlay)
        def _(e): e.app.exit()

        # ── view scrolling ──
        @kb.add("up", filter=viewing)
        def _(e):
            k, t, l, s = self.overlay
            self.overlay = (k, t, l, max(0, s - 1))

        @kb.add("down", filter=viewing)
        def _(e):
            k, t, l, s = self.overlay
            self.overlay = (k, t, l, min(max(0, len(l) - 1), s + 1))

        @kb.add("pageup", filter=viewing)
        def _(e):
            k, t, l, s = self.overlay
            self.overlay = (k, t, l, max(0, s - 20))

        @kb.add("pagedown", filter=viewing)
        def _(e):
            k, t, l, s = self.overlay
            self.overlay = (k, t, l, min(max(0, len(l) - 1), s + 20))

        # ── input typing ──
        @kb.add("<any>", filter=typing)
        def _(e):
            ch = e.data
            if ch and ch.isprintable():
                k, t, p, buf, a = self.overlay
                self.overlay = (k, t, p, buf + ch, a)

        @kb.add("backspace", filter=typing)
        def _(e):
            k, t, p, buf, a = self.overlay
            self.overlay = (k, t, p, buf[:-1], a)

        # ── overlay Enter: one handler dispatching on overlay type ──
        # (prompt_toolkit runs the LAST matching binding, so keep this single.)
        @kb.add("enter", filter=in_overlay)
        def _(e):
            kind = self.overlay[0]
            if kind == "input":
                self._commit_input()
            elif kind == "confirm":
                self._commit_confirm()
            else:  # info / message / view
                self.overlay = None

        @kb.add("y", filter=Condition(
            lambda: self.overlay is not None and self.overlay[0] == "confirm"))
        def _(e): self._commit_confirm()

        @kb.add("n", filter=Condition(
            lambda: self.overlay is not None and self.overlay[0] == "confirm"))
        def _(e): self.overlay = None

        @kb.add("escape", filter=in_overlay)
        def _(e): self.overlay = None

        return kb

    def run(self) -> None:
        self.app.run()


def main() -> None:
    left = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    right = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser("~")
    VolkovData(left, right).run()


if __name__ == "__main__":
    main()
