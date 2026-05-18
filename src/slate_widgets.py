"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live QPainter-driven preview on the right.
"""

from __future__ import annotations

import os
import time

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPointF,
    QRectF,
    QRegularExpression,
    QSettings,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontDatabase,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPen,
    QRegularExpressionValidator,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .slate import SLATE_COLORSPACE, render_slate_frame
from .timeline_slider import TimelineSlider

COMMON_FPS: list[float] = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 60.0]

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0


def _env_default(key: str, settings_key: str, settings: QSettings) -> str:
    """Return saved value, falling back to environment variable, then ''."""
    saved = settings.value(settings_key, "")
    if saved:
        return saved
    return os.environ.get(key, "")


def _read_exr_frame(path: str):
    """Read a single EXR frame at full resolution as uint16 RGB, or ``None``."""
    import numpy as np

    try:
        import OpenImageIO as oiio

        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        if spec.full_width > 0 and spec.full_height > 0:
            dx, dy = spec.full_x, spec.full_y
            dw, dh = spec.full_width, spec.full_height
        else:
            dx, dy = 0, 0
            dw, dh = spec.width, spec.height
        roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
        pixels = buf.get_pixels(oiio.UINT16, roi)
        if pixels.shape[2] < 3:
            pixels = np.repeat(pixels, 3, axis=2)
        return np.ascontiguousarray(pixels[:, :, :3])
    except Exception:
        return None


class _FrameLoaderWorker(QObject):
    """Loads a single EXR frame at *frame_path* in a background thread."""

    finished = Signal(int, object)  # (frame_number, np.ndarray | None)

    def __init__(self, frame_number: int, frame_path: str) -> None:
        super().__init__()
        self._frame_number = frame_number
        self._frame_path = frame_path

    def run(self) -> None:
        rgb = _read_exr_frame(self._frame_path)
        self.finished.emit(self._frame_number, rgb)


# ---------------------------------------------------------------------------
# Nuke-style custom-painted slider
# ---------------------------------------------------------------------------


class NukeSlider(QWidget):
    """A QPainter-drawn slider that mimics Nuke's viewer gain/gamma controls.

    Features:
    - Dark background with thin groove line
    - Labelled tick marks at user-defined values
    - Thin vertical indicator line (accent color) for current position
    - Log or linear value mapping
    - Click-and-drag interaction
    """

    valueChanged = Signal(float)

    _BG = QColor(0x1E, 0x1E, 0x1E)
    _GROOVE = QColor(0x3C, 0x3C, 0x3C)
    _TICK = QColor(0x58, 0x58, 0x58)
    _LABEL_COLOR = QColor(0x88, 0x88, 0x88)
    _INDICATOR = QColor(0xC8, 0x78, 0x28)  # Nuke orange
    _DEFAULT_MARK = QColor(0x50, 0x50, 0x50)

    def __init__(
        self,
        ticks: list[float],
        default: float,
        log_scale: bool = False,
        val_min: float = 0.0,
        val_max: float = 1.0,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._ticks = ticks
        self._default = default
        self._log_scale = log_scale
        self._val_min = val_min
        self._val_max = val_max
        self._value = default
        self._dragging = False

        import math

        if log_scale:
            self._log_min = math.log(max(val_min, 1e-10))
            self._log_max = math.log(max(val_max, 1e-10))
        else:
            self._log_min = 0.0
            self._log_max = 1.0

        self.setMinimumHeight(22)
        self.setMaximumHeight(22)
        self.setMinimumWidth(80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._font.setPointSize(8)

    # -- value <-> x mapping --

    def _value_to_t(self, val: float) -> float:
        import math

        val = max(self._val_min, min(self._val_max, val))
        if self._log_scale:
            return (math.log(max(val, 1e-10)) - self._log_min) / (self._log_max - self._log_min)
        return (val - self._val_min) / (self._val_max - self._val_min)

    def _t_to_value(self, t: float) -> float:
        import math

        t = max(0.0, min(1.0, t))
        if self._log_scale:
            return math.exp(self._log_min + t * (self._log_max - self._log_min))
        return self._val_min + t * (self._val_max - self._val_min)

    def _margin_left(self) -> int:
        return 2

    def _margin_right(self) -> int:
        return 2

    def _track_x(self) -> tuple[int, int]:
        ml = self._margin_left()
        return ml, self.width() - self._margin_right() - ml

    def _t_to_x(self, t: float) -> float:
        ml, track_w = self._track_x()
        return ml + t * track_w

    def _x_to_t(self, x: float) -> float:
        ml, track_w = self._track_x()
        if track_w <= 0:
            return 0.0
        return (x - ml) / track_w

    # -- public interface --

    def value(self) -> float:
        return self._value

    def setValue(self, val: float) -> None:
        val = max(self._val_min, min(self._val_max, val))
        if val != self._value:
            self._value = val
            self.update()

    # -- painting --

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # background
        p.fillRect(0, 0, w, h, self._BG)

        groove_y = h // 2
        ml, track_w = self._track_x()

        # groove line
        p.setPen(QPen(self._GROOVE, 1))
        p.drawLine(ml, groove_y, ml + track_w, groove_y)

        # default-value mark (thin dim vertical line)
        def_t = self._value_to_t(self._default)
        def_x = self._t_to_x(def_t)
        p.setPen(QPen(self._DEFAULT_MARK, 1))
        p.drawLine(int(def_x), 2, int(def_x), h - 2)

        # tick marks + labels
        p.setFont(self._font)
        fm = QFontMetricsF(self._font)
        for tick_val in self._ticks:
            t = self._value_to_t(tick_val)
            tx = self._t_to_x(t)
            # tick line
            p.setPen(QPen(self._TICK, 1))
            p.drawLine(int(tx), groove_y - 3, int(tx), groove_y + 3)
            # label
            if tick_val == int(tick_val) and tick_val >= 0:
                label = str(int(tick_val))
            elif tick_val >= 1:
                label = f"{tick_val:.0f}"
            elif tick_val >= 0.1:
                label = f"{tick_val:.1f}"
            else:
                label = f"{tick_val:.2f}"
            lw = fm.horizontalAdvance(label)
            lx = tx - lw / 2
            lx = max(0.0, min(float(w) - lw, lx))
            p.setPen(self._LABEL_COLOR)
            p.drawText(int(lx), h - 2, label)

        # indicator line (current value)
        cur_t = self._value_to_t(self._value)
        cur_x = self._t_to_x(cur_t)
        p.setPen(QPen(self._INDICATOR, 2))
        p.drawLine(int(cur_x), 1, int(cur_x), h - 1)

        p.end()

    # -- mouse interaction --

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Reset to default on double-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._value = self._default
            self.update()
            self.valueChanged.emit(self._value)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _set_from_x(self, x: float) -> None:
        t = self._x_to_t(x)
        val = self._t_to_value(t)
        self._value = val
        self.update()
        self.valueChanged.emit(self._value)


def extract_thumbnail_b64(input_path: str, mode: str) -> str:
    """Extract a JPEG thumbnail from the midpoint of the input as raw base64.

    Returns a plain base64 string (no data-URI prefix), or '' on failure.
    """
    import base64

    try:
        if mode == "video2exr":
            import av

            container = av.open(input_path)
            stream = container.streams.video[0]
            total = stream.frames
            if not total and stream.duration and stream.time_base:
                fps = float(stream.average_rate) if stream.average_rate else 24.0
                total = max(1, int(float(stream.duration * stream.time_base) * fps + 0.5))
            mid = max(0, (total or 1) // 2)
            fps = float(stream.average_rate) if stream.average_rate else 24.0
            target_ts = int(mid / fps / stream.time_base)
            container.seek(target_ts, stream=stream)
            frame = None
            for f in container.decode(video=0):
                frame = f
                break
            container.close()
            if frame is None:
                return ""
            import numpy as np

            arr = frame.to_ndarray(format="rgb24")
        else:
            from .sequence import find_exr_sequence_info

            _paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
            if not frames:
                return ""
            mid_idx = len(frames) // 2
            mid_frame = sorted(frames)[mid_idx]
            mid_path = seq.frame(mid_frame)

            import numpy as np
            import OpenImageIO as oiio

            img_buf = oiio.ImageBuf(mid_path)
            if img_buf.has_error:
                return ""
            spec = img_buf.spec()
            if spec.full_width > 0 and spec.full_height > 0:
                dx, dy = spec.full_x, spec.full_y
                dw, dh = spec.full_width, spec.full_height
            else:
                dx, dy = 0, 0
                dw, dh = spec.width, spec.height
            roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
            pixels = np.ascontiguousarray(img_buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
            rgb = pixels[..., :3] if pixels.shape[2] >= 3 else np.repeat(pixels, 3, axis=2)
            rgb = np.clip(rgb, 0, None)
            srgb = np.where(
                rgb <= 0.0031308,
                rgb * 12.92,
                1.055 * np.power(rgb, 1.0 / 2.4) - 0.055,
            )
            arr = np.clip(srgb * 255, 0, 255).astype(np.uint8)

        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImage

        h, w = arr.shape[:2]
        qimg = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888)

        max_w = 640
        if w > max_w:
            qimg = qimg.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)

        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        qimg.save(qbuf, "JPEG", 85)
        return base64.b64encode(qbuf.data().data()).decode("ascii")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Slate form panel
# ---------------------------------------------------------------------------


class SlateFormPanel(QWidget):
    """Form collecting all slate metadata fields.

    Top row: show, sequence, shot, version (horizontal).
    Primary fields: submit notes, artist, frame range, fps, submit for.
    Collapsible "Additional Details": take, vendor, shot_types, scope, logo.
    Resolution: width/height spins (disabled when input is present).
    """

    data_changed = Signal(dict)

    def __init__(
        self,
        settings: QSettings,
        input_path: str = "",
        mode: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._resolution_locked = False
        self._input_path = input_path
        self._mode = mode
        self._thumbnail_b64 = ""
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- Top row: Show / Seq / Shot / Version (horizontal) ---
        top_group = QGroupBox("Shot Identity")
        top_layout = QHBoxLayout(top_group)
        top_layout.setSpacing(8)

        def _labeled_field(label_text: str, widget: QLineEdit) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("font-size: 10px; color: #888;")
            col.addWidget(lbl)
            col.addWidget(widget)
            return col

        self.show_edit = self._line_env("slate/show", "SHOW", "$SHOW")
        self.sequence_edit = self._line_env("slate/sequence", "SEQ", "$SEQ")
        self.shot_edit = self._line_env("slate/shot", "SHOT", "$SHOT")

        self.version_spin = QSpinBox()
        self.version_spin.setRange(0, 9999)
        self.version_spin.setPrefix("v")
        self.version_spin.setWrapping(True)
        saved_ver = int(settings.value("slate/version_num", 1))
        self.version_spin.setValue(saved_ver)
        self.version_spin.valueChanged.connect(self._emit_changed)

        top_layout.addLayout(_labeled_field("Show", self.show_edit), 2)
        top_layout.addLayout(_labeled_field("Seq", self.sequence_edit), 2)
        top_layout.addLayout(_labeled_field("Shot", self.shot_edit), 2)

        ver_col = QVBoxLayout()
        ver_col.setSpacing(2)
        ver_lbl = QLabel("Version")
        ver_lbl.setStyleSheet("font-size: 10px; color: #888;")
        ver_col.addWidget(ver_lbl)
        ver_col.addWidget(self.version_spin)
        top_layout.addLayout(ver_col, 1)
        root.addWidget(top_group)

        # --- Primary fields (always visible) ---
        primary = QGroupBox("Slate Info")
        pf = QFormLayout(primary)
        pf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Submission notes…")
        saved_notes = settings.value("slate/notes", "")
        if saved_notes:
            self.notes_edit.setPlainText(saved_notes)

        self.submit_for_combo = QComboBox()
        for label in ("WIP", "FINAL", "CBB"):
            self.submit_for_combo.addItem(label)
        saved_sf = settings.value("slate/submit_for", "WIP")
        sf_idx = self.submit_for_combo.findText(saved_sf)
        if sf_idx >= 0:
            self.submit_for_combo.setCurrentIndex(sf_idx)

        self.artist_edit = self._line("slate/artist", "Artist Name")

        self.frame_range_edit = QLineEdit()
        self.frame_range_edit.setPlaceholderText("1001 – 1100")
        self.frame_range_edit.setReadOnly(False)

        self.fps_combo = QComboBox()
        for fps_val in COMMON_FPS:
            label = str(int(fps_val)) if fps_val == int(fps_val) else f"{fps_val:.3f}"
            self.fps_combo.addItem(label, float(fps_val))
        self._inferred_fps: float | None = None
        self._apply_fps(settings)

        pf.addRow("Submitting For", self.submit_for_combo)
        pf.addRow("Submit Notes", self.notes_edit)
        self.shot_types_edit = self._line("slate/shot_types", "2d comp, 3d, matte paint…")
        self.scope_edit = self._line("slate/scope", "VFX scope of work")
        pf.addRow("Shot Types", self.shot_types_edit)
        pf.addRow("Scope of Work", self.scope_edit)
        root.addWidget(primary)

        # --- Right-column fields (Vendor, Artist, Take, Logo) ---
        right_group = QGroupBox("Artist / Studio")
        rf = QFormLayout(right_group)
        rf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.vendor_edit = self._line("slate/vendor", "Studio / Vendor name")
        self.take_edit = self._line("slate/take", "01")
        self.logo_edit = self._line("slate/logo", "Logo text (blank to hide)")

        rf.addRow("Vendor", self.vendor_edit)
        rf.addRow("Artist", self.artist_edit)
        rf.addRow("Take", self.take_edit)
        rf.addRow("Logo / Studio", self.logo_edit)
        root.addWidget(right_group)

        # --- Output (resolution, frame range, fps, colorspace) ---
        out_group = QGroupBox("Output")
        of = QFormLayout(out_group)
        of.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(64, 16384)
        self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(64, 16384)
        self.height_spin.setSuffix(" px")

        saved_w = int(settings.value("slate/res_w", 1920))
        saved_h = int(settings.value("slate/res_h", 1080))
        self.width_spin.setValue(saved_w)
        self.height_spin.setValue(saved_h)

        res_row = QHBoxLayout()
        res_row.addWidget(self.width_spin)
        res_row.addWidget(QLabel("\u00d7"))
        res_row.addWidget(self.height_spin)
        res_row.addStretch()

        self.colorspace_edit = QLineEdit()
        self.colorspace_edit.setReadOnly(True)
        self.colorspace_edit.setEnabled(False)
        self.colorspace_edit.setPlaceholderText("Set in output color space")
        self.colorspace_edit.setToolTip("Determined by the output color space selection")

        of.addRow("Resolution", res_row)
        of.addRow("Frame Range", self.frame_range_edit)
        of.addRow("FPS", self.fps_combo)
        of.addRow("Color Space", self.colorspace_edit)
        root.addWidget(out_group)
        self._res_group = out_group

        root.addStretch()

        for widget in (
            self.show_edit,
            self.shot_edit,
            self.artist_edit,
            self.frame_range_edit,
            self.sequence_edit,
            self.take_edit,
            self.vendor_edit,
            self.shot_types_edit,
            self.scope_edit,
            self.logo_edit,
        ):
            widget.textChanged.connect(self._emit_changed)

        self.fps_combo.currentIndexChanged.connect(self._emit_changed)
        self.submit_for_combo.currentIndexChanged.connect(self._emit_changed)
        self.width_spin.valueChanged.connect(self._emit_changed)
        self.height_spin.valueChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)

        # Status tips — shown in the dialog's QStatusBar on hover
        self.show_edit.setStatusTip("Production or show code (falls back to $SHOW env var)")
        self.sequence_edit.setStatusTip("Sequence name (falls back to $SEQ env var)")
        self.shot_edit.setStatusTip("Shot name (falls back to $SHOT env var)")
        self.version_spin.setStatusTip("Version number — appears as v001, v002, etc.")
        self.submit_for_combo.setStatusTip("Submission stage: WIP, FINAL, or CBB")
        self.notes_edit.setStatusTip("Free-form notes displayed on the slate")
        self.artist_edit.setStatusTip("Artist name — who did the work")
        self.vendor_edit.setStatusTip("Studio or vendor name")
        self.take_edit.setStatusTip("Take number for this version")
        self.shot_types_edit.setStatusTip("e.g. 2D comp, 3D, matte paint, roto…")
        self.scope_edit.setStatusTip("Description of VFX scope of work for this shot")
        self.logo_edit.setStatusTip("Text displayed as logo/studio branding (blank to hide)")
        self.frame_range_edit.setStatusTip("Start – end frame range for the output")
        self.fps_combo.setStatusTip("Playback frame rate")
        self.width_spin.setStatusTip("Output resolution width in pixels")
        self.height_spin.setStatusTip("Output resolution height in pixels")
        self.colorspace_edit.setStatusTip(
            "Output color space — inherited from the conversion settings"
        )

    # --- Helpers ---

    def _line(self, key: str, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        saved = self._settings.value(key, "")
        if saved:
            edit.setText(saved)
        return edit

    def _line_env(self, key: str, env_var: str, placeholder: str) -> QLineEdit:
        """Create a QLineEdit that falls back to an environment variable.

        Input is restricted to alphanumeric characters and underscores.
        """
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9_]*")))
        val = _env_default(env_var, key, self._settings)
        if val:
            edit.setText(val)
        return edit

    def _apply_fps(self, settings: QSettings) -> None:
        """Set FPS from inferred value, saved value, or default."""
        if self._inferred_fps is not None:
            target = self._inferred_fps
        else:
            target = float(settings.value("slate/fps", 24.0))
        for i in range(self.fps_combo.count()):
            data = self.fps_combo.itemData(i)
            if data is not None and abs(data - target) < 0.01:
                self.fps_combo.setCurrentIndex(i)
                return
        self.fps_combo.setCurrentIndex(1)

    def set_inferred_fps(self, fps: float) -> None:
        """Set FPS inferred from the input media (user can still override)."""
        self._inferred_fps = fps
        for i in range(self.fps_combo.count()):
            data = self.fps_combo.itemData(i)
            if data is not None and abs(data - fps) < 0.01:
                self.fps_combo.setCurrentIndex(i)
                return

    def set_frame_range(self, frame_range: str) -> None:
        """Set the frame range from the input and lock the field."""
        if frame_range:
            self.frame_range_edit.setText(frame_range)
            self.frame_range_edit.setReadOnly(True)
            self.frame_range_edit.setEnabled(False)
            self.frame_range_edit.setToolTip("Determined from input source")

    def set_colorspace(self, name: str) -> None:
        """Display the output colorspace (read-only)."""
        self.colorspace_edit.setText(name)

    def set_thumbnail_b64(self, b64: str) -> None:
        """Store raw base64 JPEG data for the thumbnail."""
        self._thumbnail_b64 = b64

    def resolution(self) -> tuple[int, int]:
        return self.width_spin.value(), self.height_spin.value()

    def set_resolution_locked(self, width: int, height: int) -> None:
        """Lock resolution to input dimensions (disables spins)."""
        self._resolution_locked = True
        self.width_spin.setValue(width)
        self.height_spin.setValue(height)
        self.width_spin.setEnabled(False)
        self.height_spin.setEnabled(False)

    def set_resolution_unlocked(self) -> None:
        """Re-enable resolution controls."""
        self._resolution_locked = False
        self.width_spin.setEnabled(True)
        self.height_spin.setEnabled(True)

    def _save_fields(self) -> None:
        s = self._settings
        s.setValue("slate/show", self.show_edit.text())
        s.setValue("slate/shot", self.shot_edit.text())
        s.setValue("slate/version_num", self.version_spin.value())
        s.setValue("slate/artist", self.artist_edit.text())
        s.setValue("slate/frame_range", self.frame_range_edit.text())
        s.setValue("slate/fps", self.fps_combo.currentData())
        s.setValue("slate/sequence", self.sequence_edit.text())
        s.setValue("slate/take", self.take_edit.text())
        s.setValue("slate/submit_for", self.submit_for_combo.currentText())
        s.setValue("slate/vendor", self.vendor_edit.text())
        s.setValue("slate/shot_types", self.shot_types_edit.text())
        s.setValue("slate/scope", self.scope_edit.text())
        s.setValue("slate/logo", self.logo_edit.text())
        s.setValue("slate/notes", self.notes_edit.toPlainText())
        s.setValue("slate/res_w", self.width_spin.value())
        s.setValue("slate/res_h", self.height_spin.value())

    def _emit_changed(self, *_args) -> None:
        self._save_fields()
        self.data_changed.emit(self.slate_data())

    def slate_data(self) -> dict:
        """Return a dict suitable for passing to the JS ``updateSlate()`` function."""
        w, h = self.resolution()
        fps = self.fps_combo.currentData() or 24.0
        version_str = f"v{self.version_spin.value():04d}"
        data = {
            "show": self.show_edit.text() or "SHOW",
            "sequence": self.sequence_edit.text() or "SEQ",
            "shot": self.shot_edit.text() or "SHOT",
            "version": version_str,
            "take": self.take_edit.text(),
            "submitFor": self.submit_for_combo.currentText(),
            "artist": self.artist_edit.text() or "\u2014",
            "vendor": self.vendor_edit.text(),
            "shotTypes": self.shot_types_edit.text(),
            "scope": self.scope_edit.text(),
            "logo": self.logo_edit.text(),
            "date": time.strftime("%Y-%m-%d"),
            "fps": str(int(fps)) if fps == int(fps) else f"{fps:.3f}",
            "resolution": f"{w}\u00d7{h}",
            "frameRange": self.frame_range_edit.text() or "\u2014",
            "colorspace": self.colorspace_edit.text() or "\u2014",
            "bitDepth": "16-bit half",
            "notes": self.notes_edit.toPlainText(),
        }
        return data

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string, or ''."""
        return self._thumbnail_b64


