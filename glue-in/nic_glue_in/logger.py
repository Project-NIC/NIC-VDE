# SPDX-License-Identifier: MIT
"""
GlueLogger — the write-side connector itself.

What this layer is actually *for* is matching up the ports/buffers between the
two libraries so the caller never has to:

  • NIC-MLA carries a 1-byte ``flags`` field — bit 7 = *compressed*, bits 0–6 =
    ``kf_back`` (records back to the owning keyframe; 0 = this record IS one) —
    but it does not interpret it. It only stores the *compressed* bit and the
    ``kf_back`` distance so a reader (NIC-VDE / NIC-GLUE-OUT) knows to hand the
    block to the codec and where its keyframe is. WHICH codec / method lives in
    the data block's own header (DMD's byte 0), never in MLA. MLA v1.1 dropped
    the old ``rec_type``/class tag: files are homogeneous, meaning comes from the
    SCHEMA, not a per-record type byte.

  • NIC-DMD is delta-based: it needs a *fixed-width* packet, the *previous*
    packet, and a sample counter. The stateful ``DmdEncoder`` owns all of that,
    so compression lives in a per-channel object — one ``CompressedChannel``
    per stream. Because the delta only makes sense within one stream, a channel
    is tied to a single MLA station index and a single packet width. Different
    channels may use different widths (4..255 B); the width belongs to the
    channel, never to the file. ``ChannelBank`` manages several at once: one
    stateless compressor + N tiny per-stream contexts.

  • MLA's ``keyframe_intv`` is prefix metadata only (a hint for readers). The
    library default is 0; the glue seeds it from DMD's cadence so the caller
    never has to know the magic number — overridable per logger.

NIC-KSF is intentionally absent here (no encryption at rest).
"""
from __future__ import annotations

from nic_dmd import DmdEncoder, DMD_KEYFRAME_EVERY
from nic_mla import MlaCore, MlaPosixHAL, MLA_CRC_FULL
from nic_mla_archive import MlaArchive

# DMD packet width limits. The technical floor in DMD is 1 B, but below ~16 B
# compression rarely pays for the 1 B header + 2 B ANS state; 255 B is the hard
# ceiling (the sample/length fields are bytes). We accept 4..255 and leave the
# "is it worth it" judgement to the caller.
_PKT_MIN = 4
_PKT_MAX = 255


class CompressedChannel:
    """A single NIC-DMD-compressed stream feeding one MLA station index.

    Every packet in the channel MUST be exactly ``pkt_len`` bytes — that is a
    hard requirement of the delta coder, not a glue choice. Open one channel per
    independent stream; up to 255 may coexist (one per station index) if you
    really want to, though the delta only buys anything within a single stream.
    """

    def __init__(self, logger: "GlueLogger", station: int, pkt_len: int):
        if not (1 <= station <= 255):
            raise ValueError(f"station index must be 1..255, not {station}")
        if not (_PKT_MIN <= pkt_len <= _PKT_MAX):
            raise ValueError(
                f"DMD packet width must be {_PKT_MIN}..{_PKT_MAX} B, not {pkt_len}")
        self._logger = logger
        self._station = station
        self._enc = DmdEncoder(pkt_len)
        self._since_keyframe = 0   # → kf_back: records since the owning keyframe

    @property
    def station(self) -> int:
        return self._station

    @property
    def pkt_len(self) -> int:
        return self._enc.pkt_len

    def log(self, timestamp: int, row: bytes, *, subsec: int = 0) -> bytes:
        """Compress one fixed-width row and append it. Returns the stored blob.

        The DMD header (byte 0, bits 2..0) carries the sample number; sample 0 is
        a keyframe. We read it back off the produced blob — the authoritative
        signal — and translate it into MLA's ``compressed`` bit + ``kf_back``.
        The record is always stored compressed; ``kf_back`` is 0 on a keyframe
        and the distance back to it otherwise.

        Rotation seam (2b): if this record might not fit in the current file —
        worst-case DMD output is ``pkt_len + 1`` B, since expansion is never more
        than the 1-byte header — reset the stream FIRST so it is encoded as a
        keyframe. That keeps a delta from ever crossing a file boundary, so the
        first record of this stream in the freshly rotated file is a self-contained
        keyframe and the file decodes on its own. A single-file logger never
        rotates (``_will_rotate`` is always False), so this is a no-op there.
        """
        if len(row) != self.pkt_len:
            raise ValueError(
                f"row width {len(row)} != channel pkt_len {self.pkt_len}")

        if self._logger._will_rotate(self.pkt_len + 1):
            self._enc.reset()                     # next packet → keyframe

        blob = self._enc.compress(row)            # 1-B header + payload
        is_keyframe = (blob[0] & 0x07) == 0       # sample number on 3 bits == 0

        if is_keyframe:
            self._since_keyframe = 0
        else:
            self._since_keyframe += 1

        self._logger._append(
            timestamp, self._station, blob,
            compressed=True, kf_back=self._since_keyframe, subsec=subsec,
        )
        return blob

    def reset(self) -> None:
        """Drop the delta history — the next packet will be a fresh keyframe.

        This is the response to NIC-MLA's rotation signal (2b): when the writer
        rolls over to a new file, resetting every channel makes the first record
        of each stream in the new file a keyframe, so the file is independently
        decodable. ``ChannelBank.on_rotate`` does this for a whole bank.
        """
        self._enc.reset()
        self._since_keyframe = 0


