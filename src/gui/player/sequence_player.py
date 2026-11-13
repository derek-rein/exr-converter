"""Reusable EXR sequence player: transport, RAM cache, OCIO GPU/CPU display.

Used by the slate/overlay editor (with :class:`OverlayHooks`) and by the
image-sequence browser quick preview (playback only).
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.constants import APP_NAME, APP_ORG
from ...core.exr_io import image_header_size
from ...core.pixel_aspect import probe_media_aspect
from ...core.preview_proxy import ProxyTarget, preview_level_key, proxy_storage_target
from ...services.cache_prefs import cache_budget_bytes
from ...services.exr_prefetch import ExrPrefetchService
from ...services.frame_cache import FrameCache
from ...services.video_prefetch import VideoPrefetchService
from ..icons import set_button_icon
from ..ocio_gpu_plane import (
    OcioGpuImagePlane,
    gpu_ocio_available,
    nuke_viewer_gamma_power,
)
from ..style import color, set_style_property
from ..timeline_slider import TimelineSlider
from .preview_view import ImagePreviewView
from .shuttle_bar import ShuttleBar

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

_PREFETCH_WORKERS = 4


@runtime_checkable
class OverlayHooks(Protocol):
    """Optional hooks for synthetic frames (slate) and live overlays."""

    def is_synthetic_frame(self, frame: int) -> bool: ...

    def render_synthetic_frame(self, frame: int) -> tuple[object, str] | None:
        """Return ``(float32 RGB HxWx3, src_colorspace)`` for non-disk frames."""
        ...

    def gpu_overlay_rgba(self, w: int, h: int, frame: int) -> tuple[object | None, object]:
        """Return ``(linear RGBA float32 or None, cache key)`` for GPU overlay texture."""
        ...

    def cpu_composite_overlays(self, working_f32: np.ndarray, frame: int) -> np.ndarray:
        """Return working RGB with burn-in/watermark composited (shot frames)."""
        ...


class SequencePlayer(QWidget):
    """Cache-first sequence playback with OCIO viewer controls.

    Disk frames are decoded into a :class:`~src.services.frame_cache.FrameCache`
    and prefetched ahead of the playhead. Display prefers **GPU OCIO**;
    CPU ``applyRGB`` is the fallback. Optional :class:`OverlayHooks` supply
    synthetic frames (slate) and burn-in/watermark layers.
    """

    frame_changed = Signal(int)
    playing_changed = Signal(bool)
    # (in frame or None, out frame or None)
    in_out_changed = Signal(object, object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        show_cache_ui: bool = True,
        prefer_gpu: bool = True,
        show_in_out: bool = False,
        proxy_preview: bool = False,
    ) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings(APP_ORG, APP_NAME)
        self._show_cache_ui = show_cache_ui
        self._prefer_gpu = prefer_gpu
        # Browser Preview only. The full viewer leaves this false and keeps
        # full-frame prefetch.
        self._proxy_preview = bool(proxy_preview)
        self._proxy_target: ProxyTarget | None = None
        self._proxy_level_key: tuple[str, int | str] | None = None
        self._src_w = 0
        self._src_h = 0
        self._media_path = ""
        self._proxy_size_waited = False

        self._ocio_cfg: object | None = None
        self._src_colorspace: str = ""
        self._ocio_proc_cache: dict[tuple, object] = {}

        self._comp_f32 = None
        self._comp_src_space = ""
        self._working_f32 = None
        self._working_space: str = ""
        self._display_f32: np.ndarray | None = None
        self._composed_working_f32 = None
        self._preview_pixmap_item = None
        self._gpu_plane: OcioGpuImagePlane | None = None
        self._use_gpu = False
        self._gpu_init_failed = False

        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display: tuple[str, str, str] | None = None

        self._exr_seq = None
        self._video_path: str | None = None
        self._shot_frames: list[int] = []
        self._shot_frames_set: set[int] = set()
        self._first_shot: int | None = None
        self._last_shot: int | None = None
        self._current_frame: int = 1
        self._width: int = 1920
        self._height: int = 1080
        self._pixel_aspect = 1.0
        self._pixel_aspect_label = ""
        self._fps: float = 24.0
        self._in_point: int | None = None
        self._out_point: int | None = None
        self._in_btn: QToolButton | None = None
        self._out_btn: QToolButton | None = None
        self._in_out_clear_btn: QToolButton | None = None
        self._in_out_label: QLabel | None = None

        self._shot_cache = FrameCache(cache_budget_bytes(self._settings), self)
        self._prefetch: ExrPrefetchService | VideoPrefetchService | None = None
        self._playback_wait_frame: int | None = None
        self._cache_paused = False
        self._hooks: OverlayHooks | None = None

        self._cache_bar: QProgressBar | None = None
        self._cache_pause_btn: QToolButton | None = None
        self._cache_clear_btn: QToolButton | None = None
        self._cache_host: QWidget | None = None

        self._gain = 1.0
        self._gamma = 1.0
        self._display_view_pairs: list[tuple[str, str]] = []
        # Prefer colorimetric / un-tone-mapped view for video-originated media.
        self._prefer_video_monitoring = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._preview = ImagePreviewView()
        self._preview_stack = QStackedWidget()
        self._preview_stack.setStyleSheet(f"background-color: {color('VIEWER_BG')};")
        self._preview_stack.addWidget(self._preview)
        # Create the GPU plane *eagerly* (before the top-level window is shown).
        # Qt 6.4+ docs: adding the first QOpenGLWidget to an *already shown*
        # top-level recreates the native window (RasterSurface → OpenGLSurface)
        # and is a known crash path — especially on macOS dialogs. The slate
        # editor is fine because its player is built in the dialog constructor;
        # the browser must do the same (create player before exec/show).
        if prefer_gpu and gpu_ocio_available():
            try:
                plane = OcioGpuImagePlane(parent=self._preview_stack)
                plane.gpu_failed.connect(self._on_gpu_failed)
                self._preview_stack.addWidget(plane)
                self._gpu_plane = plane
                self._use_gpu = True
                self._preview_stack.setCurrentWidget(plane)
                plane.set_format(self._width, self._height)
                log.info("SequencePlayer: GPU OCIO display enabled")
            except Exception:
                log.exception("GPU OCIO preview init failed; using CPU path")
                self._gpu_plane = None
                self._use_gpu = False
                self._gpu_init_failed = True
        else:
            log.info("SequencePlayer: CPU display path (prefer_gpu=%s)", prefer_gpu)

        self._build_viewer_controls(root)
        root.addWidget(self._preview_stack, 1)

        self._preview.set_frame_size(self._width, self._height)

        self._timeline = TimelineSlider()
        ideal_h = self._timeline._ideal_height()
        self._timeline.setFixedHeight(ideal_h)
        self._timeline.set_range(1, 1)
        self._timeline.set_value(1)
        self._timeline.value_changed.connect(self._on_timeline_changed)

        self._shuttle = ShuttleBar(self._timeline, fps=self._fps)
        self._shuttle.set_advance_callback(self._playback_tick)
        self._shuttle.playing_changed.connect(self._on_shuttle_play_toggled)

        # Timeline (and in / out) stays directly under the viewer, full width.
        timeline_row = QHBoxLayout()
        timeline_row.setContentsMargins(0, 0, 0, 0)
        timeline_row.setSpacing(0)
        timeline_row.addWidget(self._timeline, 1)
        if show_in_out:
            self._build_in_out_ui(timeline_row, ideal_h)
        root.addLayout(timeline_row)

        self._build_transport_row(root)

        self._shot_cache.cache_changed.connect(self._on_shot_cache_changed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QTimer.singleShot(0, self.fit_in_view)

    def _release_gpu_plane(self) -> None:
        """Free GL resources; leave the QObject parented for Qt to destroy.

        Qt docs for QOpenGLWidget: do **not** rely on ``deleteLater()`` for GL
        cleanup (context may not be current). Also avoid ``setParent(None)`` +
        dropping the last Python ref (PySide double-destroy → SIGSEGV in
        ``QObject::~QObject``).
        """
        plane = self._gpu_plane
        self._use_gpu = False
        if plane is None:
            return
        try:
            self._preview_stack.setCurrentWidget(self._preview)
        except RuntimeError:
            pass
        try:
            plane.hide()
            plane.release_gl()
        except Exception:
            log.debug("GPU plane release_gl failed", exc_info=True)
        # Keep plane as a child of the stack; mark dead so we never re-use it.
        self._gpu_plane = None
        self._gpu_init_failed = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _begin_media_load(
        self,
        *,
        ocio_cfg: object | None,
        src_colorspace: str,
        fps: float = 0.0,
        resolution: tuple[int, int] | None = None,
    ) -> None:
        """Shared teardown + OCIO/budget reset before load_sequence / load_video."""
        self.shutdown_prefetch_only()
        self._shot_cache.clear()
        self._shot_cache.budget_bytes = cache_budget_bytes(self._settings)
        self._ocio_cfg = ocio_cfg
        self._src_colorspace = src_colorspace or ""
        self._ocio_proc_cache.clear()
        self._working_space = ""
        self._last_viewer_display = None
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._exr_seq = None
        self._video_path = None
        self._proxy_target = None
        self._proxy_level_key = None
        self._src_w = 0
        self._src_h = 0
        self._media_path = ""
        self._proxy_size_waited = False
        self._shot_frames = []
        self._shot_frames_set = set()
        self._first_shot = None
        self._last_shot = None
        self._playback_wait_frame = None
        self._set_in_out(None, None)
        if self._gpu_plane is not None:
            self._gpu_plane.reset_view_fit()
        if fps > 0:
            self.set_fps(fps)
        if resolution is not None and resolution[0] > 0 and resolution[1] > 0:
            self.set_resolution(resolution[0], resolution[1])

    def _finish_media_load(self) -> bool:
        """Wire timeline + display after frames / colorspace are known.

        Returns ``False`` if frame range was never established (fail closed).
        """
        if self._first_shot is None or self._last_shot is None:
            log.error("Media load finished without a frame range")
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False
        self._timeline.set_range(self._first_shot, self._last_shot)
        self._timeline.set_marker_frames({})
        self._current_frame = self._first_shot
        self._timeline.set_value(self._current_frame)
        self._update_frame_readout()
        self._repopulate_display_views()
        self._sync_gpu_view_settings()
        self._sync_prefetch()
        self.refresh()
        return True

    @staticmethod
    def prepare_sequence(
        input_path: str, *, ocio_cfg: object | None = None, src_colorspace: str = ""
    ) -> dict:
        """Disk work for :meth:`load_sequence`; safe to run on a worker thread.

        Lists the folder, resolves the sequence and probes the first frame's
        colour attributes. Raises on failure.
        """
        from ...core.sequence import find_exr_sequence_info, resolve_sequence_src_colorspace

        paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
        src = ""
        aspect_factor = 1.0
        aspect_label = ""
        width = 0
        height = 0
        probe_path = ""
        if frames:
            probe_path = paths[0] if paths else input_path
            src = resolve_sequence_src_colorspace(probe_path, ocio_cfg, preferred=src_colorspace)
            try:
                aspect = probe_media_aspect(probe_path)
                aspect_factor = aspect.factor
                aspect_label = aspect.label()
            except Exception:
                aspect_factor = 1.0
                aspect_label = ""
            try:
                width, height = image_header_size(probe_path)
            except Exception:
                width, height = 0, 0
        return {
            "frames": list(frames),
            "seq": seq,
            "src_colorspace": src,
            "pixel_aspect": aspect_factor,
            "pixel_aspect_label": aspect_label,
            "width": width,
            "height": height,
            "probe_path": probe_path if frames else "",
        }

    @staticmethod
    def prepare_video(
        path: str, *, ocio_cfg: object | None = None, src_colorspace: str = ""
    ) -> dict:
        """Disk work for :meth:`load_video`; safe to run on a worker thread."""
        from ...core.video import probe_video, resolve_video_src_colorspace

        w, h, fps, n_frames = probe_video(path)
        src = resolve_video_src_colorspace(path, ocio_cfg, preferred=src_colorspace)
        aspect_factor = 1.0
        aspect_label = ""
        try:
            aspect = probe_media_aspect(path)
            aspect_factor = aspect.factor
            aspect_label = aspect.label()
        except Exception:
            aspect_factor = 1.0
            aspect_label = ""
        return {
            "w": w,
            "h": h,
            "fps": fps,
            "n_frames": n_frames,
            "src_colorspace": src,
            "pixel_aspect": aspect_factor,
            "pixel_aspect_label": aspect_label,
        }

    def load_sequence(
        self,
        input_path: str,
        *,
        fps: float = 24.0,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        resolution: tuple[int, int] | None = None,
        prefer_video_monitoring: bool = False,
        prepared: dict | None = None,
    ) -> bool:
        """Resolve *input_path* as an EXR sequence and start prefetch.

        Returns ``True`` when at least one frame was found.

        *prefer_video_monitoring*: if the config exposes a video-oriented view
        via viewing rules / encodings (``getDefaultView(display, videoCS)``),
        select it; otherwise keep the config default. Leave false for
        camera/scene-linear EXR.

        *prepared* is the result of :meth:`prepare_sequence` (run it on a
        worker thread): the folder listing and header probe are then skipped
        here, so the GUI thread never touches the disk.
        """
        self._prefer_video_monitoring = bool(prefer_video_monitoring)
        self._begin_media_load(
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            fps=fps,
            resolution=resolution,
        )

        if not input_path:
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        try:
            info = (
                prepared
                if prepared is not None
                else self.prepare_sequence(
                    input_path, ocio_cfg=self._ocio_cfg, src_colorspace=self._src_colorspace
                )
            )
            frames = info.get("frames") or []
            if not frames:
                self._repopulate_display_views()
                self._sync_gpu_view_settings()
                return False
            self._shot_frames = sorted(frames)
            self._shot_frames_set = set(self._shot_frames)
            self._first_shot = self._shot_frames[0]
            self._last_shot = self._shot_frames[-1]
            self._exr_seq = info["seq"]
            self._src_colorspace = str(info.get("src_colorspace") or "")
            self.set_pixel_aspect(
                float(info.get("pixel_aspect") or 1.0),
                str(info.get("pixel_aspect_label") or ""),
            )
            self._src_w = int(info.get("width") or 0)
            self._src_h = int(info.get("height") or 0)
            self._media_path = str(info.get("probe_path") or input_path)
            if self._proxy_preview and self._src_w > 0 and self._src_h > 0:
                self.set_resolution(self._src_w, self._src_h)
            log.info(
                "SequencePlayer load: src=%r working=%r frames=%s–%s",
                self._src_colorspace,
                self._resolve_working_space(),
                self._first_shot,
                self._last_shot,
            )
        except Exception:
            log.exception("Could not resolve EXR sequence for player: %s", input_path)
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        # Same OCIO path as the slate editor: worker applies src→working into
        # the RAM cache; the GPU/CPU viewer only does working→display.
        self._prepare_proxy_target()
        self._prefetch = self._new_exr_prefetch()
        self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
        self._arm_proxy_prefetch()
        return self._finish_media_load()

    def load_video(
        self,
        path: str,
        *,
        fps: float = 0.0,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        resolution: tuple[int, int] | None = None,
        prepared: dict | None = None,
    ) -> bool:
        """Open a video file for cache-first playback (same viewer as sequences).

        Frame indices are 1-based over an estimated frame count from the probe.
        Colour: decode → worker ``src→working`` → display/view (slate contract).
        Tries the config's video-monitoring view (viewing rules) when present.
        """
        self._prefer_video_monitoring = True
        self._begin_media_load(
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            resolution=resolution,
        )

        if not path:
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        try:
            from ...core.braw import DECODE_PREVIEW as BRAW_DECODE_PREVIEW
            from ...core.braw import is_braw_path
            from ...core.braw import scale_for_decode_mode as braw_scale_for_mode
            from ...core.r3d import DECODE_PREVIEW, is_r3d_path, scale_for_decode_mode
            from ...core.source_plugins import plugin_for_path

            info = (
                prepared
                if prepared is not None
                else self.prepare_video(
                    path, ocio_cfg=self._ocio_cfg, src_colorspace=self._src_colorspace
                )
            )
            w, h = int(info.get("w") or 0), int(info.get("h") or 0)
            probed_fps, n_frames = float(info.get("fps") or 0.0), int(info.get("n_frames") or 0)
        except Exception:
            log.exception("Could not probe video for player: %s", path)
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        n_frames = max(1, int(n_frames or 1))
        use_fps = float(fps) if fps and fps > 0 else float(probed_fps or 24.0)
        if use_fps <= 0:
            use_fps = 24.0
        self.set_fps(use_fps)
        # Probe size when caller did not pass a positive resolution.
        # R3D player path uses DECODE_PREVIEW (half) — set resolution from the
        # decode ladder so the viewer matches actual prefetch buffers.
        if w > 0 and h > 0 and (resolution is None or resolution[0] <= 0 or resolution[1] <= 0):
            plugin = plugin_for_path(path)
            if plugin is not None:
                try:
                    ladder = float(plugin.preview_scale())
                except Exception:
                    ladder = 1.0
                self.set_resolution(
                    max(1, int(round(w * ladder))),
                    max(1, int(round(h * ladder))),
                )
            elif is_r3d_path(path):
                ladder = scale_for_decode_mode(DECODE_PREVIEW)
                self.set_resolution(
                    max(1, int(round(w * ladder))),
                    max(1, int(round(h * ladder))),
                )
            elif is_braw_path(path):
                ladder = braw_scale_for_mode(BRAW_DECODE_PREVIEW)
                self.set_resolution(
                    max(1, int(round(w * ladder))),
                    max(1, int(round(h * ladder))),
                )
            else:
                self.set_resolution(int(w), int(h))

        self._video_path = path
        self._shot_frames = list(range(1, n_frames + 1))
        self._shot_frames_set = set(self._shot_frames)
        self._first_shot = 1
        self._last_shot = n_frames

        self._src_colorspace = str(info.get("src_colorspace") or "")
        self._src_w = int(w)
        self._src_h = int(h)
        self._media_path = path
        self.set_pixel_aspect(
            float(info.get("pixel_aspect") or 1.0),
            str(info.get("pixel_aspect_label") or ""),
        )
        if self._proxy_preview and w > 0 and h > 0:
            self.set_resolution(int(w), int(h))
        log.info(
            "SequencePlayer load_video: %s frames=%s fps=%.3f src=%r working=%r",
            path,
            n_frames,
            use_fps,
            self._src_colorspace,
            self._resolve_working_space(),
        )

        self._prepare_proxy_target()
        self._prefetch = self._new_video_prefetch()
        self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
        self._arm_proxy_prefetch()
        return self._finish_media_load()

    def clear(self) -> None:
        """Stop playback, drop cache, and reset to an empty timeline."""
        self.set_playing(False)
        self.shutdown_prefetch_only()
        self._shot_cache.clear()
        self._exr_seq = None
        self._video_path = None
        self._shot_frames = []
        self._shot_frames_set = set()
        self._first_shot = None
        self._last_shot = None
        self._comp_f32 = None
        self._working_f32 = None
        self._display_f32 = None
        self._composed_working_f32 = None
        self._timeline.set_range(1, 1)
        self._timeline.set_marker_frames({})
        self._timeline.set_cached_frames(set())
        self._current_frame = 1
        self._timeline.set_value(1)
        self._update_frame_readout()
        if self._gpu_plane is not None:
            try:
                self._gpu_plane.set_overlay_rgba(None)
            except Exception:
                pass

    def shutdown(self) -> None:
        """Stop prefetch workers and release playback resources.

        Safe to call before the dialog closes. Does not ``deleteLater`` this
        widget — the parent dialog owns the QObject tree.
        """
        self.set_playing(False)
        self.shutdown_prefetch_only()
        try:
            self._shot_cache.cache_changed.disconnect(self._on_shot_cache_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self._shot_cache.clear()
        except RuntimeError:
            pass
        self._hooks = None
        self._working_f32 = None
        self._comp_f32 = None
        self._display_f32 = None
        self._composed_working_f32 = None
        self._release_gpu_plane()

    def restart_prefetch(self) -> bool:
        """Restart background loading for the media already loaded.

        Pairs with :meth:`shutdown_prefetch_only`: the RAM cache, frame range
        and in/out points are kept, so re-entering a preview is instant.
        Returns False when nothing is loaded.
        """
        if self._prefetch is not None:
            return True
        if self._exr_seq is not None and self._shot_frames:
            self._prefetch = self._new_exr_prefetch()
        elif self._video_path and self._shot_frames:
            self._prefetch = self._new_video_prefetch()
        else:
            return False
        self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
        self._sync_prefetch()
        self.refresh()
        return True

    def shutdown_prefetch_only(self) -> None:
        if self._prefetch is not None:
            try:
                self._prefetch.frame_loaded.disconnect(self._on_prefetch_frame_loaded)
            except (RuntimeError, TypeError):
                pass
            self._prefetch.shutdown()
            self._prefetch = None

    def set_frame(self, frame: int) -> None:
        self._goto_frame(frame)

    def frame(self) -> int:
        return self._current_frame

    def set_playing(self, playing: bool) -> None:
        self._shuttle.set_playing(playing)

    def is_playing(self) -> bool:
        return self._shuttle.is_playing()

    def set_fps(self, fps: float) -> None:
        self._fps = max(1.0, float(fps))
        self._shuttle.set_fps(self._fps)

    def set_range(self, first: int, last: int) -> None:
        self._timeline.set_range(first, last)
        if self._current_frame < first or self._current_frame > last:
            self._current_frame = first
            self._timeline.set_value(first)
        self._update_frame_readout()

    def set_markers(self, markers: dict[int, str]) -> None:
        self._timeline.set_marker_frames(dict(markers))

    def set_overlay_hooks(self, hooks: OverlayHooks | None) -> None:
        self._hooks = hooks

    def refresh(self) -> None:
        """Re-render the current frame (form/overlay changes, etc.)."""
        self._refresh_current_frame()

    def _proxy_decode_target(self) -> ProxyTarget | None:
        if not self._proxy_preview or self._src_w <= 0 or self._src_h <= 0:
            return None
        widget_w = self._preview_stack.width()
        widget_h = self._preview_stack.height()
        if widget_w < 32 or widget_h < 32:
            widget_w = self.width()
            widget_h = self.height()
        if widget_w < 32 or widget_h < 32:
            return None
        return proxy_storage_target(
            self._src_w,
            self._src_h,
            widget_w,
            widget_h,
            float(self.devicePixelRatioF() or 1.0),
            self._pixel_aspect,
        )

    def _preview_target_tuple(self) -> tuple[int, int, int] | None:
        target = self._proxy_target
        if target is None:
            return None
        return (target.width, target.height, target.bucket)

    def _prepare_proxy_target(self) -> None:
        if not self._proxy_preview:
            return
        target = self._proxy_decode_target()
        self._proxy_target = target
        if target is None or not self._media_path:
            self._proxy_level_key = None
            return
        self._proxy_level_key = preview_level_key(
            self._media_path,
            self._src_w,
            self._src_h,
            target.width,
            target.height,
        )

    def _new_exr_prefetch(self) -> ExrPrefetchService:
        return ExrPrefetchService(
            self._exr_seq,
            self._shot_cache,
            self._shot_frames,
            max_workers=1 if self._proxy_preview else _PREFETCH_WORKERS,
            frame_transform=self._build_worker_frame_transform(),
            single_frame=self._proxy_preview,
            preview_target=self._preview_target_tuple(),
            parent=self,
        )

    def _new_video_prefetch(self) -> VideoPrefetchService:
        return VideoPrefetchService(
            self._video_path or "",
            self._shot_cache,
            self._shot_frames,
            fps=self._fps,
            frame_transform=self._build_worker_frame_transform(),
            single_frame=self._proxy_preview,
            preview_target=self._preview_target_tuple(),
            parent=self,
        )

    def _arm_proxy_prefetch(self) -> None:
        """Hold the first read until the pane has a size, so it is not full-res."""
        if not self._proxy_preview or self._prefetch is None or self._proxy_target is not None:
            return
        self._prefetch.set_paused(True)
        QTimer.singleShot(0, self._retry_proxy_size)

    def _retry_proxy_size(self) -> None:
        if not self._proxy_preview or self._prefetch is None:
            return
        self._prepare_proxy_target()
        target = self._proxy_target
        if target is None:
            if not self._proxy_size_waited:
                self._proxy_size_waited = True
                QTimer.singleShot(0, self._retry_proxy_size)
                return
            self._prefetch.set_paused(False)
            self._sync_prefetch()
            self.refresh()
            return
        self._prefetch.set_preview_target(target.width, target.height, target.bucket)
        self._prefetch.set_paused(False)
        self._sync_prefetch()
        self.refresh()

    def _update_proxy_target(self) -> None:
        if not self._proxy_preview or self._prefetch is None:
            return
        if getattr(self._prefetch, "full_res_only", False):
            return
        target = self._proxy_decode_target()
        if target is None:
            return
        level = preview_level_key(
            self._media_path,
            self._src_w,
            self._src_h,
            target.width,
            target.height,
        )
        if level == self._proxy_level_key:
            return
        if self._proxy_target is not None and target.bucket < self._proxy_target.bucket:
            return
        self._proxy_target = target
        self._proxy_level_key = level
        self._shot_cache.clear()
        self._prefetch.set_preview_target(target.width, target.height, target.bucket)
        self._prefetch.set_paused(False)
        self.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_proxy_target()

    def fit_in_view(self) -> None:
        # Guard against QTimer.singleShot(0) firing after the dialog was closed.
        try:
            if self._use_gpu and self._gpu_plane is not None:
                try:
                    self._gpu_plane.fit_in_view()
                except RuntimeError:
                    return
            self._preview.fit_in_view()
        except RuntimeError:
            return

    def set_resolution(self, w: int, h: int) -> None:
        self._width = max(1, int(w))
        self._height = max(1, int(h))
        self._apply_pixel_aspect_views()

    def pixel_aspect(self) -> float:
        return float(self._pixel_aspect)

    def set_pixel_aspect(self, factor: float, label: str = "") -> None:
        """Display squeeze. ``label`` is the HUD text (empty hides it)."""
        self._pixel_aspect = float(factor) if factor and factor > 0 else 1.0
        self._pixel_aspect_label = label or ""
        self._apply_pixel_aspect_views()

    def _apply_pixel_aspect_views(self) -> None:
        self._preview.set_frame_size(
            self._width, self._height, self._pixel_aspect, self._pixel_aspect_label
        )
        if self._gpu_plane is not None:
            self._gpu_plane.set_format(self._width, self._height)
            self._gpu_plane.set_pixel_aspect(self._pixel_aspect, self._pixel_aspect_label)

    def resolution(self) -> tuple[int, int]:
        return self._width, self._height

    def first_shot_frame(self) -> int | None:
        return self._first_shot

    def last_shot_frame(self) -> int | None:
        return self._last_shot

    def shot_frames(self) -> list[int]:
        return list(self._shot_frames)

    # ------------------------------------------------------------------
    # In / out points
    # ------------------------------------------------------------------

    def in_point(self) -> int | None:
        return self._in_point

    def out_point(self) -> int | None:
        return self._out_point

    def in_out_range(self) -> tuple[int, int] | None:
        """``(first, last)`` chosen with in/out, or ``None`` when neither is set."""
        if self._in_point is None and self._out_point is None:
            return None
        if self._first_shot is None or self._last_shot is None:
            return None
        return self._play_range()

    def set_in_point(self, frame: int | None = None) -> None:
        """Set the in point (default: current frame). An out point before it is cleared."""
        if self._first_shot is None:
            return
        f = self._current_frame if frame is None else int(frame)
        out = self._out_point if self._out_point is None or self._out_point >= f else None
        self._set_in_out(f, out)

    def set_out_point(self, frame: int | None = None) -> None:
        """Set the out point (default: current frame). An in point after it is cleared."""
        if self._first_shot is None:
            return
        f = self._current_frame if frame is None else int(frame)
        inp = self._in_point if self._in_point is None or self._in_point <= f else None
        self._set_in_out(inp, f)

    def clear_in_out(self) -> None:
        self._set_in_out(None, None)

    def _set_in_out(self, in_point: int | None, out_point: int | None) -> None:
        if (in_point, out_point) == (self._in_point, self._out_point):
            return
        self._in_point = in_point
        self._out_point = out_point
        self._timeline.set_in_out(in_point, out_point)
        self._refresh_in_out_ui()
        self.in_out_changed.emit(in_point, out_point)

    def _build_transport_row(self, parent_layout: QVBoxLayout) -> None:
        """Transport centred under the viewer, frame read-out beside it.

        An empty spacer mirrors the read-out on the left, so the play button
        sits on the viewer's centre line. The cache strip, when shown, is its
        own row below.
        """
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(8)

        self._transport_spacer = QWidget()
        right = QWidget()
        right_lay = QHBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)
        self._frame_readout = QLabel()
        set_style_property(self._frame_readout, "role", "muted")
        font = self._frame_readout.font()
        font.setFeature(QFont.Tag("tnum"), 1)
        self._frame_readout.setFont(font)
        right_lay.addWidget(self._frame_readout)
        right_lay.addStretch(1)

        for w in (self._transport_spacer, right):
            w.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        row.addWidget(self._transport_spacer, 1)
        row.addWidget(self._shuttle, 0)
        row.addWidget(right, 1)
        parent_layout.addWidget(host)

        if self._show_cache_ui:
            cache_row = QWidget()
            cache_lay = QHBoxLayout(cache_row)
            cache_lay.setContentsMargins(8, 0, 8, 4)
            cache_lay.setSpacing(0)
            self._build_cache_ui(cache_lay)
            cache_lay.addStretch(1)
            parent_layout.addWidget(cache_row)

        self.frame_changed.connect(lambda _f: self._update_frame_readout())
        self._update_frame_readout()

    def _update_frame_readout(self) -> None:
        """Current frame and timeline length, e.g. ``1004 \u00b7 12 frames``."""
        first = self._timeline.first_frame
        last = self._timeline.last_frame
        total = max(0, last - first + 1)
        noun = "frame" if total == 1 else "frames"
        self._frame_readout.setText(f"{self._current_frame} \u00b7 {total} {noun}")
        # Reserve room for the widest frame number so the text never jumps.
        digits = max(len(str(first)), len(str(last)))
        widest = f"{'8' * digits} \u00b7 {total} {noun}"
        width = self._frame_readout.fontMetrics().horizontalAdvance(widest)
        self._frame_readout.setMinimumWidth(width)
        self._transport_spacer.setMinimumWidth(width)
        self._frame_readout.parentWidget().setMinimumWidth(width)

    def _build_in_out_ui(self, row: QHBoxLayout, height: int) -> None:
        host = QWidget()
        lay = QHBoxLayout(host)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(2)

        def make(text: str, tip: str) -> QToolButton:
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            set_style_property(b, "variant", "tool")
            b.setFixedHeight(22)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            lay.addWidget(b)
            return b

        self._in_btn = make("In", "Set the in point at the current frame (I)")
        self._in_btn.setCheckable(True)
        self._out_btn = make("Out", "Set the out point at the current frame (O)")
        self._out_btn.setCheckable(True)
        self._in_out_clear_btn = make("", "Clear in and out points")
        set_button_icon(self._in_out_clear_btn, "x", size=12)
        self._in_out_label = QLabel()
        set_style_property(self._in_out_label, "role", "caption")
        set_style_property(self._in_out_label, "state", "warning")
        self._in_out_label.setMinimumWidth(64)
        lay.addWidget(self._in_out_label)
        host.setFixedHeight(height)
        row.addWidget(host)

        # clicked(bool) toggles the check state first; re-sync from our state.
        self._in_btn.clicked.connect(lambda _c=False: self.set_in_point())
        self._out_btn.clicked.connect(lambda _c=False: self.set_out_point())
        self._in_out_clear_btn.clicked.connect(lambda _c=False: self.clear_in_out())
        self._refresh_in_out_ui()

    def _refresh_in_out_ui(self) -> None:
        if self._in_btn is None or self._out_btn is None:
            return
        self._in_btn.setChecked(self._in_point is not None)
        self._out_btn.setChecked(self._out_point is not None)
        if self._in_out_clear_btn is not None:
            self._in_out_clear_btn.setEnabled(
                self._in_point is not None or self._out_point is not None
            )
        if self._in_out_label is not None:
            rng = self.in_out_range()
            if rng is None:
                self._in_out_label.setText("")
                self._in_out_label.setToolTip("")
            else:
                n = rng[1] - rng[0] + 1
                self._in_out_label.setText(f"{rng[0]}\u2013{rng[1]}")
                self._in_out_label.setToolTip(f"{n} frame(s) between in and out")

    # ------------------------------------------------------------------
    # Viewer controls
    # ------------------------------------------------------------------

    def _build_viewer_controls(self, parent_layout: QVBoxLayout) -> None:
        from ..nuke_slider import NukeSlider

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 1, 4, 1)
        strip.setSpacing(6)

        gain_lbl = QLabel("Gain")
        set_style_property(gain_lbl, "role", "caption")
        strip.addWidget(gain_lbl)

        self._gain_value_label = QLabel("1.0")
        self._gain_value_label.setFixedWidth(28)
        self._gain_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._style_viewer_value_label(self._gain_value_label, 1.0)
        strip.addWidget(self._gain_value_label)

        self._gain_slider = NukeSlider(
            default=1.0,
            val_min=0.01,
            val_max=64.0,
            map_mode="log",
        )
        self._gain_slider.setMinimumWidth(110)
        self._gain_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip.addWidget(self._gain_slider, 1)

        strip.addSpacing(8)

        gamma_lbl = QLabel("Gamma")
        set_style_property(gamma_lbl, "role", "caption")
        strip.addWidget(gamma_lbl)

        self._gamma_value_label = QLabel("1.0")
        self._gamma_value_label.setFixedWidth(22)
        self._gamma_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._style_viewer_value_label(self._gamma_value_label, 1.0)
        strip.addWidget(self._gamma_value_label)

        self._gamma_slider = NukeSlider(
            default=1.0,
            val_min=0.0,
            val_max=4.0,
            map_mode="pivot",
        )
        self._gamma_slider.setMinimumWidth(110)
        self._gamma_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip.addWidget(self._gamma_slider, 1)

        strip.addSpacing(8)

        self._display_view_combo = QComboBox()
        self._display_view_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._display_view_combo.setMinimumContentsLength(12)
        self._display_view_combo.setMinimumWidth(100)
        self._display_view_combo.setMaximumWidth(168)
        self._display_view_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        self._display_view_combo.setToolTip("OCIO display / view")
        strip.addWidget(self._display_view_combo, 0)

        parent_layout.addLayout(strip)

        self._display_view_pairs = []
        self._repopulate_display_views()

        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        self._gamma_slider.valueChanged.connect(self._on_gamma_changed)
        self._display_view_combo.currentIndexChanged.connect(
            lambda _: self._invalidate_display_cache()
        )
        self._sync_gpu_view_settings()

    def _repopulate_display_views(self) -> None:
        self._display_view_combo.blockSignals(True)
        self._display_view_combo.clear()
        self._display_view_pairs.clear()

        if self._ocio_cfg is None:
            self._display_view_pairs.append(("sRGB", "Raw"))
            self._display_view_combo.addItem("sRGB")
            self._display_view_combo.blockSignals(False)
            return

        from ...core.ocio_utils import (
            default_display_view,
            list_displays,
            list_views,
            preferred_video_monitoring_view,
        )

        default_display, default_view = default_display_view(self._ocio_cfg)
        default_idx = 0

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

        # Soft prefer: ask the config (viewing rules / video encodings) for a
        # monitoring view. No hard-coded view names — if the config has none,
        # keep getDefaultView(display) from above.
        if self._prefer_video_monitoring and self._display_view_pairs:
            pref_d, pref_v = preferred_video_monitoring_view(self._ocio_cfg, default_display)
            if pref_v:
                for i, (d, v) in enumerate(self._display_view_pairs):
                    if v == pref_v and (not pref_d or d == pref_d):
                        default_idx = i
                        break

        if self._display_view_combo.count() > 0:
            self._display_view_combo.setCurrentIndex(default_idx)
        self._display_view_combo.blockSignals(False)

    def _exposure_stops(self) -> float:
        return math.log2(max(float(getattr(self, "_gain", 1.0)), 1e-10))

    def _on_gpu_failed(self, reason: str) -> None:
        log.error("Falling back to CPU OCIO preview: %s", reason)
        self._use_gpu = False
        self._preview_stack.setCurrentWidget(self._preview)
        self._apply_display_transform()

    def _sync_gpu_view_settings(self) -> None:
        if self._gpu_plane is None or not self._use_gpu:
            return
        if not self._gpu_plane.is_alive():
            self._use_gpu = False
            return
        working = self._resolve_working_space() or self._comp_src_space
        display, view = "", ""
        idx = self._display_view_combo.currentIndex()
        if 0 <= idx < len(self._display_view_pairs):
            display, view = self._display_view_pairs[idx]
        self._gpu_plane.set_ocio_view(self._ocio_cfg, working, display, view)
        self._gpu_plane.set_exposure_stops(self._exposure_stops())
        self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))

    @staticmethod
    def _style_viewer_value_label(label: QLabel, value: float, *, default: float = 1.0) -> None:
        is_default = abs(float(value) - default) < 1e-6
        # A non-default gain or gamma changes what you see: flag it.
        set_style_property(label, "role", "caption")
        set_style_property(label, "state", "" if is_default else "warning")

    def _on_gain_changed(self, gain: float) -> None:
        self._gain = gain
        if abs(gain - 1.0) < 1e-6:
            txt = "1.0"
        elif gain >= 10:
            txt = f"{gain:.0f}"
        elif gain >= 1:
            txt = f"{gain:.1f}"
        elif gain >= 0.1:
            txt = f"{gain:.2f}"
        else:
            txt = f"{gain:.3f}"
        self._gain_value_label.setText(txt)
        self._style_viewer_value_label(self._gain_value_label, gain)

        stops = self._exposure_stops()
        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            self._gpu_plane.set_exposure_stops(stops)
            return
        if self._ec_exposure_prop is not None:
            self._ec_exposure_prop.setDouble(stops)
            self._reapply_display_with_ec()
            return
        self._refresh_gain_gamma()

    def _on_gamma_changed(self, gamma: float) -> None:
        self._gamma = gamma
        if abs(gamma - 1.0) < 1e-6:
            txt = "1.0"
        elif gamma >= 1:
            txt = f"{gamma:.1f}"
        else:
            txt = f"{gamma:.2f}"
        self._gamma_value_label.setText(txt)
        self._style_viewer_value_label(self._gamma_value_label, gamma)

        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            self._gpu_plane.set_gamma(gamma)
            return
        if self._display_f32 is not None:
            self._paint_display_with_viewer_gamma(self._display_f32)
            return
        self._refresh_gain_gamma()

    # ------------------------------------------------------------------
    # Cache UI
    # ------------------------------------------------------------------

    def _build_cache_ui(self, parent_layout: QHBoxLayout) -> None:
        """Compact pause/clear + usage only. Budget % lives in Preferences."""
        self._cache_host = QWidget()
        row = QHBoxLayout(self._cache_host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        cache_lbl = QLabel("Cache")
        set_style_property(cache_lbl, "role", "muted")
        row.addWidget(cache_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self._cache_bar = QProgressBar()
        self._cache_bar.setMaximum(1000)
        self._cache_bar.setFixedWidth(150)
        self._cache_bar.setFixedHeight(16)
        self._cache_bar.setTextVisible(True)
        self._cache_bar.setToolTip(
            "Playback cache memory in use (budget set under File → Preferences)"
        )
        row.addWidget(self._cache_bar, 0, Qt.AlignmentFlag.AlignVCenter)

        self._cache_pause_btn = QToolButton()
        set_button_icon(self._cache_pause_btn, "pause", size=12)
        set_style_property(self._cache_pause_btn, "variant", "ghost")
        self._cache_pause_btn.setAccessibleName("Pause background prefetch")
        self._cache_pause_btn.setCheckable(True)
        self._cache_pause_btn.setAutoRaise(True)
        self._cache_pause_btn.setFixedSize(24, 22)
        self._cache_pause_btn.setToolTip("Pause background prefetch")
        row.addWidget(self._cache_pause_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._cache_clear_btn = QToolButton()
        set_button_icon(self._cache_clear_btn, "x", size=12)
        set_style_property(self._cache_clear_btn, "variant", "ghost")
        self._cache_clear_btn.setAccessibleName("Clear playback cache")
        self._cache_clear_btn.setAutoRaise(True)
        self._cache_clear_btn.setFixedSize(24, 22)
        self._cache_clear_btn.setToolTip("Clear playback cache")
        row.addWidget(self._cache_clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        parent_layout.addWidget(self._cache_host)

        self._cache_pause_btn.toggled.connect(self._on_cache_pause_toggled)
        self._cache_clear_btn.clicked.connect(self._on_cache_clear)
        # Apply budget from Preferences (no in-player slider).
        self._shot_cache.budget_bytes = cache_budget_bytes(self._settings)
        self._update_cache_usage_bar()

    def _update_cache_usage_bar(self) -> None:
        if self._cache_bar is None:
            return
        used = self._shot_cache.current_bytes
        budget = self._shot_cache.budget_bytes
        if budget > 0:
            self._cache_bar.setValue(min(1000, int(used * 1000 / budget)))
        else:
            self._cache_bar.setValue(0)
        used_mb = used / (1024 * 1024)
        budget_mb = budget / (1024 * 1024)
        self._cache_bar.setFormat(f"{used_mb:.0f}/{budget_mb:.0f} MB")

    def _on_cache_pause_toggled(self, paused: bool) -> None:
        self._cache_paused = paused
        if self._cache_pause_btn is not None:
            set_button_icon(self._cache_pause_btn, "play" if paused else "pause", size=12)
        if self._prefetch is not None:
            self._prefetch.set_paused(paused)
        if not paused:
            self._sync_prefetch()

    def _on_cache_clear(self) -> None:
        self._shot_cache.clear()

    def _on_shot_cache_changed(self) -> None:
        self._timeline.set_cached_frames(self._shot_cache.cached_frames())
        self._update_cache_usage_bar()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Left/Right step one frame; Space play/pause when focused."""
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._step_frame(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self._step_frame(1)
            event.accept()
            return
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_playing(not self.is_playing())
            event.accept()
            return
        if self._in_btn is not None and not event.modifiers():
            if key == Qt.Key.Key_I:
                self.set_in_point()
                event.accept()
                return
            if key == Qt.Key.Key_O:
                self.set_out_point()
                event.accept()
                return
        super().keyPressEvent(event)

    def _step_frame(self, delta: int) -> None:
        """Step playhead by *delta* frames (wraps at ends)."""
        if self.is_playing():
            self.set_playing(False)
        cur = self._timeline.value()
        first = self._timeline.first_frame
        last = self._timeline.last_frame
        if last < first:
            return
        span = last - first + 1
        nxt = first + ((cur - first + int(delta)) % span)
        self._goto_frame(nxt)

    # ------------------------------------------------------------------
    # Transport / frame routing
    # ------------------------------------------------------------------

    def _on_timeline_changed(self, frame: int) -> None:
        self._goto_frame(frame)

    def _on_shuttle_play_toggled(self, playing: bool) -> None:
        if not playing:
            self._playback_wait_frame = None
            self._shuttle.stop_timer()
        self._shot_cache.set_batch_mode(playing)
        self._sync_prefetch()
        self.playing_changed.emit(playing)
        if playing:
            self._playback_tick()

    def _next_playback_frame(self, frame: int) -> int:
        first, last = self._play_range()
        nxt = frame + 1
        if nxt > last or nxt < first:
            nxt = first
        return nxt

    def _play_range(self) -> tuple[int, int]:
        """Loop range for playback: in/out when set, else the whole timeline."""
        first = self._timeline.first_frame
        last = self._timeline.last_frame
        if self._in_point is not None:
            first = max(first, self._in_point)
        if self._out_point is not None:
            last = min(last, self._out_point)
        if last < first:
            return self._timeline.first_frame, self._timeline.last_frame
        return first, last

    def _needs_disk_cache(self, frame: int) -> bool:
        """True when *frame* is a media frame that must hit the RAM cache."""
        if self._hooks is not None and self._hooks.is_synthetic_frame(frame):
            return False
        return frame in self._shot_frames_set

    def _goto_frame(self, frame: int) -> None:
        frame = max(self._timeline.first_frame, min(frame, self._timeline.last_frame))
        if frame == self._current_frame:
            self._refresh_current_frame()
            return
        self._current_frame = frame
        self._playback_wait_frame = None
        self._timeline.set_value(frame)
        self._sync_prefetch()
        self.frame_changed.emit(frame)
        self._refresh_current_frame()

    def _sync_prefetch(self) -> None:
        if self._prefetch is not None and not self._cache_paused:
            # Prefetch context is disk-frame based; clamp to nearest shot if on slate.
            ctx = self._current_frame
            if self._hooks is not None and self._hooks.is_synthetic_frame(ctx):
                if self._first_shot is not None:
                    ctx = self._first_shot
            self._prefetch.set_context(ctx, playing=self.is_playing())

    def _playback_tick(self) -> None:
        if not self.is_playing():
            return

        cur = self._timeline.value()
        nxt = self._next_playback_frame(cur)

        if self._needs_disk_cache(nxt) and not self._shot_cache.contains(nxt):
            self._playback_wait_frame = nxt
            self._shuttle.stop_timer()
            if self._prefetch is not None:
                self._prefetch.request_immediate(nxt)
            return

        self._goto_frame(nxt)

    def _on_prefetch_frame_loaded(self, frame: int, rgb) -> None:
        if rgb is None:
            if frame == self._playback_wait_frame and self.is_playing():
                self._playback_wait_frame = None
                skip = self._next_playback_frame(frame)
                self._goto_frame(skip)
                if not self._shuttle.is_timer_active():
                    self._shuttle.start_timer()
            return

        if frame == self._current_frame:
            self._composite_shot_with_pixels(frame, rgb)

        if frame == self._playback_wait_frame and self.is_playing():
            self._goto_frame(frame)
            if not self._shuttle.is_timer_active():
                self._shuttle.start_timer()

        self._sync_prefetch()

    def _refresh_current_frame(self) -> None:
        frame = self._current_frame
        if self._hooks is not None and self._hooks.is_synthetic_frame(frame):
            self._composite_synthetic(frame)
        else:
            self._composite_shot(frame)

    def _composite_synthetic(self, frame: int) -> None:
        if self._hooks is None:
            return
        try:
            result = self._hooks.render_synthetic_frame(frame)
        except Exception:
            log.exception("Synthetic frame render failed (frame %s)", frame)
            return
        if result is None:
            return
        rgb, src_cs = result
        import numpy as np

        self._comp_f32 = np.ascontiguousarray(rgb, dtype=np.float32)
        self._comp_src_space = src_cs or ""
        self._working_f32 = None
        self._composed_working_f32 = None
        self._display_f32 = None
        self._apply_display_transform()

    def _composite_shot(self, frame: int) -> None:
        rgb = self._shot_cache.get(frame)
        if rgb is None:
            if self._prefetch is not None:
                self._prefetch.request_immediate(frame)
            return
        self._composite_shot_with_pixels(frame, rgb)

    def _composite_shot_with_pixels(self, frame: int, rgb) -> None:
        import numpy as np

        # Cache contract (slate + video browsers share this player):
        #   uint16  → source-referred, transform on GUI thread
        #   float*  → already working-space (worker src→working), display only
        if rgb.dtype == np.uint16:
            self._comp_f32 = rgb.astype(np.float32) / 65535.0
            self._comp_src_space = self._src_colorspace or ""
            self._working_f32 = None
            self._composed_working_f32 = None
        else:
            self._comp_f32 = None
            self._comp_src_space = ""
            self._working_f32 = rgb
            self._composed_working_f32 = None
        self._display_f32 = None

        # Adopt frame resolution from the first cached image if the host left
        # the constructor default. Browser Preview keeps the source plate size
        # in the HUD; the uploaded frame may be a smaller proxy.
        try:
            h, w = int(rgb.shape[0]), int(rgb.shape[1])
            if self._proxy_preview and self._src_w > 0 and self._src_h > 0:
                if (self._width, self._height) != (self._src_w, self._src_h):
                    self.set_resolution(self._src_w, self._src_h)
            elif h > 0 and w > 0 and (self._width, self._height) == (1920, 1080):
                if (w, h) != (1920, 1080):
                    self.set_resolution(w, h)
        except Exception:
            pass

        self._apply_display_transform()

    # ------------------------------------------------------------------
    # OCIO working / display path
    # ------------------------------------------------------------------

    def _resolve_working_space(self) -> str:
        if self._ocio_cfg is None:
            return ""
        if self._working_space:
            return self._working_space
        try:
            from ...core.ocio_utils import get_compositing_space

            self._working_space = get_compositing_space(self._ocio_cfg)
        except Exception:
            log.exception("Could not resolve compositing space")
            self._working_space = ""
        return self._working_space

    def _build_worker_frame_transform(self):
        if self._ocio_cfg is None:
            return None
        src_space = self._src_colorspace or ""
        cpu = self._get_src_to_working_proc(src_space)
        if cpu is None:
            return None

        import PyOpenColorIO as OCIO

        def _transform(rgb_f32):
            import numpy as np

            buf = np.ascontiguousarray(rgb_f32, dtype=np.float32)
            h, w = buf.shape[:2]
            try:
                cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
            except Exception:
                # Never cache untransformed pixels as float working-space —
                # that poisons display (video looks washed/crushed).
                log.exception("Worker src→working OCIO apply failed")
                return None
            return buf.astype(np.float16)

        return _transform

    def _get_src_to_working_proc(self, src_space: str):
        if not src_space or self._ocio_cfg is None:
            return None
        working_space = self._resolve_working_space()
        if not working_space:
            return None
        key = ("src->work", src_space, working_space)
        proc = self._ocio_proc_cache.get(key)
        if proc is not None:
            return proc
        try:
            from ...core.ocio_utils import make_cpu_processor

            proc = make_cpu_processor(self._ocio_cfg, src_space, working_space)
        except Exception:
            log.exception(
                "Failed to build src→working processor (%s → %s)", src_space, working_space
            )
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _get_working_to_display_proc(self, display: str, view: str):
        if not display or self._ocio_cfg is None:
            return None
        working_space = self._resolve_working_space() or self._comp_src_space
        if not working_space:
            return None
        key = ("work->disp", working_space, display, view)
        proc = self._ocio_proc_cache.get(key)
        if proc is not None:
            return proc
        try:
            from ...core.ocio_utils import make_display_processor

            proc = make_display_processor(
                self._ocio_cfg, working_space, display, view, exposure=0.0, gamma=1.0
            )
        except Exception:
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _ensure_viewer_display_proc(self, display: str, view: str) -> object | None:
        if not display or self._ocio_cfg is None:
            self._viewer_display_proc = None
            self._ec_exposure_prop = None
            self._ec_gamma_prop = None
            return None

        working_space = self._resolve_working_space() or self._comp_src_space
        if not working_space:
            self._viewer_display_proc = None
            self._ec_exposure_prop = None
            self._ec_gamma_prop = None
            return None

        if self._viewer_display_proc is not None and getattr(
            self, "_last_viewer_display", None
        ) == (working_space, display, view):
            return self._viewer_display_proc

        try:
            from ...core.ocio_utils import make_viewer_display_processor

            proc, exp_prop, _gamma_unused = make_viewer_display_processor(
                self._ocio_cfg, working_space, display, view
            )
        except Exception:
            proc, exp_prop = None, None

        self._viewer_display_proc = proc
        self._ec_exposure_prop = exp_prop
        self._ec_gamma_prop = None
        self._last_viewer_display = (working_space, display, view)

        if self._ec_exposure_prop is not None:
            self._ec_exposure_prop.setDouble(self._exposure_stops())

        return self._viewer_display_proc

    def _build_working_f32(self):
        import numpy as np

        if self._working_f32 is not None:
            return self._working_f32
        if self._comp_f32 is None:
            return None

        cpu = self._get_src_to_working_proc(self._comp_src_space)
        if cpu is None:
            self._working_f32 = self._comp_f32
            return self._working_f32

        try:
            import PyOpenColorIO as OCIO

            h, w = self._comp_f32.shape[:2]
            buf = np.ascontiguousarray(self._comp_f32.copy(), dtype=np.float32)
            cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
            self._working_f32 = buf
        except Exception:
            log.exception("GUI-thread src→working OCIO apply failed")
            self._working_f32 = self._comp_f32
        return self._working_f32

    def _is_shot_frame(self, frame: int) -> bool:
        if self._hooks is None:
            return True
        return not self._hooks.is_synthetic_frame(frame)

    def _apply_display_transform(self) -> None:
        import numpy as np

        working = self._build_working_f32()
        if working is None:
            return

        is_shot = self._is_shot_frame(self._current_frame)

        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            h, w = int(working.shape[0]), int(working.shape[1])
            if is_shot and self._hooks is not None:
                ov, ov_key = self._hooks.gpu_overlay_rgba(w, h, self._current_frame)
                self._gpu_plane.set_overlay_rgba(ov, key=ov_key)
            else:
                self._gpu_plane.set_overlay_rgba(None)
            self._gpu_plane.set_working_image(working)
            self._composed_working_f32 = None
            self._display_f32 = None
            return

        # ---- CPU fallback ----
        if working.dtype != np.float32:
            working = np.ascontiguousarray(working, dtype=np.float32)
        if is_shot and self._hooks is not None:
            composed = self._hooks.cpu_composite_overlays(working, self._current_frame)
        else:
            composed = working
        self._composed_working_f32 = composed
        if self._ocio_cfg is None:
            self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        idx = self._display_view_combo.currentIndex()
        if not (0 <= idx < len(self._display_view_pairs)):
            self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        display, view = self._display_view_pairs[idx]
        cpu = self._ensure_viewer_display_proc(display, view)
        if cpu is None:
            cpu = self._get_working_to_display_proc(display, view)
            if cpu is None:
                self._display_f32 = composed
                self._refresh_gain_gamma()
                return
            try:
                h, w = composed.shape[:2]
                pixels = np.ascontiguousarray(composed.reshape(-1, 3).copy())
                cpu.applyRGB(pixels)
                self._display_f32 = pixels.reshape(h, w, 3)
            except Exception:
                log.exception("Static working→display OCIO apply failed")
                self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        try:
            h, w = composed.shape[:2]
            pixels = np.ascontiguousarray(composed.reshape(-1, 3).copy())
            cpu.applyRGB(pixels)
            self._display_f32 = pixels.reshape(h, w, 3)
        except Exception:
            log.exception("Viewer EC working→display OCIO apply failed")
            self._display_f32 = composed

        self._paint_display_with_viewer_gamma(self._display_f32)

    def _invalidate_display_cache(self) -> None:
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display = None
        self._display_f32 = None
        self._sync_gpu_view_settings()
        self._apply_display_transform()

    def _refresh_gain_gamma(self) -> None:
        if self._use_gpu and self._gpu_plane is not None:
            self._gpu_plane.set_exposure_stops(self._exposure_stops())
            self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))
            return

        if self._display_f32 is None:
            return

        out = self._display_f32
        if self._ec_exposure_prop is None:
            gain = float(getattr(self, "_gain", 1.0))
            if gain != 1.0:
                out = out * gain
        self._paint_display_with_viewer_gamma(out)

    def _paint_display_with_viewer_gamma(self, rgb_f32) -> None:
        import numpy as np

        gamma = float(getattr(self, "_gamma", 1.0))
        out = rgb_f32
        if abs(gamma - 1.0) > 1e-6:
            exp = nuke_viewer_gamma_power(gamma)
            pos = np.maximum(out, 0.0)
            powered = np.power(pos, exp)
            out = np.where(out >= 0.0, powered, out)
        self._paint_display_buffer(out)

    def _paint_display_buffer(self, rgb_f32) -> None:
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap

        comp_u8 = np.ascontiguousarray((np.clip(rgb_f32, 0.0, 1.0) * 255 + 0.5).astype(np.uint8))

        fh, fw = comp_u8.shape[:2]
        bpl = (fw * 3 + 3) & ~3
        if bpl == fw * 3:
            buf = comp_u8
        else:
            buf = np.zeros((fh, bpl), dtype=np.uint8)
            flat = comp_u8.reshape(fh, -1)
            buf[:, : fw * 3] = flat
        qimg = QImage(buf.data, fw, fh, bpl, QImage.Format.Format_RGB888).copy()
        del buf
        dpr = float(self.devicePixelRatioF() or 1.0)
        if dpr > 1.01:
            qimg = qimg.scaled(
                int(fw * dpr + 0.5),
                int(fh * dpr + 0.5),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            qimg.setDevicePixelRatio(dpr)
        pix = QPixmap.fromImage(qimg)
        if dpr > 1.01:
            pix.setDevicePixelRatio(dpr)

        scene = self._preview._scene
        if self._preview_pixmap_item is not None:
            scene.removeItem(self._preview_pixmap_item)
        self._preview_pixmap_item = scene.addPixmap(pix)
        self._preview_pixmap_item.setZValue(0)

        rect = self._preview._frame_rect
        preview_w = max(1.0, float(rect.width()))
        preview_h = max(1.0, float(rect.height()))
        lw = max(1, int(round(pix.width() / max(dpr, 1.0))))
        lh = max(1, int(round(pix.height() / max(dpr, 1.0))))
        if lw > 0 and lh > 0:
            # Non-uniform scale: the fit rect is the display aspect, so a
            # squeezed plate is stretched for viewing and the pixels stay put.
            self._preview_pixmap_item.setTransform(
                QTransform.fromScale(preview_w / lw, preview_h / lh)
            )
            self._preview_pixmap_item.setPos(0, 0)

    def _reapply_display_with_ec(self) -> None:
        import numpy as np

        if self._use_gpu and self._gpu_plane is not None:
            self._gpu_plane.set_exposure_stops(self._exposure_stops())
            self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))
            return

        if self._composed_working_f32 is None or self._viewer_display_proc is None:
            self._apply_display_transform()
            return

        try:
            h, w = self._composed_working_f32.shape[:2]
            pixels = np.ascontiguousarray(self._composed_working_f32.reshape(-1, 3).copy())
            self._viewer_display_proc.applyRGB(pixels)
            self._display_f32 = pixels.reshape(h, w, 3)
        except Exception:
            self._display_f32 = self._composed_working_f32

        self._paint_display_with_viewer_gamma(self._display_f32)


__all__ = ["OverlayHooks", "SequencePlayer"]
