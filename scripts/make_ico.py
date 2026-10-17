#!/usr/bin/env python3
"""Regenerate resources/icons/icon.ico as a multi-resolution Windows icon.

Windows, the desktop shortcut, and the Inno installer all read this file
(``--windows-icon-from-ico`` in the release workflow, ``SetupIconFile`` in
``installer.iss``). A single 16×16 frame is scaled up and looks soft, so the
file carries 16, 20, 24, 32, 40, 48, 64, 96, 128, and 256. Frames below 256
are 32-bit BMP; the 256 frame is PNG, which is how Windows stores the large
image.
"""

from __future__ import annotations

import argparse
import struct
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFilter

REPO_ROOT = Path(__file__).resolve().parent.parent
ICONS = REPO_ROOT / "resources" / "icons"
SOURCE_JPG = ICONS / "icon_source.jpg"
SOURCE_PNG = ICONS / "icon.png"
OUTPUT = ICONS / "icon.ico"

# Shell / taskbar sizes, including the DPI steps Windows asks for.
SIZES: tuple[int, ...] = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)

# Mean absolute error, per channel, under which the JPEG is the same artwork
# as icon.png. icon.png is an exact Lanczos downscale of icon_source.jpg today.
_ART_MATCH_MAE = 2.0

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def _mae(a: bytes, b: bytes) -> float:
    if len(a) != len(b) or not a:
        return 255.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / len(a)


def load_source() -> Image.Image:
    """Return RGBA pixels to downscale.

    ``icon_source.jpg`` is the 960×960 master. ``icon.png`` is the 256×256
    image the app and Linux builds use. When a Lanczos fit of the JPEG matches
    that PNG and the PNG is fully opaque, the JPEG is the same artwork at
    higher resolution and is the better source for every Windows size. If the
    two ever diverge, ``icon.png`` is the current icon and we scale from it.
    """
    png = Image.open(SOURCE_PNG).convert("RGBA")
    if not SOURCE_JPG.is_file():
        return png
    jpg = Image.open(SOURCE_JPG).convert("RGB")
    fitted = jpg.resize(png.size, Image.Resampling.LANCZOS, reducing_gap=None)
    same_art = _mae(fitted.tobytes(), png.convert("RGB").tobytes()) <= _ART_MATCH_MAE
    opaque = png.getchannel("A").getextrema() == (255, 255)
    if same_art and opaque:
        return jpg.convert("RGBA")
    return png


def _sharpen(im: Image.Image, *, radius: float, percent: int, threshold: int) -> Image.Image:
    """Light unsharp mask on RGB only, so a flat alpha plane stays untouched."""
    red, green, blue, alpha = im.split()
    rgb = Image.merge("RGB", (red, green, blue))
    rgb = rgb.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))
    red, green, blue = rgb.split()
    return Image.merge("RGBA", (red, green, blue, alpha))


def render_frame(src: Image.Image, size: int) -> Image.Image:
    """Lanczos downscale. Small frames get a light sharpen so they stay crisp."""
    frame = src.resize((size, size), Image.Resampling.LANCZOS, reducing_gap=None)
    if size <= 24:
        frame = _sharpen(frame, radius=0.5, percent=70, threshold=2)
    elif size <= 48:
        frame = _sharpen(frame, radius=0.5, percent=40, threshold=2)
    return frame


def _png_bytes(im: Image.Image) -> bytes:
    buf = BytesIO()
    im.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if not data.startswith(_PNG_SIG):
        raise RuntimeError("PNG encoder did not write a PNG signature")
    return data


def _bmp_bytes(im: Image.Image) -> bytes:
    """32-bit ICO image: bottom-up BGRA DIB plus a 1-bit AND mask.

    The DIB height is XOR height + AND height. Fully opaque pixels keep a
    zero AND bit; alpha below 128 is treated as transparent.
    """
    width, height = im.size
    raw = BytesIO()
    im.save(raw, format="DIB")
    dib = bytearray(raw.getvalue())
    xor_bytes = width * height * 4
    if len(dib) != 40 + xor_bytes:
        raise RuntimeError(f"unexpected DIB size {len(dib)} for {width}x{height}")
    struct.pack_into("<i", dib, 8, height * 2)
    and_row = ((width + 31) // 32) * 4
    and_bytes = and_row * height
    mask = bytearray(and_bytes)
    pixels = im.tobytes()
    for y in range(height):
        # AND rows are bottom-up, same as the DIB.
        row_off = (height - 1 - y) * and_row
        for x in range(width):
            alpha = pixels[(y * width + x) * 4 + 3]
            if alpha < 128:
                mask[row_off + (x // 8)] |= 0x80 >> (x % 8)
    struct.pack_into("<I", dib, 20, xor_bytes + and_bytes)
    return bytes(dib) + bytes(mask)


def build_ico(frames: list[Image.Image]) -> bytes:
    """Pack square frames. 256 is stored as PNG; smaller sizes as BMP."""
    images: list[tuple[int, int, bytes]] = []
    for frame in frames:
        width, height = frame.size
        if width != height:
            raise ValueError(f"icon frames must be square, got {width}x{height}")
        if width >= 256:
            blob = _png_bytes(frame)
        else:
            blob = _bmp_bytes(frame)
        images.append((width, height, blob))

    out = BytesIO()
    out.write(struct.pack("<HHH", 0, 1, len(images)))
    offset = 6 + 16 * len(images)
    for width, height, blob in images:
        out.write(
            struct.pack(
                "<BBBBHHII",
                0 if width >= 256 else width,
                0 if height >= 256 else height,
                0,
                0,
                1,
                32,
                len(blob),
                offset,
            )
        )
        offset += len(blob)
    for _, _, blob in images:
        out.write(blob)
    return out.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=OUTPUT,
        help=f"destination .ico (default: {OUTPUT.relative_to(REPO_ROOT)})",
    )
    args = parser.parse_args()
    src = load_source()
    frames = [render_frame(src, size) for size in SIZES]
    data = build_ico(frames)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(f"wrote {args.output} ({len(data)} bytes, sizes {', '.join(map(str, SIZES))})")


if __name__ == "__main__":
    main()
