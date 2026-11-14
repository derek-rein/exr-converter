"""Video frame prefetch for the sequence player (PyAV decode → FrameCache).

Single-threaded decoder: PyAV containers are not safe for parallel consumers.

Playback / scrub strategy (FFmpeg / PyAV best practice)
-------------------------------------------------------
- ``container.seek(pts, stream=…, backward=True, any_frame=False)`` lands on the
  previous **keyframe**, not the exact target. Callers must **decode forward**
  until presentation time / frame index matches.
- Keep a long-lived ``decode()`` iterator so multi-frame packets / B-frame
  reorder buffers are not discarded mid-GOP.
- Forward play stays sequential (no re-seek) when the next requested index is
  near the decoder tip.
- Scrub lookback is kept small: every reverse jump costs a keyframe seek +
  decode-forward.
"""

from __future__ import annotations

import logging
import threading
import weakref
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from ..core.preview_proxy import get_preview_cache, preview_cache_key
from .cache_prefs import DEFAULT_LOOKBACK_RATIO
from .frame_cache import FrameCache

log = logging.getLogger(__name__)

FrameTransform = Callable[[np.ndarray], "np.ndarray | None"]

_HARD_LOOKAHEAD_FRAMES = 120
_MIN_LOOKAHEAD_FRAMES = 8
# Drop stale scrub work when the playhead moved far from a queued frame.
_FORWARD_DECODE_LIMIT = 48
# Scrubbing: avoid reverse seeks that thrash the GOP decoder.
_SCRUB_LOOKBACK_FRAMES = 2


def _apply_transform(
    rgb: np.ndarray,
    transform: FrameTransform | None,
) -> np.ndarray | None:
    if transform is None:
        # No OCIO: cache source-referred float. The viewer treats float as
        # working space only when a transform was configured.
        return np.ascontiguousarray(rgb, dtype=np.float16)
    try:
        out = transform(rgb)
    except Exception:
        log.debug("frame_transform raised", exc_info=True)
        return None
    return out


def _decode_one(
    path: str,
    frame: int,
    transform: FrameTransform | None,
    decoder_holder: dict,
    lock: threading.Lock,
    fps: float = 0.0,
    target: tuple[int, int, int] | None = None,
    on_draft: Callable[[np.ndarray], None] | None = None,
    reduced_flag: dict[str, bool] | None = None,
) -> np.ndarray | None:
    """Worker entry: decode one frame via a shared locked decoder."""
    from ..core.frame_source import open_preview_decoder

    target_size = (target[0], target[1]) if target is not None else None
    bucket = target[2] if target is not None else 0
    if target is not None:
        hit = get_preview_cache().get(preview_cache_key(path, frame, bucket))
        if hit is not None:
            if reduced_flag is not None:
                reduced_flag["reduced"] = True
            return _apply_transform(hit, transform)

    with lock:
        dec = decoder_holder.get("dec")
        if dec is None or getattr(dec, "path", None) != path:
            if dec is not None:
                dec.close()
            try:
                dec = open_preview_decoder(path, fps=fps, target_size=target_size)
            except Exception:
                log.exception("Failed to open video for prefetch: %s", path)
                decoder_holder["dec"] = None
                return None
            decoder_holder["dec"] = dec
        try:
            rgb = dec.get_frame(frame)
        except Exception:
            log.debug("get_frame failed frame=%s", frame, exc_info=True)
            return None
    if rgb is None:
        return None
    if reduced_flag is not None:
        source = getattr(dec, "source_size", None)
        if source is None:
            reduced_flag["reduced"] = target is not None
        else:
            reduced_flag["reduced"] = int(rgb.shape[1]) < int(source[0]) or int(rgb.shape[0]) < int(
                source[1]
            )
    if target is not None:
        try:
            get_preview_cache().put(preview_cache_key(path, frame, bucket), rgb)
        except Exception:
            log.debug("preview cache put failed", exc_info=True)
    # on_draft is unused for video and camera RAW: a second decode would make
    # the sharp frame later than the single reduced decode.
    del on_draft
    return _apply_transform(rgb, transform)


