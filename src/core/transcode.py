"""Video → video transcodes (the Transcode tab and the ``transcode`` CLI).

This is the shared PyAV encode engine. The Video → Proxy tab is a thin
wrapper over :func:`run_transcode` with proxy defaults (see
:mod:`src.core.proxy`), so both tabs decode, resize, watermark, and mux the
same way.

The default is a straight decode → (optional resize) → encode with **no color
change**. A YUV source is reformatted straight to the codec's pixel format
without an RGB round trip, and the source color tags are copied. An optional
OCIO transform (source color space → destination color space or display) runs
on 16-bit RGB when both spaces are set. The optional invisible TrustMark
watermark is added last, on the picture the codec receives.

Camera RAW (``.r3d`` / ``.nev`` / ``.braw``) is accepted when the matching SDK
bridge is available. Those decode to float RGB in the camera space reported by
the SDK, carry no audio, and usually want an OCIO transform.
"""

from __future__ import annotations

import datetime
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from .constants import (
    DNXHR_PROFILE,
    PRORES_KS_PROFILE,
    PRORES_VT_PROFILE,
    X26X_PRESETS,
    VideoCodecSpec,
    video_codec_by_key,
)
from .errors import ConversionCancelled

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]

# ---------------------------------------------------------------------------
# Codecs and containers
# ---------------------------------------------------------------------------

# AV1 is offered for transcodes only (not EXR → Video), when the PyAV FFmpeg
# build has SVT-AV1. 10-bit 4:2:0, the usual AV1 delivery format.
AV1_SPEC = VideoCodecSpec(
    "av1",
    "AV1 · 10-bit 4:2:0 (SVT-AV1)",
    "libsvtav1",
    "yuv420p10le",
    10,
    "4:2:0",
)

# Offered codec keys, in menu order. Each must be in ``VIDEO_CODECS`` (or be
# ``AV1_SPEC``); platform-only presets (VideoToolbox) and encoders missing from
# the FFmpeg build are filtered at runtime.
TRANSCODE_CODEC_KEYS: tuple[str, ...] = (
    "prores_proxy",
    "prores_lt",
    "prores_422",
    "prores",
    "prores_4444",
    "prores_xq",
    "prores_vt_proxy",
    "prores_vt_lt",
    "prores_vt_422",
    "prores_vt_hq",
    "prores_vt_4444",
    "prores_vt_xq",
    "dnxhr_lb",
    "dnxhr_sq",
    "dnxhr_hq",
    "dnxhr_hqx",
    "dnxhr_444",
    "h264",
    "hevc_8",
    "hevc",
    "hevc_12",
    "av1",
)
DEFAULT_TRANSCODE_CODEC = "prores"  # Apple ProRes 422 HQ (software)

# Long-GOP codecs take a quality (CRF) or bitrate setting. Intra codecs
# (ProRes, DNxHR) are fixed by their profile.
LONG_GOP_CODEC_KEYS: frozenset[str] = frozenset({"h264", "hevc_8", "hevc", "hevc_12", "av1"})

# Short codec names for the ``<codec>`` filename token.
_CODEC_SLUGS: dict[str, str] = {
    "prores_proxy": "ProRes422Proxy",
    "prores_lt": "ProRes422LT",
    "prores_422": "ProRes422",
    "prores": "ProRes422HQ",
    "prores_4444": "ProRes4444",
    "prores_xq": "ProRes4444XQ",
    "prores_vt_proxy": "ProRes422Proxy",
    "prores_vt_lt": "ProRes422LT",
    "prores_vt_422": "ProRes422",
    "prores_vt_hq": "ProRes422HQ",
    "prores_vt_4444": "ProRes4444",
    "prores_vt_xq": "ProRes4444XQ",
    "dnxhr_lb": "DNxHR_LB",
    "dnxhr_sq": "DNxHR_SQ",
    "dnxhr_hq": "DNxHR_HQ",
    "dnxhr_hqx": "DNxHR_HQX",
    "dnxhr_444": "DNxHR_444",
    "h264": "H264",
    "hevc_8": "HEVC",
    "hevc": "HEVC10",
    "hevc_12": "HEVC12",
    "av1": "AV1",
}

# Container (file extension without dot) → libav muxer name.
CONTAINER_FORMATS: dict[str, str] = {
    "mov": "mov",
    "mxf": "mxf",
    "mp4": "mp4",
    "mkv": "matroska",
}
CONTAINER_LABELS: dict[str, str] = {
    "mov": "QuickTime (.mov)",
    "mxf": "MXF OP1a (.mxf)",
    "mp4": "MPEG-4 (.mp4)",
    "mkv": "Matroska (.mkv)",
}

# MXF only takes the broadcast frame rates.
_MXF_RATES: tuple[Fraction, ...] = (
    Fraction(24000, 1001),
    Fraction(24),
    Fraction(25),
    Fraction(30000, 1001),
    Fraction(30),
    Fraction(50),
    Fraction(60000, 1001),
    Fraction(60),
)


@cache
def encoder_available(libav_name: str) -> bool:
    """True when the PyAV FFmpeg build has encoder *libav_name*."""
    try:
        from av.codec.codec import Codec

        Codec(libav_name, "w")
    except Exception:
        return False
    return True


def transcode_codec_spec(key: str) -> VideoCodecSpec | None:
    if key == AV1_SPEC.key:
        return AV1_SPEC
    return video_codec_by_key(key)


