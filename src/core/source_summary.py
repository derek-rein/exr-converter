"""Short, human facts about a source clip or sequence, for the source card.

Pure: numbers in, strings out. Unknown values (zero) are left out, so the
card never shows "0 × 0" or "0 fps".
"""

from __future__ import annotations


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def format_fps(fps: float) -> str:
    """``24 fps``, ``23.976 fps``: up to three decimals, no trailing zeros."""
    text = f"{fps:.3f}".rstrip("0").rstrip(".")
    return f"{text} fps"


def format_duration(seconds: float) -> str:
    """``m:ss`` or ``h:mm:ss``; empty for zero or negative."""
    if seconds <= 0:
        return ""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def video_details(width: int, height: int, frame_count: int, fps: float) -> list[str]:
    """Resolution, length and rate of a video, most identifying first."""
    out: list[str] = []
    if width > 0 and height > 0:
        out.append(f"{width} × {height}")
    if frame_count > 0:
        out.append(_plural(frame_count, "frame"))
    if fps > 0:
        out.append(format_fps(fps))
    if frame_count > 1 and fps > 0:
        out.append(format_duration(frame_count / fps))
    return [part for part in out if part]


def sequence_details(frame_count: int, first: int, last: int, extension: str) -> list[str]:
    """File type, length and range of an image sequence; flags missing frames."""
    out = [extension.lstrip(".").upper()] if extension else []
    if frame_count <= 0:
        return out
    out.append(_plural(frame_count, "frame"))
    if first == last:
        out.append(f"frame {first}")
        return out
    out.append(f"{first}–{last}")
    missing = (last - first + 1) - frame_count
    if missing > 0:
        out.append(f"{missing} missing")
    return out
