"""Transcode tab: batch video → video transcodes for VFX and post.

Every transcode runs an OCIO conversion: each clip's input space comes from
the clip tree (its own pick, its folder's pick, or the space detected from
its metadata) and all clips share one output space. Jobs run on the serial
:class:`~src.services.proxy_queue.JobQueue` and the shared encode engine in
:mod:`src.core.transcode` (which the proxy tab also runs on).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    COMMON_FPS,
    DEFAULT_DST_E2V,
    X26X_PRESETS,
)
from ..core.ocio_utils import find_equivalent_space
from ..core.transcode import (
    AUDIO_MODES,
    CONTAINER_LABELS,
    CRF_RANGE,
    DEFAULT_AUDIO_MODE,
    DEFAULT_BITRATE_MBPS,
    DEFAULT_CRF,
    DEFAULT_NAME_PATTERN,
    DEFAULT_OVERWRITE,
    DEFAULT_PRESET,
    DEFAULT_TRANSCODE_CODEC,
    DEFAULT_TRANSCODE_RESOLUTION,
    NAME_TOKENS,
    OVERWRITE_MODES,
    RATE_BITRATE,
    RATE_CRF,
    RES_CUSTOM,
    TRANSCODE_RESOLUTION_MENU,
    TranscodeSettings,
    audio_modes_for_container,
    available_transcode_codecs,
    collect_transcode_sources,
    containers_for_codec,
    is_long_gop,
    plan_output,
    transcode_resolution_by_key,
    transcode_source_suffixes,
    validate_settings,
)
from ..core.trustmark.payload import (
    DEFAULT_STRENGTH,
    MAX_TEXT_CHARS,
    STRENGTH_MAX,
    STRENGTH_MIN,
)
from ..services.proxy_queue import TranscodeJob, TranscodeQueue
from .color_widgets import ColorSpaceButton, lock_form_row_height
from .convert_tab import _ConvertFormBody, _form_scroll_area
from .proxy_tab import _field_label, _limit_combo_width
from .style import HINT_STYLE
from .transcode_clips import ClipColorTree

LOCATION_BESIDE = "beside"
LOCATION_CUSTOM = "custom"

_K = "transcode"

# One output space for every clip (Rec.709 display is the usual dailies /
# editorial target). Input spaces are per clip / folder in the clip tree.
DEFAULT_COLOR_DST = DEFAULT_DST_E2V

_FPS_KEEP = 0.0
_FPS_CUSTOM = -1.0

_ICON_RUNNING = "▶"
_ICON_DONE = "✓"
_ICON_FAILED = "✗"


def _fps_text(fps: float) -> str:
    return str(int(fps)) if fps == int(fps) else f"{fps:.3f}"


class TranscodeTab(QWidget):
    """Transcode form. Jobs run serially on :class:`TranscodeQueue`."""

    log_message = Signal(str)
    readiness_changed = Signal(bool)
    progress = Signal(int, int)
    busy_changed = Signal(bool)

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._queue = TranscodeQueue(self)
        self._config_source = ""
        self._config_path = ""
        self._ocio_cfg: object | None = None
        self._running_source = ""
        self._running_pct = -1

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
        self.clip_tree = ClipColorTree()
        self.clip_tree.setObjectName("transcodeClipList")
        self.clip_tree.setMinimumHeight(120)
        self.clip_tree.setMaximumHeight(240)
        self.clip_tree.setToolTip(
            "Clips to transcode when you press Convert. Drop videos or folders here too.\n"
            "Each clip shows its progress, then ✓ when done or ✗ on error (hover for details)."
        )
        in_main.addWidget(self.clip_tree)
        self._clips_hint = QLabel(
            "Click an input color space to change it. A folder's space applies to every "
            "clip inside; expand the folder to set clips one by one."
        )
        self._clips_hint.setWordWrap(True)
        self._clips_hint.setStyleSheet(HINT_STYLE)
        in_main.addWidget(self._clips_hint)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._add_files_btn = QPushButton("Add Clips…")
        self._add_folder_btn = QPushButton("Add Folder…")
        self._remove_btn = QPushButton("Remove")
        self._clear_btn = QPushButton("Clear")
        for b in (self._add_files_btn, self._add_folder_btn, self._remove_btn, self._clear_btn):
            lock_form_row_height(b)
            btn_row.addWidget(b)
        self.recursive_cb = QCheckBox("Include subfolders")
        self.recursive_cb.setToolTip("Add Folder… also adds clips in nested folders.")
        self.recursive_cb.setChecked(settings.value(f"{_K}/recursive", False, type=bool))
        btn_row.addSpacing(8)
        btn_row.addWidget(self.recursive_cb)
        btn_row.addStretch()
        in_main.addLayout(btn_row)
        layout.addWidget(in_group)

        # -- Output --
        out_group = QGroupBox("Output")
        grid = QGridLayout(out_group)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self.codec_combo = QComboBox()
        for spec in available_transcode_codecs():
            self.codec_combo.addItem(spec.display_name, spec.key)
            self.codec_combo.setItemData(
                self.codec_combo.count() - 1, spec.format_label, Qt.ItemDataRole.ToolTipRole
            )
        self._select(self.codec_combo, settings.value(f"{_K}/codec", DEFAULT_TRANSCODE_CODEC))
        self.container_combo = QComboBox()
        self.container_combo.setToolTip("File type. The choices follow the codec.")
        grid.addWidget(_field_label("Codec"), 0, 0)
        grid.addWidget(self.codec_combo, 0, 1)
        grid.addWidget(_field_label("Container"), 0, 2)
        grid.addWidget(self.container_combo, 0, 3, 1, 2)

        self.resolution_combo = QComboBox()
        for key in TRANSCODE_RESOLUTION_MENU:
            self.resolution_combo.addItem(transcode_resolution_by_key(key).label, key)
        self.resolution_combo.setToolTip(
            "Output size. Percentages and Fit never upscale. Custom is exact\n"
            "(set one side to 0 to keep the source aspect ratio)."
        )
        self._select(
            self.resolution_combo,
            settings.value(f"{_K}/resolution", DEFAULT_TRANSCODE_RESOLUTION),
        )
        self.fps_combo = QComboBox()
        self.fps_combo.addItem("Keep source", _FPS_KEEP)
        for f in COMMON_FPS:
            self.fps_combo.addItem(f"Conform to {_fps_text(f)}", float(f))
        self.fps_combo.addItem("Conform to custom…", _FPS_CUSTOM)
        self.fps_combo.setToolTip(
            "Keep source plays every frame at the source rate.\n"
            "Conform plays the same frames at a new rate (no frames added or dropped),\n"
            "so the clip gets shorter or longer. Audio is dropped when conforming."
        )
        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(1.0, 240.0)
        self.fps_spin.setDecimals(3)
        self.fps_spin.setSuffix(" fps")
        self._restore_fps(float(settings.value(f"{_K}/fps", _FPS_KEEP, type=float)))
        fps_row = QHBoxLayout()
        fps_row.setSpacing(6)
        fps_row.addWidget(self.fps_combo, 1)
        fps_row.addWidget(self.fps_spin)
        grid.addWidget(_field_label("Resolution"), 1, 0)
        grid.addWidget(self.resolution_combo, 1, 1)
        grid.addWidget(_field_label("Frame rate"), 1, 2)
        grid.addLayout(fps_row, 1, 3, 1, 2)

        self._size_label = _field_label("Size")
        self.width_spin = QSpinBox()
        self.height_spin = QSpinBox()
        for spin, key, tip in (
            (self.width_spin, "custom_width", "Width in pixels (0 = from height and aspect)"),
            (self.height_spin, "custom_height", "Height in pixels (0 = from width and aspect)"),
        ):
            spin.setRange(0, 16384)
            spin.setSingleStep(2)
            spin.setToolTip(tip)
            spin.setValue(int(settings.value(f"{_K}/{key}", 0, type=int)))
        size_row = QHBoxLayout()
        size_row.setSpacing(6)
        size_row.addWidget(self.width_spin, 1)
        self._times_label = QLabel("×")
        size_row.addWidget(self._times_label)
        size_row.addWidget(self.height_spin, 1)
        size_row.addStretch(1)
        grid.addWidget(self._size_label, 2, 0)
        grid.addLayout(size_row, 2, 1, 1, 4)

        self._quality_label = _field_label("Quality")
        self.rate_combo = QComboBox()
        self.rate_combo.addItem("Constant quality (CRF)", RATE_CRF)
        self.rate_combo.addItem("Target bitrate", RATE_BITRATE)
        self._select(self.rate_combo, settings.value(f"{_K}/rate_control", RATE_CRF))
        self.crf_spin = QSpinBox()
        self.crf_spin.setPrefix("CRF ")
        self.crf_spin.setToolTip("Lower is better quality and bigger files.")
        self.bitrate_spin = QDoubleSpinBox()
        self.bitrate_spin.setRange(0.1, 2000.0)
        self.bitrate_spin.setDecimals(1)
        self.bitrate_spin.setSuffix(" Mb/s")
        self.bitrate_spin.setValue(
            float(settings.value(f"{_K}/bitrate", DEFAULT_BITRATE_MBPS, type=float))
        )
        self.preset_combo = QComboBox()
        for p in X26X_PRESETS:
            self.preset_combo.addItem(p, p)
        self.preset_combo.setToolTip("Encoder speed. Slower is smaller at the same quality.")
        self._select(self.preset_combo, settings.value(f"{_K}/preset", DEFAULT_PRESET))
        q_row = QHBoxLayout()
        q_row.setSpacing(6)
        q_row.addWidget(self.crf_spin)
        q_row.addWidget(self.bitrate_spin)
        grid.addWidget(self._quality_label, 3, 0)
        grid.addWidget(self.rate_combo, 3, 1)
        grid.addLayout(q_row, 3, 2)
        grid.addWidget(self.preset_combo, 3, 3, 1, 2)
        self._intra_note = QLabel("Intra-frame codec: quality is set by the profile.")
        self._intra_note.setStyleSheet(HINT_STYLE)
        grid.addWidget(self._intra_note, 3, 1, 1, 4)

        self.audio_combo = QComboBox()
        self.audio_combo.setToolTip(
            "First audio track. Copy keeps the source audio as is when the\n"
            "container can hold it, and encodes it otherwise."
        )
        self.overwrite_combo = QComboBox()
        for key, label in OVERWRITE_MODES:
            self.overwrite_combo.addItem(label, key)
        self.overwrite_combo.setToolTip(
            "When the output file already exists: skip the clip, replace the file,\n"
            "or add _1, _2, … to the new file name."
        )
        self._select(self.overwrite_combo, settings.value(f"{_K}/overwrite", DEFAULT_OVERWRITE))
        grid.addWidget(_field_label("Audio"), 4, 0)
        grid.addWidget(self.audio_combo, 4, 1)
        grid.addWidget(_field_label("If file exists"), 4, 2)
        grid.addWidget(self.overwrite_combo, 4, 3, 1, 2)

        self.location_combo = QComboBox()
        self.location_combo.addItem("Next to each clip", LOCATION_BESIDE)
        self.location_combo.addItem("Custom folder", LOCATION_CUSTOM)
        self.name_edit = QLineEdit(
            str(settings.value(f"{_K}/pattern", DEFAULT_NAME_PATTERN) or DEFAULT_NAME_PATTERN)
        )
        self.name_edit.setPlaceholderText(DEFAULT_NAME_PATTERN)
        self.name_edit.setToolTip(
            "File name without the extension. Tokens:\n"
            + "\n".join(f"<{t}>  {d}" for t, d in NAME_TOKENS)
        )
        grid.addWidget(_field_label("Location"), 5, 0)
        grid.addWidget(self.location_combo, 5, 1)
        grid.addWidget(_field_label("Name"), 5, 2)
        grid.addWidget(self.name_edit, 5, 3, 1, 2)

        self._folder_label = _field_label("Folder")
        self.output_dir = QLineEdit(str(settings.value(f"{_K}/output_dir", "") or ""))
        self.output_dir.setPlaceholderText("Folder for all transcodes")
        self._browse_out = QPushButton("Browse…")
        grid.addWidget(self._folder_label, 6, 0)
        grid.addWidget(self.output_dir, 6, 1, 1, 3)
        grid.addWidget(self._browse_out, 6, 4)
        layout.addWidget(out_group)

        # -- Color and watermark --
        color_group = QGroupBox("Color and watermark")
        color_grid = QGridLayout(color_group)
        color_grid.setHorizontalSpacing(8)
        color_grid.setVerticalSpacing(6)
        color_grid.setColumnStretch(1, 1)
        self.dst_btn = ColorSpaceButton()
        self.dst_btn.setToolTip(
            "Output color space or display for every clip, from the OCIO config\n"
            "chosen at the top of the window. Each clip converts from its input\n"
            "space in the list above."
        )
        color_grid.addWidget(_field_label("Output space"), 0, 0)
        color_grid.addWidget(self.dst_btn, 0, 1)

        self.watermark_cb = QCheckBox("Invisible watermark")
        self.watermark_cb.setToolTip(
            "Embed an invisible TrustMark watermark in every frame (same as Video → Proxy).\n"
            "Off by default. The encoder model downloads once into your cache."
        )
        self.watermark_cb.setChecked(settings.value(f"{_K}/watermark", False, type=bool))
        self.watermark_payload = QLineEdit(str(settings.value(f"{_K}/watermark_text", "") or ""))
        self.watermark_payload.setMaxLength(MAX_TEXT_CHARS)
        self.watermark_payload.setPlaceholderText(
            f"Payload, up to {MAX_TEXT_CHARS} ASCII characters"
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
        wm_row.addWidget(self.watermark_payload, 1)
        wm_row.addWidget(self._strength_label)
        wm_row.addWidget(self.watermark_strength)
        color_grid.addWidget(self.watermark_cb, 1, 0)
        color_grid.addLayout(wm_row, 1, 1)
        layout.addWidget(color_group)

        for combo, chars in (
            (self.codec_combo, 18),
            (self.container_combo, 12),
            (self.resolution_combo, 12),
            (self.fps_combo, 12),
            (self.rate_combo, 14),
            (self.preset_combo, 8),
            (self.audio_combo, 12),
            (self.overwrite_combo, 10),
            (self.location_combo, 14),
        ):
            _limit_combo_width(combo, chars)
        for g in (in_group, out_group, color_group):
            g.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addStretch()

        for w in (
            self.recursive_cb,
            self.codec_combo,
            self.container_combo,
            self.resolution_combo,
            self.fps_combo,
            self.fps_spin,
            self.width_spin,
            self.height_spin,
            self.rate_combo,
            self.crf_spin,
            self.bitrate_spin,
            self.preset_combo,
            self.audio_combo,
            self.overwrite_combo,
            self.location_combo,
            self.name_edit,
            self.output_dir,
            self._browse_out,
            self.watermark_cb,
            self.watermark_payload,
            self._strength_label,
            self.watermark_strength,
        ):
            lock_form_row_height(w)

        outer.addWidget(_form_scroll_area(body))

        self._select(self.location_combo, settings.value(f"{_K}/location", LOCATION_BESIDE))
        self._on_codec_changed(save=False)
        self._sync_all()

        # -- Connections --
        self._add_files_btn.clicked.connect(self._pick_files)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn.clicked.connect(self._clear_clips)
        self._browse_out.clicked.connect(self._pick_output_dir)
        self.recursive_cb.toggled.connect(lambda v: self._save("recursive", v))
        self.codec_combo.currentIndexChanged.connect(lambda _: self._on_codec_changed())
        self.container_combo.currentIndexChanged.connect(lambda _: self._on_container_changed())
        self.resolution_combo.currentIndexChanged.connect(
            lambda _: self._changed("resolution", self.resolution_key())
        )
        self.width_spin.valueChanged.connect(lambda v: self._changed("custom_width", int(v)))
        self.height_spin.valueChanged.connect(lambda v: self._changed("custom_height", int(v)))
        self.fps_combo.currentIndexChanged.connect(lambda _: self._on_fps_changed())
        self.fps_spin.valueChanged.connect(lambda _: self._on_fps_changed())
        self.rate_combo.currentIndexChanged.connect(
            lambda _: self._changed("rate_control", self.rate_control())
        )
        self.crf_spin.valueChanged.connect(lambda v: self._save(f"crf_{self.codec_key()}", int(v)))
        self.bitrate_spin.valueChanged.connect(lambda v: self._save("bitrate", float(v)))
        self.preset_combo.currentIndexChanged.connect(
            lambda _: self._save("preset", self.preset_combo.currentData())
        )
        self.audio_combo.currentIndexChanged.connect(
            lambda _: self._save("audio", self.audio_mode())
        )
        self.overwrite_combo.currentIndexChanged.connect(
            lambda _: self._save("overwrite", self.overwrite_mode())
        )
        self.location_combo.currentIndexChanged.connect(
            lambda _: self._changed("location", self.location())
        )
        self.name_edit.textChanged.connect(lambda t: self._changed("pattern", t))
        self.output_dir.textChanged.connect(lambda t: self._changed("output_dir", t))
        self.dst_btn.space_changed.connect(lambda n: self._changed("color_dst", n))
        self.watermark_cb.toggled.connect(lambda v: self._changed("watermark", v))
        self.watermark_payload.textChanged.connect(lambda t: self._changed("watermark_text", t))
        self.watermark_strength.valueChanged.connect(
            lambda v: self._save("watermark_strength", float(v))
        )
        self.clip_tree.spaces_changed.connect(self._emit_readiness)

        self._queue.log_message.connect(self.log_message)
        self._queue.progress.connect(self._on_progress)
        self._queue.busy_changed.connect(self.busy_changed)
        self._queue.job_started.connect(self._on_job_started)
        self._queue.job_finished.connect(self._on_job_finished)
        self._queue.job_failed.connect(self._on_job_failed)

    # -- small helpers --

    @staticmethod
    def _select(combo: QComboBox, value: object) -> None:
        idx = combo.findData(value if isinstance(value, float) else str(value or ""))
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _save(self, key: str, value: object) -> None:
        self._settings.setValue(f"{_K}/{key}", value)

    def _changed(self, key: str, value: object) -> None:
        self._save(key, value)
        self._sync_all()

    def codec_key(self) -> str:
        return str(self.codec_combo.currentData() or DEFAULT_TRANSCODE_CODEC)

    def container(self) -> str:
        return str(self.container_combo.currentData() or "")

    def resolution_key(self) -> str:
        return str(self.resolution_combo.currentData() or DEFAULT_TRANSCODE_RESOLUTION)

    def rate_control(self) -> str:
        return str(self.rate_combo.currentData() or RATE_CRF)

    def audio_mode(self) -> str:
        return str(self.audio_combo.currentData() or DEFAULT_AUDIO_MODE)

    def overwrite_mode(self) -> str:
        return str(self.overwrite_combo.currentData() or DEFAULT_OVERWRITE)

    def location(self) -> str:
        return str(self.location_combo.currentData() or LOCATION_BESIDE)

    def fps(self) -> float:
        val = float(self.fps_combo.currentData() or 0.0)
        if val == _FPS_CUSTOM:
            return float(self.fps_spin.value())
        return val

    def custom_output_dir(self) -> str | None:
        if self.location() != LOCATION_CUSTOM:
            return None
        return self.output_dir.text().strip() or None

    def clips(self) -> list[str]:
        return self.clip_tree.clips()

    def input_space(self, source: str) -> str:
        """The input color space *source* converts from (``""`` if unset)."""
        return self.clip_tree.effective_space(source)

    @property
    def queue(self) -> TranscodeQueue:
        return self._queue

    def is_busy(self) -> bool:
        return self._queue.is_busy()

    def is_ready(self) -> bool:
        if self.clip_tree.clip_count() == 0:
            return False
        if self.location() == LOCATION_CUSTOM and not self.custom_output_dir():
            return False
        if self.resolution_key() == RES_CUSTOM and not (
            self.width_spin.value() or self.height_spin.value()
        ):
            return False
        if not self.dst_btn.is_valid() or self.clip_tree.detection_pending():
            return False
        return self.clip_tree.all_valid()

    def _emit_readiness(self) -> None:
        self.readiness_changed.emit(self.is_ready())

    def _restore_fps(self, saved: float) -> None:
        for i in range(self.fps_combo.count()):
            data = float(self.fps_combo.itemData(i))
            if data != _FPS_CUSTOM and abs(data - saved) < 0.001:
                self.fps_combo.setCurrentIndex(i)
                return
        self.fps_combo.setCurrentIndex(self.fps_combo.count() - 1)
        self.fps_spin.setValue(saved if saved > 0 else 24.0)

    # -- dependent controls --

    def _on_codec_changed(self, *, save: bool = True) -> None:
        key = self.codec_key()
        if save:
            self._save("codec", key)
        # Each codec remembers its own container; otherwise use the codec default.
        wanted = str(self._settings.value(f"{_K}/container_{key}", "") or "")
        self.container_combo.blockSignals(True)
        self.container_combo.clear()
        for c in containers_for_codec(key):
            self.container_combo.addItem(CONTAINER_LABELS.get(c, f".{c}"), c)
        self._select(self.container_combo, wanted)
        self.container_combo.blockSignals(False)
        lo, hi = CRF_RANGE.get(key, (0, 51))
        self.crf_spin.blockSignals(True)
        self.crf_spin.setRange(lo, hi)
        saved = self._settings.value(f"{_K}/crf_{key}", DEFAULT_CRF.get(key, 18), type=int)
        self.crf_spin.setValue(int(saved))
        self.crf_spin.blockSignals(False)
        self._on_container_changed(save=save)

    def _on_container_changed(self, *, save: bool = True) -> None:
        container = self.container()
        if save:
            self._save(f"container_{self.codec_key()}", container)
        wanted = self.audio_mode() if self.audio_combo.count() else None
        if wanted is None:
            wanted = str(self._settings.value(f"{_K}/audio", DEFAULT_AUDIO_MODE))
        labels = dict(AUDIO_MODES)
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        for mode in audio_modes_for_container(container):
            self.audio_combo.addItem(labels[mode], mode)
        self._select(self.audio_combo, wanted)
        self.audio_combo.blockSignals(False)
        self._sync_all()

    def _on_fps_changed(self) -> None:
        self._save("fps", self.fps())
        self._sync_all()

    def _sync_all(self) -> None:
        custom_size = self.resolution_key() == RES_CUSTOM
        for w in (self._size_label, self.width_spin, self._times_label, self.height_spin):
            w.setVisible(custom_size)

        self.fps_spin.setVisible(float(self.fps_combo.currentData() or 0.0) == _FPS_CUSTOM)

        long_gop = is_long_gop(self.codec_key())
        bitrate = self.rate_control() == RATE_BITRATE
        self.rate_combo.setVisible(long_gop)
        self.crf_spin.setVisible(long_gop and not bitrate)
        self.bitrate_spin.setVisible(long_gop and bitrate)
        self.preset_combo.setVisible(long_gop)
        self._intra_note.setVisible(not long_gop)

        custom = self.location() == LOCATION_CUSTOM
        for w in (self._folder_label, self.output_dir, self._browse_out):
            w.setVisible(custom)

        wm = self.watermark_cb.isChecked()
        for w in (self.watermark_payload, self._strength_label, self.watermark_strength):
            w.setEnabled(wm)
        self._emit_readiness()

    # -- OCIO --

    def populate_spaces(
        self,
        families: dict[str, list[str]],
        ocio_cfg: object | None = None,
        *,
        config_source: str = "",
        config_path: str = "",
    ) -> None:
        """Rebuild the color-space menus for the window's OCIO config."""
        self._ocio_cfg = ocio_cfg
        self._config_source = config_source
        self._config_path = config_path
        wanted = self.dst_btn.displayed_space() or str(
            self._settings.value(f"{_K}/color_dst", DEFAULT_COLOR_DST) or DEFAULT_COLOR_DST
        )
        wanted = wanted.removeprefix("⚠ ").strip()
        resolved = ""
        if ocio_cfg is not None:
            try:
                resolved = find_equivalent_space(ocio_cfg, wanted)  # pyright: ignore[reportArgumentType]
            except Exception:
                resolved = ""
        self.dst_btn.populate(families, resolved)
        if not resolved:
            self.dst_btn.set_invalid(wanted)
        self.clip_tree.set_config(families, ocio_cfg)
        self._sync_all()

    # -- clip list --

    def add_paths(self, paths: list[str]) -> int:
        """Add clips (files or folders). Returns how many new clips were listed.

        Each folder gets its own row holding its clips, so one input color
        space can be set for the whole folder.
        """
        recursive = self.recursive_cb.isChecked()
        added = 0
        loose: list[str] = []
        for raw in paths:
            if Path(raw).expanduser().is_dir():
                folder = str(Path(raw).expanduser())
                found = collect_transcode_sources([folder], recursive=recursive)
                added += self.clip_tree.add_sources(found, folder=folder)
            else:
                loose.append(raw)
        if loose:
            added += self.clip_tree.add_sources(collect_transcode_sources(loose))
        if added:
            self.log_message.emit(f"Transcode list: added {added} clip(s)")
        return added

    def handle_dropped_path(self, path: str) -> bool:
        return self.add_paths([path]) > 0

    def _set_item_state(self, source: str, prefix: str, tip: str = "") -> None:
        self.clip_tree.set_status(source, prefix, tip)

    def _pick_files(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(transcode_source_suffixes()))
        start = str(self._settings.value(f"{_K}/last_dir", "") or "")
        files, _ = QFileDialog.getOpenFileNames(
            self, "Add clips", start, f"Video ({exts});;All files (*)"
        )
        if files:
            self._save("last_dir", str(Path(files[0]).parent))
            self.add_paths(files)

    def _pick_folder(self) -> None:
        start = str(self._settings.value(f"{_K}/last_dir", "") or "")
        folder = QFileDialog.getExistingDirectory(self, "Add folder of clips", start)
        if folder:
            self._save("last_dir", folder)
            if not self.add_paths([folder]):
                self.log_message.emit(f"No video clips found in {folder}")

    def _remove_selected(self) -> None:
        self.clip_tree.remove_selected()

    def _clear_clips(self) -> None:
        self.clip_tree.clear_clips()

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Transcode output folder", self.output_dir.text().strip()
        )
        if folder:
            self.output_dir.setText(folder)

    # -- jobs --

    def current_settings(self) -> TranscodeSettings:
        """Encode settings from the form (raises ValueError when invalid).

        The input space is per clip (see :meth:`settings_for`); here it is the
        output space, a placeholder that passes validation.
        """
        watermark_text = ""
        if self.watermark_cb.isChecked():
            watermark_text = self.watermark_payload.text().strip()
        dst = self.dst_btn.current_space()
        if not dst:
            raise ValueError("Pick an output color space.")
        settings = TranscodeSettings(
            codec_key=self.codec_key(),
            container=self.container(),
            resolution=self.resolution_key(),
            custom_width=int(self.width_spin.value()),
            custom_height=int(self.height_spin.value()),
            fps=self.fps(),
            audio=self.audio_mode(),
            rate_control=self.rate_control(),
            crf=int(self.crf_spin.value()),
            bitrate_mbps=float(self.bitrate_spin.value()),
            preset=str(self.preset_combo.currentData() or DEFAULT_PRESET),
            color_src=dst,
            color_dst=dst,
            config_source=self._config_source,
            config_path=self._config_path,
            watermark_text=watermark_text,
            watermark_strength=float(self.watermark_strength.value()),
        )
        return validate_settings(settings)

    def settings_for(self, source: str, base: TranscodeSettings) -> TranscodeSettings:
        """*base* with *source*'s input space (raises ValueError when unset)."""
        src_space = self.input_space(source)
        if not src_space:
            raise ValueError("No valid input color space. Pick one in the clip list.")
        return replace(base, color_src=src_space)

    def _job_for(
        self, source: str, settings: TranscodeSettings, taken: set[str]
    ) -> TranscodeJob | None:
        """Build a job for *source*, or ``None`` if it is skipped."""
        try:
            settings = self.settings_for(source, settings)
            out = plan_output(
                source,
                settings,
                output_dir=self.custom_output_dir(),
                pattern=self.name_edit.text().strip() or DEFAULT_NAME_PATTERN,
                overwrite=self.overwrite_mode(),
                taken=taken,
            )
        except (ValueError, RuntimeError) as e:
            self.log_message.emit(f"ERROR: {Path(source).name}: {e}")
            self._set_item_state(source, _ICON_FAILED, str(e))
            return None
        if out is None:
            self.log_message.emit(f"Skip (file exists): {Path(source).name}")
            return None
        taken.add(str(out))
        return TranscodeJob(source=str(source), output=str(out), settings=settings)

    def enqueue(self, sources: list[str]) -> int:
        try:
            settings = self.current_settings()
        except ValueError as e:
            self.log_message.emit(f"ERROR: {e}")
            return 0
        if self.watermark_cb.isChecked() and not settings.watermark_text:
            self.log_message.emit("Invisible watermark skipped: payload is empty.")
        taken = {str(Path(p)) for p in self._queue.queued_outputs()}
        queued = 0
        for src in sources:
            job = self._job_for(src, settings, taken)
            if job is not None and self._queue.add(job):
                queued += 1
                if src != self._queue.current_source():
                    self._set_item_state(src, "…", "Queued")
        return queued

    def start_batch(self) -> int:
        """Queue every listed clip (the window's Convert button)."""
        if not self.is_ready():
            return 0
        clips = self.clips()
        self.log_message.emit(f"--- transcode ({len(clips)} clip(s)) ---")
        n = self.enqueue(clips)
        if n == 0:
            self.log_message.emit("Nothing to convert.")
        return n

    def cancel(self) -> None:
        self._queue.cancel_all()
        for src in self.clips():
            if self.clip_tree.status_text(src).startswith("… "):
                self._set_item_state(src, "")

    def _on_job_started(self, source: str, _output: str) -> None:
        self._running_source = source
        self._running_pct = -1
        self._set_item_state(source, f"{_ICON_RUNNING} 0%", "Transcoding")

    def _on_progress(self, cur: int, total: int) -> None:
        self.progress.emit(cur, total)
        if not self._running_source or total <= 0:
            return
        pct = int(100 * cur / total)
        if pct != self._running_pct:
            self._running_pct = pct
            self._set_item_state(self._running_source, f"{_ICON_RUNNING} {pct}%", "Transcoding")

    def _on_job_finished(self, source: str, output: str, ok: bool) -> None:
        self._running_source = ""
        if ok:
            self._set_item_state(source, _ICON_DONE, f"→ {output}")
        else:
            if not self.clip_tree.status_text(source).startswith(_ICON_FAILED):
                self._set_item_state(source, "", "Cancelled")

    def _on_job_failed(self, source: str, error: str) -> None:
        self._set_item_state(source, _ICON_FAILED, error)

    def shutdown(self) -> None:
        """Stop any running job (window close)."""
        self._queue.shutdown()
        self.clip_tree.shutdown()
