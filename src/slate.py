"""Slate rendering: HTML template -> QWebEngine grab -> numpy buffer.

Extracted from the standalone slate_maker app. The key entry point is
``render_slate_frame()`` which returns a float32 RGBA numpy array that the
conversion pipeline can write as an EXR frame or encode into a video frame.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtGui import QImage
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
DEFAULT_TEMPLATE = TEMPLATES_DIR / "slate.html"


def _qimage_to_numpy(img: QImage) -> np.ndarray:
    """Convert a QImage to a float32 RGBA numpy array in [0, 1]."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
    return arr.astype(np.float32) / 255.0


def _srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """sRGB -> linear using the IEC 61966-2-1 transfer function (RGB only)."""
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]
    low = rgb / 12.92
    high = np.power((rgb + 0.055) / 1.055, 2.4)
    linear_rgb = np.where(rgb <= 0.04045, low, high)
    return np.concatenate([linear_rgb, alpha], axis=-1)


SLATE_COLORSPACE = "sRGB"
"""The colorspace of the rendered slate frame (always sRGB from the browser)."""


def render_slate_frame(
    slate_data: dict,
    width: int,
    height: int,
    template_path: str | Path | None = None,
    linearize: bool = False,
    thumbnail_b64: str = "",
) -> np.ndarray:
    """Render the HTML slate template at the given resolution and return float32 RGBA.

    Must be called from the main thread (Qt event loop required for WebEngine).
    Uses a hidden QWebEngineView, loads the template, injects slate_data via JS,
    grabs the widget, and converts to numpy.

    The output is in **sRGB** by default (``linearize=False``).  The caller
    should use OCIO to transform from ``SLATE_COLORSPACE`` ("sRGB") to the
    pipeline's destination colorspace.

    Parameters
    ----------
    slate_data : dict
        Fields to pass to the JS ``updateSlate()`` function.
    width, height : int
        Output pixel dimensions.
    template_path : path, optional
        Path to ``slate.html``. Defaults to the bundled template.
    linearize : bool
        If True, apply sRGB-to-linear on the RGB channels (alpha untouched).
        Default is False — the caller handles colorspace conversion via OCIO.

    Returns
    -------
    np.ndarray
        float32 array of shape (height, width, 4).
    """
    if template_path is None:
        template_path = DEFAULT_TEMPLATE
    template_path = str(template_path)

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
            error.append("Failed to load slate HTML template.")
            loop.quit()
            return
        QTimer.singleShot(500, _inject)

    def _inject() -> None:
        view.page().setZoomFactor(1.0)
        js = f"updateSlate({json.dumps(slate_data)})"
        if thumbnail_b64:
            js += f"; setThumbnail('{thumbnail_b64}')"
        view.page().runJavaScript(js, lambda _: QTimer.singleShot(80, _capture))

    def _capture() -> None:
        try:
            pixmap = view.grab(view.rect())
            img = pixmap.toImage()
            pixels = _qimage_to_numpy(img)
            if linearize:
                pixels = _srgb_to_linear(pixels)
            result.append(pixels)
        except Exception as exc:
            error.append(str(exc))
        finally:
            loop.quit()

    view.loadFinished.connect(_on_loaded)
    view.load(QUrl.fromLocalFile(template_path))
    loop.exec()

    view.close()
    view.deleteLater()

    if error:
        raise RuntimeError(error[0])
    if not result:
        raise RuntimeError("Slate render produced no output.")
    return result[0]