class ChannelBank:
    """Manage several compressed streams over one logger.

    The worked example of "one stateless compressor + N tiny per-stream
    contexts": the DMD compression code itself is stateless; each channel keeps
    only its own ``DmdEncoder`` context (previous packet + sample counter).

    Stream identity: a channel is keyed by its MLA **station index** — that is
    the stream's identity inside the file. A reader tells streams apart by
    station and reads ``kf_back`` to find each one's keyframe; MLA needs no
    other per-record tag.

    Rotation seam (2b): wire ``on_rotate`` straight into a rotating writer so
    each rotated file starts every stream on a keyframe::

        bank = ChannelBank(logger)
        arch = MlaArchive(dir, on_rotate=bank.on_rotate)   # MLA calls it on rollover
    """

    def __init__(self, logger: "GlueLogger | GlueArchiveLogger"):
        self._logger = logger
        self._channels: dict[int, CompressedChannel] = {}
        # Wire the rotation seam: when the writer rolls over to a new file it
        # calls back here so every *other* open stream resets and emits a keyframe
        # next — together with the per-record check in CompressedChannel.log this
        # makes every stream start each file on a keyframe. On a single-file
        # GlueLogger this is a no-op (it never rotates).
        logger.set_on_rotate(self.on_rotate)

    def open(self, station: int, pkt_len: int) -> CompressedChannel:
        """Open (or fetch) the channel for ``station``; widths must be stable."""
        ch = self._channels.get(station)
        if ch is None:
            ch = self._logger.open_compressed_channel(station, pkt_len)
            self._channels[station] = ch
        elif ch.pkt_len != pkt_len:
            raise ValueError(
                f"station {station} already open at width {ch.pkt_len}, not {pkt_len}")
        return ch

    def log(self, station: int, pkt_len: int, timestamp: int, row: bytes,
            *, subsec: int = 0) -> bytes:
        """Compress+append one row on the channel for ``station``."""
        return self.open(station, pkt_len).log(timestamp, row, subsec=subsec)

    def reset_all(self) -> None:
        """Force every open channel to emit a keyframe next (see 2b)."""
        for ch in self._channels.values():
            ch.reset()

    def on_rotate(self, prev_seq: int, new_seq: int) -> None:
        """Callback shape for ``MlaArchive(on_rotate=...)`` — reset all streams
        so each rotated file is independently decodable."""
        self.reset_all()

    @property
    def channels(self) -> dict[int, CompressedChannel]:
        return self._channels