def available_transcode_codecs() -> list[VideoCodecSpec]:
    """Codec specs usable here, in ``TRANSCODE_CODEC_KEYS`` order."""
    out: list[VideoCodecSpec] = []
    for key in TRANSCODE_CODEC_KEYS:
        spec = transcode_codec_spec(key)
        if spec is None or not spec.is_available():
            continue
        if not encoder_available(spec.libav_codec):
            continue
        out.append(spec)
    return out


def codec_family(key: str) -> str:
    """``prores`` | ``dnxhr`` | ``h264`` | ``hevc`` | ``av1`` (``""`` if unknown)."""
    if key in PRORES_KS_PROFILE or key in PRORES_VT_PROFILE:
        return "prores"
    if key in DNXHR_PROFILE:
        return "dnxhr"
    if key == "h264":
        return "h264"
    if key in ("hevc", "hevc_8", "hevc_12"):
        return "hevc"
    if key == "av1":
        return "av1"
    return ""


def containers_for_codec(key: str) -> tuple[str, ...]:
    """Containers that hold *key*, default first."""
    fam = codec_family(key)
    if fam == "prores":
        return ("mov",)
    if fam == "dnxhr":
        return ("mov", "mxf")
    if fam in ("h264", "hevc"):
        return ("mp4", "mov")
    if fam == "av1":
        return ("mp4", "mkv")
    return ("mov",)


def resolve_container(key: str, container: str = "") -> str:
    """*container* if it can hold *key*, else the codec's default container."""
    allowed = containers_for_codec(key)
    c = (container or "").lower().lstrip(".")
    return c if c in allowed else allowed[0]


def is_long_gop(key: str) -> bool:
    return key in LONG_GOP_CODEC_KEYS


def codec_slug(key: str) -> str:
    return _CODEC_SLUGS.get(key, key)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class TranscodeResolution(NamedTuple):
    """A size choice: a scale factor, a maximum height (fit), or custom."""

    key: str
    label: str
    scale: float = 1.0
    max_height: int = 0


RES_CUSTOM = "custom"
RES_KEEP = "original"

# Superset of the proxy sizes (the proxy keys keep working here).
TRANSCODE_RESOLUTIONS: tuple[TranscodeResolution, ...] = (
    TranscodeResolution(RES_KEEP, "Keep source size", scale=1.0),
    TranscodeResolution("75", "75%", scale=0.75),
    TranscodeResolution("half", "50%", scale=0.5),
    TranscodeResolution("quarter", "25%", scale=0.25),
    TranscodeResolution("eighth", "12.5%", scale=0.125),
    TranscodeResolution("2160", "Fit 2160p", max_height=2160),
    TranscodeResolution("1080", "Fit 1080p", max_height=1080),
    TranscodeResolution("720", "Fit 720p", max_height=720),
    TranscodeResolution("540", "Fit 540p", max_height=540),
    TranscodeResolution(RES_CUSTOM, "Custom size"),
)
# The Transcode tab lists these (eighth / 540 stay valid for proxies and CLI).
TRANSCODE_RESOLUTION_MENU: tuple[str, ...] = (
    RES_KEEP,
    "75",
    "half",
    "quarter",
    "2160",
    "1080",
    "720",
    RES_CUSTOM,
)
DEFAULT_TRANSCODE_RESOLUTION = RES_KEEP


def transcode_resolution_by_key(key: str) -> TranscodeResolution:
    for res in TRANSCODE_RESOLUTIONS:
        if res.key == key:
            return res
    return TRANSCODE_RESOLUTIONS[0]


def _even(v: float) -> int:
    n = max(2, int(v + 0.5))
    return n - n % 2


def transcode_dims(
    width: int,
    height: int,
    resolution: str = RES_KEEP,
    custom_width: int = 0,
    custom_height: int = 0,
) -> tuple[int, int]:
    """Even output (w, h).

    Scale and fit presets never upscale. Custom sizes are exact; when only one
    side is set (the other is 0) the source aspect ratio picks the other.
    """
    if width <= 0 or height <= 0:
        raise ValueError("Source size is unknown.")
    res = transcode_resolution_by_key(resolution)
    if res.key == RES_CUSTOM:
        cw, ch = max(0, int(custom_width)), max(0, int(custom_height))
        if cw and ch:
            return _even(cw), _even(ch)
        if cw:
            return _even(cw), _even(cw * height / width)
        if ch:
            return _even(ch * width / height), _even(ch)
        return _even(width), _even(height)
    if res.max_height:
        scale = min(1.0, res.max_height / height)
    else:
        scale = min(1.0, res.scale)
    return _even(width * scale), _even(height * scale)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------

AUDIO_COPY = "copy"
AUDIO_AAC = "aac"
AUDIO_PCM = "pcm"  # 24-bit PCM
AUDIO_PCM16 = "pcm16"  # 16-bit PCM (proxies)
AUDIO_NONE = "none"

AUDIO_MODES: tuple[tuple[str, str], ...] = (
    (AUDIO_COPY, "Copy source audio"),
    (AUDIO_AAC, "AAC"),
    (AUDIO_PCM, "PCM 24-bit"),
    (AUDIO_NONE, "No audio"),
)
DEFAULT_AUDIO_MODE = AUDIO_COPY

