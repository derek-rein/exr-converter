"""Widgets and helpers shared by the batch tabs (Video → Proxy, Transcode)."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QBoxLayout,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from ..core.trustmark.payload import (
    DEFAULT_STRENGTH,
    MAX_TEXT_CHARS,
    STRENGTH_MAX,
    STRENGTH_MIN,
)
from ..services.proxy_queue import JobQueue
from .batch_rules import (
    LOCATION_BESIDE,
    LOCATION_CUSTOM,
    custom_output_dir,
    video_file_filter,
    watermark_payload,
)
from .color_widgets import lock_form_row_height
from .components import FILL, NARROW, WIDE, Card, FormGrid, inline_label, sized
from .empty_hint import EmptyHint
from .preferences import STRENGTH_DECIMALS, STRENGTH_STEP


def row_line(form: FormGrid, label: QLabel) -> QBoxLayout:
    """The control line of the FormGrid row that *label* heads."""
    index = form.indexOf(label)
    # PySide types getItemPosition as object; it returns (row, column, rowSpan, columnSpan).
    row = cast("tuple[int, int, int, int]", form.getItemPosition(index))[0]
    item = form.itemAtPosition(row, 1)
    line = item.layout() if item is not None else None
    assert isinstance(line, QBoxLayout)
    return line


class OutputLocation:
    """The Location combo plus the Folder row a custom folder needs.

    *output_dir* is built by the tab (a plain or token line edit) and passed
    in. :meth:`add_to` places the Location and Folder rows in the tab's form.
    """

    def __init__(
        self,
        beside_label: str,
        output_dir: QLineEdit,
        *,
        dialog_parent: QWidget,
        dialog_title: str,
    ) -> None:
        self.combo = QComboBox()
        self.combo.addItem(beside_label, LOCATION_BESIDE)
        self.combo.addItem("Custom folder", LOCATION_CUSTOM)
        self.folder_label = QLabel("Folder")  # replaced by add_to()
        self.output_dir = output_dir
        self.browse_btn = QPushButton("Browse…")
        self._dialog_parent = dialog_parent
        self._dialog_title = dialog_title
        self.browse_btn.clicked.connect(self._pick_folder)

    def add_to(self, form: FormGrid) -> None:
        """Add the Location row, then the Folder row (path and Browse…)."""
        form.add_row("Location", self.combo, width=WIDE)
        self.folder_label = form.add_row("Folder", self.output_dir, self.browse_btn, width=FILL)

    def location(self) -> str:
        return str(self.combo.currentData() or LOCATION_BESIDE)

    def custom_output_dir(self) -> str | None:
        return custom_output_dir(self.location(), self.output_dir.text())

    def restore(self, settings: QSettings, prefix: str) -> None:
        """Select the saved location (``<prefix>/location``)."""
        idx = self.combo.findData(str(settings.value(f"{prefix}/location", LOCATION_BESIDE) or ""))
        if idx >= 0:
            self.combo.setCurrentIndex(idx)

    def save_to(self, settings: QSettings, prefix: str) -> None:
        """Save the location and folder under *prefix* whenever they change."""
        self.combo.currentIndexChanged.connect(
            lambda _: settings.setValue(f"{prefix}/location", self.location())
        )
        self.output_dir.textChanged.connect(lambda t: settings.setValue(f"{prefix}/output_dir", t))

    def sync_visible(self) -> None:
        """Show the Folder row only for a custom folder."""
        custom = self.location() == LOCATION_CUSTOM
        for w in (self.folder_label, self.output_dir, self.browse_btn):
            w.setVisible(custom)

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self._dialog_parent, self._dialog_title, self.output_dir.text().strip()
        )
        if folder:
            self.output_dir.setText(folder)


class WatermarkControls:
    """The invisible-watermark checkbox, payload field and strength box.

    :meth:`add_to` lays them out as one form row: checkbox, payload, then
    "Strength" and its box. Values load from ``<prefix>/watermark``,
    ``<prefix>/watermark_text`` and ``<prefix>/watermark_strength``.
    """

    def __init__(
        self,
        settings: QSettings,
        prefix: str,
        *,
        check_text: str,
        check_tip: str,
        payload_tip: str = "",
    ) -> None:
        self.check = QCheckBox(check_text)
        self.check.setToolTip(check_tip)
        self.check.setChecked(settings.value(f"{prefix}/watermark", False, type=bool))
        self.payload = QLineEdit(str(settings.value(f"{prefix}/watermark_text", "") or ""))
        self.payload.setMaxLength(MAX_TEXT_CHARS)
        self.payload.setPlaceholderText(f"Payload, up to {MAX_TEXT_CHARS} ASCII characters")
        if payload_tip:
            self.payload.setToolTip(payload_tip)
        self.strength = QDoubleSpinBox()
        self.strength.setRange(STRENGTH_MIN, STRENGTH_MAX)
        self.strength.setSingleStep(STRENGTH_STEP)
        self.strength.setDecimals(STRENGTH_DECIMALS)
        self.strength.setValue(
            float(settings.value(f"{prefix}/watermark_strength", DEFAULT_STRENGTH, type=float))
        )
        self.strength.setToolTip("TrustMark strength. 1.00 is the normal strength.")
        self.strength_label = inline_label("Strength", self.strength)

    def add_to(self, form: FormGrid, label: str = "Watermark") -> QLabel:
        """Add the watermark row: checkbox, payload, Strength and its box.

        The payload is free text, so it takes the room the row has left and
        the strength box ends on the card's trailing edge, like Browse….
        """
        row_label = form.add_row(
            label,
            self.check,
            self.payload,
            self.strength_label,
            sized(self.strength, NARROW),
            width=FILL,
        )
        line = row_line(form, row_label)
        line.setStretchFactor(self.check, 0)
        line.setStretchFactor(self.payload, 1)
        return row_label

    def payload_text(self) -> str:
        """The payload to embed: ``""`` when off or empty."""
        return watermark_payload(self.check.isChecked(), self.payload.text())

    def sync_enabled(self) -> None:
        """Enable the payload and strength only while the checkbox is on."""
        on = self.check.isChecked()
        for w in (self.payload, self.strength_label, self.strength):
            w.setEnabled(on)

    def save_to(self, settings: QSettings, prefix: str) -> None:
        """Save the three values under *prefix* whenever they change."""
        self.check.toggled.connect(lambda v: settings.setValue(f"{prefix}/watermark", v))
        self.payload.textChanged.connect(lambda t: settings.setValue(f"{prefix}/watermark_text", t))
        self.strength.valueChanged.connect(
            lambda v: settings.setValue(f"{prefix}/watermark_strength", float(v))
        )


class SourceClipsPanel(Card):
    """The "Source clips" card: the tab's clip view, then the button row.

    The row holds Add Clips… / Add Folder… / Remove / Clear and Include
    subfolders (loaded from ``<prefix>/recursive``). The tab connects the
    buttons. *hint*, when given, sits between the view and the row.
    """

    def __init__(
        self,
        clip_view: QWidget,
        settings: QSettings,
        prefix: str,
        *,
        recursive_tip: str,
        add_folder_tip: str = "",
        hint: QWidget | None = None,
    ) -> None:
        super().__init__("Source clips")
        main = self.body
        main.addWidget(clip_view)
        if isinstance(clip_view, QAbstractItemView):
            self.empty_hint = EmptyHint(
                clip_view, "Drop clips or folders here", "or use Add Clips… / Add Folder…"
            )
        if hint is not None:
            main.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self.add_files_btn = QPushButton("Add Clips…")
        self.add_folder_btn = QPushButton("Add Folder…")
        if add_folder_tip:
            self.add_folder_btn.setToolTip(add_folder_tip)
        self.remove_btn = QPushButton("Remove")
        self.clear_btn = QPushButton("Clear")
        for b in (self.add_files_btn, self.add_folder_btn, self.remove_btn, self.clear_btn):
            lock_form_row_height(b)
            btn_row.addWidget(b)
        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setToolTip(recursive_tip)
        self.recursive_cb.setChecked(settings.value(f"{prefix}/recursive", False, type=bool))
        btn_row.addSpacing(8)
        btn_row.addWidget(self.recursive_cb)
        btn_row.addStretch()
        main.addLayout(btn_row)

    def save_to(self, settings: QSettings, prefix: str) -> None:
        """Save Include subfolders under *prefix* whenever it changes."""
        self.recursive_cb.toggled.connect(lambda v: settings.setValue(f"{prefix}/recursive", v))


class BatchTab(QWidget):
    """Base of the batch tabs: their signals, clip pickers and Convert / Cancel.

    A subclass implements :attr:`queue`, :meth:`clips`, :meth:`add_paths`,
    :meth:`enqueue`, :meth:`is_ready` and :meth:`_source_suffixes`.
    *prefix* is its QSettings group; *batch_name* heads the log when a
    batch starts.
    """

    log_message = Signal(str)
    readiness_changed = Signal(bool)
    progress = Signal(int, int)
    busy_changed = Signal(bool)

    def __init__(
        self,
        settings: QSettings,
        prefix: str,
        batch_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._prefix = prefix
        self._batch_name = batch_name

    # -- per tab --

    @property
    def queue(self) -> JobQueue:
        raise NotImplementedError

    def clips(self) -> list[str]:
        raise NotImplementedError

    def add_paths(self, paths: list[str]) -> int:
        """Add clips (files or folders). Returns how many new clips were listed."""
        raise NotImplementedError

    def enqueue(self, sources: list[str]) -> int:
        raise NotImplementedError

    def is_ready(self) -> bool:
        raise NotImplementedError

    def _source_suffixes(self) -> Iterable[str]:
        """File suffixes the Add Clips… dialog offers."""
        raise NotImplementedError

    # -- shared --

    def is_busy(self) -> bool:
        return self.queue.is_busy()

    def _emit_readiness(self) -> None:
        self.readiness_changed.emit(self.is_ready())

    def handle_dropped_path(self, path: str) -> bool:
        return self.add_paths([path]) > 0

    def _pick_files(self) -> None:
        start = str(self._settings.value(f"{self._prefix}/last_dir", "") or "")
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add clips", start, video_file_filter(self._source_suffixes())
        )
        if files:
            self._settings.setValue(f"{self._prefix}/last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def _pick_folder(self) -> None:
        start = str(self._settings.value(f"{self._prefix}/last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Add folder of clips", start)
        if folder:
            self._settings.setValue(f"{self._prefix}/last_dir", folder)
            if not self.add_paths([folder]):
                self.log_message.emit(f"No video clips found in {folder}")

    def start_batch(self) -> int:
        """Queue every listed clip (the window's Convert button)."""
        if not self.is_ready():
            return 0
        clips = self.clips()
        self.log_message.emit(f"--- {self._batch_name} ({len(clips)} clip(s)) ---")
        n = self.enqueue(clips)
        if n == 0:
            self.log_message.emit("Nothing to convert.")
        return n

    def cancel(self) -> None:
        self.queue.cancel_all()
