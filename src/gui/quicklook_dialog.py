"""macOS Quick Look dialog: turn the extensions on and pick formats."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.quicklook_prefs import (
    QUICKLOOK_FORMATS,
    clear_quicklook_cache,
    extensions_enabled_in_system,
    format_enabled,
    open_extension_settings,
    publish_quicklook_preferences,
    quicklook_installed,
    set_format_enabled,
    set_quicklook_installed,
)


class QuickLookDialog(QDialog):
    """File → Quick Look… (macOS only). The extensions start off."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Look")
        self.setMinimumWidth(520)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setSpacing(12)

        intro = QLabel(
            "Spacebar previews and Finder icons for OpenEXR and DPX (macOS 12 and "
            "later). Off until you turn it on here. Scene-linear frames use this "
            "app’s default OCIO display view; numbered frames play in the preview."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: #aaa;")
        root.addWidget(intro)

        self.enable_check = QCheckBox("Use EXR Converter for Quick Look and Finder thumbnails")
        self.enable_check.setObjectName("ql_enable")
        self.enable_check.setChecked(quicklook_installed(settings))
        root.addWidget(self.enable_check)

        self._status = QLabel()
        self._status.setObjectName("ql_status")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #888;")
        root.addWidget(self._status)

        self._formats_box = QGroupBox("Formats")
        formats = QVBoxLayout(self._formats_box)
        hint = QLabel(
            "OpenEXR and DPX start on. PNG, JPEG, WebP, TIFF, and DNG stay off so "
            "Finder keeps its own preview. A format you turn off is declined."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa;")
        formats.addWidget(hint)
        self._checks: dict[str, QCheckBox] = {}
        for fmt in QUICKLOOK_FORMATS:
            check = QCheckBox(fmt.label)
            check.setObjectName(f"ql_{fmt.key}")
            check.setToolTip(fmt.detail)
            check.setChecked(format_enabled(settings, fmt.key))
            self._checks[fmt.key] = check
            formats.addWidget(check)
        root.addWidget(self._formats_box)

        tools = QHBoxLayout()
        extensions = QPushButton("Login Items && Extensions…")
        extensions.setToolTip(
            "Opens System Settings, where macOS lists Quick Look extensions. "
            "Use it if the checkbox above does not take effect."
        )
        extensions.clicked.connect(lambda _checked=False: open_extension_settings())
        cache = QPushButton("Clear Quick Look cache")
        cache.setToolTip("Finder caches previews. OK clears it too.")
        cache.clicked.connect(lambda _checked=False: clear_quicklook_cache())
        tools.addWidget(extensions)
        tools.addWidget(cache)
        tools.addStretch()
        root.addLayout(tools)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self.enable_check.toggled.connect(self._sync_enabled)
        self._sync_enabled(self.enable_check.isChecked())
        self._refresh_status()

    def _sync_enabled(self, on: bool) -> None:
        self._formats_box.setEnabled(on)

    def _refresh_status(self) -> None:
        state = extensions_enabled_in_system()
        if state is None:
            text = (
                "macOS has not registered the extensions. They ship in the release "
                "app; move it to /Applications and open it once."
            )
        elif state:
            text = "macOS reports the extensions as on."
        else:
            text = "macOS reports the extensions as off."
        self._status.setText(text)
        self._status.setTextFormat(Qt.TextFormat.PlainText)

    def _on_accept(self) -> None:
        for key, box in self._checks.items():
            set_format_enabled(self._settings, key, box.isChecked())
        publish_quicklook_preferences(self._settings)
        set_quicklook_installed(self._settings, self.enable_check.isChecked())
        clear_quicklook_cache_quietly()
        self.accept()


def clear_quicklook_cache_quietly() -> None:
    """Clear Finder's cache; ignore failures (no ``qlmanage`` in tests / CI)."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return
    try:
        clear_quicklook_cache()
    except Exception:
        pass
