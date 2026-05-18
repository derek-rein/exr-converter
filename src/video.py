from __future__ import annotations

import av

# libavformat probe limits used when av.open()'s defaults (≈5 MB / 5 s) miss
# stream parameters — typical for vendor-tagged MXFs (e.g. Sony Venice
# X-OCN/XAVC) whose codec metadata sits past the default probe window.
# PyAV bundles libavformat, so this retrieves exactly the data ffprobe
# would, without a subprocess.
_DEEP_PROBE_OPTS = {"probesize": "100M", "analyzeduration": "100M"}


def _stream_dims(stream) -> tuple[int, int]:
    """Best-effort (width, height) without raising; returns (0, 0) on failure."""
    for getter in (
        lambda: (stream.width, stream.height),
        lambda: (stream.codec_context.coded_width, stream.codec_context.coded_height),
        lambda: (stream.codec_context.width, stream.codec_context.height),
    ):
        try:
            w, h = getter()
            if w and h:
                return int(w), int(h)
        except (AttributeError, av.error.FFmpegError):
            continue
    return 0, 0


def _decode_one_frame_dims(container) -> tuple[int, int]:
    """Decode a single video frame to learn its dimensions."""
    try:
        for frame in container.decode(video=0):
            return int(frame.width), int(frame.height)
    except (av.error.FFmpegError, StopIteration, RuntimeError):
        pass
    return 0, 0


def _stream_basics(stream) -> tuple[int, int, float, int, str, str]:
    """Pull (w, h, fps, n_frames, codec_name, pix_fmt) from a PyAV video stream."""
    w, h = _stream_dims(stream)
    try:
        fps = float(stream.average_rate) if stream.average_rate else 0.0
    except (AttributeError, av.error.FFmpegError):
        fps = 0.0
    try:
        n_frames = stream.frames or 0
    except (AttributeError, av.error.FFmpegError):
        n_frames = 0
    try:
        codec_name = stream.codec_context.name or ""
    except (AttributeError, av.error.FFmpegError):
        codec_name = ""
    try:
        pix_fmt = stream.codec_context.pix_fmt or ""
    except (AttributeError, av.error.FFmpegError):
        pix_fmt = ""
    return w, h, fps, n_frames, codec_name, pix_fmt


def probe_video(path: str) -> tuple[int, int, float, int]:
    """Return (width, height, fps, frame_count) using PyAV.

    Falls back through codec_context, a deep libavformat probe, and a
    single-frame decode when the default probe doesn't surface stream
    attributes (e.g. Sony Venice MXFs whose vendor-tagged codec parameters
    live past the default 5 MB / 5 s probe window).
    """
    duration = time_base = None

    def _gather(opts: dict | None) -> tuple[int, int, float, int]:
        nonlocal duration, time_base
        container = av.open(path, options=opts) if opts else av.open(path)
        try:
            stream = container.streams.video[0]
            w, h, fps, n_frames, _codec, _pix = _stream_basics(stream)
            try:
                duration = duration or stream.duration
                time_base = time_base or stream.time_base
            except (AttributeError, av.error.FFmpegError):
                pass
            if not (w and h):
                w2, h2 = _decode_one_frame_dims(container)
                if w2 and h2:
                    w, h = w2, h2
            return w, h, fps, n_frames
        finally:
            container.close()

    w, h, fps, n_frames = _gather(None)
    if not (w and h) or not fps or not n_frames:
        w2, h2, fps2, n2 = _gather(_DEEP_PROBE_OPTS)
        w = w or w2
        h = h or h2
        fps = fps or fps2
        n_frames = n_frames or n2

    if not fps:
        fps = 24.0
    if not n_frames and duration and time_base:
        n_frames = max(1, int(float(duration * time_base) * fps + 0.5))
    if not n_frames:
        n_frames = 1
    if not (w and h):
        raise RuntimeError(
            f"Could not determine video dimensions for {path!r}. "
            "If this is a Sony Venice/F55 X-OCN MXF, FFmpeg cannot decode "
            "it directly — transcode to ProRes/DNxHR first."
        )
    return w, h, fps, n_frames


def guess_video_colorspace_candidates(path: str) -> list[str]:
    """Return a ranked list of OCIO color space name candidates for the video.

    Uses codec, pixel format, and color metadata to infer the transfer
    characteristics. The caller should try each candidate through alias
    resolution until one matches the active OCIO config.
    """
    try:
        container = av.open(path)
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        pix_fmt = stream.codec_context.pix_fmt or ""
        color_trc = str(getattr(stream.codec_context, "color_trc", "") or "")
        container.close()
    except Exception:
        return []

    is_10bit = "10" in pix_fmt or "12" in pix_fmt or "16" in pix_fmt

    if "log" in color_trc.lower():
        return ["Cineon"]
    if "linear" in color_trc.lower():
        return ["scene_linear"]
    if "smpte2084" in color_trc.lower() or "2084" in color_trc:
        return ["Output - Rec.2100-PQ"]
    if "arib-std-b67" in color_trc.lower() or "hlg" in color_trc.lower():
        return ["Output - Rec.2100-HLG"]

    if codec == "ffv1" and is_10bit:
        return ["scene_linear"]

    return [
        "Output - Rec.709",
        "Rec.1886 Rec.709 - Display",
        "Gamma 2.4 Rec.709 - Texture",
        "sRGB - Display",
    ]


_VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".mxf",
    ".webm",
    ".m4v",
    ".ts",
}