# Audio codecs each container accepts as a stream copy.
_COPY_OK: dict[str, frozenset[str]] = {
    "mov": frozenset(
        {"aac", "alac", "mp3", "ac3", "eac3", "pcm_s16le", "pcm_s24le", "pcm_s32le"}
        | {"pcm_s16be", "pcm_s24be", "pcm_s32be", "pcm_f32le", "pcm_f32be"}
    ),
    "mp4": frozenset({"aac", "alac", "mp3", "ac3", "eac3", "opus", "flac"}),
    "mxf": frozenset({"pcm_s16le", "pcm_s24le"}),
    "mkv": frozenset(
        {"aac", "alac", "mp3", "ac3", "eac3", "opus", "flac", "vorbis"}
        | {"pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_f32le"}
    ),
}


def audio_modes_for_container(container: str) -> tuple[str, ...]:
    """Audio modes offered for *container* (MP4 cannot hold PCM)."""
    if container == "mp4":
        return (AUDIO_COPY, AUDIO_AAC, AUDIO_NONE)
    if container == "mxf":
        return (AUDIO_COPY, AUDIO_PCM, AUDIO_NONE)
    return (AUDIO_COPY, AUDIO_AAC, AUDIO_PCM, AUDIO_NONE)


def _pcm_for(container: str) -> str:
    """24-bit PCM for every container that holds PCM (.mov / .mxf / .mkv)."""
    del container
    return "pcm_s24le"


def resolve_audio(mode: str, container: str, source_codec: str = "") -> tuple[str, str]:
    """Pick what happens to the first audio track.

    Returns ``(action, note)`` where *action* is ``"copy"``, ``"none"``, or the
    libav encoder name. *note* is a log line when the choice was changed to fit
    the container ("" otherwise).
    """
    if mode == AUDIO_NONE:
        return AUDIO_NONE, ""
    fallback = "aac" if container in ("mp4", "mkv") else _pcm_for(container)
    if mode == AUDIO_COPY:
        if source_codec and source_codec in _COPY_OK.get(container, frozenset()):
            return AUDIO_COPY, ""
        if not source_codec:
            return fallback, ""
        return (
            fallback,
            f"Audio: {source_codec} cannot be copied into .{container}; encoding {fallback}.",
        )
    if mode == AUDIO_AAC:
        if container == "mxf":
            return _pcm_for(container), "Audio: MXF holds PCM only; encoding PCM."
        return "aac", ""
    if mode in (AUDIO_PCM, AUDIO_PCM16):
        if container == "mp4":
            return "aac", "Audio: MP4 cannot hold PCM; encoding AAC."
        return ("pcm_s16le" if mode == AUDIO_PCM16 else _pcm_for(container)), ""
    raise ValueError(f"Unknown audio mode: {mode!r}")


# ---------------------------------------------------------------------------
# Settings, encoder options, and output naming
# ---------------------------------------------------------------------------

RATE_CRF = "crf"
RATE_BITRATE = "bitrate"

OVERWRITE_SKIP = "skip"
OVERWRITE_REPLACE = "overwrite"
OVERWRITE_RENAME = "rename"
OVERWRITE_MODES: tuple[tuple[str, str], ...] = (
    (OVERWRITE_SKIP, "Skip"),
    (OVERWRITE_REPLACE, "Overwrite"),
    (OVERWRITE_RENAME, "Add a number"),
)
DEFAULT_OVERWRITE = OVERWRITE_SKIP

DEFAULT_NAME_PATTERN = "<name>_<codec>"
NAME_TOKENS: tuple[tuple[str, str], ...] = (
    ("name", "Source file name without extension"),
    ("codec", "Codec, e.g. ProRes422HQ or H264"),
    ("width", "Output width"),
    ("height", "Output height"),
    ("fps", "Output frame rate"),
    ("date", "Today's date (YYYY-MM-DD)"),
)

# SVT-AV1 presets (0 slowest, 13 fastest) for the shared x264 preset names.
_SVT_PRESETS: dict[str, str] = {
    "ultrafast": "12",
    "superfast": "11",
    "veryfast": "10",
    "faster": "9",
    "fast": "8",
    "medium": "6",
    "slow": "5",
    "slower": "4",
    "veryslow": "2",
}
DEFAULT_CRF: dict[str, int] = {"h264": 18, "hevc_8": 20, "hevc": 20, "hevc_12": 20, "av1": 28}
CRF_RANGE: dict[str, tuple[int, int]] = {"av1": (0, 63)}
DEFAULT_PRESET = "medium"
DEFAULT_BITRATE_MBPS = 20.0
AAC_BITRATE = 256_000


@dataclass(frozen=True)
class TranscodeSettings:
    """Everything that decides how one clip is encoded (not where it goes)."""

    codec_key: str = DEFAULT_TRANSCODE_CODEC
    container: str = ""  # "" = codec default
    resolution: str = DEFAULT_TRANSCODE_RESOLUTION
    custom_width: int = 0
    custom_height: int = 0
    fps: float = 0.0  # 0 = keep source rate; else conform to this rate
    audio: str = DEFAULT_AUDIO_MODE
    rate_control: str = RATE_CRF  # long-GOP only
    crf: int | None = None  # None = codec default
    bitrate_mbps: float = 0.0
    preset: str = ""  # x264 / x265 names; mapped for SVT-AV1
    color_src: str = ""  # both set = OCIO transform
    color_dst: str = ""
    config_source: str = ""
    config_path: str = ""
    watermark_text: str = ""
    watermark_strength: float = 1.0
    codec_opts: dict[str, str] | None = None  # extra libav options (override)

    @property
    def color_enabled(self) -> bool:
        return bool(self.color_src and self.color_dst and self.color_src != self.color_dst)


