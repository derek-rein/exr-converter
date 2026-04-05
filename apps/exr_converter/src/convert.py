from __future__ import annotations

import os
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import av
import numpy as np
import PyOpenColorIO as OCIO

from .exr_io import read_exr, write_exr
from .ocio_utils import make_cpu_processor
from .pool import process_frame_e2v, process_frame_v2e
from .sequence import find_exr_sequence
from .video import probe_video

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]

_DEFAULT_WORKERS = min(os.cpu_count() or 4, 8)


def _scaled_dims(w: int, h: int, scale: float) -> tuple[int, int]:
    """Return even-dimensioned (w, h) after applying scale."""
    if scale >= 1.0:
        return w, h
    sw = max(2, int(w * scale + 0.5))
    sh = max(2, int(h * scale + 0.5))
    sw -= sw % 2
    sh -= sh % 2
    return sw, sh


def _configure_stream(stream, codec_key: str) -> None:
    """Set codec-specific options on a PyAV output stream."""
    if codec_key in ("prores", "prores_4444"):
        profile = "3" if codec_key == "prores" else "4"
        stream.options = {"profile": profile, "vendor": "apl0"}
    elif codec_key == "h264":
        stream.options = {"crf": "18", "preset": "medium"}
    elif codec_key.startswith("dnxhr"):
        profile = "dnxhr_hq" if codec_key == "dnxhr_hq" else "dnxhr_hqx"
        stream.options = {"profile": profile}
    elif codec_key == "ffv1":
        stream.options = {"slicecrc": "1"}


# ---- video -> exr ----------------------------------------------------------


def run_video_to_exr(
    video_path: str,
    output_dir: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    dst_space: str,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
    compression: str = "dwaa",
    workers: int = 0,
    config_source: str = "",
    config_path: str = "",
    scale: float = 1.0,
) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    w, h, _fps, total = probe_video(video_path)
    ow, oh = _scaled_dims(w, h, scale)
    if log:
        res_info = f"{w}x{h}" if scale >= 1.0 else f"{w}x{h} \u2192 {ow}x{oh}"
        log(f"Input: {video_path}  ({res_info}, ~{total} frames)")

    n_workers = workers if workers > 0 else _DEFAULT_WORKERS

    if n_workers <= 1 or (not config_source and not config_path):
        _v2e_serial(
            video_path,
            output_dir,
            ocio_cfg,
            src_space,
            dst_space,
            progress,
            cancel_check,
            log,
            compression,
            ow,
            oh,
            total,
            scale,
        )
        return

    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  ({n_workers} workers)")

    stem = Path(video_path).stem
    container = av.open(video_path)
    stream = container.streams.video[0]
    max_inflight = n_workers * 2
    do_resize = scale < 1.0

    idx = 0
    try:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            pending = {}
            frame_iter = container.decode(stream)
            finished = 0

            def _submit_batch() -> None:
                nonlocal idx
                while len(pending) < max_inflight:
                    try:
                        frame = next(frame_iter)
                    except StopIteration:
                        return
                    if cancel_check and cancel_check():
                        raise RuntimeError("Cancelled")
                    idx += 1
                    if do_resize:
                        frame = frame.reformat(width=ow, height=oh)
                    rgb_u16 = frame.to_ndarray(format="rgb48le")
                    rgb_f32 = rgb_u16.astype(np.float32) * (1.0 / 65535.0)
                    out_path = str(output_dir / f"{stem}.{idx:04d}.exr")
                    fut = pool.submit(
                        process_frame_v2e,
                        idx,
                        rgb_f32,
                        out_path,
                        compression,
                        config_source,
                        config_path,
                        src_space,
                        dst_space,
                    )
                    pending[fut] = idx

            _submit_batch()
            while pending:
                done = next(iter(as_completed(pending)))
                done.result()
                del pending[done]
                finished += 1
                if progress:
                    progress(finished, max(total, idx))
                _submit_batch()
    finally:
        container.close()

    if idx == 0:
        raise RuntimeError("No frames decoded from the video file.")
    if log:
        log(f"Wrote {idx} EXR frames to {output_dir}")


def _v2e_serial(
    video_path: str,
    output_dir: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    dst_space: str,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
    compression: str,
    w: int,
    h: int,
    total: int,
    scale: float = 1.0,
) -> None:
    cpu = make_cpu_processor(ocio_cfg, src_space, dst_space)
    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  (single-threaded)")

    stem = Path(video_path).stem
    container = av.open(video_path)
    stream = container.streams.video[0]
    frame_buf = np.empty((h, w, 3), dtype=np.float32)
    do_resize = scale < 1.0

    idx = 0
    try:
        for frame in container.decode(stream):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            idx += 1
            if do_resize:
                frame = frame.reformat(width=w, height=h)
            rgb_u16 = frame.to_ndarray(format="rgb48le")
            np.multiply(rgb_u16, 1.0 / 65535.0, out=frame_buf, casting="unsafe")
            desc = OCIO.PackedImageDesc(frame_buf, w, h, 3)
            cpu.apply(desc)
            out_path = output_dir / f"{stem}.{idx:04d}.exr"
            write_exr(str(out_path), frame_buf, compression=compression)
            if progress:
                progress(idx, max(total, idx))
    finally:
        container.close()

    if idx == 0:
        raise RuntimeError("No frames decoded from the video file.")
    if log:
        log(f"Wrote {idx} EXR frames to {output_dir}")


