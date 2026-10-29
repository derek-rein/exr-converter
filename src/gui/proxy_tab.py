"""Video → Proxy tab: batch proxy generation plus a Resolve-style watch folder."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.proxy import (
    DEFAULT_PROXY_CODEC,
    DEFAULT_PROXY_RESOLUTION,
    PROXY_RESOLUTIONS,
    PROXY_SUBFOLDER,
    PROXY_VIDEO_SUFFIXES,
    available_proxy_codecs,
    check_watch_output,
    collect_proxy_sources,
    default_proxy_output,
    is_proxy_source,
)
from ..core.trustmark.payload import (
    DEFAULT_STRENGTH,
    MAX_TEXT_CHARS,
    STRENGTH_MAX,
    STRENGTH_MIN,
)
from ..core.watch_folder import DEFAULT_SETTLE_SECONDS, WatchFolder
from ..services.proxy_queue import ProxyJob, ProxyQueue, WatchPoller
from .color_widgets import lock_form_row_height
from .convert_tab import _ConvertFormBody, _form_scroll_area

LOCATION_BESIDE = "beside"
LOCATION_CUSTOM = "custom"

_K = "video2proxy"

_NO_COLOR_NOTE = "No color transform: proxies keep the source look, timecode and audio."


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return label


def _limit_combo_width(combo: QComboBox, characters: int) -> None:
    """Size the closed combo from a character count so two can share a row.

    The popup still lists the full item text. A combo sized to its longest
    item would force the form wider than the other convert tabs.
    """
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(characters)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)


class ProxyTab(QWidget):
    """Proxy generation form. Jobs run serially on :class:`ProxyQueue`."""

    log_message = Signal(str)
    readiness_changed = Signal(bool)
    progress = Signal(int, int)
    busy_changed = Signal(bool)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._queue = ProxyQueue(self)
        self._poller: WatchPoller | None = None
        self._watch_settling = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = _ConvertFormBody()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # -- Source clips --
        in_group = QGroupBox("Source clips")
        in_main = QVBoxLayout(in_group)
        in_main.setSpacing(4)
        self.clip_list = QListWidget()
        self.clip_list.setObjectName("proxyClipList")
        self.clip_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.clip_list.setMinimumHeight(96)
        # The watermark row is taller than the color note it replaced. A
        # slightly shorter clip list keeps the tab within a row of the others.
        self.clip_list.setMaximumHeight(122)
        self.clip_list.setToolTip("Clips to proxy when you press Convert. Drop videos here too.")
        in_main.addWidget(self.clip_list)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._add_files_btn = QPushButton("Add Clips…")
        self._add_folder_btn = QPushButton("Add Folder…")
        self._add_folder_btn.setToolTip(
            "Add every clip in a folder (and its subfolders when Include subfolders is on)"
        )
        self._remove_btn = QPushButton("Remove")
        self._clear_btn = QPushButton("Clear")
        for b in (self._add_files_btn, self._add_folder_btn, self._remove_btn, self._clear_btn):
            lock_form_row_height(b)
            btn_row.addWidget(b)
        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setToolTip(
            "Add Folder… includes nested folders, and the watch folder follows them too.\n"
            "Proxy folders are skipped."
        )
        self.recursive_cb.setChecked(settings.value(f"{_K}/recursive", False, type=bool))
        btn_row.addSpacing(8)
        btn_row.addWidget(self.recursive_cb)
        btn_row.addStretch()
        in_main.addLayout(btn_row)
        layout.addWidget(in_group)

        # -- Output (location, codec, and size share one group, two columns) --
        out_group = QGroupBox("Output")
        out_group.setToolTip(_NO_COLOR_NOTE)
        out_main = QVBoxLayout(out_group)
        out_main.setSpacing(4)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.location_combo = QComboBox()
        self.location_combo.addItem(
            f"{PROXY_SUBFOLDER} folder next to each clip (Resolve style)", LOCATION_BESIDE
        )
        self.location_combo.addItem("Custom folder", LOCATION_CUSTOM)
        grid.addWidget(_field_label("Location"), 0, 0)
        grid.addWidget(self.location_combo, 0, 1, 1, 4)

        self.codec_combo = QComboBox()
        for spec in available_proxy_codecs():
            self.codec_combo.addItem(spec.display_name, spec.key)
            self.codec_combo.setItemData(
                self.codec_combo.count() - 1, spec.format_label, Qt.ItemDataRole.ToolTipRole
            )
        self._select(self.codec_combo, settings.value(f"{_K}/codec", DEFAULT_PROXY_CODEC))
        self.resolution_combo = QComboBox()
        for res in PROXY_RESOLUTIONS:
            self.resolution_combo.addItem(res.label, res.key)
        self.resolution_combo.setToolTip("Proxy size relative to the source. Never upscales.")
        self._select(
            self.resolution_combo,
            settings.value(f"{_K}/resolution", DEFAULT_PROXY_RESOLUTION),
        )
        self.audio_cb = QCheckBox("Audio")
        self.audio_cb.setToolTip("Carry the source audio into the proxy")
        self.audio_cb.setChecked(settings.value(f"{_K}/audio", True, type=bool))
        grid.addWidget(_field_label("Codec"), 1, 0)
        grid.addWidget(self.codec_combo, 1, 1)
        grid.addWidget(_field_label("Resolution"), 1, 2)
        grid.addWidget(self.resolution_combo, 1, 3)
        grid.addWidget(self.audio_cb, 1, 4)

        self.suffix_edit = QLineEdit(str(settings.value(f"{_K}/suffix", "") or ""))
        self.suffix_edit.setPlaceholderText("empty keeps the source name")
        self.suffix_edit.setToolTip(
            "Added before the extension. Leave empty to keep the source name, which relinks best."
        )
        self.skip_existing_cb = QCheckBox("Skip clips that already have a proxy")
        self.skip_existing_cb.setChecked(settings.value(f"{_K}/skip_existing", True, type=bool))
        grid.addWidget(_field_label("Suffix"), 2, 0)
        grid.addWidget(self.suffix_edit, 2, 1, 1, 2)
        grid.addWidget(self.skip_existing_cb, 2, 3, 1, 2)

        # Shown only for a custom folder. Beside-source is the default and
        # does not use this path, so the row stays out of the height budget.
        self._folder_label = _field_label("Folder")
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Folder for all proxies")
        self.output_dir.setText(str(settings.value(f"{_K}/output_dir", "") or ""))
        self._browse_out = QPushButton("Browse…")
        grid.addWidget(self._folder_label, 3, 0)
        grid.addWidget(self.output_dir, 3, 1, 1, 3)
        grid.addWidget(self._browse_out, 3, 4)

        self.watermark_cb = QCheckBox("Enable")
        self.watermark_cb.setToolTip(
            "Embed an invisible TrustMark watermark in every proxy frame.\n"
            "Off by default. The encoder model downloads once into your cache."
        )
        self.watermark_cb.setChecked(settings.value(f"{_K}/watermark", False, type=bool))
        self.watermark_payload = QLineEdit(str(settings.value(f"{_K}/watermark_text", "") or ""))
        self.watermark_payload.setMaxLength(MAX_TEXT_CHARS)
        self.watermark_payload.setPlaceholderText(
            f"Payload, up to {MAX_TEXT_CHARS} ASCII characters"
        )
        self.watermark_payload.setToolTip(
            "Short ID or text. TrustMark text mode keeps 8 ASCII characters, "
            "which the official decoder can read."
        )
        self.watermark_strength = QDoubleSpinBox()
        self.watermark_strength.setRange(STRENGTH_MIN, STRENGTH_MAX)
        self.watermark_strength.setSingleStep(0.05)
        self.watermark_strength.setDecimals(2)
        self.watermark_strength.setValue(
            float(settings.value(f"{_K}/watermark_strength", DEFAULT_STRENGTH, type=float))
        )
        self.watermark_strength.setToolTip("TrustMark strength. 1.00 is the normal strength.")
        self._strength_label = QLabel("Strength")
        wm_row = QHBoxLayout()
        wm_row.setSpacing(6)
        wm_row.addWidget(self.watermark_cb)
        wm_row.addWidget(self.watermark_payload, 1)
        wm_row.addWidget(self._strength_label)
        wm_row.addWidget(self.watermark_strength)
        # Replaces the old one-line color note so the tab stays the same height
        # as the other convert tabs. The sentence remains the group tooltip.
        grid.addWidget(_field_label("Watermark"), 4, 0)
        grid.addLayout(wm_row, 4, 1, 1, 4)
        self._sync_watermark_enabled()
        out_main.addLayout(grid)
        layout.addWidget(out_group)

        # -- Watch folder (always shown; status lives in the group title) --
        self._watch_group = QGroupBox()
        watch_row = QHBoxLayout(self._watch_group)
        watch_row.setSpacing(6)
        self.watch_dir = QLineEdit(str(settings.value(f"{_K}/watch_dir", "") or ""))
        self.watch_dir.setPlaceholderText("Folder to watch for new clips")
        self._browse_watch = QPushButton("Browse…")
        self.existing_cb = QCheckBox("Existing clips too")
        self.existing_cb.setToolTip(
            "When watching starts, proxy the clips already in the folder too.\n"
            "Off: only clips that arrive after you start watching."
        )
        self.existing_cb.setChecked(settings.value(f"{_K}/watch_existing", False, type=bool))
        self.watch_btn = QPushButton("Start Watching")
        self.watch_btn.setObjectName("watchBtn")
        self.watch_btn.setCheckable(True)
        # Same width for Start and Stop, so toggling never shifts the row.
        widths = []
        for text in ("Stop Watching", "Start Watching"):
            self.watch_btn.setText(text)
            widths.append(self.watch_btn.sizeHint().width())
        self.watch_btn.setMinimumWidth(max(widths))
        watch_row.addWidget(self.watch_dir, 1)
        watch_row.addWidget(self._browse_watch)
        watch_row.addSpacing(8)
        watch_row.addWidget(self.existing_cb)
        watch_row.addWidget(self.watch_btn)
        layout.addWidget(self._watch_group)

        for combo, chars in (
            (self.location_combo, 28),
            (self.codec_combo, 18),
            (self.resolution_combo, 12),
        ):
            _limit_combo_width(combo, chars)
        for g in (in_group, out_group, self._watch_group):
            g.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addStretch()

        for w in (
            self.location_combo,
            self.output_dir,
            self._browse_out,
            self.suffix_edit,
            self.skip_existing_cb,
            self.codec_combo,
            self.resolution_combo,
            self.audio_cb,
            self.watermark_cb,
            self.watermark_payload,
            self._strength_label,
            self.watermark_strength,
            self.recursive_cb,
            self.watch_btn,
            self.watch_dir,
            self._browse_watch,
            self.existing_cb,
        ):
            lock_form_row_height(w)

        outer.addWidget(_form_scroll_area(body))

        self._select(self.location_combo, settings.value(f"{_K}/location", LOCATION_BESIDE))
        self._sync_location_enabled()

        # -- Connections --
        self._add_files_btn.clicked.connect(self._pick_files)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn.clicked.connect(self._clear_clips)
        self._browse_out.clicked.connect(self._pick_output_dir)
        self._browse_watch.clicked.connect(self._pick_watch_dir)
        self.watch_btn.toggled.connect(self._on_watch_toggled)
        self.location_combo.currentIndexChanged.connect(lambda _: self._on_location_changed())
        self.output_dir.textChanged.connect(
            lambda t: self._settings.setValue(f"{_K}/output_dir", t)
        )
        self.output_dir.textChanged.connect(lambda _: self._emit_readiness())
        self.suffix_edit.textChanged.connect(lambda t: self._settings.setValue(f"{_K}/suffix", t))
        self.skip_existing_cb.toggled.connect(
            lambda v: self._settings.setValue(f"{_K}/skip_existing", v)
        )
        self.codec_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(f"{_K}/codec", self.codec_key())
        )
        self.resolution_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(f"{_K}/resolution", self.resolution_key())
        )
        self.audio_cb.toggled.connect(lambda v: self._settings.setValue(f"{_K}/audio", v))
        self.watermark_cb.toggled.connect(self._on_watermark_toggled)
        self.watermark_payload.textChanged.connect(
            lambda t: self._settings.setValue(f"{_K}/watermark_text", t)
        )
        self.watermark_strength.valueChanged.connect(
            lambda v: self._settings.setValue(f"{_K}/watermark_strength", float(v))
        )
        self.watch_dir.textChanged.connect(lambda t: self._settings.setValue(f"{_K}/watch_dir", t))
        self.recursive_cb.toggled.connect(lambda v: self._settings.setValue(f"{_K}/recursive", v))
        self.existing_cb.toggled.connect(
            lambda v: self._settings.setValue(f"{_K}/watch_existing", v)
        )
        self.clip_list.model().rowsInserted.connect(lambda *_: self._emit_readiness())
        self.clip_list.model().rowsRemoved.connect(lambda *_: self._emit_readiness())

        self._queue.log_message.connect(self.log_message)
        self._queue.progress.connect(self.progress)
        self._queue.busy_changed.connect(self.busy_changed)
        self._queue.queue_changed.connect(lambda _: self._update_watch_status())
        self._queue.job_finished.connect(self._on_job_finished)
        self._update_watch_status()

    # -- small helpers --

    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        idx = combo.findData(str(value or ""))
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def codec_key(self) -> str:
        return str(self.codec_combo.currentData() or DEFAULT_PROXY_CODEC)

    def resolution_key(self) -> str:
        return str(self.resolution_combo.currentData() or DEFAULT_PROXY_RESOLUTION)

    def location(self) -> str:
        return str(self.location_combo.currentData() or LOCATION_BESIDE)

    def custom_output_dir(self) -> str | None:
        if self.location() != LOCATION_CUSTOM:
            return None
        return self.output_dir.text().strip() or None

    def clips(self) -> list[str]:
        return [
            str(self.clip_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.clip_list.count())
        ]

    @property
    def queue(self) -> ProxyQueue:
        return self._queue

    def is_busy(self) -> bool:
        return self._queue.is_busy()

    def is_watching(self) -> bool:
        return self._poller is not None

    def is_ready(self) -> bool:
        if self.clip_list.count() == 0:
            return False
        return not (self.location() == LOCATION_CUSTOM and not self.custom_output_dir())

    def _emit_readiness(self) -> None:
        self.readiness_changed.emit(self.is_ready())

    def _sync_location_enabled(self) -> None:
        custom = self.location() == LOCATION_CUSTOM
        for w in (self._folder_label, self.output_dir, self._browse_out):
            w.setVisible(custom)
        self.output_dir.setEnabled(custom)
        self._browse_out.setEnabled(custom)

    def _sync_watermark_enabled(self) -> None:
        on = self.watermark_cb.isChecked()
        for widget in (self.watermark_payload, self._strength_label, self.watermark_strength):
            widget.setEnabled(on)

    def _on_watermark_toggled(self, on: bool) -> None:
        self._settings.setValue(f"{_K}/watermark", on)
        self._sync_watermark_enabled()

    def _on_location_changed(self) -> None:
        self._settings.setValue(f"{_K}/location", self.location())
        self._sync_location_enabled()
        self._emit_readiness()

    # -- clip list --

    def add_paths(self, paths: list[str]) -> int:
        """Add clips (files or folders). Returns how many new clips were listed."""
        existing = set(self.clips())
        added = 0
        for src in collect_proxy_sources(paths, recursive=self.recursive_cb.isChecked()):
            key = str(src)
            if key in existing:
                continue
            item = QListWidgetItem(src.name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(key)
            self.clip_list.addItem(item)
            existing.add(key)
            added += 1
        if added:
            self.log_message.emit(f"Proxy list: added {added} clip(s)")
        return added

    def handle_dropped_path(self, path: str) -> bool:
        return self.add_paths([path]) > 0

    def _pick_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(PROXY_VIDEO_SUFFIXES))
        start = str(self._settings.value(f"{_K}/last_dir", "") or "")
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add clips", start, f"Video ({exts});;All files (*)"
        )
        if files:
            self._settings.setValue(f"{_K}/last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def _pick_folder(self) -> None:
        start = str(self._settings.value(f"{_K}/last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Add folder of clips", start)
        if folder:
            self._settings.setValue(f"{_K}/last_dir", folder)
            if not self.add_paths([folder]):
                self.log_message.emit(f"No video clips found in {folder}")

    def _remove_selected(self) -> None:
        for item in self.clip_list.selectedItems():
            self.clip_list.takeItem(self.clip_list.row(item))

    def _clear_clips(self) -> None:
        self.clip_list.clear()

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Proxy output folder", self.output_dir.text().strip()
        )
        if folder:
            self.output_dir.setText(folder)

    def _pick_watch_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Watch folder", self.watch_dir.text().strip()
        )
        if folder:
            self.watch_dir.setText(folder)

    # -- jobs --

    def _job_for(self, source: str) -> ProxyJob | None:
        """Build a job for *source*, or ``None`` if its proxy already exists."""
        out = default_proxy_output(
            source,
            self.codec_key(),
            output_dir=self.custom_output_dir(),
            suffix=self.suffix_edit.text().strip(),
        )
        if self.skip_existing_cb.isChecked() and out.exists():
            self.log_message.emit(f"Skip (proxy exists): {out}")
            return None
        watermark_text = ""
        if self.watermark_cb.isChecked():
            watermark_text = self.watermark_payload.text().strip()
            if not watermark_text:
                self.log_message.emit("Invisible watermark skipped: payload is empty.")
        return ProxyJob(
            source=str(source),
            output=str(out),
            codec_key=self.codec_key(),
            resolution=self.resolution_key(),
            audio=self.audio_cb.isChecked(),
            watermark_text=watermark_text,
            watermark_strength=float(self.watermark_strength.value()),
        )

    def enqueue(self, sources: list[str]) -> int:
        queued = 0
        for src in sources:
            job = self._job_for(src)
            if job is not None and self._queue.add(job):
                queued += 1
        return queued

    def start_batch(self) -> int:
        """Queue every listed clip (the window's Convert button)."""
        if not self.is_ready():
            return 0
        clips = self.clips()
        self.log_message.emit(f"--- video2proxy ({len(clips)} clip(s)) ---")
        n = self.enqueue(clips)
        if n == 0:
            self.log_message.emit("Nothing to convert.")
        return n

    def cancel(self) -> None:
        self._queue.cancel_all()

    def _on_job_finished(self, source: str, output: str, ok: bool) -> None:
        if ok:
            for i in range(self.clip_list.count()):
                item = self.clip_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == source:
                    item.setText(f"✓ {Path(source).name}")
                    break
        self._update_watch_status()

    # -- watch folder --

    def start_watching(self) -> bool:
        folder = self.watch_dir.text().strip()
        if not folder or not Path(folder).is_dir():
            self.log_message.emit(f"Watch folder not found: {folder or '(empty)'}")
            return False
        try:
            check_watch_output(folder, self.custom_output_dir())
        except ValueError as e:
            self.log_message.emit(f"ERROR: {e}")
            return False
        excl = (self.custom_output_dir(),) if self.custom_output_dir() else ()
        watcher = WatchFolder(
            folder,
            recursive=self.recursive_cb.isChecked(),
            include_existing=self.existing_cb.isChecked(),
            settle_seconds=DEFAULT_SETTLE_SECONDS,
            exclude_dirs=excl,
        )
        self._poller = WatchPoller(watcher, parent=self)
        self._poller.files_ready.connect(self._on_watch_files)
        self._poller.settling.connect(self._on_watch_settling)
        self._poller.start()
        self.log_message.emit(f"Watching {folder} for new clips")
        for w in (self.watch_dir, self._browse_watch, self.recursive_cb, self.existing_cb):
            w.setEnabled(False)
        self._update_watch_status()
        return True

    def stop_watching(self) -> None:
        poller = self._poller
        self._poller = None
        if poller is not None:
            poller.stop()
            poller.deleteLater()
            self.log_message.emit("Stopped watching")
        for w in (self.watch_dir, self._browse_watch, self.recursive_cb, self.existing_cb):
            w.setEnabled(True)
        self._watch_settling = 0
        self._update_watch_status()

    def _on_watch_toggled(self, on: bool) -> None:
        if on and self._poller is None:
            if not self.start_watching():
                self.watch_btn.blockSignals(True)
                self.watch_btn.setChecked(False)
                self.watch_btn.blockSignals(False)
        elif not on and self._poller is not None:
            self.stop_watching()
        self.watch_btn.setText("Stop Watching" if self._poller is not None else "Start Watching")

    def _on_watch_files(self, paths: list[str]) -> None:
        if self._poller is None:
            return
        for p in paths:
            if is_proxy_source(p):
                self.log_message.emit(f"Watch folder: new clip {Path(p).name}")
                self.enqueue([p])

    def _on_watch_settling(self, n: int) -> None:
        if n != self._watch_settling:
            self._watch_settling = n
            self._update_watch_status()

    def _update_watch_status(self) -> None:
        parts: list[str] = []
        if self._poller is not None:
            parts.append("Watching")
            if self._watch_settling:
                parts.append(f"{self._watch_settling} copying")
        else:
            parts.append("Not watching")
        pending = self._queue.pending()
        if pending:
            parts.append(f"{pending} in queue")
        if self._queue.completed:
            parts.append(f"{self._queue.completed} done")
        if self._queue.failed:
            parts.append(f"{self._queue.failed} failed")
        self._watch_group.setTitle("Watch folder · " + " · ".join(parts))

    def shutdown(self) -> None:
        """Stop the watcher and any running job (window close)."""
        if self._poller is not None:
            self._poller.stop()
            self._poller = None
        self._queue.shutdown()
