#!/usr/bin/env python3
"""
NIC-MLA  —  schema table builder

Builds the self-describing *decode table* that travels inside the MLA file
(in the prefix free space, [MLA_SCHEMA_OFF .. ), covered by the prefix CRC).
The firmware embeds the table at format() time; any reader that links the MLA
library (e.g. the Volkov editor) reads it back and can export to CSV/SQL
WITHOUT any prior knowledge — the station carries everything itself.

Each field carries its OWN descriptor (14 B), so you can add arbitrary fields
and they stay self-describing:

    field descriptor (14 B):
        [+0]  width  1 B   bytes on the wire (1 / 2 / 4)
        [+1]  unit   1 B   code from the universal UNIT vocabulary (below)
        [+2]  exp10  1 B   signed exponent (see formula)
        [+3]  flags  1 B   bit0 = signed value; bits 1-7 reserved
        [+4]  offset 2 B   i16 LE, additive calibration term
        [+6]  name   8 B   field name, UTF-8, NUL-padded (host display / CSV header)

    physical = (raw + offset) * 10**exp10

Table binary layout (contract version MLA_SCHEMA_VER = 1):

    [0] tbl_ver  1 B   = 1
    [1] n_log    1 B   number of LOG fields
    [2] n_data   1 B   number of DATA fields
    [3 ..]             n_log + n_data field descriptors (14 B each)

    total = 3 + 14 * (n_log + n_data)

The table lives at [34 ..) in the prefix and is covered by the prefix CRC. It
normally fits the 512 B prefix; if it overflows, the prefix grows in whole
512 B blocks and the CRC moves to the prefix's last 2 bytes.

The UNIT vocabulary is universal (SI-ish) and shared by the spec — it is NOT
device-specific, so it does not need to travel in the file. The field
*composition* (which sensors, what scale/width) is device-specific and IS
carried in the file.

Usage:
    python3 tools/mla_schema.py        # builds the example, writes c/mla_schema_table.h

    # or drive it yourself:
    from mla_schema import SchemaBuilder
    sb = SchemaBuilder()
    sb.log("datetime"); sb.log("station"); sb.log("region")
    sb.data("temp_in", unit="degC", width=2, exp10=-1, signed=True)
    sb.emit_c("c/mla_schema_table.h")

Python 3.10+   |   MIT   |   ★ Viva La Resistánce ★
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# ── Format constants (mirror nic_mla_format.h) ──────────────────────────────
MLA_SCHEMA_VER = 1                        # the one and only table version (v1.0)
MLA_SCHEMA_OFF = 34                       # = MLA_PFX_HDR_SIZE
MLA_PREFIX_SIZE = 512                     # base prefix block; grows in 512 B steps if needed
MLA_NAME_LEN   = 8                        # bytes reserved for a field name, UTF-8, NUL-padded
MLA_FIELD_CORE = 6                        # width, unit, exp10, flags, offset[i16]
MLA_FIELD_SIZE = MLA_FIELD_CORE + MLA_NAME_LEN   # 14 — one field descriptor
MLA_DATA_MAX   = 255                      # max data payload per record (1 B length in the log)
MLA_PREFIX_MAX = 8 * MLA_PREFIX_SIZE      # 4 KB / 8 sectors — hard ceiling for the prefix


def prefix_byte_len(schema_len: int) -> int:
    """Total prefix size for a schema of `schema_len` bytes (CRC included).

    The prefix is normally 512 B (schema fits in [34..510), CRC at [510]). If
    the schema would overflow that, the prefix grows in whole 512 B blocks and
    the CRC sits in the last 2 bytes. Mirrors _prefix_byte_len in nic_mla.py.
    """
    need = MLA_SCHEMA_OFF + schema_len + 2            # header + schema + CRC16
    if need <= MLA_PREFIX_SIZE:
        return MLA_PREFIX_SIZE
    return -(-need // MLA_PREFIX_SIZE) * MLA_PREFIX_SIZE   # round up to 512


# ── Universal unit vocabulary (shared by the spec; name -> code) ────────────
UNITS: dict[str, int] = {
    "raw":     0,   # untyped / dimensionless
    "degC":    1, "degF": 2, "K": 3,
    "pct":     4,            # %
    "Pa":      5, "hPa": 6, "kPa": 7,
    "V":       8, "A": 9, "W": 10,
    "Wh":     11, "kWh": 12, "MWh": 13,
    "unix_s": 14,           # Unix seconds
    "id":     15,           # identifier (station / region / channel)
    "ppm":    16, "lux": 17, "m_s": 18,   # m/s
    "mm":     19, "count": 20,
}
UNIT_NAME = {v: k for k, v in UNITS.items()}


@dataclass(frozen=True)
class Field:
    name:   str          # column name — up to MLA_NAME_LEN bytes, carried on the wire
    width:  int          # 1 / 2 / 4
    unit:   str          # key into UNITS
    exp10:  int = 0      # physical = (raw + offset) * 10**exp10
    signed: bool = False
    offset: int = 0      # i16 additive calibration term (raw units)

    def name_bytes(self) -> bytes:
        """The field name as exactly MLA_NAME_LEN bytes (UTF-8, NUL-padded)."""
        enc = self.name.encode("utf-8")
        if len(enc) > MLA_NAME_LEN:
            raise ValueError(f"{self.name!r}: name is {len(enc)} B, max {MLA_NAME_LEN}")
        return enc + b"\x00" * (MLA_NAME_LEN - len(enc))

    def descriptor(self) -> bytes:
        """14 B: 6 B core (width, unit, exp10, flags, offset) + 8 B name."""
        if self.width not in (1, 2, 4):
            raise ValueError(f"{self.name}: width must be 1/2/4, got {self.width}")
        if self.unit not in UNITS:
            raise ValueError(f"{self.name}: unknown unit '{self.unit}' (add it to UNITS)")
        if not -128 <= self.exp10 <= 127:
            raise ValueError(f"{self.name}: exp10 out of signed-byte range")
        if not -32768 <= self.offset <= 32767:
            raise ValueError(f"{self.name}: offset out of i16 range")
        flags = 0x01 if self.signed else 0x00
        off = self.offset & 0xFFFF
        core = bytes([self.width, UNITS[self.unit], self.exp10 & 0xFF, flags,
                      off & 0xFF, (off >> 8) & 0xFF])
        return core + self.name_bytes()

    @classmethod
    def from_descriptor(cls, buf: bytes, name: str = "") -> "Field":
        """Inverse of descriptor(): decode a 14 B descriptor back to a Field.

        The name is read from [6:14]; if it is blank, the `name` argument (e.g. a
        generated placeholder) is used instead.
        """
        if len(buf) < MLA_FIELD_SIZE:
            raise ValueError(f"descriptor: need {MLA_FIELD_SIZE} B, got {len(buf)}")
        width, unit_code, exp10_raw, flags = buf[0], buf[1], buf[2], buf[3]
        if unit_code not in UNIT_NAME:
            raise ValueError(f"descriptor: unknown unit code {unit_code}")
        exp10  = exp10_raw - 256 if exp10_raw >= 128 else exp10_raw          # signed byte
        off    = buf[4] | (buf[5] << 8)
        offset = off - 0x10000 if off >= 0x8000 else off                    # i16 LE
        embedded = buf[MLA_FIELD_CORE:MLA_FIELD_SIZE].split(b"\x00", 1)[0]
        if embedded:
            name = embedded.decode("utf-8", "replace")
        return cls(name=name, width=width, unit=UNIT_NAME[unit_code],
                   exp10=exp10, signed=bool(flags & 0x01), offset=offset)


# ── Field presets (handy names for common log/data fields) ───────────────────
PRESETS: dict[str, Field] = {
    "datetime": Field("datetime", 4, "unix_s"),
    "station":  Field("station",  2, "id"),
    "region":   Field("region",   2, "id"),
    "channel":  Field("channel",  2, "id"),
}


# ── Reader (host-only; inverse of the builder) ──────────────────────────────
#  The decode table travels inside the 512 B prefix at [MLA_SCHEMA_OFF .. ),
#  covered by the prefix CRC. These helpers read it back so any reader that
#  links this module can decode a station's records WITHOUT prior knowledge.
#  Pure host code — the write-only MCU path never uses it.

def schema_byte_len(prefix: bytes) -> int:
    """Total on-the-wire length of the embedded schema table (0 if none).

    The table is self-sizing from its 3 B header, so this works on just the
    first block of the prefix even when the prefix is extended past 512 B.
    """
    if len(prefix) < MLA_SCHEMA_OFF + 3:
        return 0
    if prefix[MLA_SCHEMA_OFF] != MLA_SCHEMA_VER:      # 0x00/0xFF/other → no table
        return 0
    n_log, n_data = prefix[MLA_SCHEMA_OFF + 1], prefix[MLA_SCHEMA_OFF + 2]
    return 3 + MLA_FIELD_SIZE * (n_log + n_data)


def read_schema(prefix: bytes) -> tuple[list[Field] | None, list[Field] | None]:
    """Decode the schema table from a prefix (512 B, or larger if extended).

    Reads from offset MLA_SCHEMA_OFF (34):
        [34] tbl_ver  [35] n_log  [36] n_data  [37 ..] (n_log+n_data) × 14 B

    Returns (log_fields, data_fields). A file written without a schema
    (tbl_ver byte 0x00 or 0xFF — zero padding or fresh 0xFF) yields
    (None, None). Raises ValueError on an unsupported version or a truncated
    table.
    """
    if len(prefix) <= MLA_SCHEMA_OFF:
        return (None, None)
    tbl_ver = prefix[MLA_SCHEMA_OFF]
    if tbl_ver in (0x00, 0xFF):                      # no schema embedded
        return (None, None)
    if tbl_ver != MLA_SCHEMA_VER:
        raise ValueError(f"schema: unsupported tbl_ver {tbl_ver} "
                         f"(this reader supports {MLA_SCHEMA_VER})")
    if len(prefix) < MLA_SCHEMA_OFF + 3:
        raise ValueError("schema: truncated header")
    n_log  = prefix[MLA_SCHEMA_OFF + 1]
    n_data = prefix[MLA_SCHEMA_OFF + 2]
    need   = 3 + MLA_FIELD_SIZE * (n_log + n_data)
    if len(prefix) < MLA_SCHEMA_OFF + need:
        raise ValueError("schema: truncated descriptors")

    pos = MLA_SCHEMA_OFF + 3
    def take(count: int, label: str) -> list[Field]:
        nonlocal pos
        out = []
        for i in range(count):
            out.append(Field.from_descriptor(prefix[pos:pos + MLA_FIELD_SIZE],
                                             name=f"{label}{i}"))
            pos += MLA_FIELD_SIZE
        return out

    return take(n_log, "log"), take(n_data, "data")


def decode_value(field: Field, raw_bytes: bytes) -> float | int:
    """Decode one packed field: physical = (raw + offset) * 10**exp10.

    raw_bytes must be exactly field.width bytes (little-endian). The result is
    int when exp10 >= 0, otherwise float.
    """
    if len(raw_bytes) != field.width:
        raise ValueError(f"{field.name}: expected {field.width} B, got {len(raw_bytes)}")
    raw = int.from_bytes(raw_bytes, "little", signed=field.signed)
    scaled = raw + field.offset
    if field.exp10 == 0:
        return scaled
    return scaled * (10 ** field.exp10 if field.exp10 > 0 else 10.0 ** field.exp10)


def decode_payload(data_fields: list[Field],
                   payload: bytes) -> list[tuple[str, str, float | int]]:
    """Split a packed data payload by field width and decode each value.

    The payload is the sensor values packed back-to-back, in data_fields order.
    Returns a list of (name, unit, value). Raises ValueError if the payload
    length does not match the schema's total width.
    """
    total = sum(f.width for f in data_fields)
    if len(payload) != total:
        raise ValueError(f"payload {len(payload)} B does not match schema width {total} B")
    out, pos = [], 0
    for f in data_fields:
        out.append((f.name, f.unit, decode_value(f, payload[pos:pos + f.width])))
        pos += f.width
    return out


# ── Builder ────────────────────────────────────────────────────
class SchemaBuilder:
    """Configure the schema by adding fields, then emit the table.

    Use .log(...) for the (fixed) LOG-header fields and .data(...) for the
    variable data payload. Either pass a preset name, or a full field spec.
    """

    def __init__(self) -> None:
        self.log_fields:  list[Field] = []
        self.data_fields: list[Field] = []

    def _make(self, name, unit, width, exp10, signed, offset) -> Field:
        if unit is None and name in PRESETS:      # preset by name
            return PRESETS[name]
        if unit is None:
            raise ValueError(f"'{name}' is not a preset — give unit/width explicitly")
        return Field(name, width, unit, exp10, signed, offset)

    def log(self, name, *, unit=None, width=2, exp10=0, signed=False,
            offset=0) -> "SchemaBuilder":
        self.log_fields.append(self._make(name, unit, width, exp10, signed, offset))
        return self

    def data(self, name, *, unit=None, width=2, exp10=0, signed=False,
             offset=0) -> "SchemaBuilder":
        self.data_fields.append(self._make(name, unit, width, exp10, signed, offset))
        return self

    # — outputs —
    def data_width(self) -> int:
        return sum(f.width for f in self.data_fields)

    def table(self) -> bytes:
        if len(self.log_fields) > 255 or len(self.data_fields) > 255:
            raise ValueError("n_log / n_data must each fit in 1 byte")
        dw = self.data_width()
        if dw > MLA_DATA_MAX:
            raise ValueError(f"data payload {dw} B exceeds MLA_DATA_MAX={MLA_DATA_MAX}")
        out = bytes([MLA_SCHEMA_VER, len(self.log_fields), len(self.data_fields)])
        for f in self.log_fields + self.data_fields:
            out += f.descriptor()
        # A table that overflows the base 512 B prefix grows it in 512 B steps
        # (the CRC moves to the new end), but only up to MLA_PREFIX_MAX.
        psize = prefix_byte_len(len(out))
        if psize > MLA_PREFIX_MAX:
            raise ValueError(
                f"schema needs a {psize} B prefix, exceeds "
                f"MLA_PREFIX_MAX={MLA_PREFIX_MAX} ({MLA_PREFIX_MAX // MLA_PREFIX_SIZE} sectors)"
            )
        return out

    def prefix_size(self) -> int:
        """Total prefix size (B) needed to carry this table, incl. the CRC."""
        return prefix_byte_len(len(self.table()))

    def describe(self) -> str:
        def row(f: Field) -> str:
            scale = "" if f.exp10 == 0 else f"  ×10^{f.exp10}"
            off = "" if f.offset == 0 else f"  {f.offset:+d}"
            sign = "i" if f.signed else "u"
            return f"  {f.name:<14} {f.width}B {sign}  {f.unit}{scale}{off}"
        lines = ["LOG schema:"]   + [row(f) for f in self.log_fields]
        lines += ["DATA schema:"] + [row(f) for f in self.data_fields]
        lines += [f"data payload = {self.data_width()} B  (max {MLA_DATA_MAX})"]
        return "\n".join(lines)

    def emit_c(self, c_path: str) -> None:
        """Emit a split pair so the table is a SEPARATE compilation unit:

            mla_schema_table.h  — stable declaration; the generic firmware
                                  includes this and never changes.
            mla_schema_table.c  — the swappable bytes; regenerate THIS file
                                  to build a different station.
        """
        table = self.table()
        rows = ", ".join(f"0x{b:02X}" for b in table)
        summary = "\n".join(" * " + ln for ln in self.describe().splitlines())

        base = os.path.basename(c_path)
        stem = base[:-2] if base.endswith(".c") else base
        h_name = stem + ".h"
        h_path = os.path.join(os.path.dirname(c_path), h_name)
        guard = stem.upper() + "_H"

        header = f"""/*
 * {h_name}  —  GENERATED by tools/mla_schema.py  (do not edit by hand)
 *
 * Stable declaration of the schema/decode table. The generic firmware
 * includes this; only {stem}.c changes when you build a different station.
 *
 * Embed at format() time:
 *     mla_w_format_ex(&w, hal, file_size, crc_mode, cluster_shift,
 *                     checkpoint_shift, keyframe_intv,
 *                     mla_schema_table, MLA_SCHEMA_TABLE_LEN);
 */
