"""Pane-sized preview targets, native scale steps, and a small frame cache.

Browser Preview asks each decoder for the smallest native resolution that still
covers the widget. Convert and the full viewer do not use this module.

The cache key is the file, its mtime, the frame, and a power-of-two size
bucket. Resizing the pane inside one bucket does not decode again.
"""

from __future__ import annotations

import math
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from .pixel_aspect import display_size

# Current player preview is half-res for these SDKs. Browser Preview may step
# down from that, and does not step up (a full debayer would be slower than
# the preview the app already uses).
R3D_PREVIEW_CEILING = 0.5
BRAW_PREVIEW_CEILING = 0.5

# (linear scale, short name). Names map to bridge decode modes below.
R3D_PREVIEW_LEVELS: tuple[tuple[float, str], ...] = (
    (1.0, "full"),
    (0.5, "half"),
    (0.25, "quarter"),
    (0.125, "eighth"),
    (0.0625, "sixteenth"),
)
BRAW_PREVIEW_LEVELS: tuple[tuple[float, str], ...] = (
    (1.0, "full"),
    (0.5, "half"),
    (0.25, "quarter"),
    (0.125, "eighth"),
)

# Bridge C ABI values. Tests lock these to src.core.r3d.constants /
# src.core.braw.constants so a ladder change cannot drift.
R3D_PREVIEW_MODES: dict[str, int] = {
    "full": 0,  # DECODE_FULL_PREMIUM
    "half": 2,  # DECODE_HALF_GOOD (same size as half premium, cheaper)
    "quarter": 3,  # DECODE_QUARTER_GOOD
    "eighth": 4,  # DECODE_EIGHTH_GOOD
    "sixteenth": 5,  # DECODE_SIXTEENTH_GOOD
}
BRAW_PREVIEW_MODES: dict[str, int] = {
    "full": 0,  # DECODE_FULL
    "half": 1,  # DECODE_HALF
    "quarter": 2,  # DECODE_QUARTER
    "eighth": 3,  # DECODE_EIGHTH
}

_MAX_BUCKET = 16384
_DEFAULT_CACHE_ENTRIES = 48
_DEFAULT_CACHE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class ProxyTarget:
    """Storage size to request, and the power-of-two bucket it belongs to."""

    bucket: int
    width: int
    height: int


def bucket_edge(edge: int) -> int:
    """Smallest power of two that is still >= *edge* (capped)."""
    n = max(1, int(edge))
    power = 1
    while power < n and power < _MAX_BUCKET:
        power <<= 1
    return power


def proxy_storage_target(
    src_w: int,
    src_h: int,
    widget_w: int,
    widget_h: int,
    device_pixel_ratio: float,
    pixel_aspect: float,
) -> ProxyTarget | None:
    """Storage resolution that covers the widget after squeeze and screen density.

    *pixel_aspect* is the display pixel aspect from :mod:`src.core.pixel_aspect`
    (2.0 means each stored pixel is drawn twice as wide). The long edge is
    rounded up to a power of two so a small resize stays on the same decode.
    The result is never larger than the source.
    """
    src_w = int(src_w)
    src_h = int(src_h)
    if src_w <= 0 or src_h <= 0 or widget_w <= 0 or widget_h <= 0:
        return None
    dpr = float(device_pixel_ratio) if device_pixel_ratio and device_pixel_ratio > 0 else 1.0
    box_w = max(1, int(math.ceil(widget_w * dpr)))
    box_h = max(1, int(math.ceil(widget_h * dpr)))
    par = float(pixel_aspect) if pixel_aspect and pixel_aspect > 0 else 1.0
    disp_w, disp_h = display_size(src_w, src_h, par)
    fit = min(box_w / disp_w, box_h / disp_h, 1.0)
    fit_w = max(1, int(math.ceil(disp_w * fit - 1e-9)))
    fit_h = max(1, int(math.ceil(disp_h * fit - 1e-9)))
    storage_w = min(src_w, max(1, int(math.ceil(fit_w / par - 1e-9))))
    storage_h = min(src_h, fit_h)
    bucket = bucket_edge(max(storage_w, storage_h))
    width, height = _canonical_storage(src_w, src_h, bucket)
    return ProxyTarget(bucket=bucket, width=width, height=height)


def _canonical_storage(src_w: int, src_h: int, bucket: int) -> tuple[int, int]:
    """Source aspect at *bucket* on the long edge, capped at the source."""
    edge = min(int(bucket), max(src_w, src_h))
    if src_w >= src_h:
        width = edge
        height = max(1, int(round(src_h * edge / src_w)))
    else:
        height = edge
        width = max(1, int(round(src_w * edge / src_h)))
    return min(width, src_w), min(height, src_h)


def pick_named_level(
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
    levels: tuple[tuple[float, str], ...],
    *,
    ceiling: float | None = None,
) -> str:
    """Smallest *levels* entry whose output still covers *target*.

    *ceiling* drops scales above it (the resolution the player already uses)
    so a large pane cannot pick a more expensive decode than today. When
    nothing under the ceiling covers the target, the largest allowed scale
    is returned.
    """
    src_w = max(1, int(src_w))
    src_h = max(1, int(src_h))
    target_w = max(1, int(target_w))
    target_h = max(1, int(target_h))
    allowed = [
        (scale, name) for scale, name in levels if ceiling is None or scale <= ceiling + 1e-6
    ]
    if not allowed:
        allowed = list(levels)
    covering: list[tuple[int, float, str]] = []
    for scale, name in allowed:
        out_w = max(1, int(round(src_w * scale)))
        out_h = max(1, int(round(src_h * scale)))
        if out_w >= target_w and out_h >= target_h:
            covering.append((out_w * out_h, scale, name))
    if covering:
        covering.sort()
        return covering[0][2]
    allowed.sort(key=lambda item: item[0], reverse=True)
    return allowed[0][1]


