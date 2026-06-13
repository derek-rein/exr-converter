"""Slate rendering: pure QPainter -> numpy buffer.

Renders a film-style slate frame using QPainter (no web engine, no HTML).
The layout is faithfully ported from the previous HTML/CSS template:
identity bar at the top, red divider, then a two-column body with metadata
on the left and a thumbnail + SMPTE colour bars + summary table + vendor
logo on the right.

Sizes are expressed in ``vh``/``vw`` (viewport %) units so the layout is
fully resolution-independent.

Public surface
--------------
- :func:`render_slate_frame` -> ``float32`` RGBA numpy array, used by the
  conversion pipeline to inject a slate frame.
- :class:`SlatePreviewWidget` -> a QWidget that paints the slate live,
  used by :mod:`.slate_widgets` for the editor preview.
- :data:`SLATE_COLORSPACE` -> the colour space of the rendered frame
  (``"sRGB"``); the caller is responsible for OCIO-transforming into the
  pipeline's destination space.
"""

from __future__ import annotations

import base64
import logging

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QImage,
    QLinearGradient,
    QPainter,
)
from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


SLATE_COLORSPACE = "sRGB"
"""The colorspace of the rendered slate frame (always sRGB)."""


# ---------------------------------------------------------------------------
# Palette (sRGB approximations of the previous oklch/rec2020 design tokens)
# ---------------------------------------------------------------------------

_BG = QColor(51, 51, 51)
_BG_DEEP = QColor(35, 35, 35)
_TEXT = QColor(225, 225, 225)
_TEXT_BRIGHT = QColor(247, 247, 247)
_TEXT_MID = QColor(197, 197, 197)
_TEXT_DIM = QColor(137, 137, 137)
_TEXT_MUTED = QColor(93, 93, 93)
_BORDER = QColor(77, 77, 77)
_BORDER_FAINT = QColor(58, 58, 58)
_RED_DIVIDER = QColor(206, 33, 26)

# SMPTE 75% bars
_BAR_W = QColor(191, 191, 191)
_BAR_Y = QColor(191, 191, 0)
_BAR_C = QColor(0, 191, 191)
_BAR_G = QColor(0, 191, 0)
_BAR_M = QColor(191, 0, 191)
_BAR_R = QColor(191, 0, 0)
_BAR_B = QColor(0, 0, 191)
_BAR_BLACK = QColor(0, 0, 0)
_BAR_FULL_WHITE = QColor(255, 255, 255)

# PLUGE patches
_PLUGE_SUB = QColor(5, 5, 5)
_PLUGE_REF = QColor(12, 12, 12)
_PLUGE_OVER = QColor(22, 22, 22)

# Alternating-row table backgrounds
_TABLE_ROW_ODD = QColor(44, 44, 44)
_TABLE_ROW_EVEN = QColor(53, 53, 53)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _font(px: float, bold: bool = False, italic: bool = False) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bold)
    f.setItalic(italic)
    return f


def decode_thumbnail_b64(b64: str) -> QImage | None:
    """Decode a raw base64 (JPEG/PNG) string into a QImage, or return ``None``."""
    if not b64:
        return None
    try:
        data = base64.b64decode(b64)
    except Exception:
        return None
    img = QImage()
    if not img.loadFromData(data):
        return None
    return img


def _draw_text_baseline(
    p: QPainter,
    font: QFont,
    color: QColor,
    x: float,
    baseline_y: float,
    text: str,
) -> float:
    """Draw text with its baseline at ``baseline_y``; return horizontal advance."""
    p.setFont(font)
    p.setPen(color)
    p.drawText(int(round(x)), int(round(baseline_y)), text)
    return QFontMetricsF(font).horizontalAdvance(text)


# ---------------------------------------------------------------------------
# Core paint routine
# ---------------------------------------------------------------------------


