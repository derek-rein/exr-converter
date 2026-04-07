"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live WebEngine preview on the right.
"""

from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QPointF, QRectF, QRegularExpression, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QRegularExpressionValidator, QWheelEvent
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .slate import DEFAULT_TEMPLATE

COMMON_FPS: list[float] = [23.976, 24.0, 25.0, 29.97, 30.0, 48.0, 60.0]

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
            img = frame.to_image()
        else:
            from .sequence import find_exr_sequence_info

            _paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
            if not frames:
                return ""
            mid_idx = len(frames) // 2
            mid_frame = sorted(frames)[mid_idx]
            mid_path = seq.frame(mid_frame)

            import OpenImageIO as oiio

            inp = oiio.ImageInput.open(mid_path)
            if not inp:
                return ""
            spec = inp.spec()
            import numpy as np

            buf = np.zeros((spec.height, spec.width, spec.nchannels), dtype=np.float32)
            inp.read_image(buf)
            inp.close()
            rgb = buf[..., :3]
            rgb = np.clip(rgb, 0, None)
            srgb = np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * np.power(rgb, 1.0 / 2.4) - 0.055)
            srgb = np.clip(srgb * 255, 0, 255).astype(np.uint8)
            from PIL import Image

            img = Image.fromarray(srgb)

        max_w = 640
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)))

        import io

        buf_io = io.BytesIO()
        img.save(buf_io, format="JPEG", quality=85)
        return base64.b64encode(buf_io.getvalue()).decode("ascii")
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

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal)
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

        self._proxy: QGraphicsProxyWidget = self._scene.addWidget(web_view)
        self._web = web_view

        self._panning = False
        self._zooming = False
        self._last_pos = None
        self._zoom_anchor_scene = None

    def set_slate_size(self, w: int, h: int) -> None:
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._web.setFixedSize(preview_w, preview_h)
        self._proxy.setMinimumSize(preview_w, preview_h)
        self._proxy.setMaximumSize(preview_w, preview_h)
        self._proxy.resize(preview_w, preview_h)
        self._scene.setSceneRect(QRectF(0, 0, preview_w, preview_h))
        self._web.page().setZoomFactor(1.0)
        self.fit_in_view()

    def fit_in_view(self) -> None:
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _view_pos(self, event) -> QPointF:
        return event.position()

    def _to_scene(self, view_pt: QPointF) -> QPointF:
        return self.mapToScene(view_pt.toPoint())

    def _scale_by(self, factor: float, center_scene: QPointF) -> None:
        cur = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor))
        s = target / cur
        if abs(s - 1.0) < 1e-7:
            return
        xf = self.transform()
        cx, cy = center_scene.x(), center_scene.y()
        xf.translate(cx, cy)
        xf.scale(s, s)
        xf.translate(-cx, -cy)
        self.setTransform(xf)

    def mousePressEvent(self, event):
        btn = event.button()
        if btn == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pos = self._view_pos(event)
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = self._view_pos(event)
            self._zoom_anchor_scene = self._to_scene(self._last_pos)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = self._view_pos(event)
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - int(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - int(delta.y()))
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            s = 1.02 ** (dx * 0.5)
            self._scale_by(s, self._zoom_anchor_scene)
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
            self._zoom_anchor_scene = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s = 1.02 ** (delta * self.WHEEL_SCALE_FACTOR)
        center = self._to_scene(self._view_pos(event))
        self._scale_by(s, center)
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
    """Modal dialog for editing slate data with a live preview.

    Left side: ``SlateFormPanel`` in a scroll area.
    Right side: ``SlatePreviewView`` with a WebEngine preview.
    Bottom: OK / Cancel buttons.
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
        self.setWindowTitle("Edit Slate")
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

        # --- Right: web preview ---
        self._web = QWebEngineView()
        self._web.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ShowScrollBars, False
        )
        self._web.page().setBackgroundColor(QColor("#323232"))

        self._preview = SlatePreviewView(self._web)
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)
        splitter.addWidget(self._preview)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setSizes([380, 1020])

        layout.addWidget(splitter, 1)

        # --- Bottom: buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(12, 8, 12, 8)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        btn_layout.addWidget(buttons)
        layout.addLayout(btn_layout)

        # --- Load template + live preview ---
        self._load_template()
        self._form.data_changed.connect(self._push_preview)
        self._form.data_changed.connect(self._update_preview_size)
        self._web.loadFinished.connect(self._on_template_loaded)

    def _load_template(self) -> None:
        url = QUrl.fromLocalFile(str(DEFAULT_TEMPLATE))
        self._web.load(url)

    def _on_template_loaded(self, ok: bool) -> None:
        if ok:
            self._push_preview(self._form.slate_data())
            self._push_thumbnail()
            self._preview.fit_in_view()

    def _push_preview(self, data: dict | None = None) -> None:
        if data is None:
            data = self._form.slate_data()
        js = f"if(typeof updateSlate==='function') updateSlate({json.dumps(data)})"
        self._web.page().runJavaScript(js)

    def _push_thumbnail(self) -> None:
        b64 = self._form.thumbnail_b64()
        if not b64:
            return
        js = f"if(typeof setThumbnail==='function') setThumbnail('{b64}')"
        self._web.page().runJavaScript(js)

    def _update_preview_size(self, _data: dict | None = None) -> None:
        w, h = self._form.resolution()
        self._preview.set_slate_size(w, h)

    def slate_data(self) -> dict:
        """Return the form data."""
        return self._form.slate_data()

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string."""
        return self._form.thumbnail_b64()

    def resolution(self) -> tuple[int, int]:
        return self._form.resolution()
