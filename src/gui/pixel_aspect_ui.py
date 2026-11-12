"""Pixel-aspect combo shared by convert, proxy, and transcode."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QWidget

from ..core.pixel_aspect import PIXEL_ASPECT_CHOICES, parse_pixel_aspect_choice

_TOOLTIP = (
    "Auto reads lens squeeze or pixel aspect from the clip. "
    "A fixed value tags the output and leaves the pixels squeezed."
)


def make_pixel_aspect_combo(
    settings,
    settings_key: str,
    parent: QWidget | None = None,
) -> QComboBox:
    """Combo whose item data is ``auto`` or a factor string."""
    combo = QComboBox(parent)
    combo.setToolTip(_TOOLTIP)
    for key, label in PIXEL_ASPECT_CHOICES:
        combo.addItem(label, key)
    saved = str(settings.value(settings_key, "auto") or "auto")
    idx = combo.findData(saved)
    combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _save() -> None:
        settings.setValue(settings_key, str(combo.currentData() or "auto"))

    combo.currentIndexChanged.connect(lambda _i: _save())
    return combo


def combo_override(combo: QComboBox) -> float | None:
    """``None`` when the combo is Auto, else the forced factor."""
    return parse_pixel_aspect_choice(str(combo.currentData() or "auto"))


__all__ = ["combo_override", "make_pixel_aspect_combo"]