def validate_settings(settings: TranscodeSettings) -> TranscodeSettings:
    """Return *settings* with the container resolved; raise ValueError if invalid."""
    spec = transcode_codec_spec(settings.codec_key)
    if spec is None:
        raise ValueError(f"Unknown codec: {settings.codec_key!r}")
    if spec.libav_codec == "oxideav_prores":
        raise ValueError("oxideav ProRes is not offered for transcodes.")
    container = (settings.container or "").lower().lstrip(".")
    if container and container not in containers_for_codec(settings.codec_key):
        allowed = ", ".join(f".{c}" for c in containers_for_codec(settings.codec_key))
        raise ValueError(f"{spec.display_name} cannot go in .{container} (use {allowed}).")
    if settings.resolution not in {r.key for r in TRANSCODE_RESOLUTIONS}:
        raise ValueError(f"Unknown resolution: {settings.resolution!r}")
    if settings.resolution == RES_CUSTOM and not (settings.custom_width or settings.custom_height):
        raise ValueError("Custom size needs a width and/or a height.")
    if settings.custom_width < 0 or settings.custom_height < 0:
        raise ValueError("Custom size cannot be negative.")
    if settings.fps < 0 or settings.fps > 240:
        raise ValueError("Frame rate must be between 1 and 240 fps (0 keeps the source).")
    if settings.audio not in {AUDIO_COPY, AUDIO_AAC, AUDIO_PCM, AUDIO_PCM16, AUDIO_NONE}:
        raise ValueError(f"Unknown audio mode: {settings.audio!r}")
    if settings.rate_control not in (RATE_CRF, RATE_BITRATE):
        raise ValueError(f"Unknown rate control: {settings.rate_control!r}")
    if is_long_gop(settings.codec_key):
        if settings.rate_control == RATE_BITRATE and settings.bitrate_mbps <= 0:
            raise ValueError("Bitrate must be above 0 Mb/s.")
        if settings.crf is not None:
            lo, hi = CRF_RANGE.get(settings.codec_key, (0, 51))
            if not lo <= settings.crf <= hi:
                raise ValueError(f"CRF must be between {lo} and {hi}.")
        if settings.preset and settings.preset not in X26X_PRESETS:
            raise ValueError(f"Unknown preset: {settings.preset!r}")
    if bool(settings.color_src) != bool(settings.color_dst):
        raise ValueError("Color transform needs both a source and a destination space.")
    return replace(settings, container=resolve_container(settings.codec_key, container))


def encoder_options(settings: TranscodeSettings) -> tuple[dict[str, str], int]:
    """libav options and video bit rate (0 = none) for *settings*.

    ProRes / DNxHR take their profile from the codec key. Long-GOP codecs use
    CRF (default) or a target bitrate, plus a speed preset.
    """
    from .convert import default_codec_opts

    key = settings.codec_key
    opts: dict[str, str] = dict(default_codec_opts(key))
    bit_rate = 0
    if is_long_gop(key):
        opts.pop("crf", None)
        preset = settings.preset or DEFAULT_PRESET
        if key == "av1":
            opts["preset"] = _SVT_PRESETS.get(preset, _SVT_PRESETS[DEFAULT_PRESET])
        else:
            opts["preset"] = preset
        if settings.rate_control == RATE_BITRATE:
            bit_rate = int(round(settings.bitrate_mbps * 1_000_000))
        else:
            crf = settings.crf if settings.crf is not None else DEFAULT_CRF.get(key, 18)
            opts["crf"] = str(int(crf))
        if key == "hevc_8" or key == "hevc" or key == "hevc_12":
            # Quiet x265's per-encode banner and stats in the log.
            opts.setdefault("x265-params", "log-level=error")
    if settings.codec_opts:
        opts.update({str(k): str(v) for k, v in settings.codec_opts.items()})
    return opts, bit_rate


