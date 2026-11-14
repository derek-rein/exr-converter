"""macOS Quick Look dialog: turn the extensions on and pick formats."""

from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
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
from .components import FIELD_WIDTH, NATURAL, Card, FormGrid
from .style import set_style_property

# A note under a checkbox starts where the checkbox text starts.
_CHECK_TEXT_INDENT = 23


def _note(text: str, role: str = "hint", indent: int = 0) -> QLabel:
    """A wrapped line under a field, as wide as a typical WIDE control."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setContentsMargins(indent, 0, 0, 0)
    label.setFixedWidth(FIELD_WIDTH + indent)
    set_style_property(label, "role", role)
    _reserve_lines(label)
    return label


def _reserve_lines(label: QLabel) -> None:
    """Layouts size rows from sizeHint, which ignores wrapping; reserve every line."""
    label.ensurePolished()
    label.setMinimumHeight(label.heightForWidth(label.width()))


class QuickLookDialog(QDialog):
    """File → Quick Look… (macOS only). The extensions start off."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quick Look")
        self.setMinimumWidth(520)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        main = Card(
            "Quick Look",
            "Spacebar previews and Finder icons for OpenEXR and DPX (macOS 12 and later).",
        )
        form = FormGrid()
        main.body.addLayout(form)

        self.enable_check = QCheckBox("Use EXR Converter for Quick Look and Finder thumbnails")
        self.enable_check.setObjectName("ql_enable")
        self.enable_check.setChecked(quicklook_installed(settings))
        form.add_row("Extensions", self.enable_check, width=NATURAL)
        form.addWidget(
            _note(
                "Off until you turn it on here. Scene-linear frames use this "
                "app’s default OCIO display view; numbered frames play in the preview.",
                indent=_CHECK_TEXT_INDENT,
            ),
            form.rowCount(),
            1,
        )

        self._status = _note("", "muted")
        self._status.setObjectName("ql_status")
        status_label = form.add_row("Status", self._status, width=NATURAL)
        form.setAlignment(status_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        extensions = QPushButton("Login Items && Extensions…")
        extensions.setToolTip(
            "Opens System Settings, where macOS lists Quick Look extensions. "
            "Use it if the checkbox above does not take effect."
        )
        extensions.clicked.connect(lambda _checked=False: open_extension_settings())
        cache = QPushButton("Clear Quick Look cache")
        cache.setToolTip("Finder caches previews. OK clears it too.")
        cache.clicked.connect(lambda _checked=False: clear_quicklook_cache())
        form.add_row("System", extensions, cache, width=NATURAL)
        root.addWidget(main)

        self._formats_box = Card(
            "Formats", "OpenEXR and DPX start on. A format you turn off is declined."
        )
        formats_form = FormGrid()
        self._formats_box.body.addLayout(formats_form)
        # Two columns, filled row by row so Tab reads across then down.
        checks = QWidget()
        grid = QGridLayout(checks)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(6)
        self._checks: dict[str, QCheckBox] = {}
        for index, fmt in enumerate(QUICKLOOK_FORMATS):
            check = QCheckBox(fmt.label)
            check.setObjectName(f"ql_{fmt.key}")
            check.setToolTip(fmt.detail)
            check.setChecked(format_enabled(settings, fmt.key))
            self._checks[fmt.key] = check
            grid.addWidget(check, index // 2, index % 2)
        preview_label = formats_form.add_row("Preview", checks, width=NATURAL)
        formats_form.setAlignment(
            preview_label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        formats_form.addWidget(
            _note("PNG, JPEG, WebP, TIFF, and DNG stay off so Finder keeps its own preview."),
            formats_form.rowCount(),
            1,
        )
        root.addWidget(self._formats_box)
        root.addStretch(1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        set_style_property(buttons.button(QDialogButtonBox.StandardButton.Ok), "variant", "primary")
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
        _reserve_lines(self._status)

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
