# SPDX-License-Identifier: MIT
"""
from_mla.py — the converter that connects NIC-MLA (+ NIC-DMD) to miniSEED.

It reads a ``.mla`` container with the vendored NIC-MLA reference, turns each
record's payload into raw integer sample counts (decompressing NIC-DMD blobs
when the record's ``compressed`` bit is set), groups them into per-station,
per-SCHEMA-field channels, and writes one miniSEED byte stream.

What MLA carries vs what miniSEED needs:
  • MLA `timestamp` (u32 s) + `subsec` (u16) → the channel's start time.
  • MLA SCHEMA DATA fields                  → one miniSEED channel each (raw counts;
    calibration `offset`/`exp10` stay out — that belongs in StationXML metadata).
  • MLA STATION table                       → SEED network/station/location codes.
  • The sample RATE is NOT in MLA           → the caller supplies `sample_rate_hz`
    (e.g. the device ODR); `subsec` only pins the sub-second phase of the start.

Assumes each (station, channel) is one evenly-sampled, contiguous series (true
for synchronised acquisition, e.g. NIC-Quake). Gap-splitting is left to a later
pass — this is a worked converter, not a framework.
"""
from __future__ import annotations

from nic_mla import MlaCore, MlaPosixHAL
from mla_schema import mla_read_schema, mla_read_stations, mla_split_station
from nic_dmd import DmdDecoder

from .steim import STEIM2
from .mseed import write_stream


def _default_channel(name: str, idx: int) -> str:
    """Fallback SEED channel code from a SCHEMA field name (3 chars, upper)."""
    code = "".join(ch for ch in name.upper() if ch.isalnum())[:3]
    return code or f"C{idx:02d}"[:3]


class MseedExporter:
    """Convert a NIC-MLA container to miniSEED.

    sample_rate_hz — sampling rate of the series (device ODR); required.
    network        — SEED network code (≤2 chars).
    location       — SEED location code (≤2 chars).
    version        — STEIM1 or STEIM2 (default STEIM2).
    reclen         — miniSEED record length in bytes (power of two, default 512).
    subsec_unit    — how to read MLA's `subsec` into a sub-second fraction:
                     "index" → subsec / sample_rate_hz (sample number in the second),
                     "ms"    → subsec / 1000,
                     "tick"  → subsec / 65536,
                     or a callable subsec -> seconds.
    channel_map    — {schema_field_name: seed_channel_code}; else derived.
    station_map    — {mla_station_index: (network, station, location)} or
                     {mla_station_index: "STA"}; else derived from the STATION table.
    """

    def __init__(self, *, sample_rate_hz: float,
                 network: str = "XX", location: str = "",
                 version: int = STEIM2, reclen: int = 512,
                 subsec_unit="index",
                 channel_map: dict | None = None,
                 station_map: dict | None = None):
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be > 0")
        self.rate = float(sample_rate_hz)
        self.network = network
        self.location = location
        self.version = version
        self.reclen = reclen
        self.subsec_unit = subsec_unit
        self.channel_map = dict(channel_map or {})
        self.station_map = dict(station_map or {})

    # ── helpers ──────────────────────────────────────────────────────────────
    def _subsec_fraction(self, subsec: int) -> float:
        u = self.subsec_unit
        if callable(u):
            return float(u(subsec))
        if u == "index":
            return subsec / self.rate
        if u == "ms":
            return subsec / 1000.0
        if u == "tick":
            return subsec / 65536.0
        raise ValueError(f"unknown subsec_unit {u!r}")

    def _station_codes(self, idx: int, stations) -> tuple[str, str, str]:
        if idx in self.station_map:
            v = self.station_map[idx]
            if isinstance(v, (tuple, list)):
                net, sta, loc = (list(v) + [self.network, "", self.location])[:3]
                return str(net), str(sta), str(loc)
            return self.network, str(v), self.location
        sta = str(idx)
        if stations and 1 <= idx <= len(stations):
            region, number, _ = mla_split_station(stations[idx - 1])
            sta = str(number)
        return self.network, sta[:5], self.location

    def _channel_code(self, field, idx: int) -> str:
        return self.channel_map.get(field.name) or _default_channel(field.name, idx)

    # ── main ───────────────────────────────────────────────────────────────────
    def export(self, mla_path: str, out_path: str) -> dict:
        """Read `mla_path`, write miniSEED to `out_path`. Returns stats."""
        with MlaPosixHAL(mla_path) as hal:
            core = MlaCore(hal)
            core.mount()
            data_fields = mla_read_schema(core._prefix.to_bytes())[1]
            if not data_fields:
                raise ValueError("MLA file has no SCHEMA — cannot map channels")
            stations = mla_read_stations(core._prefix.to_bytes())
            pkt_len = sum(f.width for f in data_fields)

            decoders: dict[int, DmdDecoder] = {}
            series: dict[tuple[int, int], list[int]] = {}
            start: dict[int, tuple[int, float]] = {}

            for rec, payload in core:                       # oldest first
                st = rec.station
                if rec.compressed:
                    dec = decoders.get(st) or decoders.setdefault(st, DmdDecoder(pkt_len))
                    row = dec.decompress(payload)
                else:
                    row = payload
                if len(row) != pkt_len:
                    continue                                # not a schema row — skip
                if st not in start:
                    start[st] = (rec.timestamp, self._subsec_fraction(rec.subsec))
                pos = 0
                for fi, f in enumerate(data_fields):
                    val = int.from_bytes(row[pos:pos + f.width], "little", signed=f.signed)
                    pos += f.width
                    series.setdefault((st, fi), []).append(val)

        blob = bytearray()
        n_chan = n_rec_samples = 0
        seq = 1
        for (st, fi), samples in sorted(series.items()):
            net, sta, loc = self._station_codes(st, stations)
            cha = self._channel_code(data_fields[fi], fi)
            u, frac = start[st]
            blob += write_stream(samples, start_unix=u, start_frac=frac,
                                 sample_rate_hz=self.rate, network=net, station=sta,
                                 location=loc, channel=cha, version=self.version,
                                 reclen=self.reclen, seq_start=seq)
            n_chan += 1
            n_rec_samples += len(samples)
            seq = 1 + len(blob) // self.reclen              # next record's sequence no.

        with open(out_path, "wb") as fh:
            fh.write(blob)
        return {"channels": n_chan, "samples": n_rec_samples,
                "records": len(blob) // self.reclen, "bytes": len(blob),
                "out": out_path}


def export_mla_to_mseed(mla_path: str, out_path: str, *, sample_rate_hz: float,
                        **kw) -> dict:
    """Convenience wrapper: one-shot MLA → miniSEED conversion."""
    return MseedExporter(sample_rate_hz=sample_rate_hz, **kw).export(mla_path, out_path)
