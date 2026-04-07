from __future__ import annotations

import av


def probe_video(path: str) -> tuple[int, int, float, int]:
    """Return (width, height, fps, frame_count) using PyAV."""
    container = av.open(path)
    stream = container.streams.video[0]
    w, h = stream.width, stream.height
    fps = float(stream.average_rate) if stream.average_rate else 24.0
    n_frames = stream.frames
    if not n_frames and stream.duration and stream.time_base:
        n_frames = max(1, int(float(stream.duration * stream.time_base) * fps + 0.5))
    if not n_frames:
        n_frames = 1
    container.close()
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
            if vs:
                row["resolution"] = f"{vs.width}\u00d7{vs.height}"
                row["codec"] = vs.codec_context.name
                fps = float(vs.average_rate) if vs.average_rate else 0
                if fps:
                    row["fps"] = str(int(fps)) if fps == int(fps) else f"{fps:.3f}"
                else:
                    row["fps"] = ""
                if vs.frames and vs.frames > 0:
                    row["frames"] = str(vs.frames)
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

        for i, stream in enumerate(container.streams.video):
            pfx = "Video" if i == 0 else f"Video[{i}]"
            result[f"{pfx} codec"] = stream.codec_context.name
            long = stream.codec_context.codec.long_name
            if long:
                result[f"{pfx} codec (long)"] = long
            result[f"{pfx} resolution"] = f"{stream.width}\u00d7{stream.height}"
            if stream.codec_context.pix_fmt:
                result[f"{pfx} pix_fmt"] = stream.codec_context.pix_fmt
            fps = float(stream.average_rate) if stream.average_rate else None
            if fps:
                if fps == int(fps):
                    result[f"{pfx} fps"] = str(int(fps))
                else:
                    result[f"{pfx} fps"] = f"{fps:.3f}"
            if stream.frames:
                result[f"{pfx} frames"] = str(stream.frames)
            if stream.codec_context.profile:
                result[f"{pfx} profile"] = stream.codec_context.profile

        for i, stream in enumerate(container.streams.audio):
            pfx = "Audio" if i == 0 else f"Audio[{i}]"
            result[f"{pfx} codec"] = stream.codec_context.name
            result[f"{pfx} sample_rate"] = f"{stream.codec_context.sample_rate} Hz"
            result[f"{pfx} channels"] = str(stream.codec_context.channels)

        container.close()
    except Exception as e:
        result["error"] = str(e)
    return result
