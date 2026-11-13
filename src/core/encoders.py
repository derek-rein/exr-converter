"""Video encoder setup shared by EXR → Video, Transcode, and proxies.

Two decisions live here:

* **Threads.** FFmpeg encoders default to slice threads. ``prores_ks`` and
  ``cfhd`` run several times faster with frame threads, while ``dnxhd`` and
  ``ffv1`` run several times slower with them. :func:`configure_threads`
  picks per encoder.
* **Hardware H.264 / HEVC / AV1.** In :data:`ENCODER_AUTO` mode,
  :func:`pick_encoder` replaces libx264 / libx265 / SVT-AV1 with VideoToolbox
  (macOS) or NVENC, Quick Sync, AMF (Windows / Linux), or Windows Media
  Foundation (Windows only) when that encoder opens on this
  machine at the output size with the options it will use. Media Foundation
  is the last resort: it uses whatever GPU the OS drivers expose. Bit depth
  and chroma stay as labelled: 8-bit uses ``nv12``, 10-bit uses ``p010le``,
  4:2:0 only. AV1 has no VideoToolbox encoder. HEVC 12-bit always uses
  software.

A hardware choice carries its software ``fallback``. :func:`run_with_fallback`
retries an encode with it when the hardware encoder fails.
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any

import av
from av.error import FFmpegError

from .constants import ENCODER_AUTO, X26X_PRESETS

_FRAME_THREADED = frozenset({"prores_ks", "cfhd"})

# Codec key → hardware encoder family (``<family>_<backend>``).
_HW_FAMILY: dict[str, str] = {"h264": "h264", "hevc_8": "hevc", "hevc": "hevc", "av1": "av1"}
_HW_PIX_FMT: dict[int, str] = {8: "nv12", 10: "p010le"}

# Hardware quality scales sit close to the x264 / x265 CRF scale but spend
# fewer bits at the same number; this keeps the two roughly level.
HW_QUALITY_OFFSET = 2

# libavcodec's FF_QP2LAMBDA: ``-q:v N`` is ``global_quality = N * 118``.
_FF_QP2LAMBDA = 118

_NVENC_PRESETS: dict[str, str] = {
    "ultrafast": "p1",
    "superfast": "p2",
    "veryfast": "p3",
    "faster": "p3",
    "fast": "p4",
    "medium": "p5",
    "slow": "p6",
    "slower": "p7",
    "veryslow": "p7",
}
_QSV_PRESETS: dict[str, str] = {
    "ultrafast": "veryfast",
    "superfast": "veryfast",
    "veryfast": "veryfast",
    "faster": "faster",
    "fast": "fast",
    "medium": "medium",
    "slow": "slow",
    "slower": "slower",
    "veryslow": "veryslow",
}
_AMF_QUALITY: dict[str, str] = {
    "ultrafast": "speed",
    "superfast": "speed",
    "veryfast": "speed",
    "faster": "speed",
    "fast": "balanced",
    "medium": "balanced",
    "slow": "quality",
    "slower": "quality",
    "veryslow": "quality",
}


@dataclass(frozen=True)
class _Backend:
    name: str
    label: str
    platforms: tuple[str, ...]
    families: tuple[str, ...] = ("h264", "hevc", "av1")


# Probe order: the first backend whose encoder opens wins.
_BACKENDS: tuple[_Backend, ...] = (
    _Backend("videotoolbox", "Apple VideoToolbox", ("darwin",), ("h264", "hevc")),
    _Backend("nvenc", "NVIDIA NVENC", ("win32", "linux")),
    _Backend("qsv", "Intel Quick Sync", ("win32", "linux")),
    _Backend("amf", "AMD AMF", ("win32", "linux")),
    _Backend("mf", "Windows Media Foundation", ("win32",)),
)
_BACKEND_LABELS: dict[str, str] = {b.name: b.label for b in _BACKENDS}


class EncoderFailed(RuntimeError):
    """The video encoder could not open, or failed while encoding."""


@dataclass(frozen=True)
class EncoderChoice:
    """One concrete libav encoder plus everything needed to configure it."""

    libav_codec: str
    pix_fmt: str
    options: dict[str, str] = field(default_factory=dict)
    bit_rate: int = 0
    backend: str = ""  # "" = software
    fallback: EncoderChoice | None = field(default=None, compare=False)

    @property
    def hardware(self) -> bool:
        return bool(self.backend)

    @property
    def label(self) -> str:
        """``NVIDIA NVENC (hevc_nvenc)`` for hardware, else the libav name."""
        if not self.hardware:
            return self.libav_codec
        return f"{_BACKEND_LABELS.get(self.backend, self.backend)} ({self.libav_codec})"

    def configure(self, stream: Any) -> None:
        """Set pix_fmt, threads, bit rate, and options on a new output stream."""
        stream.pix_fmt = self.pix_fmt
        configure_threads(stream, self.libav_codec)
        if self.hardware or self.bit_rate:
            # Hardware: 0 so libavcodec's 200 kb/s default never caps constant quality.
            stream.bit_rate = self.bit_rate
        if self.options:
            stream.options = dict(self.options)


def configure_threads(stream: Any, libav_codec: str) -> None:
    """Frame threads where they pay off; FFmpeg's slice default everywhere else."""
    if libav_codec in _FRAME_THREADED:
        stream.thread_type = "FRAME"


