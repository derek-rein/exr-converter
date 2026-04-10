"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live WebEngine preview on the right.
"""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QRegularExpression,
    QSettings,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QPainter, QRegularExpressionValidator, QWheelEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .slate import DEFAULT_TEMPLATE, TEMPLATES_DIR

COMMON_FPS: list[float] = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 60.0]

BURNIN_TEMPLATE = TEMPLATES_DIR / "burnin.html"

_BURNIN_POSITIONS = [
    ("top_left", "Top-left"),
    ("top_center", "Top-center"),
    ("top_right", "Top-right"),
    ("bottom_left", "Bottom-left"),
    ("bottom_center", "Bottom-center"),
    ("bottom_right", "Bottom-right"),
]

_BURNIN_DEFAULTS = {
    "top_left": "{vendor}",
    "top_center": "{show_full}",
    "top_right": "{date}",
    "bottom_left": "{version_name}",
    "bottom_center": "",
    "bottom_right": "{frames}",
}

_BURNIN_TOKENS = (
    "{vendor}  {show}  {show_full}  {seq}  {shot}  {version}  "
    "{version_name}  {artist}  {date}  {frames}  {fps}  "
    "{resolution}  {colorspace}  {frame}"
)

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0


def _env_default(key: str, settings_key: str, settings: QSettings) -> str:
    """Return saved value, falling back to environment variable, then ''."""
    saved = settings.value(settings_key, "")
    if saved:
        return saved
    return os.environ.get(key, "")


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
# Slate preview (pan/zoom graphics view)
# ---------------------------------------------------------------------------


class SlatePreviewView(QGraphicsView):
    """QGraphicsView with Nuke-style pan/zoom navigation.

    Zoom anchors to the screen-space mouse position (scroll wheel and RMB
    drag), exactly like Nuke / pyqtgraph.  All navigation is done via the
    view transform — scroll bars and scene-rect clamping are bypassed so the
    user can freely pan and zoom without hitting invisible walls.

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal, anchored at press position)
      - F key: fit slate in view
    """

    WHEEL_SCALE_FACTOR = 1.0 / 4.0

    def __init__(self, web_view: QWebEngineView, parent: QWidget | None = None):
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

        # Effectively-infinite scene rect prevents Qt from re-centering
        # the content when the item is smaller than the viewport.
        self._scene.setSceneRect(-1e6, -1e6, 2e6, 2e6)

        self._proxy: QGraphicsProxyWidget = self._scene.addWidget(web_view)
        self._web = web_view
        self._slate_rect = QRectF(0, 0, 1920, 1080)

        self._panning = False
        self._zooming = False
        self._last_pos: QPointF | None = None
        self._zoom_anchor_view: QPointF | None = None

    def set_slate_size(self, w: int, h: int) -> None:
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._web.setFixedSize(preview_w, preview_h)
        self._proxy.setMinimumSize(preview_w, preview_h)
        self._proxy.setMaximumSize(preview_w, preview_h)
        self._proxy.resize(preview_w, preview_h)
        self._slate_rect = QRectF(0, 0, preview_w, preview_h)
        self._web.page().setZoomFactor(1.0)
        self.fit_in_view()

    def fit_in_view(self) -> None:
        self.fitInView(self._slate_rect, Qt.AspectRatioMode.KeepAspectRatio)

    # -- zoom-to-cursor (Nuke / pyqtgraph style) --

    def _scale_at(self, factor: float, view_anchor: QPointF) -> None:
        """Scale around the scene point under *view_anchor* (view coords).

        Builds T' = T · Translate(cx,cy) · Scale(s) · Translate(-cx,-cy)
        and applies it atomically via setTransform so Qt cannot re-center.
        """
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

    # -- pan (transform-based, no scroll-bar clamping) --

    def _translate_view(self, dx: float, dy: float) -> None:
        """Pan by (dx, dy) screen pixels."""
        t = self.transform()
        s = t.m11()
        t.translate(dx / s, dy / s)
        self.setTransform(t)

    # -- events --

    def mousePressEvent(self, event):
        btn = event.button()
        if btn == Qt.MouseButton.MiddleButton:
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
        if btn == Qt.MouseButton.MiddleButton and self._panning:
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

    Left side: ``SlateFormPanel`` in a scroll area (slate fields + burn-in fields).
    Right side: ``QTabWidget`` with two tabs:
      - **Slate**: WebEngine preview of the static slate frame.
      - **Shot Preview**: Thumbnail background with the burn-in text overlay.
    Bottom: Status bar with OK / Cancel.
    """

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
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Slate & Overlay Editor")
        self.resize(1400, 850)

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

        self._thumb_b64 = ""
        if input_path:
            thumb = extract_thumbnail_b64(input_path, mode)
            if thumb:
                self._thumb_b64 = thumb
                self._form.set_thumbnail_b64(thumb)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        # --- Right: tabbed preview ---
        self._preview_tabs = QTabWidget()

        # Tab 1: Slate preview
        self._slate_web = QWebEngineView()
        self._slate_web.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self._slate_web.page().setBackgroundColor(QColor("#323232"))
        self._slate_preview = SlatePreviewView(self._slate_web)
        w, h = self._form.resolution()
        self._slate_preview.set_slate_size(w, h)
        self._preview_tabs.addTab(self._slate_preview, "Slate")

        # Tab 2: Shot preview (thumbnail bg + burn-in overlay)
        self._shot_container = QWidget()
        shot_layout = QVBoxLayout(self._shot_container)
        shot_layout.setContentsMargins(0, 0, 0, 0)

        self._burnin_web = QWebEngineView()
        self._burnin_web.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self._burnin_web.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self._shot_preview = SlatePreviewView(self._burnin_web)
        self._shot_preview.set_slate_size(w, h)
        # Dark background; the thumbnail is composited behind via the scene
        self._shot_preview.setBackgroundBrush(QBrush(QColor("#1a1a1a")))
        shot_layout.addWidget(self._shot_preview)
        self._preview_tabs.addTab(self._shot_container, "Shot Preview")

        splitter.addWidget(self._preview_tabs)

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

        # --- Load templates + live preview ---
        self._slate_loaded = False
        self._burnin_loaded = False

        self._slate_web.loadFinished.connect(self._on_slate_loaded)
        self._slate_web.load(QUrl.fromLocalFile(str(DEFAULT_TEMPLATE)))

        self._burnin_web.loadFinished.connect(self._on_burnin_loaded)
        self._burnin_web.load(QUrl.fromLocalFile(str(BURNIN_TEMPLATE)))

        self._form.data_changed.connect(self._push_slate_preview)
        self._form.data_changed.connect(self._update_preview_size)
        self._form.burnin_changed.connect(self._push_burnin_preview)

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.StatusTip:
            self._status_bar.showMessage(ev.tip())
            return True
        return super().event(ev)

    # -- Slate preview --

    def _on_slate_loaded(self, ok: bool) -> None:
        self._slate_loaded = ok
        if ok:
            self._push_slate_preview(self._form.slate_data())
            self._push_slate_thumbnail()
            self._slate_preview.fit_in_view()

    def _push_slate_preview(self, data: dict | None = None) -> None:
        if not self._slate_loaded:
            return
        if data is None:
            data = self._form.slate_data()
        js = f"if(typeof updateSlate==='function') updateSlate({json.dumps(data)})"
        self._slate_web.page().runJavaScript(js)

    def _push_slate_thumbnail(self) -> None:
        b64 = self._form.thumbnail_b64()
        if not b64:
            return
        js = f"if(typeof setThumbnail==='function') setThumbnail('{b64}')"
        self._slate_web.page().runJavaScript(js)

    # -- Shot / burn-in preview --

    def _on_burnin_loaded(self, ok: bool) -> None:
        self._burnin_loaded = ok
        if ok:
            self._push_burnin_preview(self._form.burnin_data())
            self._set_shot_background()
            self._shot_preview.fit_in_view()

    def _push_burnin_preview(self, data: dict | None = None) -> None:
        if not self._burnin_loaded:
            return
        if data is None:
            data = self._form.burnin_data()
        js = f"if(typeof updateBurnin==='function') updateBurnin({json.dumps(data)})"
        self._burnin_web.page().runJavaScript(js)

    def _set_shot_background(self) -> None:
        """Place the input thumbnail behind the burn-in web view in the scene."""
        if not self._thumb_b64:
            return
        import base64

        raw = base64.b64decode(self._thumb_b64)
        from PySide6.QtGui import QPixmap

        pix = QPixmap()
        pix.loadFromData(raw)
        if pix.isNull():
            return
        scene = self._shot_preview._scene
        # Insert pixmap behind the web proxy (z=0 for bg, proxy is higher)
        bg_item = scene.addPixmap(pix)
        bg_item.setZValue(-1)
        # Scale to match the web view size
        web_w = self._burnin_web.width()
        web_h = self._burnin_web.height()
        if pix.width() > 0 and pix.height() > 0:
            sx = web_w / pix.width()
            sy = web_h / pix.height()
            bg_item.setScale(min(sx, sy))
            # Center if aspect ratio differs
            scaled_w = pix.width() * min(sx, sy)
            scaled_h = pix.height() * min(sx, sy)
            bg_item.setPos((web_w - scaled_w) / 2, (web_h - scaled_h) / 2)
        self._shot_bg_item = bg_item

    # -- Shared --

    def _update_preview_size(self, _data: dict | None = None) -> None:
        w, h = self._form.resolution()
        self._slate_preview.set_slate_size(w, h)
        self._shot_preview.set_slate_size(w, h)

    def slate_data(self) -> dict:
        """Return the slate form data."""
        return self._form.slate_data()

    def burnin_data(self) -> dict:
        """Return the burn-in configuration."""
        return self._form.burnin_data()

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string."""
        return self._form.thumbnail_b64()

    def resolution(self) -> tuple[int, int]:
        return self._form.resolution()
