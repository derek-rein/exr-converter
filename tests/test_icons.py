"""Bundled UI icons resolve from Qt resources and tint to palette colours."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

import src.rc_resources  # noqa: F401 - registers :/ui/*.png
from src.gui.icons import icon, set_button_icon
from src.gui.style import PALETTE, color

SVG_DIR = Path(__file__).resolve().parent.parent / "resources" / "icons" / "lucide"
NAMES = sorted(p.stem for p in SVG_DIR.glob("*.svg"))


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_every_svg_has_a_bundled_png(app: QApplication) -> None:
    assert NAMES, "no Lucide sources found"
    missing = [n for n in NAMES if icon(n).isNull()]
    assert missing == [], f"run `make icons resources`: {missing}"


def test_icon_is_tinted_with_the_token(app: QApplication) -> None:
    image = icon("check", "ACCENT").pixmap(48, 48).toImage()
    # Most opaque pixel carries the tint.
    best = max(
        (image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())),
        key=lambda c: c.alpha(),
    )
    assert best.alpha() > 200
    assert best.name() == color("ACCENT")


def test_unknown_icon_is_null_not_a_crash(app: QApplication) -> None:
    assert icon("no-such-glyph").isNull()


def test_set_button_icon_replaces_glyph_text(app: QApplication) -> None:
    button = QPushButton("↺")
    set_button_icon(button, "rotate-ccw")
    assert button.text() == ""
    assert not button.icon().isNull()


def test_palette_tokens_are_hex() -> None:
    for name, value in PALETTE.items():
        assert value.startswith("#") and len(value) == 7, name
