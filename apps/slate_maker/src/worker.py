from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView

try:
    import OpenImageIO as oiio
except ImportError:
    import oiio as oiio  # type: ignore[no-redef]

from .constants import BIT_DEPTH_HALF, COLORSPACE_LINEAR

log = logging.getLogger(__name__)


@dataclass
class RenderSettings:
    output_path: str
    width: int
    height: int
    slate_data: dict = field(default_factory=dict)
    bit_depth: str = BIT_DEPTH_HALF
    colorspace: str = COLORSPACE_LINEAR


def qimage_to_numpy(img: QImage) -> np.ndarray:
    """Convert a QImage to a float32 RGBA numpy array."""
    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4)).copy()
    return arr.astype(np.float32) / 255.0


def srgb_to_linear(arr: np.ndarray) -> np.ndarray:
    """sRGB → linear using the IEC 61966-2-1 transfer function (RGB only)."""
    rgb = arr[..., :3]
    alpha = arr[..., 3:4]
    low = rgb / 12.92
    high = np.power((rgb + 0.055) / 1.055, 2.4)
    linear_rgb = np.where(rgb <= 0.04045, low, high)
    return np.concatenate([linear_rgb, alpha], axis=-1)


def write_exr(
    path: str,
    pixels: np.ndarray,
    bit_depth: str,
    metadata: dict[str, str] | None = None,
) -> None:
    """Write a float32 RGBA numpy array to an EXR file via OpenImageIO."""
    h, w, c = pixels.shape
    type_map = {"half": oiio.HALF, "float": oiio.FLOAT}
    oiio_type = type_map.get(bit_depth, oiio.HALF)

    spec = oiio.ImageSpec(w, h, c, oiio_type)
    spec.channelnames = ("R", "G", "B", "A")
    spec.attribute("compression", "zips")

    if metadata:
        for k, v in metadata.items():
            spec.attribute(k, v)

    out = oiio.ImageOutput.create(path)
    if not out:
        raise RuntimeError(f"OIIO cannot create output: {oiio.geterror()}")
    if not out.open(path, spec):
        raise RuntimeError(f"OIIO open failed: {out.geterror()}")
    if not out.write_image(pixels):
        raise RuntimeError(f"OIIO write failed: {out.geterror()}")
    out.close()


class RenderWorker(QObject):
    """Renders the HTML slate to a single EXR file.

    Uses an offscreen QWebEngineView with QWidget.grab() for capture.
    Runs on the main thread via QTimer steps so the UI stays responsive.
    """

    finished = Signal(str)  # output path on success
    error = Signal(str)

    def __init__(self, settings: RenderSettings, html_path: str, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = settings
        self._html_path = html_path
        self._cancelled = False
        self._view: QWebEngineView | None = None

    def cancel(self):
        self._cancelled = True

    @Slot()
    def start(self):
        s = self._settings
        Path(s.output_path).parent.mkdir(parents=True, exist_ok=True)

        view = QWebEngineView()
        view.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        view.resize(s.width, s.height)
        view.page().settings().setAttribute(QWebEngineSettings.WebAttribute.ShowScrollBars, False)
        view.page().setBackgroundColor(Qt.GlobalColor.transparent)
        view.show()
        self._view = view

        html_url = QUrl.fromLocalFile(self._html_path)

        def on_loaded(ok: bool):
            if not ok:
                self.error.emit("Failed to load slate HTML template.")
                self.finished.emit("")
                return
            QTimer.singleShot(500, self._inject_data)

        view.loadFinished.connect(on_loaded)
        view.load(html_url)

    def _inject_data(self):
        if self._cancelled:
            self._cleanup()
            return
        s = self._settings
        self._view.page().setZoomFactor(1.0)
        js = f"updateSlate({json.dumps(s.slate_data)})"
        self._view.page().runJavaScript(js, self._on_js_done)

    def _on_js_done(self, _result=None):
        QTimer.singleShot(80, self._capture)

    def _capture(self):
        if self._cancelled:
            self._cleanup()
            return

        s = self._settings
        try:
            pixmap = self._view.grab(self._view.rect())
            img = pixmap.toImage()
            pixels = qimage_to_numpy(img)

            if s.colorspace == COLORSPACE_LINEAR:
                pixels = srgb_to_linear(pixels)

            metadata = {
                "slate:project": s.slate_data.get("project", ""),
                "slate:shot": s.slate_data.get("shot", ""),
                "slate:version": s.slate_data.get("version", ""),
                "slate:artist": s.slate_data.get("artist", ""),
                "slate:date": time.strftime("%Y-%m-%d"),
            }
            write_exr(s.output_path, pixels, s.bit_depth, metadata)

            self._cleanup()
            self.finished.emit(s.output_path)

        except Exception as exc:
            log.exception("Slate render failed")
            self._cleanup()
            self.error.emit(str(exc))

    def _cleanup(self):
        if self._view:
            self._view.close()
            self._view.deleteLater()
            self._view = None
