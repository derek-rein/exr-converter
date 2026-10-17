#!/usr/bin/env python3
"""Read an invisible TrustMark watermark from a video.

Samples frames, majority-votes the raw bits, then applies TrustMark's BCH_5
text decoder. The first run downloads decoder_Q.onnx into the per-user cache
(or EXR_TRUSTMARK_CACHE). EXR Converter does not ship that file.

Examples:
  uv run python scripts/trustmark_detect.py proxy.mov
  uv run python scripts/trustmark_detect.py proxy.mp4 --samples 15
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.core.trustmark.detect import detect_video  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="Video to read (proxy, H.264, ProRes, ...)")
    parser.add_argument(
        "--samples",
        type=int,
        default=9,
        help="How many frames to sample across the file (default: 9)",
    )
    args = parser.parse_args(argv)
    path = Path(args.video)
    if not path.is_file():
        print(f"Error: video not found: {path}", file=sys.stderr)
        return 2
    if args.samples < 1:
        print("Error: --samples must be at least 1.", file=sys.stderr)
        return 2

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    try:
        found = detect_video(path, samples=args.samples, log=_log)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if found.detected:
        print(found.text)
        print(
            f"detected: yes  frames: {found.frames}  bitflips: {found.bitflips}",
            file=sys.stderr,
        )
        return 0
    print("detected: no", file=sys.stderr)
    print(f"frames: {found.frames}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
