"""Exact RGB → Y'CbCr for software (PyAV / FFmpeg) video encodes.

Handing ``rgb48le`` frames to an encoder lets libswscale pick the matrix and
the rounding. With no colour space on the frame it uses BT.601, and its
fixed-point path adds small range offsets (about +2.5 12-bit luma codes on
software ProRes 4444 in a smooth gradient, plus a different offset per chroma
channel). For the planar 10/12-bit formats software ProRes and DNxHR use, this
module does the BT.709 limited-range conversion in float with correct rounding
instead, the same maths as the oxideav writer (``native/exr_prores``). Other
Y'CbCr formats go through swscale with BT.709 named explicitly. RGB formats
are left alone.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from functools import cache

import av
import numpy as np

KR = 0.2126
KB = 0.0722
KG = 1.0 - KR - KB

# Pixels per conversion strip: small enough that a strip's float64 temporaries
# stay in L2 cache (about 8 rows of 4K). Strips run on a thread pool because
# NumPy releases the GIL inside each operation.
_STRIP_PIXELS = 32_768

# pix_fmt → (bit depth, horizontal chroma subsampling, has alpha plane)
_EXACT_FORMATS: dict[str, tuple[int, int, bool]] = {
    "yuv444p10le": (10, 1, False),
    "yuva444p10le": (10, 1, True),
    "yuv422p10le": (10, 2, False),
    "yuv444p12le": (12, 1, False),
    "yuva444p12le": (12, 1, True),
    "yuv422p12le": (12, 2, False),
}

# Alpha-capable pix_fmt → the same layout without the alpha plane.
_DROP_ALPHA: dict[str, str] = {
    "yuva444p10le": "yuv444p10le",
    "yuva444p12le": "yuv444p12le",
    "yuva444p16le": "yuv444p16le",
    "yuva422p10le": "yuv422p10le",
    "yuva420p": "yuv420p",
}

_AVCOL_SPC_BT709 = 1
_AVCOL_RANGE_MPEG = 1


def pix_fmt_has_alpha(pix_fmt: str) -> bool:
    return pix_fmt in _DROP_ALPHA or pix_fmt.startswith("yuva")


def pix_fmt_without_alpha(pix_fmt: str) -> str:
    """Same Y'CbCr layout without an alpha plane (unchanged if there is none to drop)."""
    return _DROP_ALPHA.get(pix_fmt, pix_fmt)


def is_ycbcr_pix_fmt(pix_fmt: str) -> bool:
    """True for Y'CbCr pixel formats that need a matrix (not RGB / GBR / gray)."""
    name = pix_fmt.lower()
    if name.startswith("yuvj"):  # full-range JPEG formats: leave to the encoder
        return False
    return name.startswith(("yuv", "yuva", "nv", "p01", "p21", "p41", "ayuv", "y210", "v210"))


def has_exact_path(pix_fmt: str) -> bool:
    return pix_fmt in _EXACT_FORMATS


