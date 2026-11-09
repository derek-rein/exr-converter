"""Hardware video decode (H.264 / HEVC / AV1 / VP9) through PyAV's ``hwaccel``.

:func:`open_input` opens a file like ``av.open`` and, in :data:`DECODER_AUTO`
mode, decodes on the GPU when this machine can: NVDEC (``cuda``), D3D11 /
DXVA2 on Windows (any vendor), VAAPI or Quick Sync on Linux, VideoToolbox on
macOS. Only 8- and 10-bit 4:2:0 streams qualify. Hardware decoders do not
take 4:2:2 / 4:4:4 or 12-bit, so those stay in software, as does everything
that is not one of the codecs above (ProRes, DNxHR, MJPEG, …).

Before committing to a device, one frame is decoded with it. That catches a
driver or profile the GPU cannot decode, and the result is cached per
codec / format / size / device. Any failure falls back to the software
container, so a machine without a usable GPU decodes as it always did.

Decoded YUV is bit-identical to software. Hardware returns 8-bit frames as
``nv12``, and swscale converts that to RGB about one 8-bit level apart from
``yuv420p``, so :func:`normalize_frame` puts ``nv12`` back to ``yuv420p``
(an exact plane split). Call it on frames from a hardware-opened container.
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

import av

from .constants import DECODER_AUTO, DECODER_MODES

log = logging.getLogger(__name__)

# libav codec name → decodable on a GPU (as 4:2:0, 8 or 10 bit).
_HW_CODECS = frozenset({"h264", "hevc", "av1", "vp9"})
_HW_PIX_FMTS = frozenset({"yuv420p", "yuv420p10le", "yuvj420p"})

# Probe order per platform. The first device that decodes a frame wins.
_DEVICES: dict[str, tuple[str, ...]] = {
    "win32": ("cuda", "d3d11va", "dxva2"),
    "linux": ("cuda", "vaapi", "qsv"),
    "darwin": ("videotoolbox",),
}

_mode = DECODER_AUTO
_lock = threading.Lock()
_probed: dict[tuple[str, str, int, int, str], bool] = {}
_logged: set[str] = set()


def set_decode_mode(mode: str) -> None:
    """Process-wide decode mode: ``auto`` (GPU when possible) or ``software``."""
    global _mode
    _mode = mode if mode in dict(DECODER_MODES) else DECODER_AUTO


def decode_mode() -> str:
    return _mode


def candidate_devices() -> list[str]:
    """Hardware device types to try here, in order (only those this FFmpeg build has)."""
    try:
        from av.codec.hwaccel import hwdevices_available

        have = set(hwdevices_available())
    except Exception:
        return []
    return [d for d in _DEVICES.get(sys.platform, ()) if d in have]


def _eligible(stream: Any) -> tuple[str, str, int, int] | None:
    ctx = stream.codec_context
    name = str(ctx.name or "")
    pix_fmt = str(getattr(ctx, "pix_fmt", "") or "")
    if name not in _HW_CODECS or pix_fmt not in _HW_PIX_FMTS:
        return None
    return name, pix_fmt, int(ctx.width or 0), int(ctx.height or 0)


def _first_frame_decodes(path: str, device: str, options: dict[str, str] | None) -> bool:
    from av.codec.hwaccel import HWAccel

    try:
        with av.open(
            path,
            options=options,
            hwaccel=HWAccel(device, allow_software_fallback=False),
        ) as c:
            stream = c.streams.video[0]
            for _ in c.decode(stream):
                return True
    except Exception:
        log.debug("Hardware decode probe failed: %s on %s", device, path, exc_info=True)
    return False


def open_input(
    path: str, *, options: dict[str, str] | None = None, log_fn: Any = None
) -> tuple[Any, str]:
    """``(container, device)``. *device* is ``""`` for software decode.

    Opens *path* like ``av.open``; in auto mode an eligible stream is reopened
    with a GPU decoder that is known to decode it.
    """
    soft = av.open(path, options=options) if options else av.open(path)
    if _mode != DECODER_AUTO or not soft.streams.video:
        return soft, ""
    key = _eligible(soft.streams.video[0])
    devices = candidate_devices() if key else []
    if key is None or not devices:
        return soft, ""

    from av.codec.hwaccel import HWAccel

    for device in devices:
        pkey = (*key, device)
        with _lock:
            ok = _probed.get(pkey)
        if ok is None:
            ok = _first_frame_decodes(path, device, options)
            with _lock:
                _probed[pkey] = ok
        if not ok:
            continue
        try:
            hw = av.open(
                path,
                options=options,
                hwaccel=HWAccel(device, allow_software_fallback=False),
            )
        except Exception:
            log.debug("Hardware decode open failed: %s", device, exc_info=True)
            continue
        soft.close()
        if log_fn and device not in _logged:
            _logged.add(device)
            log_fn(f"Decoding {key[0]} on the GPU ({device}).")
        return hw, device
    return soft, ""


def normalize_frame(frame: Any, device: str) -> Any:
    """Undo the hardware ``nv12`` layout so RGB conversion matches software decode."""
    if device and frame.format.name == "nv12":
        return frame.reformat(format="yuv420p")
    return frame


def clear_probe_cache() -> None:
    """Forget which devices decode which streams (tests, driver changes)."""
    with _lock:
        _probed.clear()
        _logged.clear()