_TOKEN_RE = re.compile(r"<([A-Za-z_]+)>")
_BAD_NAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _fps_label(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-3:
        return str(int(round(fps)))
    return f"{fps:.3f}".rstrip("0").rstrip(".")


def format_output_name(
    pattern: str,
    source: str | os.PathLike[str],
    *,
    codec_key: str,
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
    today: datetime.date | None = None,
) -> str:
    """Expand ``<token>`` names in *pattern* into a file stem (no extension).

    Unknown tokens raise :class:`ValueError`. Characters that are not allowed
    in file names become ``_``.
    """
    pattern = (pattern or "").strip() or DEFAULT_NAME_PATTERN
    values = {
        "name": Path(source).stem,
        "codec": codec_slug(codec_key),
        "width": str(width) if width else "",
        "height": str(height) if height else "",
        "fps": _fps_label(fps) if fps else "",
        "date": (today or datetime.date.today()).isoformat(),
    }

    def _sub(m: re.Match[str]) -> str:
        tok = m.group(1).lower()
        if tok not in values:
            known = ", ".join(f"<{t}>" for t, _ in NAME_TOKENS)
            raise ValueError(f"Unknown name token <{m.group(1)}> (use {known}).")
        return values[tok]

    stem = _TOKEN_RE.sub(_sub, pattern)
    stem = _BAD_NAME_CHARS.sub("_", stem).strip().strip(".")
    if not stem:
        raise ValueError("The file name pattern produced an empty name.")
    return stem


def plan_output(
    source: str | os.PathLike[str],
    settings: TranscodeSettings,
    *,
    output_dir: str | os.PathLike[str] | None = None,
    pattern: str = DEFAULT_NAME_PATTERN,
    overwrite: str = DEFAULT_OVERWRITE,
    width: int = 0,
    height: int = 0,
    fps: float = 0.0,
    taken: set[str] | None = None,
) -> Path | None:
    """Output path for *source*, or ``None`` when it exists and *overwrite* is skip.

    Without *output_dir* the file goes next to the source. The output can never
    be the source file itself. *taken* holds paths already planned in this
    batch so "Add a number" does not hand two clips the same name. Entries are
    compared with :func:`_norm` (absolute path, and case-folded on Windows),
    so a raw ``str(path)`` from the GUI or CLI matches.
    """
    src = Path(source)
    container = resolve_container(settings.codec_key, settings.container)
    folder = Path(output_dir).expanduser() if output_dir else src.parent
    if not (width and height and fps) and _needs_probe(pattern):
        width, height, fps = output_geometry(src, settings)
    stem = format_output_name(
        pattern, src, codec_key=settings.codec_key, width=width, height=height, fps=fps
    )
    out = folder / f"{stem}.{container}"
    taken_norm = {_norm(p) for p in (taken or set())}

    def _busy(p: Path) -> bool:
        return p.exists() or _norm(p) in taken_norm

    if _norm(out) == _norm(src):
        if overwrite != OVERWRITE_RENAME:
            raise ValueError(
                f"Output would replace the source {src.name}. Change the name pattern or folder."
            )
    if overwrite == OVERWRITE_RENAME:
        n = 1
        cand = out
        while _busy(cand) or _norm(cand) == _norm(src):
            cand = folder / f"{stem}_{n}.{container}"
            n += 1
        return cand
    if out.exists() and overwrite == OVERWRITE_SKIP:
        return None
    return out


def _needs_probe(pattern: str) -> bool:
    p = (pattern or "").lower()
    return any(f"<{t}>" in p for t in ("width", "height", "fps"))


def output_geometry(
    source: str | os.PathLike[str], settings: TranscodeSettings
) -> tuple[int, int, float]:
    """Output (width, height, fps) for *source* with *settings* (probes the file)."""
    from .video import probe_video

    w, h, fps, _n = probe_video(str(source))
    ow, oh = transcode_dims(
        w, h, settings.resolution, settings.custom_width, settings.custom_height
    )
    return ow, oh, (settings.fps if settings.fps > 0 else float(fps))


def _norm(p: str | os.PathLike[str]) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

TRANSCODE_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".m4v", ".ts", ".mts", ".m2ts"}
)


def _raw_kind(path: str | os.PathLike[str]) -> str:
    """``"r3d"`` / ``"braw"`` when *path* is camera RAW with its SDK present."""
    from .braw import is_available as braw_available
    from .braw import is_braw_path
    from .r3d import is_available as r3d_available
    from .r3d import is_r3d_path

    p = str(path)
    if is_r3d_path(p):
        return "r3d" if r3d_available() else ""
    if is_braw_path(p):
        return "braw" if braw_available() else ""
    return ""


def transcode_source_suffixes() -> frozenset[str]:
    """Extensions the Transcode tab accepts on this machine."""
    from .braw import BRAW_SUFFIXES
    from .braw import is_available as braw_available
    from .r3d import R3D_SUFFIXES
    from .r3d import is_available as r3d_available

    out = set(TRANSCODE_VIDEO_SUFFIXES)
    if r3d_available():
        out |= set(R3D_SUFFIXES)
    if braw_available():
        out |= set(BRAW_SUFFIXES)
    return frozenset(out)


def is_transcode_source(path: str | os.PathLike[str]) -> bool:
    """True for a video (or camera RAW with its SDK) the Transcode tab can read."""
    from .video import is_ignored_media_filename, is_libav_unsafe_media

    p = Path(path)
    if p.name.startswith(".") or is_ignored_media_filename(p.name):
        return False
    if _raw_kind(p):
        return True
    if is_libav_unsafe_media(str(p)):
        return False
    return p.suffix.lower() in TRANSCODE_VIDEO_SUFFIXES


def collect_transcode_sources(
    paths: list[str | os.PathLike[str]], *, recursive: bool = False
) -> list[Path]:
    """Expand files and folders into a sorted, de-duplicated source list."""
    from .proxy import collect_proxy_sources

    return collect_proxy_sources(paths, recursive=recursive, accept=is_transcode_source)


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------


def _partial_path(output: Path) -> Path:
    """Hidden sibling written during encode, renamed on success."""
    return output.with_name(f".{output.stem}.partial{output.suffix}")


def _source_timecode(container: Any, stream: Any) -> str:
    for meta in (getattr(stream, "metadata", None), getattr(container, "metadata", None)):
        tc = (meta or {}).get("timecode")
        if tc:
            return str(tc)
    return ""


def _timecode_for_rate(tc: str, rate: Fraction) -> str:
    """Drop-frame timecode only fits 29.97 / 59.94; use non-drop otherwise."""
    if not tc or not re.search(r"[;.,]", tc):
        return tc
    if rate in (Fraction(30000, 1001), Fraction(60000, 1001)):
        return tc
    return re.sub(r"[;.,]", ":", tc)