class VideoPrefetchService(QObject):
    """Priority video decoder feeding :class:`FrameCache` (1 worker)."""

    frame_loaded = Signal(int, object)
    _delivery_ready = Signal(int, object, int)

    def __init__(
        self,
        path: str,
        cache: FrameCache,
        shot_frames: list[int],
        *,
        fps: float = 0.0,
        lookback_ratio: float = DEFAULT_LOOKBACK_RATIO,
        frame_transform: FrameTransform | None = None,
        single_frame: bool = False,
        preview_target: tuple[int, int, int] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._cache = cache
        self._shot_frames = sorted(shot_frames)
        self._frame_index = {f: i for i, f in enumerate(self._shot_frames)}
        self._frame_set = set(self._shot_frames)
        # Same rate as SequencePlayer timeline / probe_video (seek clock).
        self._fps = float(fps) if fps and fps > 0 else 0.0
        # lookback_ratio kept for API parity with EXR; scrub uses a fixed small
        # reverse window (seek cost) instead of a large lookback ratio.
        self._lookback_ratio = max(0.0, min(1.0, lookback_ratio))
        self._frame_transform = frame_transform
        self._single_frame = bool(single_frame)
        self._preview_target = preview_target
        self._full_res_only = False
        self._draft_once = bool(single_frame and preview_target)

        self._current = self._shot_frames[0] if self._shot_frames else 1
        self._playing = False
        self._paused = False
        self._generation = 0
        # Set by shutdown(): the pool is gone, so nothing may be scheduled again.
        self._closed = False
        # Latest scrub / play target — drop stale queue work when this moves.
        self._wanted: int | None = None

        self._queue: deque[int] = deque()
        self._queued: set[int] = set()
        self._inflight: dict[int, Future] = {}

        self._decoder_lock = threading.Lock()
        self._decoder_holder: dict = {"dec": None}

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vid-prefetch")

        self._kick_timer = QTimer(self)
        self._kick_timer.setSingleShot(True)
        self._kick_timer.setInterval(0)
        self._kick_timer.timeout.connect(self._fill_slots)
        self._delivery_ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def shutdown(self) -> None:
        self._closed = True
        self._generation += 1
        self._kick_timer.stop()
        self._queue.clear()
        self._queued.clear()
        for fut in self._inflight.values():
            fut.cancel()
        self._inflight.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._decoder_lock:
            dec = self._decoder_holder.get("dec")
            if dec is not None:
                dec.close()
            self._decoder_holder["dec"] = None

    @property
    def full_res_only(self) -> bool:
        """True once a frame had no cheaper decode than the full raster."""
        return self._full_res_only

    def set_preview_target(self, width: int, height: int, bucket: int) -> None:
        """Switch browser-preview resolution and drop the open decoder."""
        target = (int(width), int(height), int(bucket))
        if target == self._preview_target:
            return
        self._preview_target = target
        self._generation += 1
        self._draft_once = False
        with self._decoder_lock:
            dec = self._decoder_holder.get("dec")
            if dec is not None:
                try:
                    dec.close()
                except Exception:
                    log.debug("preview decoder close failed", exc_info=True)
            self._decoder_holder["dec"] = None
        self._rebuild_queue()
        self._schedule_kick()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._schedule_kick()

    def set_context(self, current_frame: int, *, playing: bool = False) -> None:
        self._current = current_frame
        self._playing = playing
        self._wanted = current_frame
        self._rebuild_queue()
        self._schedule_kick()

    def request_immediate(self, frame: int) -> bool:
        if frame not in self._frame_set:
            return False
        self._wanted = frame
        if self._cache.contains(frame):
            return True
        if frame in self._inflight:
            return True
        # Scrub coalesce: drop other pending work so the worker serves the
        # playhead next (in-flight decode still finishes — cannot cancel mid-GOP).
        if not self._playing:
            self._queue.clear()
            self._queued.clear()
        elif frame in self._queue:
            self._queue.remove(frame)
            self._queued.discard(frame)
        self._queue.appendleft(frame)
        self._queued.add(frame)
        self._schedule_kick()
        return True

    def set_frame_transform(self, transform: FrameTransform | None) -> None:
        self._frame_transform = transform

    def _cache_capacity(self) -> int:
        sample_bytes = self._cache.estimate_frame_bytes()
        budget_bytes = self._cache.budget_bytes
        if sample_bytes > 0 and budget_bytes > 0:
            return max(_MIN_LOOKAHEAD_FRAMES, budget_bytes // sample_bytes)
        return _MIN_LOOKAHEAD_FRAMES

    def _lookback_ahead_counts(self) -> tuple[int, int]:
        if self._single_frame:
            return 0, 0
        capacity = min(self._cache_capacity(), _HARD_LOOKAHEAD_FRAMES + 32)
        if self._playing:
            return 0, min(capacity, _HARD_LOOKAHEAD_FRAMES)
        # Scrub: tiny lookback (reverse seeks are expensive), prioritise ahead
        # so the decoder can stay sequential after landing on the playhead.
        lookback = min(_SCRUB_LOOKBACK_FRAMES, max(0, capacity - 1))
        lookahead = max(1, capacity - lookback)
        return lookback, min(lookahead, _HARD_LOOKAHEAD_FRAMES)

    def _frame_at_offset(self, start_frame: int, offset: int) -> int | None:
        idx = self._frame_index.get(start_frame)
        if idx is None:
            return None
        n = len(self._shot_frames)
        if n == 0:
            return None
        j = idx + offset
        if self._playing:
            # Looping transport: warm the wrap target while playing.
            return self._shot_frames[j % n]
        # Scrub: no wrap — reverse/forward seeks across the loop point thrash.
        if j < 0 or j >= n:
            return None
        return self._shot_frames[j]

    def _rebuild_queue(self) -> None:
        if self._paused:
            self._queue.clear()
            self._queued.clear()
            return
        anchor = self._current
        if anchor not in self._frame_set:
            anchor = self._shot_frames[0] if self._shot_frames else anchor
        lookback_n, lookahead_n = self._lookback_ahead_counts()
        cached_snapshot = self._cache.cached_frames()
        inflight_snapshot = set(self._inflight.keys())
        want: list[int] = []
        want_set: set[int] = set()

        def add(frame: int | None) -> None:
            if frame is None or frame in want_set:
                return
            if frame in cached_snapshot or frame in inflight_snapshot:
                return
            want.append(frame)
            want_set.add(frame)

        add(anchor)
        for offset in range(1, lookahead_n + 1):
            add(self._frame_at_offset(anchor, offset))
        for offset in range(1, lookback_n + 1):
            add(self._frame_at_offset(anchor, -offset))
        self._queue = deque(want)
        self._queued = want_set

    def _schedule_kick(self) -> None:
        if self._closed:
            return
        if not self._kick_timer.isActive():
            self._kick_timer.start()

    def _fill_slots(self) -> None:
        if self._closed or self._paused:
            return
        gen = self._generation
        capacity = self._cache_capacity()
        cached_count = len(self._cache.cached_frames())
        while len(self._inflight) < 1 and self._queue:
            if gen != self._generation:
                return
            if cached_count + len(self._inflight) >= capacity:
                return
            frame = self._queue.popleft()
            self._queued.discard(frame)
            if self._cache.contains(frame) or frame in self._inflight:
                continue
            # While scrubbing, skip stale queue entries far from the playhead
            # so a fast drag does not backfill abandoned positions first.
            if (
                not self._playing
                and self._wanted is not None
                and abs(frame - self._wanted) > _FORWARD_DECODE_LIMIT
            ):
                continue
            want_draft = self._draft_once
            if want_draft:
                self._draft_once = False
            reduced_flag: dict[str, bool] = {"reduced": True}

            def _on_draft(rgb: np.ndarray, fr: int = frame, g: int = gen) -> None:
                try:
                    self._delivery_ready.emit(fr, ("draft", rgb), g)
                except RuntimeError:
                    return

            fut = self._pool.submit(
                _decode_one,
                self._path,
                frame,
                self._frame_transform,
                self._decoder_holder,
                self._decoder_lock,
                self._fps,
                self._preview_target,
                _on_draft if want_draft else None,
                reduced_flag,
            )
            self._inflight[frame] = fut
            # Weak ref: the pool thread must never hold the last reference, or the
            # QObject (and its QTimer) is destroyed off the GUI thread and crashes.
            ref = weakref.ref(self)

            def _done(
                f: Future,
                fr: int = frame,
                g: int = gen,
                ref=ref,
                flag: dict[str, bool] = reduced_flag,
            ) -> None:
                svc = ref()
                if svc is not None:
                    svc._on_done(fr, f, g, flag)

            fut.add_done_callback(_done)

    def _on_done(
        self,
        frame: int,
        fut: Future,
        generation: int,
        reduced_flag: dict[str, bool] | None = None,
    ) -> None:
        # A stale result still goes to _deliver: it drops the pixels but
        # releases the in-flight slot, or prefetch stalls for good.
        rgb = None
        if generation == self._generation:
            try:
                rgb = fut.result()
            except Exception:
                rgb = None
            if reduced_flag is not None and not reduced_flag.get("reduced", True):
                self._full_res_only = True
        try:
            self._delivery_ready.emit(frame, rgb, generation)
        except RuntimeError:
            return

    def _deliver(self, frame: int, payload: object, generation: int) -> None:
        if isinstance(payload, tuple) and len(payload) == 2 and payload[0] == "draft":
            if generation != self._generation:
                return
            image = payload[1]
            if image is not None:
                self.frame_loaded.emit(frame, image)
            return
        rgb = payload if isinstance(payload, np.ndarray) else None
        self._inflight.pop(frame, None)
        if generation != self._generation:
            self._schedule_kick()
            return
        had_sample_before = self._cache.estimate_frame_bytes() > 0
        if rgb is not None:
            self._cache.put(frame, rgb)
        self.frame_loaded.emit(frame, rgb)
        if not had_sample_before and rgb is not None:
            self._rebuild_queue()
        self._schedule_kick()


__all__ = ["VideoPrefetchService"]
