"""Volkov Data core — GUI-free storage/transform logic (reusable headless)."""

__version__ = "1.2"

from .backend import VdeBackend, VdeBackendError, VdeEntry, VdeUnsupported
from .local import VdeLocalBackend
from .mla import VdeMlaBackend, vde_record_kind_name

__all__ = [
    "VdeBackend", "VdeBackendError", "VdeEntry", "VdeUnsupported",
    "VdeLocalBackend", "VdeMlaBackend", "vde_record_kind_name", "vde_open_backend",
]


def vde_open_backend(path: str) -> VdeBackend:
    """Open a path as a backend: a directory → VdeLocalBackend, an .mla → VdeMlaBackend."""
    import os
    if os.path.isdir(path):
        return VdeLocalBackend(path)
    if path.lower().endswith(".mla"):
        return VdeMlaBackend(path, parent=VdeLocalBackend(os.path.dirname(path) or "."))
    raise VdeBackendError(f"Don't know how to open: {path}")
