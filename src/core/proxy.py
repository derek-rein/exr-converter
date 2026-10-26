"""Video → video proxy transcodes (DaVinci Resolve-style proxy generation).

Proxies are a straight decode → resize → encode with PyAV, through the shared
transcode engine in :mod:`src.core.transcode`. There is no OCIO
step: a proxy keeps the source's look (log stays log) so an editor can relink
it to the camera original. Timecode and audio are carried across when present.

An optional invisible TrustMark watermark is applied to every output frame
after resize, on the RGB image the codec receives (the pixels the proxy
shows). There is no view transform before that step.

Output defaults to a ``Proxy`` folder next to each source clip with the same
basename, which is where Resolve looks when relinking proxy media.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from .constants import video_codec_by_key

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]

# Codecs offered for proxies, lightest first. Each key must exist in
# ``VIDEO_CODECS``; platform-only presets (VideoToolbox) are filtered at runtime.
PROXY_CODEC_KEYS: tuple[str, ...] = (
    "prores_proxy",
    "prores_lt",
    "prores_vt_proxy",
    "prores_vt_lt",
    "dnxhr_lb",
    "dnxhr_sq",
    "h264",
    "hevc_8",
)
DEFAULT_PROXY_CODEC = "prores_proxy"

# Proxy-friendly encoder settings: fast and small, not delivery quality.
_PROXY_CODEC_OPTS: dict[str, dict[str, str]] = {
    "h264": {"crf": "23", "preset": "veryfast"},
    "hevc_8": {"crf": "26", "preset": "fast"},
}


class ProxyResolution(NamedTuple):
    """One proxy size choice: a scale factor, or a maximum output height."""

    key: str
    label: str
    scale: float = 1.0
    max_height: int = 0


PROXY_RESOLUTIONS: tuple[ProxyResolution, ...] = (
    ProxyResolution("half", "Half", scale=0.5),
    ProxyResolution("quarter", "Quarter", scale=0.25),
    ProxyResolution("eighth", "One-eighth", scale=0.125),
    ProxyResolution("original", "Original", scale=1.0),
    ProxyResolution("1080", "Fit 1080p", max_height=1080),
    ProxyResolution("720", "Fit 720p", max_height=720),
    ProxyResolution("540", "Fit 540p", max_height=540),
)
DEFAULT_PROXY_RESOLUTION = "half"

# Resolve keeps proxies in a ``Proxy`` folder beside the camera originals.
PROXY_SUBFOLDER = "Proxy"

PROXY_VIDEO_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".mxf", ".webm", ".m4v", ".ts", ".mts", ".m2ts"}
)


def available_proxy_codecs() -> list:
    """Proxy codec specs usable on this OS, in ``PROXY_CODEC_KEYS`` order."""
    out = []
    for key in PROXY_CODEC_KEYS:
        spec = video_codec_by_key(key)
        if spec is not None and spec.is_available():
            out.append(spec)
    return out


def proxy_resolution_by_key(key: str) -> ProxyResolution:
    for res in PROXY_RESOLUTIONS:
        if res.key == key:
            return res
    return proxy_resolution_by_key(DEFAULT_PROXY_RESOLUTION)


def proxy_dims(width: int, height: int, resolution: str) -> tuple[int, int]:
    """Even (w, h) for *resolution*. Never upscales the source."""
    res = proxy_resolution_by_key(resolution)
    if res.max_height:
        scale = min(1.0, res.max_height / height) if height > 0 else 1.0
    else:
        scale = min(1.0, res.scale)
    w = max(2, int(width * scale + 0.5))
    h = max(2, int(height * scale + 0.5))
    return w - w % 2, h - h % 2


def proxy_extension(codec_key: str) -> str:
    """Container extension for a proxy codec (H.264 / HEVC → ``.mp4``, else ``.mov``)."""
    if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
        return ".mp4"
    return ".mov"


def default_proxy_output(
    source: str | os.PathLike[str],
    codec_key: str = DEFAULT_PROXY_CODEC,
    *,
    output_dir: str | os.PathLike[str] | None = None,
    suffix: str = "",
) -> Path:
    """Where the proxy for *source* goes.

    Without *output_dir*, ``<source_dir>/Proxy/<stem><suffix><ext>``.
    """
    src = Path(source)
    folder = Path(output_dir) if output_dir else src.parent / PROXY_SUBFOLDER
    return folder / f"{src.stem}{suffix}{proxy_extension(codec_key)}"


def is_proxy_source(path: str | os.PathLike[str]) -> bool:
    """True when *path* names a video PyAV can safely transcode to a proxy."""
    from .video import is_ignored_media_filename, is_libav_unsafe_media

    p = Path(path)
    if p.name.startswith(".") or is_ignored_media_filename(p.name):
        return False
    if is_libav_unsafe_media(str(p)):
        return False
    return p.suffix.lower() in PROXY_VIDEO_SUFFIXES


def run_video_to_proxy(
    input_path: str | os.PathLike[str],
    output_path: str | os.PathLike[str],
    *,
    codec_key: str = DEFAULT_PROXY_CODEC,
    resolution: str = DEFAULT_PROXY_RESOLUTION,
    audio: bool = True,
    codec_opts: dict[str, str] | None = None,
    watermark_text: str = "",
    watermark_strength: float = 1.0,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
) -> Path:
    """Transcode *input_path* into a proxy at *output_path*.

    Runs the shared :func:`~src.core.transcode.run_transcode` engine with proxy
    settings: no color transform, proxy codec options, and PCM audio in
    ``.mov`` (AAC in ``.mp4``). The engine writes to a hidden
    ``.<name>.partial<ext>`` file and renames it into place when the encode
    finishes, so a watch folder or editor never sees a half-written proxy.
    Returns the final output path.

    *watermark_text* embeds an invisible TrustMark payload when it is not
    empty. The model downloads on first use. PyAV and ONNX Runtime stay
    inside this call so importing the proxy module does not load them.
    """
    from .transcode import AUDIO_AAC, AUDIO_NONE, AUDIO_PCM16, TranscodeSettings, run_transcode

    src = Path(input_path)
    out = Path(output_path)
    if not is_proxy_source(src):
        raise RuntimeError(f"Not a supported proxy source: {src.name}")
    spec = video_codec_by_key(codec_key)
    if spec is None or not spec.is_available():
        raise RuntimeError(f"Proxy codec {codec_key!r} is not available on this platform.")
    if spec.libav_codec == "oxideav_prores":
        raise RuntimeError("oxideav ProRes is not offered for proxies.")

    opts = dict(_PROXY_CODEC_OPTS.get(codec_key, {}))
    if codec_opts:
        opts.update(codec_opts)
    if audio:
        audio_mode = AUDIO_AAC if out.suffix.lower() == ".mp4" else AUDIO_PCM16
    else:
        audio_mode = AUDIO_NONE
    settings = TranscodeSettings(
        codec_key=codec_key,
        container=out.suffix.lower().lstrip("."),
        resolution=resolution,
        audio=audio_mode,
        watermark_text=watermark_text if watermark_text.strip() else "",
        watermark_strength=watermark_strength,
        codec_opts=opts or None,
    )
    return run_transcode(
        src,
        out,
        settings,
        progress=progress,
        cancel_check=cancel_check,
        log=log,
        label="Proxy",
    )


def collect_proxy_sources(
    paths: list[str | os.PathLike[str]],
    *,
    recursive: bool = False,
    accept: Callable[[Path], bool] | None = None,
) -> list[Path]:
    """Expand files and folders into a sorted, de-duplicated list of proxy sources.

    Folders skip their own ``Proxy`` subfolder so proxies are never re-proxied.
    *accept* replaces :func:`is_proxy_source` as the file filter (the
    Transcode tab passes its own).
    """
    ok = accept or is_proxy_source
    found: dict[str, Path] = {}
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            for child in it:
                if PROXY_SUBFOLDER in child.relative_to(p).parts[:-1]:
                    continue
                if child.is_file() and ok(child):
                    found[os.path.normcase(str(child.resolve()))] = child
        elif p.is_file() and ok(p):
            found[os.path.normcase(str(p.resolve()))] = p
    return sorted(found.values())


def check_watch_output(
    watch_dir: str | os.PathLike[str], output_dir: str | os.PathLike[str] | None
) -> None:
    """Raise :class:`ValueError` if proxies would land back in the watched folder.

    Writing proxies into the folder being watched would make every proxy look
    like a new clip and loop forever. Subfolders are fine: the watcher skips
    ``Proxy`` folders and the custom output folder.
    """
    if not output_dir:
        return
    a = os.path.normcase(os.path.abspath(os.fspath(watch_dir)))
    b = os.path.normcase(os.path.abspath(os.fspath(output_dir)))
    if a == b:
        raise ValueError(
            "The proxy output folder cannot be the watched folder itself. "
            "Leave it empty to use a Proxy subfolder, or pick another folder."
        )
