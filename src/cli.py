from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .constants import (
    DEFAULT_DST_E2V,
    DEFAULT_DST_V2E,
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_START_FRAME,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    VIDEO_CODECS,
)
from .convert import run_exr_to_video, run_video_to_exr
from .ocio_utils import resolve_ocio_for_cli

_CODEC_KEYS = [k for k, *_ in VIDEO_CODECS]


def _resolve_config_source(ocio_arg: str | None) -> tuple[str, str]:
    """Return (config_source, config_path) for the pool workers."""
    if ocio_arg:
        return ("", str(Path(ocio_arg).expanduser()))
    env = os.environ.get("OCIO", "")
    if env and Path(env).expanduser().is_file():
        return ("", str(Path(env).expanduser()))
    from .ocio_utils import list_builtin_configs

    builtins = list_builtin_configs()
    recommended = [b for b in builtins if b[2]]
    name = recommended[0][0] if recommended else builtins[-1][0]
    return (name, "")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert between video and EXR sequences (PyAV + OCIO + OIIO).",
    )
    p.add_argument("--headless", action="store_true", help="Synonym for CLI mode.")
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="Launch the GUI, verify it initializes, then exit.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel workers (0 = auto, 1 = single-threaded)",
    )
    sub = p.add_subparsers(dest="command")

    v2e = sub.add_parser("video2exr", help="Video -> OCIO -> EXR sequence.")
    v2e.add_argument("-i", "--input", required=True)
    v2e.add_argument("-o", "--output-dir", required=True)
    v2e.add_argument("--ocio", default=None, help="OCIO config (overrides $OCIO / built-in)")
    v2e.add_argument("--src", default=DEFAULT_SRC_V2E)
    v2e.add_argument("--dst", default=DEFAULT_DST_V2E)
    v2e.add_argument(
        "--exr-compression",
        default=DEFAULT_EXR_COMPRESSION,
        choices=EXR_COMPRESSIONS,
        help=f"EXR compression (default: {DEFAULT_EXR_COMPRESSION})",
    )
    v2e.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Output scale factor (e.g. 0.5 for half res)",
    )
    v2e.add_argument(
        "--padding",
        type=int,
        default=DEFAULT_FRAME_PADDING,
        help=(
            "Frame number padding as # count"
            f" (default: {DEFAULT_FRAME_PADDING} = {'#' * DEFAULT_FRAME_PADDING})"
        ),
    )
    v2e.add_argument(
        "--start-frame",
        type=int,
        default=DEFAULT_START_FRAME,
        help=f"First frame number (default: {DEFAULT_START_FRAME})",
    )
    v2e.add_argument(
        "--frame-range",
        default="",
        help="Nuke-style frame range (e.g. 1-100, 1-50x2). Empty = all.",
    )

    e2v = sub.add_parser("exr2video", help="EXR sequence -> OCIO -> video.")
    e2v.add_argument("-i", "--input", required=True)
    e2v.add_argument("-o", "--output", required=True)
    e2v.add_argument("--fps", type=float, default=24.0)
    e2v.add_argument("--ocio", default=None, help="OCIO config (overrides $OCIO / built-in)")
    e2v.add_argument("--src", default=DEFAULT_SRC_E2V)
    e2v.add_argument("--dst", default=DEFAULT_DST_E2V)
    e2v.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Output scale factor (e.g. 0.5 for half res)",
    )
    e2v.add_argument(
        "--codec",
        default=DEFAULT_VIDEO_CODEC,
        choices=_CODEC_KEYS,
        help=f"Video codec (default: {DEFAULT_VIDEO_CODEC})",
    )
    e2v.add_argument(
        "--frame-range",
        default="",
        help="Nuke-style frame range (e.g. 1001-1100, 1-50x2). Empty = all.",
    )

    return p


def run_cli(args: argparse.Namespace) -> int:
    def _progress(cur: int, total: int) -> None:
        pct = int(100 * cur / total) if total else 0
        print(f"\r[{pct:3d}%] {cur}/{total}", end="", file=sys.stderr, flush=True)

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    try:
        cfg = resolve_ocio_for_cli(args.ocio)
        cs, cp = _resolve_config_source(args.ocio)

        frame_set: set[int] | None = None
        if getattr(args, "frame_range", ""):
            from .framerange import parse_frame_range

            frames = parse_frame_range(args.frame_range)
            if frames:
                frame_set = set(frames)

        if args.command == "video2exr":
            run_video_to_exr(
                args.input,
                Path(args.output_dir),
                cfg,
                args.src,
                args.dst,
                progress=_progress,
                log=_log,
                compression=args.exr_compression,
                workers=args.workers,
                config_source=cs,
                config_path=cp,
                scale=args.scale,
                padding=args.padding,
                start_frame=args.start_frame,
                frame_set=frame_set,
            )
        else:
            codec_key = args.codec
            codec_name = "libx264"
            pix_fmt = "yuv420p"
            for k, _display, c, p in VIDEO_CODECS:
                if k == codec_key:
                    codec_name, pix_fmt = c, p
                    break
            run_exr_to_video(
                args.input,
                Path(args.output),
                cfg,
                args.src,
                args.dst,
                args.fps,
                progress=_progress,
                log=_log,
                workers=args.workers,
                config_source=cs,
                config_path=cp,
                scale=args.scale,
                video_codec=codec_name,
                pix_fmt_out=pix_fmt,
                codec_key=codec_key,
                frame_set=frame_set,
            )
        print(file=sys.stderr)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    return 0
