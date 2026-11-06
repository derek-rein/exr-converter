"""Write small synthetic ProRes RAW clips for the tests.

There is no ProRes RAW encoder in FFmpeg, and test clips from cameras are
large and not ours to commit. This writes a valid bitstream FFmpeg's
``prores_raw`` decoder reads, plus a minimal QuickTime ``.mov`` around it.

Only DC coefficients are coded, so every 8x8 block of a Bayer component is
flat. One block covers 16x16 sensor pixels. That is enough to place known
patches and check black/white levels, white balance, the camera matrix and
the bit depth through the whole decode path.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

# Decoder codebooks (FFmpeg libavcodec/prores_raw.c).
_DC_FIRST_CB = 700
_DC_ROW_CB = 100
_DC_CB = (0x010, 0x021, 0x032, 0x033, 0x033, 0x033, 0x044, 0x044, 0x044, 0x044, 0x044, 0x044, 0x076)
_LN_START_CB = 66
_RN_START_CB = 0


class _BitWriter:
    def __init__(self) -> None:
        self._bits: list[int] = []

    def put(self, value: int, nbits: int) -> None:
        for i in range(nbits - 1, -1, -1):
            self._bits.append((value >> i) & 1)

    def to_bytes(self) -> bytes:
        bits = self._bits + [0] * (-len(self._bits) % 8)
        out = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for b in bits[i : i + 8]:
                byte = (byte << 1) | b
            out.append(byte)
        return bytes(out)


def _put_value(bw: _BitWriter, value: int, codebook: int) -> None:
    """Inverse of the decoder's ``get_value``."""
    switch_bits = codebook >> 8
    rice_order = codebook & 0xF
    exp_order = (codebook >> 4) & 0xF
    if value < 0:
        raise ValueError("ProRes RAW codes are unsigned")
    if value < (switch_bits + 1) << rice_order:
        q = value >> rice_order
        bw.put(0, q)
        bw.put(1, 1)
        bw.put(value & ((1 << rice_order) - 1), rice_order)
        return
    v = value - ((switch_bits + 1) << rice_order) + (1 << exp_order)
    length = v.bit_length()
    q = length - exp_order + switch_bits
    if q + length > 32:
        raise ValueError(f"value {value} too large for codebook {codebook:#x}")
    bw.put(0, q)
    bw.put(v, length)


def _encode_component(dc_values: Sequence[int] | np.ndarray, nb_blocks: int) -> bytes:
    """One Bayer component of one tile: flat blocks, no AC coefficients."""
    bw = _BitWriter()
    first = int(dc_values[0]) - 1
    dc = 2 * first if first >= 0 else -2 * first - 1
    _put_value(bw, dc, _DC_FIRST_CB)
    prev = int(dc_values[0])
    sign = 0
    for n in range(1, nb_blocks):
        if (n & 15) == 1:
            codebook = _DC_ROW_CB
        else:
            codebook = _DC_CB[min((dc + 1) >> 1, len(_DC_CB) - 1)]
        delta = int(dc_values[n]) - prev
        magnitude = abs(delta)
        if magnitude == 0:
            dc = 0
        else:
            parity = sign ^ (1 if delta < 0 else 0)
            dc = 2 * magnitude - 1 if parity else 2 * magnitude
        _put_value(bw, dc, codebook)
        sign = 1 if delta < 0 else 0
        prev = int(dc_values[n])
    nb_codes = 64 * nb_blocks
    _put_value(bw, 0, _LN_START_CB)  # no leading AC coefficients
    _put_value(bw, nb_codes - nb_blocks - 1, _RN_START_CB)  # zero run to the end
    return bw.to_bytes()


@dataclass
class FrameHeader:
    width: int
    height: int
    sensel_range: int = 0xFFFF - 0x200  # white = range + 0x100
    wb_red: float = 1.0
    wb_blue: float = 1.0
    # Camera RGB to XYZ D65, row-major.
    matrix: tuple[float, ...] = (
        0.4124564,
        0.3575761,
        0.1804375,
        0.2126729,
        0.7151522,
        0.0721750,
        0.0193339,
        0.1191920,
        0.9503041,
    )
    gain: float = 1.0
    cct: int = 5600
    crop: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, right, top, bottom
    vendor: bytes = b"atm0"
    curve: tuple[int, ...] | None = None  # 8 control points; None = decoder default
    qscale: int = 1
    qmat: tuple[int, ...] = field(default_factory=lambda: (1,) * 64)


