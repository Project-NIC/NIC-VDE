"""
Storage backend abstraction for Volkov Data.

This package is **GUI-free** on purpose: it holds all the logic for browsing and
manipulating data sources (the local filesystem, an MLA container, …) so the
same core can be reused headless — e.g. for a future datalogger / remote-
management tool with no GUI on top.

A backend exposes a uniform, file-manager-like view:

    list()  -> [VdeEntry, ...]          what's "in here" (incl. ".." to go up)
    enter(entry) -> VdeBackend | None   descend into a container (dir / .mla / "..")
    read(entry)  -> bytes            raw bytes for viewing
    info(entry)  -> [(label, value)] human-readable details
    mkdir / delete / rename / put_file   mutating operations (may be unsupported)

Two backends ship today:
    VdeLocalBackend  — host filesystem (the primary path)
    VdeMlaBackend    — records inside an .mla container, browsed as if they were files
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class VdeBackendError(Exception):
    """A backend operation failed (I/O, bad format, …) — shown to the user."""


class VdeUnsupported(VdeBackendError):
    """The operation is not supported by this backend (e.g. mkdir inside MLA)."""


@dataclass
class VdeEntry:
    """One row in a panel: a child of the current backend location."""

    name: str
    is_container: bool = False          # can enter() descend into it?
    size: int = 0
    mtime: float | None = None          # unix seconds (file mtime / record time)
    kind: str = "file"                  # dir | file | mla | record | updir
    meta: dict = field(default_factory=dict)


class VdeBackend(ABC):
    """A browsable data source. Stateless w.r.t. the cursor (the GUI owns that)."""

    # ── identity ────────────────────────────────────────────────────────────
    @property
    @abstractmethod
    def location(self) -> str:
        """Human path shown in the panel frame title."""

    @property
    def label(self) -> str:
        """Last path component — used to restore the cursor when stepping up."""
        loc = self.location.replace("\\", "/").rstrip("/")
        return loc.rsplit("/", 1)[-1] if "/" in loc else loc

    # ── browsing ────────────────────────────────────────────────────────────
    @abstractmethod
    def list(self) -> list[VdeEntry]:
        """Children of this location, '..' first when going up is possible."""

    @abstractmethod
    def enter(self, entry: VdeEntry) -> "VdeBackend | None":
        """Descend into a container (or '..'); None if the entry isn't enterable."""

    def read(self, entry: VdeEntry) -> bytes:
        """Raw bytes of a leaf entry, for viewing."""
        raise VdeUnsupported("This item cannot be viewed")

    def info(self, entry: VdeEntry) -> list[tuple[str, str]]:
        """Human-readable details about an entry (label, value) pairs."""
        rows = [("Name", entry.name), ("Kind", entry.kind)]
        if not entry.is_container:
            rows.append(("Size", f"{entry.size} B"))
        return rows

    # ── mutating operations (default: unsupported) ──────────────────────────
    def mkdir(self, name: str) -> None:
        raise VdeUnsupported("Cannot create a directory here")

    def delete(self, entry: VdeEntry) -> None:
        raise VdeUnsupported("Cannot delete here")

    def rename(self, entry: VdeEntry, new_name: str) -> None:
        raise VdeUnsupported("Cannot rename here")

    def put_file(self, name: str, data: bytes) -> None:
        """Receive a copied file (used by copy between panels)."""
        raise VdeUnsupported("Cannot write here")

    def exists(self, name: str) -> bool:
        """Whether an entry with this name already exists here (for overwrite check)."""
        return False

    # ── lifecycle ───────────────────────────────────────────────────────────
    def close(self) -> None:
        """Release any held resources (open files, …). Safe to call repeatedly."""
