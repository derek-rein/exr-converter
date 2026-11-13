"""Rasterise the Lucide SVGs in resources/icons/lucide to PNGs.

Release builds strip Qt6Svg and the icon-engine plugins, so the app cannot
load SVG at run time. This script runs at development time (QtSvg is in the
dev environment) and writes:

* ``resources/icons/ui/<name>.png``: white glyph, 48 px (16 pt at 3x).
  ``src/gui/icons.py`` tints and scales these.
* ``resources/images/<name>.png`` and ``<name>@2x.png``: the few fixed-colour
  indicators the stylesheet references by URL (combo arrows, check mark).

Run ``make icons`` after adding an SVG, then ``make resources``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "resources" / "icons" / "lucide"
UI_DIR = ROOT / "resources" / "icons" / "ui"
IMG_DIR = ROOT / "resources" / "images"

UI_SIZE = 48

# Stylesheet indicators: (output name, svg, colour, logical size in px).
# Colours mirror FG_DIM / ACCENT / white in src/gui/style.py.
QSS_IMAGES = [
    ("arrow_down", "chevron-down", "#9a9a9a", 12),
    ("arrow_down_accent", "chevron-down", "#e08a2e", 12),
    ("arrow_up", "chevron-up", "#9a9a9a", 12),
    ("arrow_right", "chevron-right", "#9a9a9a", 12),
    ("check", "check", "#ffffff", 12),
]


def render(svg: Path, size: int, colour: str, stroke: float = 2.0) -> QImage:
    text = svg.read_text(encoding="utf-8").replace("currentColor", colour)
    text = text.replace('stroke-width="2"', f'stroke-width="{stroke}"')
    renderer = QSvgRenderer(QByteArray(text.encode("utf-8")))
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def main() -> int:
    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - Qt needs an app for painting
    UI_DIR.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in SVG_DIR.glob("*.svg"))
    for name in names:
        render(SVG_DIR / f"{name}.svg", UI_SIZE, "#ffffff").save(str(UI_DIR / f"{name}.png"))
    for out, svg, colour, size in QSS_IMAGES:
        QColor(colour)  # fail early on a typo
        # A heavier stroke keeps a 12 px chevron legible.
        render(SVG_DIR / f"{svg}.svg", size, colour, 2.5).save(str(IMG_DIR / f"{out}.png"))
        render(SVG_DIR / f"{svg}.svg", size * 2, colour, 2.5).save(str(IMG_DIR / f"{out}@2x.png"))
    # Tree disclosure arrows: Qt stretches the image over the whole branch
    # cell, so the glyph sits small in a transparent 20 px square.
    for out, svg in (("branch_closed", "chevron-right"), ("branch_open", "chevron-down")):
        for scale, suffix in ((1, ""), (2, "@2x")):
            canvas = QImage(20 * scale, 20 * scale, QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(Qt.GlobalColor.transparent)
            glyph = render(SVG_DIR / f"{svg}.svg", 10 * scale, "#9a9a9a", 2.5)
            painter = QPainter(canvas)
            painter.drawImage(5 * scale, 5 * scale, glyph)
            painter.end()
            canvas.save(str(IMG_DIR / f"{out}{suffix}.png"))
    print(f"icons: {len(names)} glyphs, {len(QSS_IMAGES)} stylesheet images")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
