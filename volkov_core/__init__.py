"""Volkov Data core — GUI-free storage/transform logic (reusable headless)."""

__version__ = "1.0"

from .backend import Backend, BackendError, Entry, Unsupported
from .local import LocalBackend
from .mla import MlaBackend, rec_type_name

__all__ = [
    "Backend", "BackendError", "Entry", "Unsupported",
    "LocalBackend", "MlaBackend", "rec_type_name", "open_backend",
]


def open_backend(path: str) -> Backend:
    """Open a path as a backend: a directory → LocalBackend, an .mla → MlaBackend."""
    import os
    if os.path.isdir(path):
        return LocalBackend(path)
    if path.lower().endswith(".mla"):
        return MlaBackend(path, parent=LocalBackend(os.path.dirname(path) or "."))
    raise BackendError(f"Don't know how to open: {path}")
