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

import av
import numpy as np

KR = 0.2126
KB = 0.0722
KG = 1.0 - KR - KB

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
    rgb = np.asarray(rgb_u16, dtype=np.float64) / 65535.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    y = KR * r + KG * g + KB * b
    cb = (b - y) / (2.0 * (1.0 - KB))
    cr = (r - y) / (2.0 * (1.0 - KR))
    scale = float(1 << (bit_depth - 8))
    max_code = (1 << bit_depth) - 1
    if chroma_step == 2:
        if cb.shape[1] % 2:
            cb = np.concatenate([cb, cb[:, -1:]], axis=1)
            cr = np.concatenate([cr, cr[:, -1:]], axis=1)
        cb = 0.5 * (cb[:, 0::2] + cb[:, 1::2])
        cr = 0.5 * (cr[:, 0::2] + cr[:, 1::2])
    y_code = np.clip(np.rint((16.0 + 219.0 * y) * scale), 0, max_code).astype(np.uint16)
    cb_code = np.clip(np.rint((128.0 + 224.0 * cb) * scale), 0, max_code).astype(np.uint16)
    cr_code = np.clip(np.rint((128.0 + 224.0 * cr) * scale), 0, max_code).astype(np.uint16)
    return y_code, cb_code, cr_code


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