class GlueLogger:
    """A thin datalogger over a single NIC-MLA container.

    The classic path is ``log_raw`` / ``log_event`` — take a row, store it. For
    a demonstration of compression, ``open_compressed_channel`` hands back a
    ``CompressedChannel`` that runs the row through NIC-DMD first (or use
    ``ChannelBank`` for several streams at once).
    """

    def __init__(self, path: str, *,
                 schema_table: bytes = b"",
                 station_table: bytes = b"",
                 keyframe_intv: int | None = None,
                 file_size: int = 256 * 1024,
                 crc_mode: int = MLA_CRC_FULL,
                 create: bool = True):
        """Open (and by default create+format) an MLA container.

        keyframe_intv — prefix hint only. ``None`` lets the glue seed DMD's
        cadence (so compressed channels line up without the caller knowing it);
        pass an explicit int (e.g. 0 for a pure-RAW logger) to override.
        """
        self.path = path
        self._kfi = DMD_KEYFRAME_EVERY if keyframe_intv is None else keyframe_intv

        if create:
            self._hal = MlaPosixHAL.create(path, file_size=file_size)
        else:
            self._hal = MlaPosixHAL(path)
        self._hal.__enter__()
        self._core = MlaCore(self._hal)
        if create:
            self._core.format(file_size=file_size, crc_mode=crc_mode,
                              keyframe_intv=self._kfi,
                              schema_table=schema_table,
                              station_table=station_table)
        else:
            self._core.mount()

    # ── classic datalogger path (no compression) ───────────────────────────
    def log_raw(self, timestamp: int, station: int, data: bytes,
                *, subsec: int = 0) -> None:
        """Store one row verbatim (uncompressed)."""
        self._append(timestamp, station, data, compressed=False, subsec=subsec)

    def log_event(self, timestamp: int, station: int, text, *, subsec: int = 0) -> None:
        """Store a text / status event (e.g. ``"PING"``) uncompressed.

        Note: MLA v1.1 carries no per-record type tag, so at the MLA layer an
        event is just an uncompressed record like any other — tell them apart by
        station / schema / context, not by a record type.
        """
        if isinstance(text, str):
            text = text.encode("utf-8")
        self._append(timestamp, station, text, compressed=False, subsec=subsec)

    # ── optional compression path ───────────────────────────────────────────
    def open_compressed_channel(self, station: int, pkt_len: int) -> CompressedChannel:
        """Open a NIC-DMD-compressed stream for one station / one fixed width."""
        return CompressedChannel(self, station, pkt_len)

    # ── rotation seam (no-op: a single file never rotates) ───────────────────
    def _will_rotate(self, data_len: int) -> bool:
        """A single-file logger never rotates — it fills up and raises instead.
        (``GlueArchiveLogger`` overrides this to predict a rollover.)"""
        return False

    def set_on_rotate(self, cb) -> None:
        """No-op here; present so ``ChannelBank`` can wire the same way for both
        loggers. Only ``GlueArchiveLogger`` actually rotates."""
        return None

    # ── internals / lifecycle ────────────────────────────────────────────────
    def _append(self, timestamp: int, station: int, data: bytes,
                *, compressed: bool = False, kf_back: int = 0, subsec: int = 0) -> None:
        self._core.append(timestamp, station, data,
                          subsec=subsec, compressed=compressed, kf_back=kf_back)

    @property
    def keyframe_intv(self) -> int:
        return self._kfi

    @property
    def record_count(self) -> int:
        return self._core.record_count

    def sync(self) -> None:
        self._core.sync()

    def close(self) -> None:
        self.sync()
        self._hal.__exit__()

    def __enter__(self) -> "GlueLogger":
        return self

    def __exit__(self, *_) -> None:
        self.close()