# ---------------------------------------------------------------------------
# Slate preview (single pan/zoom graphics view for all tabs)
# ---------------------------------------------------------------------------


class SlatePreviewView(QGraphicsView):
    """Single QGraphicsView with Nuke-style pan/zoom for the slate editor.

    Both the slate HTML proxy and the shot-preview (thumbnail + burn-in)
    proxy live in the **same** scene.  A tab bar toggles which group is
    visible — one view, one transform, one background.

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal, anchored at press position)
      - F key: fit in view
    """

    WHEEL_SCALE_FACTOR = 1.0 / 4.0

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#323232")))

        self._scene.setSceneRect(-1e6, -1e6, 2e6, 2e6)

        self._slate_rect = QRectF(0, 0, 1920, 1080)

        self._panning = False
        self._zooming = False
        self._last_pos: QPointF | None = None
        self._zoom_anchor_view: QPointF | None = None

    def set_slate_size(self, w: int, h: int) -> None:
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._slate_rect = QRectF(0, 0, preview_w, preview_h)

    def fit_in_view(self) -> None:
        self.fitInView(self._slate_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _scale_at(self, factor: float, view_anchor: QPointF) -> None:
        cur = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor))
        s = target / cur
        if abs(s - 1.0) < 1e-7:
            return
        scene_pt = self.mapToScene(view_anchor.toPoint())
        cx, cy = scene_pt.x(), scene_pt.y()
        t = self.transform()
        t.translate(cx, cy)
        t.scale(s, s)
        t.translate(-cx, -cy)
        self.setTransform(t)

    def _translate_view(self, dx: float, dy: float) -> None:
        t = self.transform()
        s = t.m11()
        t.translate(dx / s, dy / s)
        self.setTransform(t)

    # -- events --

    def mousePressEvent(self, event):
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._last_pos = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = event.position()
            self._zoom_anchor_view = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self._translate_view(delta.x(), delta.y())
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            s = 1.02 ** (dx * 0.5)
            self._scale_at(s, self._zoom_anchor_view)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton) and self._panning:
            self._panning = False
            self._last_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            self._last_pos = None
            self._zoom_anchor_view = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s = 1.02 ** (delta * self.WHEEL_SCALE_FACTOR)
        self._scale_at(s, event.position())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_in_view()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# Slate dialog