def hardware_options(backend: str, *, crf: int, preset: str, bit_rate: int) -> dict[str, str]:
    """libav options for a hardware encoder from x264-style *crf* / *preset*.

    With *bit_rate* set, the encoder targets that rate and *crf* is ignored.
    """
    preset = preset if preset in X26X_PRESETS else "medium"
    q = 0 if bit_rate else max(1, min(51, int(crf) - HW_QUALITY_OFFSET))
    if backend == "nvenc":
        opts = {"preset": _NVENC_PRESETS[preset], "tune": "hq", "rc": "vbr"}
        if q:
            opts["cq"] = str(q)
        return opts
    if backend == "qsv":
        opts = {"preset": _QSV_PRESETS[preset]}
        if q:
            opts["global_quality"] = str(q)
        return opts
    if backend == "amf":
        opts = {"quality": _AMF_QUALITY[preset]}
        if q:
            opts.update(rc="cqp", qp_i=str(q), qp_p=str(q), qp_b=str(q))
        else:
            opts["rc"] = "vbr_peak"
        return opts
    if backend == "mf":
        # hw_encoding=1 refuses Microsoft's software MFT, so "hardware" stays honest.
        opts = {"hw_encoding": "1"}
        if q:
            opts.update(rate_control="quality", quality=str(max(1, min(100, 100 - 2 * int(crf)))))
        return opts
    if backend == "videotoolbox":
        if not q:
            return {}
        # VideoToolbox quality runs 1 (worst) to 100 (best).
        vt_q = max(1, min(100, 100 - 2 * int(crf)))
        return {"flags": "+qscale", "global_quality": str(vt_q * _FF_QP2LAMBDA)}
    raise ValueError(f"Unknown hardware encoder backend: {backend!r}")


def hardware_family(codec_key: str, bit_depth: int) -> str:
    """Hardware family (``h264`` / ``hevc`` / ``av1``) for *codec_key* at *bit_depth*, or ``""``."""
    if bit_depth not in _HW_PIX_FMT:
        return ""
    return _HW_FAMILY.get(codec_key, "")


def pick_encoder(
    software: EncoderChoice,
    *,
    codec_key: str,
    bit_depth: int,
    mode: str = ENCODER_AUTO,
    crf: int = 18,
    preset: str = "medium",
    width: int,
    height: int,
) -> EncoderChoice:
    """Hardware encoder for *codec_key* when *mode* allows it and one works, else *software*."""
    if mode != ENCODER_AUTO:
        return software
    family = hardware_family(codec_key, bit_depth)
    if not family:
        return software
    pix_fmt = _HW_PIX_FMT[bit_depth]
    for backend in _BACKENDS:
        if sys.platform not in backend.platforms or family not in backend.families:
            continue
        choice = EncoderChoice(
            f"{family}_{backend.name}",
            pix_fmt,
            hardware_options(backend.name, crf=crf, preset=preset, bit_rate=software.bit_rate),
            software.bit_rate,
            backend.name,
            fallback=software,
        )
        if encoder_opens(choice, width, height):
            return choice
    return software


_probe_lock = threading.Lock()


def encoder_opens(choice: EncoderChoice, width: int, height: int) -> bool:
    """True when *choice* opens and encodes one *width* × *height* frame here (cached)."""
    with _probe_lock:
        return _probe(
            choice.libav_codec,
            choice.pix_fmt,
            tuple(sorted(choice.options.items())),
            choice.bit_rate,
            int(width),
            int(height),
        )


@cache
def _probe(
    libav_codec: str,
    pix_fmt: str,
    options: tuple[tuple[str, str], ...],
    bit_rate: int,
    width: int,
    height: int,
) -> bool:
    try:
        av.codec.Codec(libav_codec, "w")
    except Exception:
        return False
    try:
        cc = av.CodecContext.create(libav_codec, "w")
        cc.width, cc.height, cc.pix_fmt = width, height, pix_fmt
        cc.time_base = Fraction(1, 24)
        cc.bit_rate = bit_rate
        cc.options = dict(options)
        frame = av.VideoFrame(width, height, pix_fmt)
        frame.pts = 0
        cc.encode(frame)
        cc.encode(None)
    except Exception:
        return False
    return True


def open_encoder(stream: Any) -> None:
    """Open *stream*'s encoder now, so a failure surfaces before anything is muxed."""
    try:
        stream.codec_context.open()
    except FFmpegError as e:
        raise EncoderFailed(f"{stream.codec_context.name} could not open: {e}") from e


def encode_frame(stream: Any, frame: Any) -> list[Any]:
    """``stream.encode(frame)`` with encoder errors raised as :class:`EncoderFailed`."""
    try:
        return stream.encode(frame)
    except FFmpegError as e:
        raise EncoderFailed(f"{stream.codec_context.name} failed: {e}") from e


def run_with_fallback[T](
    choice: EncoderChoice,
    attempt: Callable[[EncoderChoice], T],
    log: Callable[[str], None] | None = None,
) -> T:
    """``attempt(choice)``; on :class:`EncoderFailed`, retry with ``choice.fallback``."""
    while True:
        try:
            return attempt(choice)
        except EncoderFailed as e:
            if choice.fallback is None:
                raise
            if log:
                log(f"{e}. Encoding again with {choice.fallback.label}.")
            choice = choice.fallback


def partial_output_path(output: Path) -> Path:
    """Hidden sibling written during an encode, renamed into place on success."""
    return output.with_name(f".{output.stem}.partial{output.suffix}")
