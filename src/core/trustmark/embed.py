"""Embed a TrustMark Q watermark with ONNX Runtime. No PyTorch.

The encoder sees a 256x256 copy of the frame. The residual (stego minus
cover, with the per-channel mean removed) is scaled back up and added to
the full-resolution RGB. That is the TrustMark resize path, and it is what
the official decoder expects.

Pass display-referred RGB, after any OCIO view transform. Video proxies do
not run a view transform (they keep the source look), so the proxy pipeline
calls this on the resized frame the codec is about to encode.
"""

from __future__ import annotations

import os
import threading

import numpy as np
import onnxruntime as ort

from .models import ENCODER_FILENAME, LogCallback, ensure_model
from .payload import encode_text_tensor, normalize_strength, normalize_watermark_text
from .resample import content_region, resize_bilinear, rgb_u8_to_model

_ENC_IMAGE = "onnx::Concat_0"
_ENC_BITS = "onnx::Gemm_1"
_SESSION: ort.InferenceSession | None = None
_LOCK = threading.Lock()


def _as_array(value: object) -> np.ndarray:
    if isinstance(value, np.ndarray):
        return value
    raise RuntimeError("TrustMark encoder returned an unexpected output.")


def _encoder_session(log: LogCallback | None) -> ort.InferenceSession:
    global _SESSION
    with _LOCK:
        if _SESSION is None:
            path = ensure_model(ENCODER_FILENAME, log=log)
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


class FrameWatermark:
    """Reusable encoder state for one payload. Apply it to every frame."""

    def __init__(self, text: str, strength: float = 1.0, log: LogCallback | None = None) -> None:
        self.text = normalize_watermark_text(text)
        self.strength = normalize_strength(strength)
        self._packet = encode_text_tensor(self.text)
        self._session = _encoder_session(log)

    def apply(self, rgb: np.ndarray) -> np.ndarray:
        """Return a new uint8 RGB frame with the watermark added."""
        if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
            raise ValueError("Watermark expects uint8 HxWx3 RGB.")
        height, width = int(rgb.shape[0]), int(rgb.shape[1])
        top, left, region_h, region_w = content_region(height, width)
        crop = rgb[top : top + region_h, left : left + region_w]
        cover = rgb_u8_to_model(crop)
        with _LOCK:
            outputs = self._session.run(None, {_ENC_IMAGE: cover, _ENC_BITS: self._packet})
            stego = _as_array(outputs[0])
        residual = np.clip(stego, -1.0, 1.0) - cover
        residual -= residual.mean(axis=(2, 3), keepdims=True)
        upscaled = resize_bilinear(np.transpose(residual[0], (1, 2, 0)), region_h, region_w)
        base = crop.astype(np.float32) / 127.5 - 1.0
        mixed = np.clip(base + upscaled * self.strength, -1.0, 1.0)
        marked = np.clip(np.rint((mixed + 1.0) * 127.5), 0, 255).astype(np.uint8)
        if region_h == height and region_w == width:
            return marked
        out = np.array(rgb, copy=True)
        out[top : top + region_h, left : left + region_w] = marked
        return out
