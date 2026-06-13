#!/usr/bin/env python3
"""
nic_mla_hal_nor.py  —  NIC-MLA HAL for NOR flash   ⚠ EXPERIMENTAL / FROZEN ⚠

╔══════════════════════════════════════════════════════════════════════════╗
║  THEORETICAL OPTION ONLY. NOT RECOMMENDED FOR PRODUCTION. NOT DEVELOPED    ║
║  FURTHER.                                                                  ║
║                                                                          ║
║  Why: direct raw SPI-NOR/NAND is vendor-specific (Winbond W25Q etc.) and  ║
║  partial-page / partial-block writes can put some controllers into an      ║
║  error / "lockdown" state (controller safety mechanisms). For this         ║
║  project we agreed to run on an SD/flash card — the card's own controller  ║
║  handles wear-leveling, ECC and remapping itself. This module stays here   ║
║  only as an experimental simulator / reference interface, not as the       ║
║  intended path.                                                            ║
╚══════════════════════════════════════════════════════════════════════════╝

Contains:
  MlaNorSimHAL  — an in-RAM simulator of W25Q NOR flash
                   (for testing on a PC without real hardware)

Simulated NOR flash constraints (W25Q class):
  • Page Program: max 256 B per command, must not cross a page boundary.
    If a write exceeds the 256 B page, the HAL transparently splits it.
  • A write is an AND operation: only 1→0 is possible. An attempt at 0→1 raises
    RuntimeError. (On a real chip the byte would be silently written wrong —
    this catches it during tests.)
  • Erase: a 4 KB sector or the whole chip. Erase sets bytes to 0xFF.

A real SPI HAL is INTENTIONALLY not implemented (see experimental/README.md).

Python 3.10+  |  MIT  |  ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
import sys

# This experimental module lives in experimental/; the core is one level up.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from nic_mla import MlaHAL, MLA_DEFAULT_SIZE


class MlaNorSimHAL(MlaHAL):
    """
    In-RAM simulator of W25Q NOR flash memory.

    Usage:
        hal = MlaNorSimHAL(size=64*1024)   # 64 KB NOR flash
        mla = MlaCore(hal)
        mla.format()
        mla.append(...)
    """

    PAGE_SIZE   = 256    # B — Page Program
    SECTOR_SIZE = 4096   # B — Sector Erase (4 KB)
    BLOCK_SIZE  = 65536  # B — Block Erase (64 KB)

    def __init__(self, size: int = MLA_DEFAULT_SIZE):
        if size % self.SECTOR_SIZE != 0:
            raise ValueError(f"Size ({size} B) must be a multiple of the sector ({self.SECTOR_SIZE} B)")
        self._mem  = bytearray(b"\xff" * size)  # fresh chip: all 0xFF
        self._size = size
        # ── Statistics (for checking in tests) ──
        self.stat_page_programs  = 0
        self.stat_sector_erases  = 0
        self.stat_chip_erases    = 0

    # ── HAL interface ───────────────────────────────────────────────────────────

    def read(self, off: int, n: int) -> bytes:
        self._check_bounds(off, n)
        return bytes(self._mem[off:off + n])

    def write(self, off: int, data: bytes) -> None:
        """
        Emulates Page Program with automatic splitting at page boundaries.

        If a write crosses the 256 B page, the HAL transparently splits it into
        two Page Program commands — exactly as a real SPI HAL must do, because
        on a page overflow the W25Q wraps back to the start of the same page.

        A write is AND (1→0 only). An attempt at 0→1 raises RuntimeError.
        """
        n = len(data)
        self._check_bounds(off, n)
        pos = 0
        while pos < n:
            page_base = ((off + pos) // self.PAGE_SIZE) * self.PAGE_SIZE
            page_end  = page_base + self.PAGE_SIZE
            chunk_len = min(page_end - (off + pos), n - pos)

            for i in range(chunk_len):
                abs_off  = off + pos + i
                existing = self._mem[abs_off]
                new_byte = data[pos + i]
                # Detect an attempt at 0→1 (impossible on NOR without an erase)
                if new_byte & ~existing & 0xFF:
                    raise RuntimeError(
                        f"NOR: 0→1 attempt at address {abs_off:#010x} "
                        f"(existing={existing:#04x}, writing={new_byte:#04x}). "
                        f"A sector_erase() is required."
                    )
                self._mem[abs_off] &= new_byte

            self.stat_page_programs += 1
            pos += chunk_len

    def sync(self) -> None:
        pass   # in-memory simulation — nothing to flush

    def size(self) -> int:
        return self._size

    # ── Erase operations ────────────────────────────────────────────────────────

    def sector_erase(self, off: int) -> None:
        """Erase a 4 KB sector (set to 0xFF). The offset must be 4 KB aligned."""
        if off % self.SECTOR_SIZE != 0:
            raise ValueError(f"Sector erase: offset {off:#x} is not aligned to {self.SECTOR_SIZE} B")
        self._check_bounds(off, self.SECTOR_SIZE)
        end = off + self.SECTOR_SIZE
        self._mem[off:end] = b"\xff" * self.SECTOR_SIZE
        self.stat_sector_erases += 1

    def block_erase(self, off: int) -> None:
        """Erase a 64 KB block. The offset must be 64 KB aligned."""
        if off % self.BLOCK_SIZE != 0:
            raise ValueError(f"Block erase: offset {off:#x} is not aligned to {self.BLOCK_SIZE} B")
        self._check_bounds(off, self.BLOCK_SIZE)
        end = off + self.BLOCK_SIZE
        self._mem[off:end] = b"\xff" * self.BLOCK_SIZE
        self.stat_chip_erases += 1   # (abusing the counter, but fine for the test)

    def chip_erase(self) -> None:
        """Erase the whole chip (set everything to 0xFF)."""
        self._mem[:] = b"\xff" * self._size
        self.stat_chip_erases += 1

    # ── Extra helpers for testing / debugging ─────────────────────────────────

    def get_image(self) -> bytes:
        """Return the whole flash content as bytes (for inspection)."""
        return bytes(self._mem)

    def save(self, path: str) -> None:
        """Save the flash image to a file (for debugging and offline analysis)."""
        with open(path, "wb") as f:
            f.write(self._mem)

    @classmethod
    def load(cls, path: str) -> MlaNorSimHAL:
        """Load a flash image from a file."""
        with open(path, "rb") as f:
            data = f.read()
        hal = cls(size=len(data))
        hal._mem[:] = data
        return hal

    def stats(self) -> dict:
        return {
            "page_programs":  self.stat_page_programs,
            "sector_erases":  self.stat_sector_erases,
            "chip_erases":    self.stat_chip_erases,
        }

    # ── Internal ────────────────────────────────────────────────────────────────

    def _check_bounds(self, off: int, n: int) -> None:
        if off < 0 or off + n > self._size:
            raise ValueError(f"Out of range: off={off:#x}, n={n}, size={self._size:#x}")