#ifndef {guard}
#define {guard}

#include <stdint.h>

#define MLA_SCHEMA_TABLE_LEN {len(table)}u
extern const uint8_t mla_schema_table[MLA_SCHEMA_TABLE_LEN];

#endif /* {guard} */
"""
        source = f"""/*
 * {stem}.c  —  GENERATED by tools/mla_schema.py  (do not edit by hand)
 *
 * The device-specific schema. Swap this file (regenerate from tools/mla_schema.py)
 * to make a different station — the rest of the firmware stays identical.
 *
{summary}
 */
#include "{h_name}"

const uint8_t mla_schema_table[MLA_SCHEMA_TABLE_LEN] = {{ {rows} }};
"""
        with open(h_path, "w") as f:
            f.write(header)
        with open(c_path, "w") as f:
            f.write(source)


# ── Example / CLI ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sb = SchemaBuilder()

    # ── EDIT HERE — add as many fields as you need ─────────
    # LOG header: stripped from the incoming packet into the fixed 24 B log
    sb.log("datetime").log("station").log("region")

    # DATA payload: one entry per sensor value, packed back-to-back
    sb.data("temp_in",   unit="degC", width=2, exp10=-1, signed=True, offset=-15)  # calib.
    sb.data("temp_out",  unit="degC", width=2, exp10=-1, signed=True)
    sb.data("humidity",  unit="pct",  width=2, exp10=-1)
    sb.data("pressure",  unit="hPa",  width=2, exp10=-1)
    sb.data("wind",      unit="m_s",  width=2, exp10=-1)
    sb.data("voltage",   unit="V",    width=2, exp10=-2)
    sb.data("current",   unit="A",    width=2, exp10=-3, signed=True)
    sb.data("power",     unit="W",    width=2)
    sb.data("energy",    unit="kWh",  width=4)
    sb.data("co2",       unit="ppm",  width=2)
    sb.data("lux",       unit="lux",  width=4)
    sb.data("rain",      unit="mm",   width=2, exp10=-1)
    # ─────────────────────────────────────────────────────────────────────────

    table = sb.table()
    print(sb.describe())
    print(f"\ntable = {len(table)} B  (prefix {sb.prefix_size()} B): "
          + " ".join(f"{b:02X}" for b in table))

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = os.path.join(repo, "c", "mla_schema_table.c")
    sb.emit_c(out)
    print(f"\nwrote {out} + {out[:-2]}.h")
