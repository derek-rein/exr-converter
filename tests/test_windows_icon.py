"""Windows icon.ico carries the shell sizes, including 16 and 256."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image
from PIL.IcoImagePlugin import IcoFile

ICON = Path("resources/icons/icon.ico")
ICON_PNG = Path("resources/icons/icon.png")
# Sizes Windows asks for. 256 is stored as a PNG frame; 16 must be present
# so the shell does not invent it by scaling something else.
EXPECTED_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _ico_frames(data: bytes) -> dict[int, bytes]:
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    assert reserved == 0
    assert kind == 1
    frames: dict[int, bytes] = {}
    for index in range(count):
        width_b, height_b, _colors, _reserved, _planes, _bpp, size, offset = struct.unpack_from(
            "<BBBBHHII", data, 6 + index * 16
        )
        width = width_b or 256
        height = height_b or 256
        assert width == height
        frames[width] = data[offset : offset + size]
    return frames


def test_icon_ico_contains_16_and_256() -> None:
    data = ICON.read_bytes()
    frames = _ico_frames(data)
    assert 16 in frames
    assert 256 in frames
    assert tuple(sorted(frames)) == EXPECTED_SIZES
    assert frames[256].startswith(_PNG_SIG)
    # Smaller frames are classic 32-bit BMP (BITMAPINFOHEADER), not PNG.
    assert frames[16].startswith(b"\x28\x00\x00\x00")

    ico = IcoFile(io.BytesIO(data))
    assert {(16, 16), (256, 256)} <= ico.sizes()
    for size in ((16, 16), (256, 256)):
        frame = ico.getimage(size)
        frame.load()
        assert frame.size == size


def test_icon_256_matches_current_png_art() -> None:
    """The large frame is the same artwork as the in-app icon.png."""
    frames = _ico_frames(ICON.read_bytes())
    with Image.open(ICON_PNG) as png, Image.open(io.BytesIO(frames[256])) as big:
        assert big.size == (256, 256)
        assert big.convert("RGBA").tobytes() == png.convert("RGBA").tobytes()
