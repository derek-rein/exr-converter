"""Video → Proxy tab: batch proxy generation plus a Resolve-style watch folder."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
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
from ..core.watch_folder import DEFAULT_SETTLE_SECONDS, WatchFolder
from ..services.proxy_queue import ProxyJob, ProxyQueue, WatchPoller
from .batch_common import (
    BatchTab,
    OutputLocation,
    SourceClipsPanel,
    WatermarkControls,
    row_line,
)
from .batch_rules import LOCATION_BESIDE, LOCATION_CUSTOM  # noqa: F401 (re-exported)
from .color_widgets import lock_form_row_height
from .components import FILL, NARROW, WIDE, Card, FormGrid, inline_label, sized
from .convert_tab import _ConvertFormBody, _form_scroll_area
from .pixel_aspect_ui import combo_override, make_pixel_aspect_combo
from .preferences import encoder_mode

_K = "video2proxy"

_NO_COLOR_NOTE = "No color transform: proxies keep the source look, timecode and audio."


class ProxyTab(BatchTab):
    """Proxy generation form. Jobs run serially on :class:`ProxyQueue`."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(settings, _K, "video2proxy", parent)
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
        self.clip_list = QListWidget()
        self.clip_list.setObjectName("proxyClipList")
        self.clip_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.clip_list.setMinimumHeight(96)
        # Capped so the whole tab fits the default window without scrolling.
        self.clip_list.setMaximumHeight(122)
        self.clip_list.setToolTip("Clips to proxy when you press Convert. Drop videos here too.")
        in_group = self._sources = SourceClipsPanel(
            self.clip_list,
            settings,
            _K,
            recursive_tip=(
                "Add Folder… includes nested folders, and the watch folder follows them too.\n"
                "Proxy folders are skipped."
            ),
            add_folder_tip=(
                "Add every clip in a folder (and its subfolders when Include subfolders is on)"
            ),
        )
        self._add_files_btn = in_group.add_files_btn
        self._add_folder_btn = in_group.add_folder_btn
        self._remove_btn = in_group.remove_btn
        self._clear_btn = in_group.clear_btn
        self.recursive_cb = in_group.recursive_cb
        layout.addWidget(in_group)

        # -- Output --
        out_group = Card("Output", _NO_COLOR_NOTE)
        out_group.setToolTip(_NO_COLOR_NOTE)
        form = FormGrid()
        out_group.body.addLayout(form)

        output_dir = QLineEdit()
        output_dir.setPlaceholderText("Folder for all proxies")
        output_dir.setText(str(settings.value(f"{_K}/output_dir", "") or ""))
        self._location = OutputLocation(
            f"{PROXY_SUBFOLDER} folder next to each clip (Resolve style)",
            output_dir,
            dialog_parent=self,
            dialog_title="Proxy output folder",
        )
        self.location_combo = self._location.combo
        # The Folder row shows only for a custom folder. Beside-source is the
        # default and does not use this path.
        self._location.add_to(form)
        self._folder_label = self._location.folder_label
        self.output_dir = self._location.output_dir
        self._browse_out = self._location.browse_btn

        self.codec_combo = QComboBox()
        for spec in available_proxy_codecs():
            self.codec_combo.addItem(spec.display_name, spec.key)
            self.codec_combo.setItemData(
                self.codec_combo.count() - 1, spec.format_label, Qt.ItemDataRole.ToolTipRole
            )
        self._select(self.codec_combo, settings.value(f"{_K}/codec", DEFAULT_PROXY_CODEC))
        self.audio_cb = QCheckBox("Audio")
        self.audio_cb.setToolTip("Carry the source audio into the proxy")
        self.audio_cb.setChecked(settings.value(f"{_K}/audio", True, type=bool))
        form.add_row("Codec", self.codec_combo, self.audio_cb, width=WIDE, spacing=14)

        self.resolution_combo = QComboBox()
        for res in PROXY_RESOLUTIONS:
            self.resolution_combo.addItem(res.label, res.key)
        self.resolution_combo.setToolTip("Proxy size relative to the source. Never upscales.")
        self._select(
            self.resolution_combo,
            settings.value(f"{_K}/resolution", DEFAULT_PROXY_RESOLUTION),
        )
        self.pixel_aspect_combo = make_pixel_aspect_combo(settings, f"{_K}/pixel_aspect", self)
        # Two picture-size choices share a row, as Scale and Pixel aspect do
        # on the convert tabs.
        form.add_row(
            "Resolution",
            self.resolution_combo,
            inline_label("Pixel aspect", self.pixel_aspect_combo),
            sized(self.pixel_aspect_combo, NARROW),
            width=WIDE,
        )

        self.suffix_edit = QLineEdit(str(settings.value(f"{_K}/suffix", "") or ""))
        self.suffix_edit.setPlaceholderText("empty keeps the source name")
        self.suffix_edit.setToolTip(
            "Added before the extension. Leave empty to keep the source name, which relinks best."
        )
        self.skip_existing_cb = QCheckBox("Skip clips that already have a proxy")
        self.skip_existing_cb.setChecked(settings.value(f"{_K}/skip_existing", True, type=bool))
        form.add_row("Suffix", self.suffix_edit, self.skip_existing_cb, width=WIDE, spacing=14)

        self._watermark = WatermarkControls(
            settings,
            _K,
            check_text="Enable",
            check_tip=(
                "Embed an invisible TrustMark watermark in every proxy frame.\n"
                "Off by default. The encoder model downloads once into your cache."
            ),
            payload_tip=(
                "Short ID or text. TrustMark text mode keeps 8 ASCII characters, "
                "which the official decoder can read."
            ),
        )
        self.watermark_cb = self._watermark.check
        self.watermark_payload = self._watermark.payload
        self.watermark_strength = self._watermark.strength
        self._strength_label = self._watermark.strength_label
        self._watermark.add_to(form)
        self._sync_watermark_enabled()
        layout.addWidget(out_group)

        # -- Watch folder (always shown; status lives in the card title) --
        self._watch_group = Card(
            "Watch folder", "New clips that land here are proxied with the settings above."
        )
        watch_form = FormGrid()
        self._watch_group.body.addLayout(watch_form)
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
        watch_line = row_line(
            watch_form,
            watch_form.add_row(
                "Folder",
                self.watch_dir,
                self._browse_watch,
                self.existing_cb,
                self.watch_btn,
                width=FILL,
            ),
        )
        watch_line.insertSpacing(watch_line.indexOf(self.existing_cb), 6)
        layout.addWidget(self._watch_group)

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
            self.pixel_aspect_combo,
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

        self._location.restore(settings, _K)
        self._sync_location_enabled()

        # -- Connections --
        self._add_files_btn.clicked.connect(self._pick_files)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn.clicked.connect(self._clear_clips)
        self._browse_watch.clicked.connect(self._pick_watch_dir)
        self.watch_btn.toggled.connect(self._on_watch_toggled)
        self._location.save_to(settings, _K)
        self.location_combo.currentIndexChanged.connect(lambda _: self._on_location_changed())
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
        self._watermark.save_to(settings, _K)
        self.watermark_cb.toggled.connect(lambda _: self._sync_watermark_enabled())
        self.watch_dir.textChanged.connect(lambda t: self._settings.setValue(f"{_K}/watch_dir", t))
        self._sources.save_to(settings, _K)
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
        return self._location.location()

    def custom_output_dir(self) -> str | None:
        return self._location.custom_output_dir()

    def clips(self) -> list[str]:
        return [
            str(self.clip_list.item(i).data(Qt.ItemDataRole.UserRole))
            for i in range(self.clip_list.count())
        ]

    @property
    def queue(self) -> ProxyQueue:
        return self._queue

    def is_watching(self) -> bool:
        return self._poller is not None

    def is_ready(self) -> bool:
        if self.clip_list.count() == 0:
            return False
        return not (self.location() == LOCATION_CUSTOM and not self.custom_output_dir())

    def _sync_location_enabled(self) -> None:
        self._location.sync_visible()
        custom = self.location() == LOCATION_CUSTOM
        self.output_dir.setEnabled(custom)
        self._browse_out.setEnabled(custom)

    def _sync_watermark_enabled(self) -> None:
        self._watermark.sync_enabled()

    def _on_location_changed(self) -> None:
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

    def _source_suffixes(self) -> Iterable[str]:
        return PROXY_VIDEO_SUFFIXES

    def _remove_selected(self) -> None:
        for item in self.clip_list.selectedItems():
            self.clip_list.takeItem(self.clip_list.row(item))

    def _clear_clips(self) -> None:
        self.clip_list.clear()

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
        watermark_text = self._watermark.payload_text()
        if self.watermark_cb.isChecked() and not watermark_text:
            self.log_message.emit("Invisible watermark skipped: payload is empty.")
        return ProxyJob(
            source=str(source),
            output=str(out),
            codec_key=self.codec_key(),
            resolution=self.resolution_key(),
            audio=self.audio_cb.isChecked(),
            watermark_text=watermark_text,
            watermark_strength=float(self.watermark_strength.value()),
            encoder=encoder_mode(self._settings),
            pixel_aspect=combo_override(self.pixel_aspect_combo),
        )

    def enqueue(self, sources: list[str]) -> int:
        queued = 0
        for src in sources:
            job = self._job_for(src)
            if job is not None and self._queue.add(job):
                queued += 1
        return queued

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
