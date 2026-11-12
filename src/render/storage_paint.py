"""Paint overlays in display space, then squeeze them into the stored raster.

A plate with pixel aspect 2 is stored squeezed. Text drawn in those pixels
looks stretched once the image is unsqueezed. Paint at the display size
(width times the pixel aspect) and scale back so type and boxes are round
when the file is viewed with that pixel aspect.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from ..core.pixel_aspect import display_size

PaintFn = Callable[[QPainter, int, int], None]


def paint_storage_image(
    width: int,
    height: int,
    pixel_aspect: float,
    fill,
    paint: PaintFn,
) -> QImage:
    """Return an RGBA image of *width* x *height* with display-correct artwork."""
    storage_w = max(1, int(width))
    storage_h = max(1, int(height))
    disp_w, disp_h = display_size(storage_w, storage_h, pixel_aspect)
    image = QImage(disp_w, disp_h, QImage.Format.Format_RGBA8888)
    image.fill(fill)
    painter = QPainter(image)
    try:
        paint(painter, disp_w, disp_h)
    finally:
        painter.end()
    if disp_w != storage_w or disp_h != storage_h:
        image = image.scaled(
            storage_w,
            storage_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image


__all__ = ["paint_storage_image"]