def rgb_to_ycbcr_codes(
    rgb_u16: np.ndarray, bit_depth: int, chroma_step: int = 1
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Full-range RGB48 → BT.709 limited-range Y, Cb, Cr integer codes (uint16 planes).

    Luma spans ``16..235`` and chroma ``16..240`` scaled by ``2**(bit_depth-8)``
    (64..940 / 64..960 at 10-bit, 256..3760 / 256..3840 at 12-bit), rounded to
    nearest. *chroma_step* 2 averages horizontal pairs for 4:2:2 before rounding.
    """
    rgb_u16 = np.asarray(rgb_u16)
    h, w = rgb_u16.shape[:2]
    cw = (w + 1) // 2 if chroma_step == 2 else w
    y = np.empty((h, w), dtype=np.uint16)
    cb = np.empty((h, cw), dtype=np.uint16)
    cr = np.empty((h, cw), dtype=np.uint16)
    rows = max(1, _STRIP_PIXELS // max(1, w))

    def strip(r0: int) -> None:
        r1 = r0 + rows
        _codes_into(rgb_u16[r0:r1], bit_depth, chroma_step, y[r0:r1], cb[r0:r1], cr[r0:r1])

    starts = range(0, h, rows)
    if len(starts) <= 1:
        for r0 in starts:
            strip(r0)
    else:
        list(_strip_pool().map(strip, starts))
    return y, cb, cr


@cache
def _strip_pool() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=min(16, os.cpu_count() or 4), thread_name_prefix="yuv")


def _codes_into(
    rgb_u16: np.ndarray,
    bit_depth: int,
    chroma_step: int,
    y_out: np.ndarray,
    cb_out: np.ndarray,
    cr_out: np.ndarray,
) -> None:
    """One strip of :func:`rgb_to_ycbcr_codes`, in place where it can be.

    The float operations and their order match the reference formula, so the
    codes are bit-identical to an unstripped conversion.
    """
    rgb = rgb_u16.astype(np.float64)
    rgb /= 65535.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = KR * r
    y += KG * g
    y += KB * b
    cb = b - y
    cb /= 2.0 * (1.0 - KB)
    cr = r - y
    cr /= 2.0 * (1.0 - KR)
    if chroma_step == 2:
        if cb.shape[1] % 2:
            cb = np.concatenate([cb, cb[:, -1:]], axis=1)
            cr = np.concatenate([cr, cr[:, -1:]], axis=1)
        cb = 0.5 * (cb[:, 0::2] + cb[:, 1::2])
        cr = 0.5 * (cr[:, 0::2] + cr[:, 1::2])
    scale = float(1 << (bit_depth - 8))
    max_code = (1 << bit_depth) - 1
    for plane, offset, gain, out in (
        (y, 16.0, 219.0, y_out),
        (cb, 128.0, 224.0, cb_out),
        (cr, 128.0, 224.0, cr_out),
    ):
        plane *= gain
        plane += offset
        plane *= scale
        np.rint(plane, out=plane)
        np.clip(plane, 0, max_code, out=plane)
        out[...] = plane


def _fill_plane(plane, data: np.ndarray) -> None:
    """Copy a uint16 (H, W) array into a PyAV plane, honouring line padding."""
    data = np.ascontiguousarray(data, dtype="<u2")
    h, w = data.shape
    stride = plane.line_size // 2
    if stride != w:
        padded = np.zeros((h, stride), dtype="<u2")
        padded[:, :w] = data
        data = padded
    plane.update(data.tobytes())


def rgb48_to_video_frame(
    rgb_u16: np.ndarray,
    pix_fmt: str,
    alpha_u16: np.ndarray | None = None,
) -> av.VideoFrame:
    """Build an encoder-ready frame in *pix_fmt* from full-range RGB48.

    Exact float maths for the formats in ``_EXACT_FORMATS``; swscale with an
    explicit BT.709 matrix for other Y'CbCr formats; plain ``rgb48le`` for RGB
    targets (the encoder converts as before). *alpha_u16* (H×W, 0..65535)
    fills the alpha plane of ``yuva*`` formats; it is opaque when omitted.
    """
    rgb_u16 = np.ascontiguousarray(rgb_u16, dtype=np.uint16)
    h, w = rgb_u16.shape[:2]
    spec = _EXACT_FORMATS.get(pix_fmt)
    if spec is not None:
        depth, step, has_alpha = spec
        y, cb, cr = rgb_to_ycbcr_codes(rgb_u16, depth, step)
        frame = av.VideoFrame(w, h, pix_fmt)
        _fill_plane(frame.planes[0], y)
        _fill_plane(frame.planes[1], cb)
        _fill_plane(frame.planes[2], cr)
        if has_alpha:
            max_code = (1 << depth) - 1
            if alpha_u16 is None:
                a = np.full((h, w), max_code, dtype=np.uint16)
            else:
                a64 = np.asarray(alpha_u16, dtype=np.float64) * (max_code / 65535.0)
                a = np.clip(np.rint(a64), 0, max_code).astype(np.uint16)
            _fill_plane(frame.planes[3], a)
        frame.colorspace = _AVCOL_SPC_BT709
        frame.color_range = _AVCOL_RANGE_MPEG
        return frame

    vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
    if not is_ycbcr_pix_fmt(pix_fmt):
        return vf
    out = vf.reformat(format=pix_fmt, dst_colorspace="ITU709", dst_color_range="MPEG")
    out.colorspace = _AVCOL_SPC_BT709
    out.color_range = _AVCOL_RANGE_MPEG
    return out