def encode_frame(dc: np.ndarray, header: FrameHeader, *, align: int = 3) -> bytes:
    """Encode one frame.

    ``dc`` has shape ``(4, tile_rows, blocks_per_row)``: DC value per block for
    Bayer components R, G (row 0), G (row 1), B in RGGB order.
    """
    comps, rows, blocks_per_row = dc.shape
    if comps != 4:
        raise ValueError("dc needs 4 components")
    unit = 1 << align
    if blocks_per_row % unit:
        raise ValueError("blocks_per_row must be a multiple of 2**align")
    if header.width != blocks_per_row * 16 or header.height != rows * 16:
        raise ValueError("header size does not match the block grid")

    flags = (align << 1) | 1
    if header.curve is not None:
        flags |= 1 << 4
    hdr = bytearray()
    hdr += bytes([0, 0])  # reserved, version 0
    hdr += header.vendor[:4].ljust(4, b"\0")
    hdr += struct.pack(">HH", header.width, header.height)
    hdr += bytes(header.crop)
    hdr += struct.pack(">H", 0)  # RGGB
    hdr += struct.pack(">H", header.sensel_range)
    hdr += struct.pack(">ff", header.wb_red, header.wb_blue)
    hdr += struct.pack(">9f", *header.matrix)
    hdr += struct.pack(">f", header.gain)
    hdr += struct.pack(">H", header.cct)
    hdr += struct.pack(">H", flags)
    hdr += bytes(header.qmat)
    if header.curve is not None:
        hdr += struct.pack(">8H", *header.curve)

    # Decoder component order inside a tile: (1,0), (0,1), (1,1), (0,0).
    order = (2, 1, 3, 0)
    tiles: list[bytes] = []
    for row in range(rows):
        for start in range(0, blocks_per_row, unit):
            parts = [_encode_component(dc[c, row, start : start + unit], unit) for c in order]
            tile = bytes([8 << 3, header.qscale & 0xFF])
            tile += struct.pack(">HHH", len(parts[0]), len(parts[1]), len(parts[2]))
            tile += b"".join(parts)
            tiles.append(tile)

    body = bytearray()
    body += b"prrf"
    body += struct.pack(">H", len(hdr) + 2)
    body += hdr
    for tile in tiles:
        body += struct.pack(">H", len(tile))
    for tile in tiles:
        body += tile
    return struct.pack(">I", len(body) + 4) + bytes(body)


# ---------------------------------------------------------------------------
# Minimal QuickTime writer
# ---------------------------------------------------------------------------


def _atom(kind: bytes, *payload: bytes) -> bytes:
    data = b"".join(payload)
    return struct.pack(">I", len(data) + 8) + kind + data


def _full(kind: bytes, version_flags: int, *payload: bytes) -> bytes:
    return _atom(kind, struct.pack(">I", version_flags), *payload)


_MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


def write_mov(
    path: str,
    frames: Sequence[bytes],
    width: int,
    height: int,
    *,
    fps: int = 24,
    fourcc: bytes = b"aprn",
) -> None:
    """Write ``frames`` as one ProRes RAW video track."""
    timescale = fps * 100
    frame_dur = 100
    duration = frame_dur * len(frames)

    ftyp = _atom(b"ftyp", b"qt  ", struct.pack(">I", 0), b"qt  ")
    mdat_payload = b"".join(frames)
    mdat = _atom(b"mdat", mdat_payload)
    first_offset = len(ftyp) + 8

    mvhd = _full(
        b"mvhd",
        0,
        struct.pack(">IIII", 0, 0, timescale, duration),
        struct.pack(">IH", 0x10000, 0x100),
        b"\0" * 10,
        _MATRIX,
        b"\0" * 24,
        struct.pack(">I", 2),
    )
    tkhd = _full(
        b"tkhd",
        0xF,
        struct.pack(">IIIII", 0, 0, 1, 0, duration),
        b"\0" * 8,
        struct.pack(">HHHH", 0, 0, 0, 0),
        _MATRIX,
        struct.pack(">II", width << 16, height << 16),
    )
    mdhd = _full(b"mdhd", 0, struct.pack(">IIIIHH", 0, 0, timescale, duration, 0, 0))
    hdlr = _full(b"hdlr", 0, b"mhlr", b"vide", b"\0" * 12, b"\0")
    vmhd = _full(b"vmhd", 1, b"\0" * 8)
    dref = _full(b"dref", 0, struct.pack(">I", 1), _full(b"alis", 1))
    dinf = _atom(b"dinf", dref)
    entry = (
        b"\0" * 6
        + struct.pack(">H", 1)
        + struct.pack(">HH", 0, 0)
        + b"appl"
        + struct.pack(">II", 0, 0)
        + struct.pack(">HH", width, height)
        + struct.pack(">II", 0x480000, 0x480000)
        + struct.pack(">I", 0)
        + struct.pack(">H", 1)
        + bytes([16])
        + b"Apple ProRes RAW".ljust(31, b"\0")
        + struct.pack(">Hh", 24, -1)
    )
    stsd = _full(b"stsd", 0, struct.pack(">I", 1), _atom(fourcc, entry))
    stts = _full(b"stts", 0, struct.pack(">III", 1, len(frames), frame_dur))
    stsc = _full(b"stsc", 0, struct.pack(">IIII", 1, 1, len(frames), 1))
    stsz = _full(
        b"stsz",
        0,
        struct.pack(">II", 0, len(frames)),
        b"".join(struct.pack(">I", len(f)) for f in frames),
    )
    stco = _full(b"stco", 0, struct.pack(">II", 1, first_offset))
    stbl = _atom(b"stbl", stsd, stts, stsc, stsz, stco)
    minf = _atom(b"minf", vmhd, hdlr, dinf, stbl)
    mdia = _atom(b"mdia", mdhd, hdlr, minf)
    trak = _atom(b"trak", tkhd, mdia)
    moov = _atom(b"moov", mvhd, trak)
    with open(path, "wb") as fh:
        fh.write(ftyp + mdat + moov)
