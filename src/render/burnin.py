"""Burn-in (overlay / chyron) renderer using QPainter.

Renders six fixed text positions onto a transparent buffer:

    top_left: vendor        top_center: show        top_right: date
    bottom_left: version    (center empty)          bottom_right: frames

The resulting RGBA overlay has real per-pixel alpha (no difference-matte
trick required), so it composites cleanly onto rgb48 frames via OIIO.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def burnin_fields_from_slate(
    slate_data: dict,
    input_path: str = "",
) -> dict[str, str]:
    """Derive the six fixed burn-in text fields from existing slate data."""
    vendor = slate_data.get("vendor", "")
    show = slate_data.get("show", "")
    frames = slate_data.get("frameRange", "")
    date_str = datetime.date.today().isoformat()

    stem = Path(input_path).stem if input_path else ""
    seq = slate_data.get("sequence", "")
    shot = slate_data.get("shot", "")
    version_name = f"{show}_{seq}_{shot}_{stem}" if show else stem

    return {
        "top_left": vendor,
        "top_center": show,
        "top_right": date_str,
        "bottom_left": version_name,
        "bottom_center": "",
        "bottom_right": frames,
    }


def render_burnin_overlay(
    width: int,
    height: int,
    fields: dict[str, str],
) -> np.ndarray:
    """Paint the six burn-in text cells and return a uint8 RGBA overlay."""
    img = QImage(int(width), int(height), QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))

    p = QPainter(img)
    try:
        _paint_burnin(p, int(width), int(height), fields)
    finally:
        p.end()

    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape((height, width, 4)).copy()


def composite_burnin(
    frame_u16: np.ndarray,
    overlay_u8: np.ndarray,
) -> np.ndarray:
    """Composite a uint8 RGBA overlay onto an rgb48 (uint16) frame via OIIO.

    Uses ``ImageBufAlgo.over()`` for correct alpha-over compositing.
    Returns a new uint16 array (h, w, 3).
    """
    import OpenImageIO as oiio

    h, w = frame_u16.shape[:2]

    fg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    fg_buf = oiio.ImageBuf(fg_spec)
    fg_buf.set_pixels(oiio.ROI.All, overlay_u8.astype(np.float32) / 255.0)

    bg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    bg_buf = oiio.ImageBuf(bg_spec)
    frame_f = frame_u16.astype(np.float32) / 65535.0
    rgba = np.concatenate([frame_f, np.ones((h, w, 1), dtype=np.float32)], axis=-1)
    bg_buf.set_pixels(oiio.ROI.All, rgba)

    result_buf = oiio.ImageBufAlgo.over(fg_buf, bg_buf)

    pixels = result_buf.get_pixels(oiio.FLOAT)
    rgb = pixels[:, :, :3]
    return np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)


# ---------------------------------------------------------------------------
# Painter
# ---------------------------------------------------------------------------


_FG = QColor(210, 210, 210, 230)
_BG = QColor(0, 0, 0, 128)


def _mono_font(px: float) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(max(1, int(round(px))))
    f.setWeight(QFont.Weight.DemiBold)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 103.0)
    return f


def _paint_burnin(p: QPainter, width: int, height: int, fields: dict[str, str]) -> None:
    """Draw six corner text cells onto a transparent canvas."""
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

    vh = height / 100.0
    vw = width / 100.0
    font = _mono_font(2.5 * vh)
    fm = QFontMetricsF(font)
    pad_x = 0.5 * vw
    pad_y = 0.3 * vh

    cells = (
        ("top_left", Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
        ("top_center", Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop),
        ("top_right", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop),
        ("bottom_left", Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
        ("bottom_center", Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom),
        ("bottom_right", Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom),
    )

    for key, align in cells:
        text = str(fields.get(key) or "")
        if not text:
            continue
        text_w = fm.horizontalAdvance(text)
        text_h = fm.height()
        box_w = text_w + 2 * pad_x
        box_h = text_h + 2 * pad_y

        is_top = bool(align & Qt.AlignmentFlag.AlignTop)
        if align & Qt.AlignmentFlag.AlignLeft:
            bx = 0.0
        elif align & Qt.AlignmentFlag.AlignRight:
            bx = float(width) - box_w
        else:
            bx = (float(width) - box_w) / 2.0
        by = 0.0 if is_top else float(height) - box_h

        p.fillRect(QRectF(bx, by, box_w, box_h), _BG)
        p.setFont(font)
        p.setPen(_FG)
        baseline = by + pad_y + fm.ascent()
        p.drawText(int(round(bx + pad_x)), int(round(baseline)), text)


__all__ = [
    "burnin_fields_from_slate",
    "composite_burnin",
    "render_burnin_overlay",
]
