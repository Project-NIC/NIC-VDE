# SPDX-License-Identifier: MIT
"""
NIC-MSEED — export NIC-MLA logs to miniSEED (Steim-1 / Steim-2).

The seismology-interop bridge of the NIC ecosystem: it reads a ``.mla`` container
(decompressing NIC-DMD blobs on the way), pulls the raw integer counts out per
SCHEMA channel, and writes standard **miniSEED** records so the data drops
straight into ObsPy / SeisComp / SWARM and the FDSN toolchain.

    .mla  ──▶  [NIC-DMD decode if compressed]  ──▶  per-channel int counts  ──▶  miniSEED

Two layers, like the rest of the ecosystem:
  • ``steim`` / ``mseed`` — container-agnostic core (ints → Steim frames → records).
  • ``from_mla``          — the converter that wires NIC-MLA + NIC-DMD to it.

The vendored dependencies live under ``third_party/`` (the same way NIC-GLUE-IN
and NIC-VDE vendor them). Importing this package puts them on ``sys.path``.
"""
from __future__ import annotations

import os
import sys

# ── Make the vendored libraries importable ──────────────────────────────────
_TP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "third_party"))
for _p in (os.path.join(_TP, "nic_mla"), os.path.join(_TP, "nic_dmd")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from .steim import STEIM1, STEIM2, encode, decode, encode_record, decode_record  # noqa: E402
from .mseed import write_stream, read_stream, rate_factor_mult  # noqa: E402
from .from_mla import MseedExporter, export_mla_to_mseed  # noqa: E402

__all__ = [
    "MseedExporter", "export_mla_to_mseed",
    "STEIM1", "STEIM2",
    "encode", "decode", "encode_record", "decode_record",
    "write_stream", "read_stream", "rate_factor_mult",
]

__version__ = "0.1.0"