def _as_fraction(rate: Fraction | int) -> Fraction:
    return Fraction(rate)


# libav AVColorSpace → swscale matrix for RGB conversions.
_SWS_MATRIX: dict[int, int] = {1: 1, 5: 5, 6: 6, 7: 7, 9: 9, 10: 9}


def _sws_matrix(ctx: Any) -> int:
    """swscale matrix id for a source (BT.709 for HD when untagged, else BT.601).

    *ctx* is a decoder context or frame with ``colorspace`` and ``height``.
    """
    cs = int(getattr(ctx, "colorspace", 2) or 2)
    if cs in _SWS_MATRIX:
        return _SWS_MATRIX[cs]
    return 1 if int(getattr(ctx, "height", 0) or 0) >= 720 else 6


def _copy_color_tags(out_v: Any, src_ctx: Any, *, matrix_only: bool) -> None:
    """Copy colour tags from the source stream onto the encoder.

    Must run before the first packet is muxed: the container header (and the
    encoder) open on the first mux, which can be an audio packet.
    With an OCIO transform only the YUV matrix tag is kept, because the
    primaries and transfer of the new picture are no longer the source's.
    """
    cc = out_v.codec_context
    try:
        cs = int(getattr(src_ctx, "colorspace", 2) or 2)
        if cs != 2:
            cc.colorspace = cs
        if matrix_only:
            return
        for attr in ("color_primaries", "color_trc"):
            v = int(getattr(src_ctx, attr, 2) or 2)
            if v != 2:
                setattr(cc, attr, v)
    except Exception:
        pass