# ---- exr -> video ----------------------------------------------------------


def run_exr_to_video(
    input_spec: str,
    output_video: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    dst_space: str,
    fps: float,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
    video_codec: str = "libx264",
    pix_fmt_out: str = "yuv420p",
    workers: int = 0,
    config_source: str = "",
    config_path: str = "",
    scale: float = 1.0,
    codec_key: str = "h264",
) -> None:
    paths, basename = find_exr_sequence(input_spec)
    total = len(paths)
    if total == 0:
        raise RuntimeError("No EXR frames to encode.")

    first = read_exr(paths[0])
    h, w = first.shape[:2]
    ow, oh = _scaled_dims(w, h, scale)
    if log:
        res_info = f"{w}x{h}" if scale >= 1.0 else f"{w}x{h} \u2192 {ow}x{oh}"
        log(f"Sequence: {basename} ({total} frames, {res_info})")

    n_workers = workers if workers > 0 else _DEFAULT_WORKERS

    if n_workers <= 1 or (not config_source and not config_path):
        _e2v_serial(
            paths,
            output_video,
            ocio_cfg,
            src_space,
            dst_space,
            fps,
            progress,
            cancel_check,
            log,
            video_codec,
            pix_fmt_out,
            ow,
            oh,
            total,
            scale,
            codec_key,
        )
        return

    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  ({n_workers} workers)")

    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    container = av.open(str(output_video), mode="w")
    stream = container.add_stream(video_codec, rate=int(fps))
    stream.width = ow
    stream.height = oh
    stream.pix_fmt = pix_fmt_out
    _configure_stream(stream, codec_key)

    max_inflight = n_workers * 2
    do_resize = scale < 1.0

    try:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            pending = {}
            ready: dict[int, np.ndarray] = {}
            next_encode = 1
            submit_idx = 0

            def _submit_batch() -> None:
                nonlocal submit_idx
                while len(pending) < max_inflight and submit_idx < total:
                    if cancel_check and cancel_check():
                        raise RuntimeError("Cancelled")
                    path = paths[submit_idx]
                    frame_idx = submit_idx + 1
                    submit_idx += 1
                    fut = pool.submit(
                        process_frame_e2v,
                        frame_idx,
                        path,
                        config_source,
                        config_path,
                        src_space,
                        dst_space,
                    )
                    pending[fut] = frame_idx

            def _drain_ready() -> None:
                nonlocal next_encode
                while next_encode in ready:
                    rgb_u16 = ready.pop(next_encode)
                    vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
                    if do_resize:
                        vf = vf.reformat(width=ow, height=oh)
                    for packet in stream.encode(vf):
                        container.mux(packet)
                    if progress:
                        progress(next_encode, total)
                    next_encode += 1

            _submit_batch()
            while pending:
                done = next(iter(as_completed(pending)))
                pending.pop(done)
                fidx, rgb_u16 = done.result()
                ready[fidx] = rgb_u16
                _drain_ready()
                _submit_batch()

            _drain_ready()

        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    if log:
        log(f"Wrote {output_video} ({total} frames, {fps} fps)")


def _e2v_serial(
    paths: list[str],
    output_video: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    dst_space: str,
    fps: float,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
    video_codec: str,
    pix_fmt_out: str,
    w: int,
    h: int,
    total: int,
    scale: float = 1.0,
    codec_key: str = "h264",
) -> None:
    cpu = make_cpu_processor(ocio_cfg, src_space, dst_space)
    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  (single-threaded)")

    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    container = av.open(str(output_video), mode="w")
    stream = container.add_stream(video_codec, rate=int(fps))
    stream.width = w
    stream.height = h
    stream.pix_fmt = pix_fmt_out
    _configure_stream(stream, codec_key)

    do_resize = scale < 1.0

    try:
        for idx, path in enumerate(paths, 1):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            rgb = read_exr(path)
            frame_buf = np.ascontiguousarray(rgb, dtype=np.float32)
            fh, fw = frame_buf.shape[:2]
            desc = OCIO.PackedImageDesc(frame_buf, fw, fh, 3)
            cpu.apply(desc)
            rgb_u16 = np.clip(frame_buf * 65535.0, 0.0, 65535.0).astype(np.uint16)
            vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
            if do_resize:
                vf = vf.reformat(width=w, height=h)
            for packet in stream.encode(vf):
                container.mux(packet)
            if progress:
                progress(idx, total)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    if log:
        log(f"Wrote {output_video} ({total} frames, {fps} fps)")
