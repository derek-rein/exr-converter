"""Transcode tab: batch video → video transcodes for VFX and post.

Every transcode runs an OCIO conversion: each clip's input space comes from
the clip tree (its own pick, its folder's pick, or the space detected from
its metadata) and all clips share one output space. Jobs run on the serial
:class:`~src.services.proxy_queue.JobQueue` and the shared encode engine in
:mod:`src.core.transcode` (which the proxy tab also runs on).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    COMMON_FPS,
    DEFAULT_DST_E2V,
    X26X_PRESETS,
)
from ..core.ocio_utils import find_equivalent_space
from ..core.path_tokens import PATH_TOKEN_GROUPS, tokens_tooltip
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
from ..services.proxy_queue import TranscodeJob, TranscodeQueue
from .batch_common import (
    CLIP_LIST_MAX_HEIGHT,
    CLIP_LIST_MIN_HEIGHT,
    BatchTab,
    OutputLocation,
    SourceClipsPanel,
)
from .batch_rules import LOCATION_BESIDE, LOCATION_CUSTOM  # noqa: F401 (re-exported)
from .color_widgets import ColorSpaceButton, lock_form_row_height
from .components import FILL, NARROW, WIDE, Card, DialogFooter, FormGrid, inline_label, sized
from .convert_tab import _ConvertFormBody, _form_scroll_area
from .filename_filter import BAD_PATH_CHARS, BAD_PATTERN_CHARS, install_filename_filter
from .icons import set_button_icon
from .pixel_aspect_ui import combo_override, make_pixel_aspect_combo
from .preferences import encoder_mode, resolve_invisible_watermark
from .style import set_style_property
from .token_line_edit import TokenLineEdit
from .transcode_clips import ClipColorTree

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


@dataclass(frozen=True)
class QualitySettings:
    """Rate control for the long-GOP codecs (H.264, HEVC, AV1).

    One plain value, held by the tab and replaced whole by the Quality dialog.
    ``crf`` belongs to the current codec: each codec keeps its own saved CRF.
    """

    rate_control: str = RATE_CRF
    crf: int = 18
    bitrate_mbps: float = DEFAULT_BITRATE_MBPS
    preset: str = DEFAULT_PRESET


_RATE_OPTIONS = (("Constant quality (CRF)", RATE_CRF), ("Target bitrate", RATE_BITRATE))


class TranscodeTab(BatchTab):
    """Transcode form. Jobs run serially on :class:`TranscodeQueue`."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(settings, _K, "transcode", parent)
        self._migrate_watermark_settings(settings)
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
        self.clip_tree = ClipColorTree()
        self.clip_tree.setObjectName("transcodeClipList")
        self.clip_tree.setMinimumHeight(CLIP_LIST_MIN_HEIGHT)
        self.clip_tree.setMaximumHeight(CLIP_LIST_MAX_HEIGHT)
        self.clip_tree.setToolTip(
            "Clips to transcode when you press Convert. Drop videos or folders here too.\n"
            "Each clip shows its progress, then ✓ when done or ✗ on error (hover for details).\n"
            "Click an input color space to change it. A folder's space applies to every clip "
            "inside; expand the folder to set clips one by one."
        )
        in_group = self._sources = SourceClipsPanel(
            self.clip_tree,
            settings,
            _K,
            recursive_tip="Add Folder… also adds clips in nested folders.",
        )
        self._add_files_btn = in_group.add_files_btn
        self._add_folder_btn = in_group.add_folder_btn
        self._remove_btn = in_group.remove_btn
        self._clear_btn = in_group.clear_btn
        self.recursive_cb = in_group.recursive_cb
        layout.addWidget(in_group)

        # -- Output --
        out_group = Card("Output")
        form = FormGrid()
        out_group.body.addLayout(form)

        self.codec_combo = QComboBox()
        for spec in available_transcode_codecs():
            self.codec_combo.addItem(spec.display_name, spec.key)
            self.codec_combo.setItemData(
                self.codec_combo.count() - 1, spec.format_label, Qt.ItemDataRole.ToolTipRole
            )
        self._select(self.codec_combo, settings.value(f"{_K}/codec", DEFAULT_TRANSCODE_CODEC))
        self.container_combo = QComboBox()
        self.container_combo.setToolTip("File type. The choices follow the codec.")
        self._container_label = inline_label("Container", self.container_combo)
        # Rate control (CRF / bitrate, preset) lives behind a gear, as on EXR → Video.
        # The gear shows only for codecs that have those settings.
        self._quality_btn = QToolButton()
        set_button_icon(self._quality_btn, "settings")
        set_style_property(self._quality_btn, "variant", "ghost")
        self._quality_btn.setAccessibleName("Quality settings")
        self._quality_btn.setAutoRaise(True)
        self._quality_btn.setFixedWidth(28)
        self._quality_btn.setToolTip("Quality settings…")
        self._quality_btn.clicked.connect(self._open_quality_dialog)
        form.add_row(
            "Codec",
            self.codec_combo,
            self._quality_btn,
            self._container_label,
            sized(self.container_combo, NARROW),
            width=WIDE,
        )

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
        self._times_label = QLabel("×")
        # Shown only while Resolution is Custom (see _sync_all).
        self._size_label = form.add_row(
            "Size",
            self.width_spin,
            self._times_label,
            sized(self.height_spin, NARROW),
            width=NARROW,
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
        # The custom rate box shows only for "Conform to custom…".
        form.add_row("Frame rate", self.fps_combo, sized(self.fps_spin, NARROW), width=WIDE)

        self.audio_combo = QComboBox()
        self.audio_combo.setToolTip(
            "First audio track. Copy keeps the source audio as is when the\n"
            "container can hold it, and encodes it otherwise."
        )
        form.add_row("Audio", self.audio_combo, width=WIDE)

        # Rate control is a plain value, edited in the Quality dialog (the gear).
        self._quality = self._load_quality(settings, self.codec_key())

        output_dir = TokenLineEdit(
            groups=PATH_TOKEN_GROUPS,
            tooltip=(
                "Folder for all transcodes. It can use variables too, e.g.\n"
                "/Volumes/Dailies/<date>/<reel>  or  $DAILIES_ROOT/<reel>\n"
                "$VAR / ${VAR} read environment variables (${VAR:-default} for a fallback).\n"
                "Right-click to insert a variable:\n\n" + tokens_tooltip()
            ),
        )
        output_dir.setText(str(settings.value(f"{_K}/output_dir", "") or ""))
        output_dir.setPlaceholderText("Folder for all transcodes")
        self._location = OutputLocation(
            "Next to each clip",
            output_dir,
            dialog_parent=self,
            dialog_title="Transcode output folder",
        )
        self.location_combo = self._location.combo
        self._location.add_to(form)
        self._folder_label = self._location.folder_label
        self.output_dir = output_dir
        self._browse_out = self._location.browse_btn

        self.name_edit = TokenLineEdit(
            groups=PATH_TOKEN_GROUPS,
            tooltip=(
                "File name without the extension. Use / to make sub-folders, e.g.\n"
                "<reel>/<name>/<srcresolution>/<name>_<codec>\n"
                "$VAR / ${VAR} read environment variables.\n"
                "Right-click to insert a variable:\n\n" + tokens_tooltip()
            ),
        )
        self.name_edit.setText(
            str(settings.value(f"{_K}/pattern", DEFAULT_NAME_PATTERN) or DEFAULT_NAME_PATTERN)
        )
        self.name_edit.setPlaceholderText(DEFAULT_NAME_PATTERN)
        # "/" makes sub-folders and <tokens> use < >, so only the never-valid ones go.
        install_filename_filter(self.name_edit, BAD_PATTERN_CHARS)
        install_filename_filter(output_dir, BAD_PATH_CHARS)
        self.overwrite_combo = QComboBox()
        for key, label in OVERWRITE_MODES:
            self.overwrite_combo.addItem(label, key)
        self.overwrite_combo.setToolTip(
            "When the output file already exists: skip the clip, replace the file,\n"
            "or add _1, _2, … to the new file name."
        )
        self._select(self.overwrite_combo, settings.value(f"{_K}/overwrite", DEFAULT_OVERWRITE))
        form.add_row(
            "Name",
            self.name_edit,
            inline_label("If file exists", self.overwrite_combo),
            sized(self.overwrite_combo, NARROW),
            width=FILL,
        )

        # -- Color and watermark --
        # Color and the watermark are part of the Output section: one card fewer
        # saves a header and its padding.
        color_form = form
        self.dst_btn = ColorSpaceButton()
        self.dst_btn.setToolTip(
            "Output color space or display for every clip, from the OCIO config\n"
            "chosen at the top of the window. Each clip converts from its input\n"
            "space in the list above."
        )
        # The invisible watermark is a checkbox on the Output space row. Its payload
        # and strength are the app-wide values in Preferences.
        self.watermark_cb = QCheckBox("Invisible WM")
        self.watermark_cb.setToolTip(
            "Embed an invisible TrustMark watermark in every frame.\n"
            "Set the payload and strength in Preferences → Invisible watermark.\n"
            "The encoder model downloads once into your cache."
        )
        self.watermark_cb.setChecked(settings.value(f"{_K}/watermark", False, type=bool))
        self.watermark_cb.toggled.connect(lambda v: self._settings.setValue(f"{_K}/watermark", v))
        color_form.add_row("Output space", self.dst_btn, self.watermark_cb, width=WIDE, spacing=14)
        layout.addWidget(out_group)

        for g in (in_group, out_group):
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
            self.pixel_aspect_combo,
            self.audio_combo,
            self.overwrite_combo,
            self.location_combo,
            self.name_edit,
            self.output_dir,
            self._browse_out,
            self.watermark_cb,
        ):
            lock_form_row_height(w)

        outer.addWidget(_form_scroll_area(body))

        # "If file exists" shares the Name row; tab through it in reading order.
        QWidget.setTabOrder(self.name_edit, self.overwrite_combo)
        QWidget.setTabOrder(self.overwrite_combo, self.dst_btn)

        self._location.restore(settings, _K)
        self._on_codec_changed(save=False)
        self._sync_all()

        # -- Connections --
        self._add_files_btn.clicked.connect(self._pick_files)
        self._add_folder_btn.clicked.connect(self._pick_folder)
        self._remove_btn.clicked.connect(self._remove_selected)
        self._clear_btn.clicked.connect(self._clear_clips)
        self._sources.save_to(settings, _K)
        self.codec_combo.currentIndexChanged.connect(lambda _: self._on_codec_changed())
        self.container_combo.currentIndexChanged.connect(lambda _: self._on_container_changed())
        self.resolution_combo.currentIndexChanged.connect(
            lambda _: self._changed("resolution", self.resolution_key())
        )
        self.width_spin.valueChanged.connect(lambda v: self._changed("custom_width", int(v)))
        self.height_spin.valueChanged.connect(lambda v: self._changed("custom_height", int(v)))
        self.fps_combo.currentIndexChanged.connect(lambda _: self._on_fps_changed())
        self.fps_spin.valueChanged.connect(lambda _: self._on_fps_changed())
        self.audio_combo.currentIndexChanged.connect(
            lambda _: self._save("audio", self.audio_mode())
        )
        self.overwrite_combo.currentIndexChanged.connect(
            lambda _: self._save("overwrite", self.overwrite_mode())
        )
        self._location.save_to(settings, _K)
        self.location_combo.currentIndexChanged.connect(lambda _: self._sync_all())
        self.name_edit.textChanged.connect(lambda t: self._changed("pattern", t))
        self.output_dir.textChanged.connect(lambda _: self._sync_all())
        self.dst_btn.space_changed.connect(lambda n: self._changed("color_dst", n))
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

    def quality(self) -> QualitySettings:
        return self._quality

    def rate_control(self) -> str:
        return self._quality.rate_control

    def set_quality(self, quality: QualitySettings) -> None:
        """Adopt *quality* (clamping CRF to the codec's range) and save it."""
        lo, hi = CRF_RANGE.get(self.codec_key(), (0, 51))
        quality = replace(quality, crf=min(max(int(quality.crf), lo), hi))
        self._quality = quality
        self._save("rate_control", quality.rate_control)
        self._save(f"crf_{self.codec_key()}", quality.crf)
        self._save("bitrate", quality.bitrate_mbps)
        self._save("preset", quality.preset)

    @staticmethod
    def _load_quality(settings: QSettings, codec_key: str) -> QualitySettings:
        """The saved rate control, with the CRF saved for *codec_key*."""
        lo, hi = CRF_RANGE.get(codec_key, (0, 51))
        rate = str(settings.value(f"{_K}/rate_control", RATE_CRF) or RATE_CRF)
        preset = str(settings.value(f"{_K}/preset", DEFAULT_PRESET) or DEFAULT_PRESET)
        crf = int(settings.value(f"{_K}/crf_{codec_key}", DEFAULT_CRF.get(codec_key, 18), type=int))
        return QualitySettings(
            rate_control=rate if rate in dict(_RATE_OPTIONS).values() else RATE_CRF,
            crf=min(max(crf, lo), hi),
            bitrate_mbps=float(settings.value(f"{_K}/bitrate", DEFAULT_BITRATE_MBPS, type=float)),
            preset=preset if preset in X26X_PRESETS else DEFAULT_PRESET,
        )

    def audio_mode(self) -> str:
        return str(self.audio_combo.currentData() or DEFAULT_AUDIO_MODE)

    def overwrite_mode(self) -> str:
        return str(self.overwrite_combo.currentData() or DEFAULT_OVERWRITE)

    def location(self) -> str:
        return self._location.location()

    def fps(self) -> float:
        val = float(self.fps_combo.currentData() or 0.0)
        if val == _FPS_CUSTOM:
            return float(self.fps_spin.value())
        return val

    def custom_output_dir(self) -> str | None:
        return self._location.custom_output_dir()

    def clips(self) -> list[str]:
        return self.clip_tree.clips()

    def input_space(self, source: str) -> str:
        """The input color space *source* converts from (``""`` if unset)."""
        return self.clip_tree.effective_space(source)

    @property
    def queue(self) -> TranscodeQueue:
        return self._queue

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

    @staticmethod
    def _migrate_watermark_settings(settings: QSettings) -> None:
        """Carry a payload and strength saved on this tab into Preferences, once.

        Before, Transcode kept its own invisible-watermark payload and strength.
        They now come from Preferences, so a user's own values are copied there
        (only when Preferences has none yet) instead of silently reverting to the
        default payload.
        """
        from ..services.app_settings import Keys
        from .preferences import clean_invisible_wm_template

        old_text, old_strength = f"{_K}/watermark_text", f"{_K}/watermark_strength"
        if not (settings.contains(old_text) or settings.contains(old_strength)):
            return
        if not settings.contains(Keys.INVISIBLE_WM_TEMPLATE):
            text = clean_invisible_wm_template(str(settings.value(old_text, "") or ""))
            if text:
                settings.setValue(Keys.INVISIBLE_WM_TEMPLATE, text)
            if settings.contains(old_strength) and not settings.contains(
                Keys.INVISIBLE_WM_STRENGTH
            ):
                settings.setValue(Keys.INVISIBLE_WM_STRENGTH, settings.value(old_strength))
        settings.remove(old_text)
        settings.remove(old_strength)

    def _open_quality_dialog(self) -> None:
        dlg = _QualityDialog(self.codec_key(), self.codec_combo.currentText(), self._quality, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_quality(dlg.quality())

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
        # One possible container leaves nothing to choose.
        several = self.container_combo.count() > 1
        self.container_combo.setEnabled(several)
        self._container_label.setEnabled(several)
        # The CRF belongs to the codec: switch to the one saved for this codec.
        self._quality = replace(self._quality, crf=self._load_quality(self._settings, key).crf)
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
        self._quality_btn.setVisible(long_gop)

        self._location.sync_visible()

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

    def _set_item_state(self, source: str, prefix: str, tip: str = "") -> None:
        self.clip_tree.set_status(source, prefix, tip)

    def _source_suffixes(self) -> Iterable[str]:
        return transcode_source_suffixes()

    def _remove_selected(self) -> None:
        self.clip_tree.remove_selected()

    def _clear_clips(self) -> None:
        self.clip_tree.clear_clips()

    # -- jobs --

    def current_settings(self) -> TranscodeSettings:
        """Encode settings from the form (raises ValueError when invalid).

        The input space is per clip (see :meth:`settings_for`); here it is the
        output space, a placeholder that passes validation.
        """
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
            rate_control=self._quality.rate_control,
            crf=self._quality.crf,
            bitrate_mbps=self._quality.bitrate_mbps,
            preset=self._quality.preset,
            color_src=dst,
            color_dst=dst,
            config_source=self._config_source,
            config_path=self._config_path,
            pixel_aspect=combo_override(self.pixel_aspect_combo),
            encoder=encoder_mode(self._settings),
        )
        return validate_settings(settings)

    def settings_for(self, source: str, base: TranscodeSettings) -> TranscodeSettings:
        """*base* with *source*'s input space (raises ValueError when unset)."""
        src_space = self.input_space(source)
        if not src_space:
            raise ValueError("No valid input color space. Pick one in the clip list.")
        settings = replace(base, color_src=src_space)
        if self.watermark_cb.isChecked():
            # The payload may use {src}, so it is filled in per clip.
            text, strength = resolve_invisible_watermark(
                self._settings, source_name=Path(source).stem
            )
            settings = replace(settings, watermark_text=text, watermark_strength=strength)
        return settings

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
        if self.watermark_cb.isChecked():
            # Once for the batch: says when the Preferences payload is empty.
            resolve_invisible_watermark(self._settings, log=self.log_message.emit)
        taken = {str(Path(p)) for p in self._queue.queued_outputs()}
        queued = 0
        for src in sources:
            job = self._job_for(src, settings, taken)
            if job is not None and self._queue.add(job):
                queued += 1
                if src != self._queue.current_source():
                    self._set_item_state(src, "…", "Queued")
        return queued

    def cancel(self) -> None:
        super().cancel()
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


