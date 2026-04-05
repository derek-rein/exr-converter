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
