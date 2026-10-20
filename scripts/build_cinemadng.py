#!/usr/bin/env python3
"""Build the small CinemaDNG reader (libcinemadng).

The library links only to the C runtime. It reads uncompressed and lossless
JPEG Bayer frames. It does not link the Adobe DNG SDK.

Usage::

  python3 scripts/build_cinemadng.py
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "native" / "cinemadng" / "cinemadng.c"
OUT_DIR = ROOT / "build" / "cinemadng"


def output_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libcinemadng.dylib"
    if system == "Windows":
        return "cinemadng.dll"
    return "libcinemadng.so"


def build() -> Path:
    if not SRC.is_file():
        raise SystemExit(f"missing source: {SRC}")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / output_name()
    system = platform.system()
    if system == "Windows":
        cl = shutil.which("cl")
        if not cl:
            raise SystemExit("cl.exe not found. Run from an MSVC developer prompt.")
        cmd = [
            cl,
            "/nologo",
            "/O2",
            "/std:c11",
            "/LD",
            f"/Fe:{out.name}",
            str(SRC),
        ]
        print(" ".join(cmd), flush=True)
        subprocess.run(cmd, cwd=OUT_DIR, check=True)
    else:
        cc = os.environ.get("CC")
        if not cc:
            cc = "clang" if system == "Darwin" else "gcc"
        cmd = [
            cc,
            "-shared",
            "-fPIC",
            "-O2",
            "-std=c11",
            "-fvisibility=hidden",
        ]
        if system != "Darwin":
            cmd.append("-s")
        cmd.extend(["-o", str(out), str(SRC)])
        print(" ".join(cmd), flush=True)
        subprocess.run(cmd, check=True)
    if not out.is_file():
        raise SystemExit(f"build did not produce {out}")
    print(f"built {out} ({out.stat().st_size} bytes)", flush=True)
    return out


def main() -> None:
    build()


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.returncode)
