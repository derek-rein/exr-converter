"""Burn-in (overlay / chyron) renderer.

Renders an HTML overlay template via QWebEngine with a transparent background,
then composites onto video frames as a uint8 RGBA numpy array.

Positions follow the Netflix VFX template layout:

    top_left        top_center        top_right
    bottom_left     bottom_center     bottom_right

Overlays should not be 100% white — the template uses ~75% grey with a
subtle drop shadow for legibility over any image content.
"""

from __future__ import annotations

import json

import numpy as np
from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

from .slate import TEMPLATES_DIR


def _qimage_to_numpy_u8(img: QImage) -> np.ndarray:
    """Convert a QImage to a uint8 RGBA numpy array."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()


BURNIN_TEMPLATE = TEMPLATES_DIR / "burnin.html"


def resolve_tokens(template: str, context: dict[str, str]) -> str:
    """Replace {token} placeholders with values from *context*."""
    result = template
    for key, val in context.items():
        result = result.replace(f"{{{key}}}", str(val))
    return result


def render_burnin_overlay(
    width: int,
    height: int,
    burnin_data: dict,
    context: dict[str, str],
) -> np.ndarray:
    """Render the burn-in HTML template and return a uint8 RGBA overlay.

    Must be called from the main thread (Qt event loop required).

    Parameters
    ----------
    width, height : Output frame dimensions.
    burnin_data : Dict with keys ``fields``, ``opacity``, ``font_pct``.
    context : Token values like ``{"vendor": "Studio", ...}``.

    Returns
    -------
    np.ndarray
        uint8 array of shape (height, width, 4) — premultiplied RGBA.
    """
    fields: dict[str, str] = burnin_data.get("fields", {})
    resolved = {}
    for key, template in fields.items():
        resolved[key] = resolve_tokens(template, context) if template else ""

    js_data = json.dumps(
        {
            "fields": resolved,
            "opacity": burnin_data.get("opacity", 0.5),
            "font_pct": burnin_data.get("font_pct", 2.5),
        }
    )

    loop = QEventLoop()
    result: list[np.ndarray] = []
    error: list[str] = []

    view = QWebEngineView()
    view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    view.resize(width, height)
    view.page().settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
    view.page().setBackgroundColor(Qt.GlobalColor.transparent)
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
        view.page().runJavaScript(js, lambda _: QTimer.singleShot(80, _capture))

    def _capture() -> None:
        try:
            pixmap = view.grab(view.rect())
            img = pixmap.toImage()
            result.append(_qimage_to_numpy_u8(img))
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

    # Foreground: overlay RGBA as float
    fg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    fg_buf = oiio.ImageBuf(fg_spec)
    fg_buf.set_pixels(oiio.ROI.All, overlay_u8.astype(np.float32) / 255.0)

    # Background: frame RGB → RGBA with alpha=1
    bg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    bg_buf = oiio.ImageBuf(bg_spec)
    frame_f = frame_u16.astype(np.float32) / 65535.0
    rgba = np.concatenate([frame_f, np.ones((h, w, 1), dtype=np.float32)], axis=-1)
    bg_buf.set_pixels(oiio.ROI.All, rgba)

    result_buf = oiio.ImageBufAlgo.over(fg_buf, bg_buf)

    pixels = result_buf.get_pixels(oiio.FLOAT)
    rgb = pixels[:, :, :3]
    return np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)
