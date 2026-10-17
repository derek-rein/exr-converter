"""Read a TrustMark Q watermark back from RGB frames or a video file.

This loads ``decoder_Q.onnx``, which the application itself never ships.
``scripts/trustmark_detect.py`` is the supported way to download it.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
import onnxruntime as ort

from ..video import av_frame_to_rgb_ndarray
from .models import DECODER_FILENAME, LogCallback, ensure_model
from .payload import decode_text_packet, logits_to_packet, majority_packet
from .resample import content_region, rgb_u8_to_model

_DEC_IMAGE = "image"
_SESSION: ort.InferenceSession | None = None
_LOCK = threading.Lock()


def _as_array(value: object) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value
    raise RuntimeError("TrustMark decoder returned an unexpected output.")


def _decoder_session(log: LogCallback | None) -> ort.InferenceSession:
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            path = ensure_model(DECODER_FILENAME, log=log)
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            options.intra_op_num_threads = min(4, os.cpu_count() or 1)
            options.log_severity_level = 3
            _SESSION = ort.InferenceSession(
                str(path),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        return _SESSION


def decode_rgb(rgb: np.ndarray, *, log: LogCallback | None = None) -> tuple[str, bool, int]:
    """Decode one uint8 RGB frame. Returns ``(text, ok, bit_flips)``."""
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise ValueError("TrustMark decode expects uint8 HxWx3 RGB.")
    height, width = int(rgb.shape[0]), int(rgb.shape[1])
    top, left, region_h, region_w = content_region(height, width)
    crop = rgb[top : top + region_h, left : left + region_w]
    image = rgb_u8_to_model(crop)
    session = _decoder_session(log)
    with _LOCK:
        logits = _as_array(session.run(None, {_DEC_IMAGE: image})[0])
    return decode_text_packet(logits_to_packet(logits))


def _sample_indices(total: int, samples: int) -> list[int]:
    count = max(1, min(samples, total))
    if count == 1:
        raw = [total // 2]
    else:
        raw = [int(round(i * (total - 1) / (count - 1))) for i in range(count)]
    indices: list[int] = []
    for index in raw:
        if index not in indices:
            indices.append(index)
    return indices


def _count_frames(path: Path) -> int:
    container = av.open(str(path))
    try:
        stream = container.streams.video[0]
        known = int(stream.frames or 0)
        if known > 0:
            return known
        return sum(1 for _ in container.decode(stream))
    finally:
        container.close()


def sample_rgb_frames(path: str | Path, samples: int) -> list[np.ndarray]:
    """Evenly spaced uint8 RGB frames. Stops once the last sample is read."""
    src = Path(path)
    total = _count_frames(src)
    if total <= 0:
        raise RuntimeError(f"No video frames in {src.name}.")
    wanted = _sample_indices(total, samples)
    needed = set(wanted)
    last = max(wanted)
    found: dict[int, np.ndarray] = {}
    container = av.open(str(src))
    try:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        for index, frame in enumerate(container.decode(stream)):
            if index in needed:
                found[index] = av_frame_to_rgb_ndarray(frame, "rgb24")
            if index >= last:
                break
    finally:
        container.close()
    return [found[index] for index in wanted if index in found]


@dataclass(frozen=True)
class WatermarkRead:
    """Result of sampling a video and majority-voting the raw bits."""

    text: str
    detected: bool
    frames: int
    bitflips: int


def detect_video(
    path: str | Path,
    *,
    samples: int = 9,
    log: LogCallback | None = None,
) -> WatermarkRead:
    """Sample frames, majority-vote bits, then BCH-decode the payload."""
    if samples < 1:
        raise ValueError("Sample count must be at least 1.")
    frames = sample_rgb_frames(path, samples)
    if not frames:
        raise RuntimeError(f"No video frames could be read from {path}.")
    packets: list[str] = []
    session = _decoder_session(log)
    for rgb in frames:
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
        top, left, region_h, region_w = content_region(height, width)
        crop = rgb[top : top + region_h, left : left + region_w]
        image = rgb_u8_to_model(crop)
        with _LOCK:
            logits = _as_array(session.run(None, {_DEC_IMAGE: image})[0])
        packets.append(logits_to_packet(logits))
    text, ok, flips = decode_text_packet(majority_packet(packets))
    return WatermarkRead(text=text, detected=ok, frames=len(packets), bitflips=flips)