class GlueArchiveLogger:
    """A rotating datalogger — the same write API as :class:`GlueLogger`, but over
    an :class:`MlaArchive` (``MLA00000.MLA``, ``MLA00001.MLA``, …) instead of a
    single file. When the current file fills up the archive rolls over to the
    next one on its own.

    Independently decodable files (2b): each stream's first record in *every* file
    is a keyframe, so any single file decodes on its own without the ones before
    it. Two cooperating seams make that hold for any number of streams:

      • the stream that *triggers* the rollover is encoded as a keyframe up front
        — :meth:`CompressedChannel.log` calls :meth:`_will_rotate` before
        compressing, so a delta never crosses a file boundary;
      • every *other* open stream is reset by the ``on_rotate`` callback (wired
        through :class:`ChannelBank`), so its next record is a keyframe too.

    MlaArchive writes the schema/station tables into every rotated file's prefix,
    so each file is fully self-describing on its own as well.

    Usage::

        with GlueArchiveLogger(dir, schema_table=..., station_table=...) as lg:
            bank = ChannelBank(lg)                 # auto-wires the rotation seam
            bank.log(station, pkt_len, ts, row)    # rotates + keyframes itself
    """

    def __init__(self, directory: str, *,
                 schema_table: bytes = b"",
                 station_table: bytes = b"",
                 keyframe_intv: int | None = None,
                 file_size: int = 256 * 1024,
                 crc_mode: int = MLA_CRC_FULL,
                 base: str = "MLA",
                 digits: int = 5):
        """Open (creating if needed) a rotating archive in ``directory``.

        keyframe_intv — prefix hint only; ``None`` seeds DMD's cadence (same as
        GlueLogger). The schema/station tables are written into every file.
        """
        self.dir = directory
        self._kfi = DMD_KEYFRAME_EVERY if keyframe_intv is None else keyframe_intv
        self._on_rotate_cb = None
        self._archive = MlaArchive(
            directory, file_size=file_size, base=base, digits=digits,
            crc_mode=crc_mode, keyframe_intv=self._kfi,
            schema_table=schema_table, station_table=station_table,
            on_rotate=self._fire_rotate,
        )

    # ── rotation seam ─────────────────────────────────────────────────────────
    def _fire_rotate(self, prev_seq: int, new_seq: int) -> None:
        """MlaArchive calls this right after a rollover; forward it to whoever
        registered (ChannelBank), so all streams reset → keyframe in the new file."""
        if self._on_rotate_cb is not None:
            self._on_rotate_cb(prev_seq, new_seq)

    def set_on_rotate(self, cb) -> None:
        """Register the rollover callback (wired by ChannelBank)."""
        self._on_rotate_cb = cb

    def _will_rotate(self, data_len: int) -> bool:
        """True if appending ``data_len`` payload bytes would roll over to a new
        file — lets a compressed channel emit a keyframe up front."""
        return self._archive.will_rotate(data_len)

    # ── classic datalogger path (no compression) ─────────────────────────────
    def log_raw(self, timestamp: int, station: int, data: bytes,
                *, subsec: int = 0) -> bool:
        """Store one row verbatim (uncompressed). Returns True if it landed in a
        freshly rotated file (RAW records are self-contained, so it is only FYI)."""
        return self._append(timestamp, station, data, compressed=False, subsec=subsec)

    def log_event(self, timestamp: int, station: int, text, *, subsec: int = 0) -> bool:
        """Store a text / status event uncompressed (see GlueLogger.log_event)."""
        if isinstance(text, str):
            text = text.encode("utf-8")
        return self._append(timestamp, station, text, compressed=False, subsec=subsec)

    # ── compression path ──────────────────────────────────────────────────────
    def open_compressed_channel(self, station: int, pkt_len: int) -> CompressedChannel:
        """Open a NIC-DMD-compressed stream for one station / one fixed width."""
        return CompressedChannel(self, station, pkt_len)

    # ── internals / lifecycle ──────────────────────────────────────────────────
    def _append(self, timestamp: int, station: int, data: bytes,
                *, compressed: bool = False, kf_back: int = 0, subsec: int = 0) -> bool:
        return self._archive.append(timestamp, station, data,
                                    subsec=subsec, compressed=compressed, kf_back=kf_back)

    @property
    def keyframe_intv(self) -> int:
        return self._kfi

    @property
    def file_count(self) -> int:
        return self._archive.file_count

    def sync(self) -> None:
        self._archive.sync()

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> "GlueArchiveLogger":
        return self

    def __exit__(self, *_) -> None:
        self.close()