# ---------------------------------------------------------------------------


class SlateDialog(QDialog):
    """Modal dialog for editing slate + burn-in overlay data with live preview.

    Left side: :class:`SlateFormPanel` in a scroll area.
    Right side: a single :class:`SlatePreviewView` (one scene, one transform)
    with a Nuke-style :class:`TimelineSlider` at the bottom.  Frame
    ``first - 1`` shows the slate; frames ``first .. last`` show the actual
    EXR shot frames with the burn-in composited on top.

    Shot frames are loaded on demand by a background ``QThread`` and cached
    in an in-memory LRU.  Slate, burn-in render, OIIO composite, and OCIO
    display transform run on the main thread; gain/gamma is a fast post pass.
    """

    _MAX_SHOT_CACHE = 12

    def __init__(
        self,
        settings: QSettings,
        locked_width: int = 0,
        locked_height: int = 0,
        input_path: str = "",
        mode: str = "",
        inferred_fps: float = 0.0,
        frame_range: str = "",
        dst_colorspace: str = "",
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Slate & Overlay Editor")
        self.resize(1400, 850)

        self._input_path = input_path
        self._mode = mode
        self._ocio_cfg = ocio_cfg
        self._src_colorspace = src_colorspace

        # Pixmap pipeline (single item): comp -> display -> gain/gamma
        self._comp_f32 = None  # float32 RGB in *current* source space
        self._comp_src_space = ""  # OCIO source name for _comp_f32 ('' = sRGB slate)
        self._display_f32 = None  # float32 RGB after OCIO display transform
        self._preview_pixmap_item = None

        # Shot frame loader / cache (only used when scrubbing real frames)
        self._exr_seq = None
        self._shot_frames: list[int] = []
        self._first_shot: int | None = None
        self._last_shot: int | None = None
        self._slate_frame: int = 0
        self._current_frame: int = 0
        self._frame_cache: dict[int, object] = {}
        self._frame_cache_order: list[int] = []
        self._frame_thread: QThread | None = None
        self._frame_worker: _FrameLoaderWorker | None = None
        self._pending_frame: int | None = None

        # Resolve EXR frame range (only available for exr2video mode)
        if input_path and mode == "exr2video":
            try:
                from .sequence import find_exr_sequence_info

                _paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
                if frames:
                    self._shot_frames = sorted(frames)
                    self._first_shot = self._shot_frames[0]
                    self._last_shot = self._shot_frames[-1]
                    self._exr_seq = seq
                    self._slate_frame = self._first_shot - 1
                    self._current_frame = self._slate_frame
            except Exception:
                pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: form ---
        self._form = SlateFormPanel(settings, input_path=input_path, mode=mode)
        if inferred_fps > 0:
            self._form.set_inferred_fps(inferred_fps)
        if frame_range:
            self._form.set_frame_range(frame_range)
        if dst_colorspace:
            self._form.set_colorspace(dst_colorspace)
        if locked_width > 0 and locked_height > 0:
            self._form.set_resolution_locked(locked_width, locked_height)

        if input_path:
            thumb = extract_thumbnail_b64(input_path, mode)
            if thumb:
                self._form.set_thumbnail_b64(thumb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        # --- Right: viewer controls + preview + timeline ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        self._build_viewer_controls(right_layout)

        self._preview = SlatePreviewView()
        right_layout.addWidget(self._preview, 1)

        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)

        # Timeline scrubber (only meaningful when there are shot frames)
        self._timeline: TimelineSlider | None = None
        if self._exr_seq is not None and self._shot_frames:
            self._timeline = TimelineSlider()
            ideal_h = self._timeline._ideal_height()
            self._timeline.setFixedHeight(ideal_h)
            self._timeline.set_range(self._slate_frame, self._last_shot)
            self._timeline.set_marker_frames({self._slate_frame: "SLATE"})
            self._timeline.set_value(self._slate_frame)
            self._timeline.value_changed.connect(self._on_timeline_changed)
            right_layout.addWidget(self._timeline)

        splitter.addWidget(right_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([380, 1020])

        layout.addWidget(splitter, 1)

        # --- Bottom: status bar with embedded OK / Cancel ---
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._status_bar.addPermanentWidget(buttons)
        layout.addWidget(self._status_bar)

        # --- Live preview wiring ---
        # Form changes (slate metadata + burn-in fields) → invalidate cached
        # composites and re-render whatever frame the user is currently on.
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_current_frame)

        self._form.data_changed.connect(lambda _: self._refresh_timer.start())
        self._form.data_changed.connect(self._update_preview_size)

        QTimer.singleShot(0, self._refresh_current_frame)
        QTimer.singleShot(0, self._preview.fit_in_view)

    # -- Viewer controls (Nuke-style) --

    _GAIN_TICKS = [0.01, 0.1, 0.3, 1.0, 3.0, 10.0, 30.0, 64.0]
    _GAMMA_TICKS = [0.0, 0.1, 0.4, 0.7, 1.0, 2.0, 3.0, 4.0]
    _FSTOP_STEPS = (1, 2, 4, 8, 16, 32, 64)

    def _build_viewer_controls(self, parent_layout: QVBoxLayout) -> None:
        """Build Nuke-style gain/gamma sliders + display colorspace combo."""
        strip = QHBoxLayout()
        strip.setContentsMargins(4, 1, 4, 1)
        strip.setSpacing(4)

        # --- f-stop step arrows ---
        self._fstop_idx = 3  # f/8 default
        fstop_left = QLabel("\u25c4")
        fstop_left.setCursor(Qt.CursorShape.PointingHandCursor)
        fstop_left.setFixedWidth(8)
        fstop_left.setStyleSheet("font-size: 8px; color: #888;")
        fstop_left.mousePressEvent = lambda _e: self._step_fstop(-1)

        self._fstop_label = QLabel(f"f/{self._FSTOP_STEPS[self._fstop_idx]}")
        self._fstop_label.setFixedWidth(24)
        self._fstop_label.setStyleSheet("font-size: 9px; color: #888;")

        fstop_right = QLabel("\u25ba")
        fstop_right.setCursor(Qt.CursorShape.PointingHandCursor)
        fstop_right.setFixedWidth(8)
        fstop_right.setStyleSheet("font-size: 8px; color: #888;")
        fstop_right.mousePressEvent = lambda _e: self._step_fstop(1)

        strip.addWidget(fstop_left)
        strip.addWidget(self._fstop_label)
        strip.addWidget(fstop_right)

        # --- Gain value label + slider ---
        self._gain_value_label = QLabel("1")
        self._gain_value_label.setFixedWidth(28)
        self._gain_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._gain_value_label.setStyleSheet("font-size: 10px; color: #d4d4d4;")
        strip.addWidget(self._gain_value_label)

        self._gain_slider = NukeSlider(
            ticks=self._GAIN_TICKS,
            default=1.0,
            log_scale=True,
            val_min=0.01,
            val_max=64.0,
        )
        strip.addWidget(self._gain_slider, 1)

        strip.addSpacing(4)

        # --- Gamma label + slider ---
        gamma_lbl = QLabel("\u03b3")
        gamma_lbl.setFixedWidth(10)
        gamma_lbl.setStyleSheet("font-size: 10px; color: #888;")
        strip.addWidget(gamma_lbl)

        self._gamma_value_label = QLabel("1")
        self._gamma_value_label.setFixedWidth(22)
        self._gamma_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._gamma_value_label.setStyleSheet("font-size: 10px; color: #d4d4d4;")
        strip.addWidget(self._gamma_value_label)

        self._gamma_slider = NukeSlider(
            ticks=self._GAMMA_TICKS,
            default=1.0,
            log_scale=False,
            val_min=0.0,
            val_max=4.0,
        )
        strip.addWidget(self._gamma_slider, 1)

        strip.addSpacing(8)

        # --- Display colorspace combo ---
        self._display_view_combo = QComboBox()
        self._display_view_combo.setMinimumWidth(120)
        strip.addWidget(self._display_view_combo)

        parent_layout.addLayout(strip)

        # Populate display/view from OCIO config
        self._display_view_pairs: list[tuple[str, str]] = []
        if self._ocio_cfg is not None:
            self._populate_display_view_combo()
        else:
            self._display_view_pairs.append(("sRGB", "Raw"))
            self._display_view_combo.addItem("sRGB")

        # Wire signals — gain/gamma are fast numpy post-process only;
        # display/view change re-runs the heavy OCIO pass.
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        self._gamma_slider.valueChanged.connect(self._on_gamma_changed)
        self._display_view_combo.currentIndexChanged.connect(
            lambda _: self._invalidate_display_cache()
        )

        self._gain = 1.0
        self._gamma = 1.0

    def _populate_display_view_combo(self) -> None:
        from .ocio_utils import list_displays, list_views

        self._display_view_combo.blockSignals(True)
        self._display_view_combo.clear()
        self._display_view_pairs.clear()

        default_display = ""
        default_view = ""
        default_idx = 0
        try:
            default_display = self._ocio_cfg.getDefaultDisplay()
            default_view = self._ocio_cfg.getDefaultView(default_display)
        except Exception:
            pass

        try:
            displays = list_displays(self._ocio_cfg)
            idx = 0
            for display in displays:
                views = list_views(self._ocio_cfg, display)
                for view in views:
                    self._display_view_pairs.append((display, view))
                    if len(displays) == 1:
                        label = view
                    else:
                        label = f"{display} / {view}"
                    self._display_view_combo.addItem(label)
                    if display == default_display and view == default_view:
                        default_idx = idx
                    idx += 1
        except Exception:
            pass

        if self._display_view_combo.count() > 0:
            self._display_view_combo.setCurrentIndex(default_idx)
        self._display_view_combo.blockSignals(False)

    def _step_fstop(self, direction: int) -> None:
        new = self._fstop_idx + direction
        self._fstop_idx = max(0, min(len(self._FSTOP_STEPS) - 1, new))
        self._fstop_label.setText(f"f/{self._FSTOP_STEPS[self._fstop_idx]}")

    def _on_gain_changed(self, gain: float) -> None:
        self._gain = gain
        if gain >= 10:
            txt = f"{gain:.0f}"
        elif gain >= 1:
            txt = f"{gain:.1f}"
        elif gain >= 0.1:
            txt = f"{gain:.2f}"
        else:
            txt = f"{gain:.3f}"
        self._gain_value_label.setText(txt)
        self._refresh_gain_gamma()

    def _on_gamma_changed(self, gamma: float) -> None:
        self._gamma = gamma
        if gamma >= 1:
            txt = f"{gamma:.1f}"
        else:
            txt = f"{gamma:.2f}"
        self._gamma_value_label.setText(txt)
        self._refresh_gain_gamma()

    # -- Tab switching --

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.StatusTip:
            self._status_bar.showMessage(ev.tip())
            return True
        return super().event(ev)

    # -- Frame routing --

    def _on_timeline_changed(self, frame: int) -> None:
        """User scrubbed the timeline — re-render whatever frame they landed on."""
        if frame == self._current_frame:
            return
        self._current_frame = frame
        self._refresh_current_frame()

    def _refresh_current_frame(self) -> None:
        """Render whichever frame the timeline points at (slate or a shot frame)."""
        if self._current_frame == self._slate_frame:
            self._composite_slate()
        else:
            self._composite_shot(self._current_frame)

    # -- Slate path --

    def _composite_slate(self) -> None:
        """Render the slate at preview resolution and feed it into the OCIO pass."""
        import numpy as np

        w_full, h_full = self._form.resolution()
        preview_h = 1080
        preview_w = max(1, int(preview_h * w_full / max(h_full, 1)))
        try:
            rgba = render_slate_frame(
                self._form.slate_data(),
                preview_w,
                preview_h,
                thumbnail_b64=self._form.thumbnail_b64(),
            )
        except Exception:
            return

        self._comp_f32 = np.ascontiguousarray(rgba[..., :3].copy(), dtype=np.float32)
        self._comp_src_space = SLATE_COLORSPACE
        self._display_f32 = None
        self._apply_display_transform()

    # -- Shot path --

    def _composite_shot(self, frame: int) -> None:
        """Composite burn-in onto shot ``frame`` and feed into the OCIO pass.

        If the frame is cached, runs synchronously.  Otherwise, queues a
        background load and waits for ``_on_frame_loaded`` to call us again.
        """
        rgb = self._frame_cache.get(frame)
        if rgb is None:
            self._request_frame_load(frame)
            return
        self._touch_frame_cache(frame)
        self._composite_shot_with_pixels(frame, rgb)

    def _composite_shot_with_pixels(self, frame: int, rgb_u16) -> None:
        import numpy as np

        from .burnin import (
            burnin_fields_from_slate,
            composite_burnin,
            render_burnin_overlay,
        )

        fh, fw = rgb_u16.shape[:2]
        fields = burnin_fields_from_slate(self._form.slate_data(), self._input_path)
        try:
            overlay_rgba = render_burnin_overlay(fw, fh, fields)
        except RuntimeError:
            return
        comp_u16 = composite_burnin(rgb_u16, overlay_rgba)
        self._comp_f32 = comp_u16.astype(np.float32) / 65535.0
        self._comp_src_space = self._src_colorspace or ""
        self._display_f32 = None
        self._apply_display_transform()

    # -- Background loader / cache --

    def _request_frame_load(self, frame: int) -> None:
        """Schedule a background read of *frame*.  If a worker is already busy
        with another frame, remember this one as the pending target — the
        completion handler will pick it up next.
        """
        if self._exr_seq is None:
            return
        if self._frame_thread is not None:
            self._pending_frame = frame
            return
        self._start_frame_loader(frame)

    def _start_frame_loader(self, frame: int) -> None:
        try:
            path = self._exr_seq.frame(frame)
        except Exception:
            return
        self._frame_thread = QThread()
        self._frame_worker = _FrameLoaderWorker(frame, path)
        self._frame_worker.moveToThread(self._frame_thread)
        self._frame_thread.started.connect(self._frame_worker.run)
        # Order matters: handle the result first, *then* tell the thread to
        # quit.  Thread refs are cleared in _on_thread_finished once the
        # event loop has actually exited, otherwise Python can drop the last
        # ref while the QThread is still running (-> "Destroyed while
        # thread is still running" abort).
        self._frame_worker.finished.connect(self._on_frame_loaded)
        self._frame_worker.finished.connect(self._frame_thread.quit)
        self._frame_thread.finished.connect(self._on_thread_finished)
        self._frame_thread.start()

    def _on_frame_loaded(self, frame: int, rgb) -> None:
        """Background loader finished — cache the frame; display if still current.

        ``self._frame_thread`` is *not* cleared here — that happens in
        :meth:`_on_thread_finished` when the QThread has actually exited.
        """
        if rgb is not None:
            self._cache_put(frame, rgb)
            if self._timeline is not None:
                self._timeline.set_cached_frames(set(self._frame_cache_order))

        if frame == self._current_frame and rgb is not None:
            self._composite_shot_with_pixels(frame, rgb)

    def _on_thread_finished(self) -> None:
        """Tear down the just-finished loader thread and kick off the next one
        if the user scrubbed away while it was running."""
        thread = self._frame_thread
        worker = self._frame_worker
        self._frame_thread = None
        self._frame_worker = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()

        target = self._pending_frame
        self._pending_frame = None
        if target is not None and target == self._current_frame:
            if target in self._frame_cache:
                self._touch_frame_cache(target)
                self._composite_shot_with_pixels(target, self._frame_cache[target])
            else:
                self._start_frame_loader(target)

    def _cache_put(self, frame: int, rgb) -> None:
        if frame in self._frame_cache:
            self._touch_frame_cache(frame)
            return
        self._frame_cache[frame] = rgb
        self._frame_cache_order.append(frame)
        while len(self._frame_cache_order) > self._MAX_SHOT_CACHE:
            evict = self._frame_cache_order.pop(0)
            self._frame_cache.pop(evict, None)

    def _touch_frame_cache(self, frame: int) -> None:
        if frame in self._frame_cache_order:
            self._frame_cache_order.remove(frame)
            self._frame_cache_order.append(frame)

    def done(self, result: int) -> None:
        """Stop the background frame loader thread before the dialog closes."""
        if self._frame_thread is not None and self._frame_thread.isRunning():
            self._frame_thread.quit()
            self._frame_thread.wait()
        self._frame_worker = None
        self._frame_thread = None
        super().done(result)

    # -- OCIO display transform + pixmap update --

    def _apply_display_transform(self) -> None:
        """Heavy pass: run OCIO ``src → display/view`` once for the current
        composite, cache the result in ``self._display_f32``, then chain into
        the fast gain/gamma post-process."""
        import numpy as np

        if self._comp_f32 is None:
            return

        src_space = self._comp_src_space
        if self._ocio_cfg is not None and src_space:
            idx = self._display_view_combo.currentIndex()
            if 0 <= idx < len(self._display_view_pairs):
                display, view = self._display_view_pairs[idx]
                try:
                    from .ocio_utils import make_display_processor

                    cpu = make_display_processor(
                        self._ocio_cfg,
                        src_space,
                        display,
                        view,
                        exposure=0.0,
                        gamma=1.0,
                    )
                    h, w = self._comp_f32.shape[:2]
                    pixels = np.ascontiguousarray(self._comp_f32.reshape(-1, 3).copy())
                    cpu.applyRGB(pixels)
                    self._display_f32 = pixels.reshape(h, w, 3)
                except Exception:
                    self._display_f32 = self._comp_f32
            else:
                self._display_f32 = self._comp_f32
        else:
            self._display_f32 = self._comp_f32

        self._refresh_gain_gamma()

    def _invalidate_display_cache(self) -> None:
        """Display/view combo changed — re-run OCIO on whatever frame is up."""
        self._display_f32 = None
        self._apply_display_transform()

    def _refresh_gain_gamma(self) -> None:
        """Apply gain * pow(1/gamma) to ``self._display_f32`` and update the
        single preview pixmap.  Fires on every slider tick — pure numpy,
        effectively instant.
        """
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap

        if self._display_f32 is None:
            return

        gain = float(getattr(self, "_gain", 1.0))
        gamma = float(getattr(self, "_gamma", 1.0))

        out = self._display_f32
        if gain != 1.0:
            out = out * gain
        if gamma != 1.0:
            # Clamp gamma to a tiny positive value so 0 doesn't blow up the
            # power op; dragging to 0 just shows a near-black preview, which
            # matches Nuke's behaviour.
            safe_gamma = max(gamma, 1e-3)
            out = np.clip(out, 0.0, None)
            out = np.power(out, 1.0 / safe_gamma)

        comp_u8 = (np.clip(out, 0.0, 1.0) * 255 + 0.5).astype(np.uint8)

        fh, fw = comp_u8.shape[:2]
        qimg = QImage(comp_u8.data, fw, fh, fw * 3, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg.copy())

        scene = self._preview._scene
        if self._preview_pixmap_item is not None:
            scene.removeItem(self._preview_pixmap_item)
        self._preview_pixmap_item = scene.addPixmap(pix)
        self._preview_pixmap_item.setZValue(0)

        w, h = self._form.resolution()
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        if pix.width() > 0 and pix.height() > 0:
            sx = preview_w / pix.width()
            sy = preview_h / pix.height()
            s = min(sx, sy)
            self._preview_pixmap_item.setScale(s)
            self._preview_pixmap_item.setPos(
                (preview_w - pix.width() * s) / 2,
                (preview_h - pix.height() * s) / 2,
            )

    # -- Shared --

    def _update_preview_size(self, _data: dict | None = None) -> None:
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)

    def slate_data(self) -> dict:
        """Return the slate form data."""
        return self._form.slate_data()

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string."""
        return self._form.thumbnail_b64()

    def resolution(self) -> tuple[int, int]:
        return self._form.resolution()
