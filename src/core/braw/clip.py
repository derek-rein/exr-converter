"""High-level BRAW clip open / decode / metadata API."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .constants import BRAW_SRC_COLORSPACE_CANDIDATES, CLIP_META_KEYS, DECODE_FULL
from .native import clip_info_struct_type, get_lib, last_error, unavailable_reason


class BRAWUnavailableError(RuntimeError):
    """Raised when BRAW support is requested but the bridge/SDK is missing."""


class BRAWError(RuntimeError):
    """BRAW SDK / bridge operation failed."""


@dataclass(frozen=True)
class BRAWClipInfo:
    width: int
    height: int
    frame_count: int
    fps: float
    colorspace_hint: str
    sdk_version: str
    camera_type: str


class BRAWClip:
    """Open Blackmagic RAW clip (context manager).

    Decode is safe from one worker at a time. Do not share one instance across
    concurrent decoders without external locking.
    """

    def __init__(self, path: str | Path) -> None:
        lib = get_lib()
        if lib is None:
            raise BRAWUnavailableError(unavailable_reason())
        self.path = str(Path(path).resolve())
        self._lib = lib
        handle = self._lib.braw_bridge_open(self.path.encode("utf-8"))
        if not handle:
            raise BRAWError(last_error(self._lib) or f"Failed to open {self.path}")
        self._handle: Any = handle
        self._info = self._read_info()

    def _read_info(self) -> BRAWClipInfo:
        info = clip_info_struct_type()()
        rc = self._lib.braw_bridge_clip_info(self._handle, ctypes.byref(info))
        if rc != 0:
            raise BRAWError(last_error(self._lib) or "clip_info failed")
        hint = info.colorspace_hint.decode("utf-8", errors="replace").strip()
        ver = info.sdk_version.decode("utf-8", errors="replace").strip()
        cam = info.camera_type.decode("utf-8", errors="replace").strip()
        return BRAWClipInfo(
            width=int(info.width),
            height=int(info.height),
            frame_count=int(info.frame_count),
            fps=float(info.fps) if info.fps > 0 else 24.0,
            colorspace_hint=hint or BRAW_SRC_COLORSPACE_CANDIDATES[0],
            sdk_version=ver,
            camera_type=cam,
        )

    @property
    def info(self) -> BRAWClipInfo:
        return self._info

    def close(self) -> None:
        if self._handle and self._lib is not None:
            self._lib.braw_bridge_close(self._handle)
            self._handle = None

    def __enter__(self) -> BRAWClip:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def decode_frame(self, frame_index: int, *, mode: int = DECODE_FULL) -> np.ndarray:
        """Decode 0-based *frame_index* → float32 RGB ``(H, W, 3)`` Linear ACES AP0."""
        if not self._handle:
            raise BRAWError("Clip is closed")
        w = ctypes.c_uint32(0)
        h = ctypes.c_uint32(0)
        nbytes = self._lib.braw_bridge_decode_buffer_bytes(
            self._handle, int(mode), ctypes.byref(w), ctypes.byref(h)
        )
        if nbytes == 0 or w.value == 0 or h.value == 0:
            raise BRAWError(last_error(self._lib) or "invalid decode buffer size")

        arr = np.empty((int(h.value), int(w.value), 3), dtype=np.float32)
        flat = arr.reshape(-1)
        rc = self._lib.braw_bridge_decode_frame(
            self._handle,
            ctypes.c_uint32(frame_index),
            int(mode),
            flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            flat.nbytes,
            ctypes.byref(w),
            ctypes.byref(h),
        )
        if rc != 0:
            raise BRAWError(last_error(self._lib) or f"decode failed frame={frame_index} rc={rc}")
        if int(w.value) != arr.shape[1] or int(h.value) != arr.shape[0]:
            arr = flat[: int(h.value) * int(w.value) * 3].reshape((int(h.value), int(w.value), 3))
        return arr

    def metadata_string(self, key: str) -> str:
        if not self._handle:
            return ""
        buf = ctypes.create_string_buffer(1024)
        rc = self._lib.braw_bridge_metadata_string(self._handle, key.encode("utf-8"), buf, 1024)
        if rc != 1:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip()

    def timecode(self, frame_index: int = 0) -> str:
        if not self._handle:
            return ""
        buf = ctypes.create_string_buffer(64)
        rc = self._lib.braw_bridge_timecode(self._handle, ctypes.c_uint32(frame_index), buf, 64)
        if rc != 1:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip()

    def clip_metadata_dict(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in CLIP_META_KEYS:
            val = self.metadata_string(key)
            if val:
                out[key] = val
        info = self._info
        out.setdefault("image_width", str(info.width))
        out.setdefault("image_height", str(info.height))
        out.setdefault("framerate", f"{info.fps:.6f}".rstrip("0").rstrip("."))
        if info.sdk_version:
            out["sdk_version"] = info.sdk_version
        if info.colorspace_hint:
            out["colorspace"] = info.colorspace_hint
        tc = self.timecode(0)
        if tc:
            out["start_timecode"] = tc
        return out


def probe_braw(path: str | Path) -> tuple[int, int, float, int]:
    with BRAWClip(path) as clip:
        i = clip.info
        return i.width, i.height, i.fps, max(1, i.frame_count)


def braw_exr_attributes(path: str | Path, *, frame_index: int = 0) -> dict[str, str]:
    with BRAWClip(path) as clip:
        meta = clip.clip_metadata_dict()
        tc = clip.timecode(frame_index)
        if tc:
            meta["timecode"] = tc
    return {f"exrconverter:braw:{k}": v for k, v in meta.items() if v}