def _paint_slate(
    p: QPainter,
    width: int,
    height: int,
    data: dict,
    thumbnail: QImage | None = None,
) -> None:
    """Paint the full slate into ``p`` covering ``(0, 0, width, height)``."""
    p.setRenderHints(
        QPainter.RenderHint.Antialiasing
        | QPainter.RenderHint.TextAntialiasing
        | QPainter.RenderHint.SmoothPixmapTransform
    )
    p.fillRect(0, 0, width, height, _BG)

    vh = height / 100.0
    vw = width / 100.0
    pad_x = 2.0 * vw
    pad_y = 2.5 * vh

    # ── Top: identity bar (Show · Seq · Shot · Version) ──
    label_font = _font(1.9 * vh)
    value_font = _font(3.8 * vh, bold=True)
    sep_font = _font(3.0 * vh)

    fm_value = QFontMetricsF(value_font)
    fm_sep = QFontMetricsF(sep_font)

    cells = [
        ("SHOW", str(data.get("show") or "SHOW")),
        ("SEQ", str(data.get("sequence") or "SEQ")),
        ("SHOT", str(data.get("shot") or "SHOT")),
        ("VERSION", str(data.get("version") or "v001")),
    ]

    y_id_top = pad_y
    baseline = y_id_top + fm_value.ascent()
    cur_x = pad_x
    cell_gap = 1.2 * vw
    label_value_gap = 0.3 * vw

    for idx, (label, value) in enumerate(cells):
        if idx > 0:
            sep_w = fm_sep.horizontalAdvance("\u00b7")
            _draw_text_baseline(p, sep_font, _TEXT_MUTED, cur_x, baseline, "\u00b7")
            cur_x += sep_w + cell_gap
        adv_l = _draw_text_baseline(p, label_font, _TEXT_MUTED, cur_x, baseline, label)
        cur_x += adv_l + label_value_gap
        adv_v = _draw_text_baseline(p, value_font, _TEXT_BRIGHT, cur_x, baseline, value)
        cur_x += adv_v + cell_gap * 0.4

    y_after_id = y_id_top + fm_value.height() + 0.8 * vh

    # ── Red divider ──
    div_h = max(1.0, 0.3 * vh)
    p.fillRect(QRectF(pad_x, y_after_id, width - 2 * pad_x, div_h), _RED_DIVIDER)
    y_after_div = y_after_id + div_h + 1.2 * vh

    # ── Body geometry ──
    right_col_w = 30.0 * vw
    right_col_x = width - pad_x - right_col_w
    left_col_x = pad_x
    left_col_right_pad = 1.5 * vw

    # ── Left column: "Submitting For" header + field rows ──
    sub_label_font = _font(2.2 * vh)
    sub_value_font = _font(5.5 * vh, bold=True)
    fm_sv = QFontMetricsF(sub_value_font)

    sub_top = y_after_div
    sub_baseline = sub_top + fm_sv.ascent()

    sx = left_col_x
    adv = _draw_text_baseline(p, sub_label_font, _TEXT_DIM, sx, sub_baseline, "Submitting For:")
    sx += adv + 0.4 * vw
    _draw_text_baseline(
        p,
        sub_value_font,
        _TEXT_BRIGHT,
        sx,
        sub_baseline,
        str(data.get("submitFor") or "WIP"),
    )

    y_after_sub = sub_top + fm_sv.height() + 0.8 * vh

    field_label_font = _font(2.1 * vh)
    field_value_font = _font(3.2 * vh, bold=True)
    field_value_body_font = _font(2.7 * vh)
    fm_fl = QFontMetricsF(field_label_font)

    rows: list[tuple[str, str, str]] = []
    if data.get("date"):
        rows.append(("Date:", str(data["date"]), "value"))
    if data.get("notes"):
        rows.append(("Submission Note:", str(data["notes"]), "body"))
    if data.get("shotTypes"):
        rows.append(("Shot Types:", str(data["shotTypes"]), "value"))
    if data.get("description"):
        rows.append(("Shot Description:", str(data["description"]), "body"))
    if data.get("scope"):
        rows.append(("VFX Scope Of Work:", str(data["scope"]), "body"))

    if rows:
        max_label_w = max(fm_fl.horizontalAdvance(r[0]) for r in rows)
    else:
        max_label_w = 0.0
    label_col_right = left_col_x + max_label_w
    value_col_x = label_col_right + 0.6 * vw
    value_col_w = max(0.0, (right_col_x - left_col_right_pad) - value_col_x)

    yfr = y_after_sub
    row_pad_v = 0.3 * vh
    row_gap = 0.3 * vh
    for label, value, kind in rows:
        if kind == "body":
            vfont = field_value_body_font
            vcolor = _TEXT_MID
        else:
            vfont = field_value_font
            vcolor = _TEXT
        fm_v = QFontMetricsF(vfont)
        flags = (
            int(Qt.AlignmentFlag.AlignTop)
            | int(Qt.AlignmentFlag.AlignLeft)
            | int(Qt.TextFlag.TextWordWrap)
        )
        rect = QRectF(value_col_x, yfr + row_pad_v, value_col_w, 1e6)
        bound = p.boundingRect(rect, flags, value)

        # Align the label baseline to the first line of the (possibly wrapped) value.
        first_baseline = yfr + row_pad_v + fm_v.ascent()
        lw = fm_fl.horizontalAdvance(label)
        _draw_text_baseline(
            p,
            field_label_font,
            _TEXT_DIM,
            label_col_right - lw,
            first_baseline,
            label,
        )

        p.setFont(vfont)
        p.setPen(vcolor)
        draw_rect = QRectF(
            value_col_x, yfr + row_pad_v, value_col_w, max(fm_v.height(), bound.height())
        )
        p.drawText(draw_rect, flags, value)

        yfr += max(fm_v.height(), bound.height()) + row_pad_v + row_gap

    # ── Right column ──
    rcx = right_col_x
    ry = y_after_div

    # Thumbnail box
    thumb_w = right_col_w
    thumb_h = thumb_w * 9.0 / 16.0
    thumb_rect = QRectF(rcx, ry, thumb_w, thumb_h)
    p.fillRect(thumb_rect, _BG_DEEP)
    p.setPen(_BORDER)
    p.drawRect(thumb_rect.adjusted(0, 0, -1, -1))

    if thumbnail is not None and not thumbnail.isNull():
        scaled = thumbnail.scaled(
            int(thumb_w),
            int(thumb_h),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = rcx + (thumb_w - scaled.width()) / 2.0
        sy = ry + (thumb_h - scaled.height()) / 2.0
        p.drawImage(QRectF(sx, sy, scaled.width(), scaled.height()), scaled)
    else:
        placeholder_font = _font(2.1 * vh)
        fm_p = QFontMetricsF(placeholder_font)
        msg = "THUMBNAIL"
        tw = fm_p.horizontalAdvance(msg)
        _draw_text_baseline(
            p,
            placeholder_font,
            _TEXT_MUTED,
            rcx + (thumb_w - tw) / 2.0,
            ry + thumb_h / 2.0 + fm_p.ascent() / 2.0,
            msg,
        )

    ry += thumb_h

    # SMPTE bars (3 stacked rows)
    main_h = 2.6 * vh
    cast_h = 0.8 * vh
    bottom_h = 1.4 * vh

    main_colors = [_BAR_W, _BAR_Y, _BAR_C, _BAR_G, _BAR_M, _BAR_R, _BAR_B]
    seg_w = thumb_w / len(main_colors)
    for i, col in enumerate(main_colors):
        p.fillRect(QRectF(rcx + i * seg_w, ry, seg_w + 1, main_h), col)
    ry += main_h

    cast_colors = [
        _BAR_B,
        _BAR_BLACK,
        _BAR_M,
        _BAR_BLACK,
        _BAR_C,
        _BAR_BLACK,
        _BAR_W,
    ]
    seg_w = thumb_w / len(cast_colors)
    for i, col in enumerate(cast_colors):
        p.fillRect(QRectF(rcx + i * seg_w, ry, seg_w + 1, cast_h), col)
    ry += cast_h

    # Bottom row: PLUGE (1 unit) + gradient (5 units) + ref-white (1 unit)
    total_units = 1 + 5 + 1
    unit_w = thumb_w / total_units
    pluge_w = unit_w
    grad_w = unit_w * 5
    ref_w = unit_w

    pluge_x = rcx
    for i, col in enumerate([_PLUGE_SUB, _PLUGE_REF, _PLUGE_OVER]):
        p.fillRect(
            QRectF(pluge_x + (pluge_w / 3.0) * i, ry, pluge_w / 3.0 + 1, bottom_h),
            col,
        )
    grad_x = pluge_x + pluge_w
    grad = QLinearGradient(grad_x, 0, grad_x + grad_w, 0)
    grad.setColorAt(0.0, _BAR_BLACK)
    grad.setColorAt(1.0, _BAR_FULL_WHITE)
    p.fillRect(QRectF(grad_x, ry, grad_w, bottom_h), QBrush(grad))
    ref_x = grad_x + grad_w
    p.fillRect(QRectF(ref_x, ry, ref_w, bottom_h), _BAR_FULL_WHITE)
    ry += bottom_h

    # Right-side fields table
    table_font_label = _font(2.1 * vh)
    table_font_value = _font(2.1 * vh, bold=True)
    fm_t = QFontMetricsF(table_font_label)
    cell_pad_y = 0.5 * vh
    cell_pad_x = 0.4 * vw
    row_h = fm_t.height() + 2 * cell_pad_y

    table_rows: list[tuple[str, str]] = []
    if data.get("vendor"):
        table_rows.append(("Vendor:", str(data["vendor"])))
    table_rows.append(("Artist:", str(data.get("artist") or "\u2014")))
    if data.get("take"):
        table_rows.append(("Take:", str(data["take"])))
    table_rows.append(("Frames:", str(data.get("frameRange") or "\u2014")))
    table_rows.append(("FPS:", str(data.get("fps") or "24")))
    table_rows.append(("Resolution:", str(data.get("resolution") or "\u2014")))
    table_rows.append(("Media Color:", str(data.get("colorspace") or "\u2014")))

    for i, (k, v) in enumerate(table_rows):
        bg = _TABLE_ROW_ODD if i % 2 == 0 else _TABLE_ROW_EVEN
        row_rect = QRectF(rcx, ry, thumb_w, row_h)
        p.fillRect(row_rect, bg)
        baseline_t = ry + cell_pad_y + fm_t.ascent()
        _draw_text_baseline(p, table_font_label, _TEXT_DIM, rcx + cell_pad_x, baseline_t, k)
        vw_adv = QFontMetricsF(table_font_value).horizontalAdvance(v)
        _draw_text_baseline(
            p,
            table_font_value,
            _TEXT,
            rcx + thumb_w - cell_pad_x - vw_adv,
            baseline_t,
            v,
        )
        # bottom hairline
        p.setPen(_BORDER_FAINT)
        p.drawLine(
            int(rcx),
            int(ry + row_h - 1),
            int(rcx + thumb_w),
            int(ry + row_h - 1),
        )
        ry += row_h

    # Logo box at bottom-right (only if "logo" provided)
    logo = str(data.get("logo") or "").strip()
    if logo:
        logo_font = _font(2.8 * vh, bold=True, italic=True)
        fm_l = QFontMetricsF(logo_font)
        box_pad_x = 0.8 * vw
        box_pad_y = 1.0 * vh
        text_w = fm_l.horizontalAdvance(logo)
        text_h = fm_l.height()
        box_w = text_w + 2 * box_pad_x
        box_h = text_h + 2 * box_pad_y
        bx = right_col_x + right_col_w - box_w
        by = height - pad_y - box_h
        p.setPen(_BORDER)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(QRectF(bx, by, box_w, box_h))
        _draw_text_baseline(
            p,
            logo_font,
            _TEXT_DIM,
            bx + box_pad_x,
            by + box_pad_y + fm_l.ascent(),
            logo,
        )


# ---------------------------------------------------------------------------
# Live preview widget (used by the editor dialog)
# ---------------------------------------------------------------------------


class SlatePreviewWidget(QWidget):
    """A QWidget that paints the slate live for the editor preview.

    Use :meth:`set_data` to update the metadata fields and :meth:`set_thumbnail_image`
    to set the embedded thumbnail.  The widget repaints whenever either changes.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data: dict = {}
        self._thumb: QImage | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setAutoFillBackground(False)

    def set_data(self, data: dict) -> None:
        self._data = dict(data)
        self.update()

    def set_thumbnail_image(self, img: QImage | None) -> None:
        self._thumb = img
        self.update()

    def set_thumbnail_b64(self, b64: str) -> None:
        self.set_thumbnail_image(decode_thumbnail_b64(b64))

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        try:
            _paint_slate(p, self.width(), self.height(), self._data, self._thumb)
        finally:
            p.end()


# ---------------------------------------------------------------------------
# Offline render (for the conversion pipeline)
# ---------------------------------------------------------------------------


def _qimage_to_numpy_rgba(img: QImage) -> np.ndarray:
    """Convert a QImage (any format) to a uint8 RGBA numpy array (h, w, 4)."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()


def _srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """sRGB -> linear (RGB only; alpha untouched)."""
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]
    low = rgb / 12.92
    high = np.power((rgb + 0.055) / 1.055, 2.4)
    linear_rgb = np.where(rgb <= 0.04045, low, high)
    return np.concatenate([linear_rgb, alpha], axis=-1)