class _QualityDialog(QDialog):
    """Rate control, quality value and preset for the long-GOP codecs.

    Takes a :class:`QualitySettings` and returns a new one from :meth:`quality`;
    it never touches the tab, so Cancel changes nothing.
    """

    def __init__(
        self, codec_key: str, codec_name: str, current: QualitySettings, parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Quality Settings — {codec_name}")
        self.setMinimumWidth(440)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)
        card = Card("Quality")
        card.setContentsMargins(12, 0, 12, 0)
        form = FormGrid()
        card.body.addLayout(form)

        self.rate = QComboBox()
        for label, key in _RATE_OPTIONS:
            self.rate.addItem(label, key)
        self.rate.setCurrentIndex(max(0, self.rate.findData(current.rate_control)))
        lo, hi = CRF_RANGE.get(codec_key, (0, 51))
        self.crf = QSpinBox()
        self.crf.setPrefix("CRF ")
        self.crf.setRange(lo, hi)
        self.crf.setValue(current.crf)
        self.crf.setToolTip("Lower is better quality and bigger files.")
        self.bitrate = QDoubleSpinBox()
        self.bitrate.setRange(0.1, 2000.0)
        self.bitrate.setDecimals(1)
        self.bitrate.setSuffix(" Mb/s")
        self.bitrate.setValue(current.bitrate_mbps)
        self.preset = QComboBox()
        for name in X26X_PRESETS:
            self.preset.addItem(name, name)
        self.preset.setCurrentIndex(max(0, self.preset.findData(current.preset)))
        self.preset.setToolTip("Encoder speed. Slower is smaller at the same quality.")

        form.add_row("Rate control", self.rate, width=WIDE)
        # CRF and bitrate share one row; only the one that applies shows.
        form.add_row("Value", self.crf, sized(self.bitrate, NARROW), width=NARROW)
        form.add_row("Preset", self.preset, width=WIDE)
        self.rate.currentIndexChanged.connect(lambda _: self._show_value_for_rate())
        self._show_value_for_rate()
        root.addWidget(card)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        set_style_property(buttons.button(QDialogButtonBox.StandardButton.Ok), "variant", "primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(DialogFooter(buttons))

    def _show_value_for_rate(self) -> None:
        by_bitrate = self.rate.currentData() == RATE_BITRATE
        self.crf.setVisible(not by_bitrate)
        self.bitrate.setVisible(by_bitrate)

    def quality(self) -> QualitySettings:
        return QualitySettings(
            rate_control=str(self.rate.currentData()),
            crf=self.crf.value(),
            bitrate_mbps=self.bitrate.value(),
            preset=str(self.preset.currentData()),
        )
