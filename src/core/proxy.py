"""Video → video proxy transcodes (DaVinci Resolve-style proxy generation).

Proxies are a straight decode → resize → encode with PyAV. There is no OCIO
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
from fractions import Fraction
from pathlib import Path
from typing import NamedTuple

import numpy as np

from .constants import video_codec_by_key
from .errors import ConversionCancelled

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


def _partial_path(output: Path) -> Path:
    """Hidden sibling written during encode, renamed on success."""
    return output.with_name(f".{output.stem}.partial{output.suffix}")


def _source_timecode(container, stream) -> str:
    for meta in (getattr(stream, "metadata", None), getattr(container, "metadata", None)):
        tc = (meta or {}).get("timecode")
        if tc:
            return str(tc)
    return ""


def _audio_codec_for(ext: str) -> str:
    # PCM keeps .mov proxies edit-friendly; MP4 cannot hold PCM.
    return "aac" if ext == ".mp4" else "pcm_s16le"


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

    Writes to a hidden ``.<name>.partial<ext>`` file and renames it into place
    when the encode finishes, so a watch folder or editor never sees a
    half-written proxy. Returns the final output path.

    *watermark_text* embeds an invisible TrustMark payload when it is not
    empty. The model downloads on first use. PyAV and ONNX Runtime stay
    inside this call so importing the proxy module does not load them.
    """
    import av

    from .convert import _configure_stream, _fps_to_rate, _video_metadata
    from .video import av_frame_reformat, probe_video

    src = Path(input_path)
    out = Path(output_path)
    if not is_proxy_source(src):
        raise RuntimeError(f"Not a supported proxy source: {src.name}")
    spec = video_codec_by_key(codec_key)
    if spec is None or not spec.is_available():
        raise RuntimeError(f"Proxy codec {codec_key!r} is not available on this platform.")
    if spec.libav_codec == "oxideav_prores":
        raise RuntimeError("oxideav ProRes is not offered for proxies.")

    w, h, fps, total = probe_video(str(src))
    ow, oh = proxy_dims(w, h, resolution)
    rate = _fps_to_rate(fps)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _partial_path(out)
    opts = dict(_PROXY_CODEC_OPTS.get(codec_key, {}))
    if codec_opts:
        opts.update(codec_opts)

    watermark = None
    if watermark_text.strip():
        from .trustmark.embed import FrameWatermark

        watermark = FrameWatermark(watermark_text, watermark_strength, log=log)
    if log:
        log(f"Proxy: {src.name} {w}×{h} → {ow}×{oh} {spec.display_name}")
        if watermark is not None:
            log(f"Invisible watermark: {watermark.text!r} at strength {watermark.strength:.2f}")

    in_c = av.open(str(src))
    try:
        in_v = in_c.streams.video[0]
        in_v.thread_type = "AUTO"
        in_a = in_c.streams.audio[0] if (audio and in_c.streams.audio) else None

        out_c = av.open(str(tmp), mode="w", format="mp4" if out.suffix == ".mp4" else "mov")
        ok = False
        try:
            out_c.metadata.update(_video_metadata(codec_key=codec_key))
            tc = _source_timecode(in_c, in_v)
            if tc:
                out_c.metadata["timecode"] = tc
            out_v = out_c.add_stream(spec.libav_codec, rate=rate)
            out_v.width = ow
            out_v.height = oh
            out_v.pix_fmt = spec.pix_fmt
            out_v.thread_type = "AUTO"
            _configure_stream(out_v, codec_key, opts)
            if tc:
                out_v.metadata["timecode"] = tc

            out_a = None
            if in_a is not None:
                try:
                    out_a = out_c.add_stream(
                        _audio_codec_for(out.suffix), rate=in_a.codec_context.sample_rate
                    )
                    layout = in_a.codec_context.layout
                    if layout is not None:
                        out_a.layout = layout.name
                except Exception as e:
                    out_a = None
                    if log:
                        log(f"Audio skipped ({e})")

            time_base = Fraction(1, 1) / Fraction(rate)
            n = 0

            def _encode_video(frame) -> None:
                nonlocal n
                if watermark is None:
                    vf = av_frame_reformat(frame, width=ow, height=oh, format=spec.pix_fmt)
                else:
                    rgb_frame = av_frame_reformat(frame, width=ow, height=oh, format="rgb24")
                    marked = np.ascontiguousarray(watermark.apply(rgb_frame.to_ndarray()))
                    vf = av.VideoFrame.from_ndarray(marked, format="rgb24")
                    vf = av_frame_reformat(vf, format=spec.pix_fmt)
                vf.pts = n
                vf.time_base = time_base
                n += 1
                for packet in out_v.encode(vf):
                    out_c.mux(packet)
                if progress:
                    progress(n, max(total, n))

            def _encode_audio(frame) -> None:
                frame.pts = None
                for packet in out_a.encode(frame):
                    out_c.mux(packet)

            streams = [in_v] if in_a is None or out_a is None else [in_v, in_a]
            for packet in in_c.demux(*streams):
                if cancel_check and cancel_check():
                    raise ConversionCancelled()
                for frame in packet.decode():
                    if packet.stream is in_v:
                        _encode_video(frame)
                    else:
                        _encode_audio(frame)

            for packet in out_v.encode():
                out_c.mux(packet)
            if out_a is not None:
                for packet in out_a.encode():
                    out_c.mux(packet)
            ok = n > 0
            if not ok:
                raise RuntimeError(f"No video frames decoded from {src.name}")
        finally:
            out_c.close()
            if not ok:
                tmp.unlink(missing_ok=True)
    finally:
        in_c.close()

    os.replace(tmp, out)
    if log:
        log(f"Wrote proxy {out} ({n} frames)")
    return out


def collect_proxy_sources(
    paths: list[str | os.PathLike[str]], *, recursive: bool = False
) -> list[Path]:
    """Expand files and folders into a sorted, de-duplicated list of proxy sources.

    Folders skip their own ``Proxy`` subfolder so proxies are never re-proxied.
    """
    found: dict[str, Path] = {}
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            it = p.rglob("*") if recursive else p.iterdir()
            for child in it:
                if PROXY_SUBFOLDER in child.relative_to(p).parts[:-1]:
                    continue
                if child.is_file() and is_proxy_source(child):
                    found[os.path.normcase(str(child.resolve()))] = child
        elif p.is_file() and is_proxy_source(p):
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