def scan_video_files(directory: str) -> list[dict[str, str]]:
    """Return summary dicts for every video file in *directory*.

    Each dict: name, resolution, codec, fps, duration, path (full).
    """
    from pathlib import Path

    results: list[dict[str, str]] = []
    try:
        entries = sorted(Path(directory).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return results

    for entry in entries:
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _VIDEO_SUFFIXES:
            continue
        row: dict[str, str] = {"name": entry.name, "path": str(entry)}
        try:
            container = av.open(str(entry))
            vs = container.streams.video[0] if container.streams.video else None
            fps = 0.0
            vw = vh = 0
            nframes = 0
            codec_name = ""
            if vs:
                vw, vh, fps, nframes, codec_name, _pix = _stream_basics(vs)

            if vs and (not (vw and vh) or not fps or not codec_name):
                # Default probe was too shallow — retry with a deeper one.
                container.close()
                container = av.open(str(entry), options=_DEEP_PROBE_OPTS)
                vs = container.streams.video[0] if container.streams.video else None
                if vs:
                    w2, h2, fps2, n2, c2, _ = _stream_basics(vs)
                    vw = vw or w2
                    vh = vh or h2
                    fps = fps or fps2
                    nframes = nframes or n2
                    codec_name = codec_name or c2

            if vs:
                row["resolution"] = f"{vw}\u00d7{vh}" if vw and vh else ""
                row["codec"] = codec_name
                if fps:
                    row["fps"] = str(int(fps)) if fps == int(fps) else f"{fps:.3f}"
                else:
                    row["fps"] = ""
                if nframes > 0:
                    row["frames"] = str(nframes)
            if container.duration:
                secs = container.duration / av.time_base
                mins, s = divmod(int(secs), 60)
                hrs, mins = divmod(mins, 60)
                if hrs:
                    row["duration"] = f"{hrs}:{mins:02d}:{s:02d}"
                else:
                    row["duration"] = f"{mins}:{s:02d}"
                if "frames" not in row and fps > 0:
                    row["frames"] = str(int(round(secs * fps)))
            else:
                row["duration"] = ""
            container.close()
        except Exception:
            row.setdefault("resolution", "")
            row.setdefault("codec", "")
            row.setdefault("fps", "")
            row.setdefault("duration", "")
            row.setdefault("frames", "")
        results.append(row)
    return results


def probe_video_metadata(path: str) -> dict[str, str]:
    """Return a dict of human-readable video metadata via PyAV."""
    result: dict[str, str] = {}
    try:
        container = av.open(path)
        result["Format"] = container.format.long_name or container.format.name

        if container.duration:
            secs = container.duration / av.time_base
            mins, s = divmod(int(secs), 60)
            hrs, mins = divmod(mins, 60)
            if hrs:
                result["Duration"] = f"{hrs}:{mins:02d}:{s:02d}"
            else:
                result["Duration"] = f"{mins}:{s:02d}"
            result["Duration (s)"] = f"{secs:.2f}"

        if container.bit_rate:
            mbps = container.bit_rate / 1_000_000
            result["Bitrate"] = f"{mbps:.2f} Mbps"

        for meta_key, meta_val in container.metadata.items():
            result[f"meta:{meta_key}"] = str(meta_val)

        deep_streams: list | None = None
        for i, stream in enumerate(container.streams.video):
            pfx = "Video" if i == 0 else f"Video[{i}]"
            sw, sh, fps, sframes, codec_name, pix_fmt = _stream_basics(stream)
            if codec_name:
                result[f"{pfx} codec"] = codec_name
            try:
                long = stream.codec_context.codec.long_name
                if long:
                    result[f"{pfx} codec (long)"] = long
            except (AttributeError, av.error.FFmpegError):
                pass
            try:
                profile = stream.codec_context.profile
            except (AttributeError, av.error.FFmpegError):
                profile = None

            if i == 0 and (not (sw and sh) or not fps or not codec_name):
                # Default probe didn't surface enough — re-open with a deep
                # libavformat probe and pull fresh basics.  PyAV bundles
                # libavformat, so we don't need ffprobe.
                if deep_streams is None:
                    try:
                        deep = av.open(path, options=_DEEP_PROBE_OPTS)
                        deep_streams = list(deep.streams.video)
                        deep.close()
                    except Exception:
                        deep_streams = []
                if i < len(deep_streams):
                    dw, dh, dfps, dn, dcodec, dpix = _stream_basics(deep_streams[i])
                    sw = sw or dw
                    sh = sh or dh
                    fps = fps or dfps
                    sframes = sframes or dn
                    if dcodec and not result.get(f"{pfx} codec"):
                        result[f"{pfx} codec"] = dcodec
                    if dpix and not pix_fmt:
                        pix_fmt = dpix

            if sw and sh:
                result[f"{pfx} resolution"] = f"{sw}\u00d7{sh}"
            if pix_fmt:
                result[f"{pfx} pix_fmt"] = pix_fmt
            if fps:
                if fps == int(fps):
                    result[f"{pfx} fps"] = str(int(fps))
                else:
                    result[f"{pfx} fps"] = f"{fps:.3f}"
            if sframes:
                result[f"{pfx} frames"] = str(sframes)
            if profile:
                result[f"{pfx} profile"] = profile

        for i, stream in enumerate(container.streams.audio):
            pfx = "Audio" if i == 0 else f"Audio[{i}]"
            result[f"{pfx} codec"] = stream.codec_context.name
            result[f"{pfx} sample_rate"] = f"{stream.codec_context.sample_rate} Hz"
            result[f"{pfx} channels"] = str(stream.codec_context.channels)

        container.close()
    except Exception as e:
        result["error"] = str(e)
    return result
