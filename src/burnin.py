"""Burn-in (overlay / chyron) renderer.

Renders an HTML overlay template via QWebEngine, then extracts alpha using a
dual-render difference matte (render on black + render on white, derive alpha
from the difference).  This correctly handles semi-transparent CSS elements
since QWebEngineView.grab() does not produce real alpha.

Fixed positions derived from the slate data:

    top_left: vendor        top_center: show        top_right: date
    bottom_left: version    (center empty)          bottom_right: frames
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .slate import TEMPLATES_DIR

BURNIN_TEMPLATE = TEMPLATES_DIR / "burnin.html"


def _qimage_to_rgb(img: QImage) -> np.ndarray:
    """Convert a QImage to a uint8 RGB numpy array (h, w, 3)."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    rgba = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
    return rgba[:, :, :3]


def difference_matte(on_black: np.ndarray, on_white: np.ndarray) -> np.ndarray:
    """Derive RGBA from two RGB renders (on black bg, on white bg).

    For each pixel, alpha = 1 - (white_render - black_render).
    Where both are identical the content is fully opaque; where they differ
    maximally it's fully transparent.  Premultiplied RGB = the black render
    (since rendering on black means RGB is already premultiplied by alpha).

    Returns uint8 RGBA array.
    """
    b = on_black.astype(np.float32)
    w = on_white.astype(np.float32)
    diff = np.mean(w - b, axis=2)
    alpha = np.clip(255.0 - diff, 0.0, 255.0)
    rgba = np.empty((*on_black.shape[:2], 4), dtype=np.uint8)
    # Un-premultiply to get straight alpha RGBA
    a_norm = np.clip(alpha, 1.0, 255.0)
    for c in range(3):
        rgba[:, :, c] = np.clip(b[:, :, c] / a_norm * 255.0, 0, 255).astype(
            np.uint8
        )
    rgba[:, :, 3] = alpha.astype(np.uint8)
    return rgba


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


def _render_on_bg(
    width: int,
    height: int,
    fields: dict[str, str],
    bg_color: QColor,
) -> np.ndarray:
    """Render the burn-in template on a solid bg and return RGB uint8 array."""
    js_data = json.dumps(fields)

    loop = QEventLoop()
    result: list[np.ndarray] = []
    error: list[str] = []

    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(width, height)
    view.page().settings().setAttribute(
        QWebEngineSettings.WebAttribute.ShowScrollBars, False
    )
    view.page().setBackgroundColor(bg_color)
    view.show()

    def _on_loaded(ok: bool) -> None:
        if not ok:
            error.append("Failed to load burn-in HTML template.")
            loop.quit()
            return
        QTimer.singleShot(100, _inject)

    def _inject() -> None:
        view.page().setZoomFactor(1.0)
        js = f"updateBurnin({js_data})"
        view.page().runJavaScript(
            js, lambda _: QTimer.singleShot(80, _capture)
        )

    def _capture() -> None:
        try:
            pixmap = view.grab(view.rect())
            result.append(_qimage_to_rgb(pixmap.toImage()))
        except Exception as exc:
            error.append(str(exc))
        finally:
            loop.quit()

    view.loadFinished.connect(_on_loaded)
    view.load(QUrl.fromLocalFile(str(BURNIN_TEMPLATE)))
    loop.exec()

    view.close()
    view.deleteLater()

    if error:
        raise RuntimeError(error[0])
    if not result:
        raise RuntimeError("Burn-in render produced no output.")
    return result[0]


def render_burnin_overlay(
    width: int,
    height: int,
    fields: dict[str, str],
) -> np.ndarray:
    """Render the burn-in HTML template and return a uint8 RGBA overlay.

    Uses a dual-render difference matte: renders on black then on white,
    derives alpha from the difference.  Must be called from the main thread.
    """
    on_black = _render_on_bg(width, height, fields, QColor(0, 0, 0))
    on_white = _render_on_bg(width, height, fields, QColor(255, 255, 255))
    return difference_matte(on_black, on_white)


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
    rgba = np.concatenate(
        [frame_f, np.ones((h, w, 1), dtype=np.float32)], axis=-1
    )
    bg_buf.set_pixels(oiio.ROI.All, rgba)

    result_buf = oiio.ImageBufAlgo.over(fg_buf, bg_buf)

    pixels = result_buf.get_pixels(oiio.FLOAT)
    rgb = pixels[:, :, :3]
    return np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)