def render_slate_frame(
    slate_data: dict,
    width: int,
    height: int,
    template_path: object | None = None,  # accepted for backwards compat; unused
    linearize: bool = False,
    thumbnail_b64: str = "",
) -> np.ndarray:
    """Paint the slate at ``(width, height)`` and return a float32 RGBA buffer.

    Output is in **sRGB** by default; the caller is expected to OCIO-transform
    from :data:`SLATE_COLORSPACE` to the pipeline's destination colorspace.
    Set ``linearize=True`` to apply the sRGB→linear transfer function locally
    (rarely needed, since OCIO usually handles it).

    The ``template_path`` argument is accepted but ignored — kept only so that
    callers from the previous HTML-template implementation continue to work.

    Returns
    -------
    np.ndarray
        ``float32`` array of shape ``(height, width, 4)`` in [0, 1].
    """
    img = QImage(int(width), int(height), QImage.Format.Format_RGBA8888)
    img.fill(_BG)

    thumbnail = decode_thumbnail_b64(thumbnail_b64) if thumbnail_b64 else None

    p = QPainter(img)
    try:
        _paint_slate(p, int(width), int(height), slate_data, thumbnail)
    finally:
        p.end()

    pixels = _qimage_to_numpy_rgba(img).astype(np.float32) / 255.0
    if linearize:
        pixels = _srgb_to_linear(pixels)
    return pixels


__all__ = [
    "SLATE_COLORSPACE",
    "SlatePreviewWidget",
    "decode_thumbnail_b64",
    "render_slate_frame",
]