@dataclass
class _Pipeline:
    """Per-frame picture work after decode: resize, OCIO, watermark, pix_fmt."""

    width: int
    height: int
    pix_fmt: str
    processor: Any = None  # OCIO CPU processor
    watermark: Any = None  # FrameWatermark
    matrix: int = 1  # swscale matrix for RGB ↔ YUV

    @property
    def needs_rgb(self) -> bool:
        return self.processor is not None or self.watermark is not None

    def from_av(self, frame: Any) -> Any:
        """Decoded PyAV frame → frame ready for the encoder."""
        import av

        from .video import av_frame_reformat

        if not self.needs_rgb:
            return av_frame_reformat(
                frame, width=self.width, height=self.height, format=self.pix_fmt
            )
        rgb = av_frame_reformat(
            frame,
            width=self.width,
            height=self.height,
            format="rgb48le",
            src_colorspace=self.matrix,
        ).to_ndarray()
        rgb = self._process_u16(rgb)
        vf = av.VideoFrame.from_ndarray(rgb, format="rgb48le")
        return av_frame_reformat(vf, format=self.pix_fmt, dst_colorspace=self.matrix)

    def from_float(self, rgb: np.ndarray) -> Any:
        """Float RGB (camera RAW decode) → frame ready for the encoder."""
        import av

        from .video import av_frame_reformat

        arr = np.ascontiguousarray(rgb[..., :3], dtype=np.float32)
        if self.processor is not None:
            from .ocio_utils import _apply_rgb_processor

            _apply_rgb_processor(self.processor, arr)
        u16 = np.clip(arr * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
        vf = av.VideoFrame.from_ndarray(u16, format="rgb48le")
        if (vf.width, vf.height) != (self.width, self.height):
            vf = av_frame_reformat(vf, width=self.width, height=self.height)
        if self.watermark is not None:
            marked = self.watermark.apply_u16(vf.to_ndarray())
            vf = av.VideoFrame.from_ndarray(np.ascontiguousarray(marked), format="rgb48le")
        return av_frame_reformat(vf, format=self.pix_fmt, dst_colorspace=self.matrix)

    def _process_u16(self, rgb: np.ndarray) -> np.ndarray:
        if self.processor is not None:
            from .ocio_utils import _apply_rgb_processor

            f = np.ascontiguousarray(rgb, dtype=np.float32) * np.float32(1.0 / 65535.0)
            _apply_rgb_processor(self.processor, f)
            rgb = np.clip(f * 65535.0 + 0.5, 0, 65535).astype(np.uint16)
        if self.watermark is not None:
            rgb = self.watermark.apply_u16(rgb)
        return np.ascontiguousarray(rgb)


def _color_processor(settings: TranscodeSettings, log: LogCallback | None) -> Any:
    if not settings.color_enabled:
        return None
    from .ocio_utils import (
        export_dest_label,
        load_config_from_source_info,
        make_export_processor,
    )

    cfg = load_config_from_source_info(settings.config_source, settings.config_path)
    for name in (settings.color_src, settings.color_dst):
        try:
            known = cfg.getColorSpace(name) is not None or name in list(cfg.getDisplays())
        except Exception:
            known = False
        if not known:
            raise RuntimeError(f"Color space {name!r} is not in the OCIO config.")
    proc = make_export_processor(cfg, settings.color_src, settings.color_dst)
    if log:
        dest = export_dest_label(cfg, settings.color_src, settings.color_dst)
        log(f"OCIO: {settings.color_src} → {dest}")
    return proc


def _open_watermark(settings: TranscodeSettings, log: LogCallback | None) -> Any:
    if not settings.watermark_text.strip():
        return None
    from .trustmark.embed import FrameWatermark

    wm = FrameWatermark(settings.watermark_text, settings.watermark_strength, log=log)
    if log:
        log(f"Invisible watermark: {wm.text!r} at strength {wm.strength:.2f}")
    return wm


def run_transcode(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    settings: TranscodeSettings | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
    label: str = "Transcode",
) -> Path:
    """Transcode *input_path* to *output_path* with *settings*.

    Writes to a hidden ``.<name>.partial<ext>`` file and renames it into place
    when the encode finishes, so a watch folder or editor never sees a
    half-written file. Returns the final output path. Raises
    :class:`~src.core.errors.ConversionCancelled` when *cancel_check* fires.
    """
    settings = validate_settings(settings or TranscodeSettings())
    src = Path(input_path)
    out = Path(output_path)
    if not is_transcode_source(src):
        raise RuntimeError(f"Not a supported video source: {src.name}")
    if _norm(src) == _norm(out):
        raise RuntimeError("The output cannot replace the source file.")
    spec = transcode_codec_spec(settings.codec_key)
    if spec is None or not spec.is_available() or not encoder_available(spec.libav_codec):
        raise RuntimeError(f"Codec {settings.codec_key!r} is not available on this platform.")
    container = out.suffix.lower().lstrip(".")
    if container not in containers_for_codec(settings.codec_key):
        raise RuntimeError(f"{spec.display_name} cannot be written to .{container}.")

    if _raw_kind(src):
        return _transcode_raw(src, out, settings, spec, container, progress, cancel_check, log)
    return _transcode_av(src, out, settings, spec, container, progress, cancel_check, log, label)


def _open_output(tmp: Path, container: str, settings: TranscodeSettings) -> Any:
    import av

    from .convert import _video_metadata

    out_c = av.open(str(tmp), mode="w", format=CONTAINER_FORMATS[container])
    meta = _video_metadata(
        settings.color_src if settings.color_enabled else "",
        settings.color_dst if settings.color_enabled else "",
        settings.codec_key,
    )
    out_c.metadata.update(meta)
    return out_c


def _add_video_stream(
    out_c: Any,
    spec: VideoCodecSpec,
    container: str,
    rate: Fraction,
    width: int,
    height: int,
    settings: TranscodeSettings,
) -> Any:
    opts, bit_rate = encoder_options(settings)
    if spec.libav_codec == "libsvtav1":
        # SVT-AV1 prints a config banner on every encode unless told otherwise.
        os.environ.setdefault("SVT_LOG", "1")
    out_v = out_c.add_stream(spec.libav_codec, rate=rate)
    out_v.width = width
    out_v.height = height
    out_v.pix_fmt = spec.pix_fmt
    out_v.thread_type = "AUTO"
    if bit_rate:
        out_v.bit_rate = bit_rate
    if opts:
        out_v.options = opts
    if codec_family(settings.codec_key) == "hevc" and container in ("mp4", "mov"):
        # hvc1 (not hev1) so QuickTime and Apple apps play the file.
        try:
            out_v.codec_tag = "hvc1"
        except Exception:
            pass
    return out_v


def _check_rate(container: str, rate: Fraction) -> None:
    if container == "mxf" and rate not in _MXF_RATES:
        raise RuntimeError(
            f"MXF needs a broadcast frame rate (23.976, 24, 25, 29.97, 30, 50, 59.94, 60), "
            f"not {float(rate):.3f}. Conform the frame rate or use .mov."
        )


def _transcode_av(
    src: Path,
    out: Path,
    settings: TranscodeSettings,
    spec: VideoCodecSpec,
    container: str,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
    label: str,
) -> Path:
    import av

    from .convert import _fps_to_rate
    from .video import probe_video

    w, h, src_fps, total = probe_video(str(src))
    ow, oh = transcode_dims(
        w, h, settings.resolution, settings.custom_width, settings.custom_height
    )
    src_rate = _as_fraction(_fps_to_rate(src_fps))
    conform = settings.fps > 0 and abs(settings.fps - float(src_rate)) > 1e-3
    rate = _as_fraction(_fps_to_rate(settings.fps)) if conform else src_rate
    _check_rate(container, rate)

    processor = _color_processor(settings, log)
    watermark = _open_watermark(settings, log)
    if log:
        size = f"{w}×{h}" if (w, h) == (ow, oh) else f"{w}×{h} → {ow}×{oh}"
        fps_note = f", {float(src_rate):.3f} → {float(rate):.3f} fps" if conform else ""
        log(f"{label}: {src.name} {size} {spec.display_name} .{container}{fps_note}")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _partial_path(out)
    n = 0

    in_c = av.open(str(src))
    try:
        in_v = in_c.streams.video[0]
        in_v.thread_type = "AUTO"
        in_a = in_c.streams.audio[0] if in_c.streams.audio else None

        action = AUDIO_NONE
        if in_a is not None and settings.audio != AUDIO_NONE:
            if conform:
                if log:
                    log("Audio dropped: conforming the frame rate changes the clip length.")
            else:
                action, note = resolve_audio(settings.audio, container, in_a.codec_context.name)
                if note and log:
                    log(note)

        out_c = _open_output(tmp, container, settings)
        ok = False
        try:
            tc = _timecode_for_rate(_source_timecode(in_c, in_v), rate)
            if tc:
                out_c.metadata["timecode"] = tc
            out_v = _add_video_stream(out_c, spec, container, rate, ow, oh, settings)
            if tc:
                out_v.metadata["timecode"] = tc
            _copy_color_tags(out_v, in_v.codec_context, matrix_only=processor is not None)

            out_a = None
            audio_offset: int | None = None
            if action == AUDIO_COPY and in_a is not None:
                try:
                    out_a = out_c.add_stream_from_template(in_a)
                except Exception as e:
                    out_a = None
                    action = AUDIO_NONE
                    if log:
                        log(f"Audio skipped ({e})")
            elif action != AUDIO_NONE and in_a is not None:
                try:
                    a_rate = in_a.codec_context.sample_rate
                    if container == "mxf":
                        a_rate = 48000
                    out_a = out_c.add_stream(action, rate=a_rate)
                    layout = in_a.codec_context.layout
                    if layout is not None:
                        out_a.layout = layout.name
                    if action == "aac":
                        out_a.bit_rate = AAC_BITRATE
                except Exception as e:
                    out_a = None
                    action = AUDIO_NONE
                    if log:
                        log(f"Audio skipped ({e})")

            pipe = _Pipeline(
                ow,
                oh,
                spec.pix_fmt,
                processor=processor,
                watermark=watermark,
                matrix=_sws_matrix(in_v.codec_context),
            )
            time_base = Fraction(1, 1) / rate

            def _encode_video(frame: Any) -> None:
                nonlocal n
                vf = pipe.from_av(frame)
                vf.pts = n
                vf.time_base = time_base
                n += 1
                for packet in out_v.encode(vf):
                    out_c.mux(packet)
                if progress:
                    progress(n, max(total, n))

            def _encode_audio(frame: Any) -> None:
                frame.pts = None
                assert out_a is not None
                for packet in out_a.encode(frame):
                    out_c.mux(packet)

            def _copy_audio(packet: Any) -> None:
                nonlocal audio_offset
                if packet.dts is None or packet.pts is None:
                    return
                if audio_offset is None:
                    audio_offset = int(packet.pts)
                packet.pts = int(packet.pts) - audio_offset
                packet.dts = int(packet.dts) - audio_offset
                packet.stream = out_a
                out_c.mux(packet)

            streams = [in_v] if out_a is None else [in_v, in_a]
            for packet in in_c.demux(*streams):
                if cancel_check and cancel_check():
                    raise ConversionCancelled()
                if packet.stream is in_v:
                    for frame in packet.decode():
                        _encode_video(frame)
                elif action == AUDIO_COPY:
                    _copy_audio(packet)
                else:
                    for frame in packet.decode():
                        _encode_audio(frame)

            for packet in out_v.encode():
                out_c.mux(packet)
            if out_a is not None and action != AUDIO_COPY:
                for packet in out_a.encode():
                    out_c.mux(packet)
            ok = n > 0
            if not ok:
                raise RuntimeError(f"No video frames decoded from {src.name}")
        finally:
            try:
                out_c.close()
            finally:
                if not ok:
                    tmp.unlink(missing_ok=True)
    finally:
        in_c.close()

    os.replace(tmp, out)
    if log:
        log(f"Wrote {out} ({n} frames)")
    return out


def _transcode_raw(
    src: Path,
    out: Path,
    settings: TranscodeSettings,
    spec: VideoCodecSpec,
    container: str,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
) -> Path:
    """Camera RAW via the R3D / BRAW SDK bridges (video only, no audio)."""
    from .convert import _fps_to_rate
    from .frame_source import open_ingest_source

    with open_ingest_source(str(src)) as source:
        info = source.info
        source.log_header(log)
        ow, oh = transcode_dims(
            info.width,
            info.height,
            settings.resolution,
            settings.custom_width,
            settings.custom_height,
        )
        src_rate = _as_fraction(_fps_to_rate(info.fps))
        rate = _as_fraction(_fps_to_rate(settings.fps)) if settings.fps > 0 else src_rate
        _check_rate(container, rate)
        processor = _color_processor(settings, log)
        if processor is None and log:
            hint = info.colorspace_hint or "the camera space"
            log(f"No color transform: frames stay in {hint}.")
        watermark = _open_watermark(settings, log)
        if log:
            log(
                f"Transcode: {src.name} {info.width}×{info.height} → {ow}×{oh} "
                f"{spec.display_name} .{container} (camera RAW, no audio)"
            )
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = _partial_path(out)
        pipe = _Pipeline(ow, oh, spec.pix_fmt, processor=processor, watermark=watermark, matrix=1)
        total = info.frame_count
        n = 0
        out_c = _open_output(tmp, container, settings)
        ok = False
        try:
            out_v = _add_video_stream(out_c, spec, container, rate, ow, oh, settings)
            try:
                out_v.codec_context.colorspace = 1
            except Exception:
                pass
            time_base = Fraction(1, 1) / rate
            for _idx, rgb, _attrs in source.iter_frames(cancel_check=cancel_check):
                if cancel_check and cancel_check():
                    raise ConversionCancelled()
                vf = pipe.from_float(rgb)
                vf.pts = n
                vf.time_base = time_base
                n += 1
                for packet in out_v.encode(vf):
                    out_c.mux(packet)
                if progress:
                    progress(n, max(total, n))
            for packet in out_v.encode():
                out_c.mux(packet)
            ok = n > 0
            if not ok:
                raise RuntimeError(f"No frames decoded from {src.name}")
        finally:
            try:
                out_c.close()
            finally:
                if not ok:
                    tmp.unlink(missing_ok=True)
    os.replace(tmp, out)
    if log:
        log(f"Wrote {out} ({n} frames)")
    return out
