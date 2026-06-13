# SPDX-License-Identifier: MIT
"""
NIC-GLUE-IN — input / write-side connector between the NIC libraries.

This package is the "glue" that lets the otherwise-independent NIC libraries
line their ports up on the *write* path:

    data row  ──▶  [optional NIC-DMD compression]  ──▶  NIC-MLA container

NIC-KSF (encryption) is deliberately **not** wired into the at-rest storage
path — storing encrypted data in the container was decided to be the wrong
layer for it (leave it to a trusted platform). KSF stays available for the
transport path, where the sender/receiver own the key.

The reading / decompressing direction lives in its sibling project
NIC-GLUE-OUT; NIC-VDE is the read-only viewer/exporter for the resulting
``.mla`` files.

The vendored dependencies live under ``third_party/`` (copies, the same way
NIC-VDE vendors NIC-MLA). Importing this package puts them on ``sys.path``.
"""
from __future__ import annotations

import os
import sys

# ── Make the vendored libraries importable ──────────────────────────────────
_TP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "third_party"))
for _p in (os.path.join(_TP, "nic_dmd"), os.path.join(_TP, "nic_mla")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Re-export the bits a caller of the glue actually needs, so they never have to
# reach into third_party/ themselves. (MLA v1.1 dropped the rec_type/class
# encoding constants — a record is now just a *compressed* bit + ``kf_back``
# distance, and meaning comes from the SCHEMA, so there is nothing to re-export
# in their place.)
from nic_dmd import DMD_KEYFRAME_EVERY  # noqa: E402
from nic_mla import (  # noqa: E402
    MLA_FLAG_COMPRESSED, MLA_KF_MASK,
    MLA_CRC_NONE, MLA_CRC_DATA, MLA_CRC_FULL,
)
from mla_schema import MlaSchemaBuilder, MlaStationTable  # noqa: E402

from .logger import (  # noqa: E402
    GlueLogger, GlueArchiveLogger, CompressedChannel, ChannelBank,
)

__version__ = "1.2"

__all__ = [
    "GlueLogger",
    "GlueArchiveLogger",
    "CompressedChannel",
    "ChannelBank",
    "MlaSchemaBuilder",
    "MlaStationTable",
    "DMD_KEYFRAME_EVERY",
    "MLA_FLAG_COMPRESSED", "MLA_KF_MASK",
    "MLA_CRC_NONE", "MLA_CRC_DATA", "MLA_CRC_FULL",
]