def r3d_preview_mode(src_w: int, src_h: int, target_w: int, target_h: int) -> int:
    """R3D / N-RAW decode mode for a browser preview of this target."""
    name = pick_named_level(
        src_w,
        src_h,
        target_w,
        target_h,
        R3D_PREVIEW_LEVELS,
        ceiling=R3D_PREVIEW_CEILING,
    )
    return R3D_PREVIEW_MODES[name]


def braw_preview_mode(src_w: int, src_h: int, target_w: int, target_h: int) -> int:
    """Blackmagic RAW decode mode for a browser preview of this target."""
    name = pick_named_level(
        src_w,
        src_h,
        target_w,
        target_h,
        BRAW_PREVIEW_LEVELS,
        ceiling=BRAW_PREVIEW_CEILING,
    )
    return BRAW_PREVIEW_MODES[name]


def pick_covering_level(
    levels: tuple[tuple[int, int], ...] | list[tuple[int, int]],
    target_w: int,
    target_h: int,
) -> int:
    """Index of the mip / ripmap level with the fewest pixels that still covers.

    *levels[0]* is the full image. When nothing covers *target*, return 0.
    """
    target_w = max(1, int(target_w))
    target_h = max(1, int(target_h))
    best: tuple[int, int] | None = None
    for index, (width, height) in enumerate(levels):
        if int(width) >= target_w and int(height) >= target_h:
            pixels = int(width) * int(height)
            if best is None or pixels < best[0] or (pixels == best[0] and index > best[1]):
                best = (pixels, index)
    if best is None:
        return 0
    return best[1]


def preview_level_key(
    path: str,
    src_w: int,
    src_h: int,
    target_w: int,
    target_h: int,
) -> tuple[str, int | str]:
    """Identity of the native decode a resize would select.

    Equal keys mean a new pane size does not need another decode.
    """
    suffix = os.path.splitext(path)[1].lower()
    if suffix in {".r3d", ".nev"}:
        return (
            "r3d",
            pick_named_level(
                src_w,
                src_h,
                target_w,
                target_h,
                R3D_PREVIEW_LEVELS,
                ceiling=R3D_PREVIEW_CEILING,
            ),
        )
    if suffix == ".braw":
        return (
            "braw",
            pick_named_level(
                src_w,
                src_h,
                target_w,
                target_h,
                BRAW_PREVIEW_LEVELS,
                ceiling=BRAW_PREVIEW_CEILING,
            ),
        )
    if suffix == ".dng":
        half_w = max(1, int(src_w) // 2)
        half_h = max(1, int(src_h) // 2)
        kind = "half" if int(target_w) <= half_w and int(target_h) <= half_h else "full"
        return ("dng", kind)
    return ("bucket", bucket_edge(max(int(target_w), int(target_h))))


def file_mtime_ns(path: str) -> int:
    try:
        return int(os.stat(path).st_mtime_ns)
    except OSError:
        return 0


def preview_cache_key(
    path: str,
    frame: int,
    bucket: int,
    mtime_ns: int | None = None,
) -> tuple[str, int, int, int]:
    """``(path, mtime, frame, bucket)``. A new mtime or bucket is a miss."""
    if mtime_ns is None:
        mtime_ns = file_mtime_ns(path)
    norm = os.path.normcase(os.path.abspath(path))
    return (norm, int(mtime_ns), int(frame), int(bucket))


class PreviewCache:
    """LRU of decoded preview frames, safe to call from worker threads."""

    def __init__(
        self,
        max_entries: int = _DEFAULT_CACHE_ENTRIES,
        max_bytes: int = _DEFAULT_CACHE_BYTES,
    ) -> None:
        self._max_entries = max(1, int(max_entries))
        self._max_bytes = max(1, int(max_bytes))
        self._lock = threading.Lock()
        self._store: OrderedDict[tuple[str, int, int, int], np.ndarray] = OrderedDict()
        self._bytes = 0

    def get(self, key: tuple[str, int, int, int]) -> np.ndarray | None:
        with self._lock:
            found = self._store.get(key)
            if found is None:
                return None
            self._store.move_to_end(key)
            return found

    def put(self, key: tuple[str, int, int, int], rgb: np.ndarray) -> None:
        arr = np.ascontiguousarray(rgb)
        with self._lock:
            old = self._store.pop(key, None)
            if old is not None:
                self._bytes -= int(old.nbytes)
            self._store[key] = arr
            self._bytes += int(arr.nbytes)
            self._evict()

    def _evict(self) -> None:
        while self._store and (
            len(self._store) > self._max_entries or self._bytes > self._max_bytes
        ):
            _key, arr = self._store.popitem(last=False)
            self._bytes -= int(arr.nbytes)

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


_cache: PreviewCache | None = None
_cache_lock = threading.Lock()


def get_preview_cache() -> PreviewCache:
    """Process-wide preview cache (browser only)."""
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is None:
            _cache = PreviewCache()
    return _cache
