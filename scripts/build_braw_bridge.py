#!/usr/bin/env python3
"""Build the optional Blackmagic RAW bridge shared library (wrapper).

The bridge source is confidential and lives in the private repo
derek-rein/braw-sdk-private (bridge/), not in this MIT repository. This wrapper
locates it (BRAW_BRIDGE_SRC, ~/code/braw-sdk-private/bridge, or downloads the
repo tarball at BRAW_BRIDGE_REF using BRAW_SDK_READ_TOKEN / gh auth) and runs
its build script. Output: build/braw/.

Usage:
  BRAW_SDK_ROOT=/path/to/sdk python3 scripts/build_braw_bridge.py [-v] [--sdk PATH] [--out DIR]
  python3 scripts/build_braw_bridge.py --dry-run   # only locate/fetch the source

See docs/braw.md.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from private_bridge import ROOT, find_bridge  # noqa: E402

DEFAULT_REPO = "derek-rein/braw-sdk-private"
DEFAULT_REF = "bridge-v1"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sdk", default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "braw")
    ap.add_argument("--repo", default=os.environ.get("BRAW_BRIDGE_REPO", DEFAULT_REPO))
    ap.add_argument("--ref", default=os.environ.get("BRAW_BRIDGE_REF", DEFAULT_REF))
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    bridge = find_bridge("braw", a.repo, a.ref, "BRAW_SDK_READ_TOKEN")
    cmd = [sys.executable, str(bridge / "build_braw_bridge.py"), "--out", str(a.out.resolve())]
    if a.sdk:
        cmd += ["--sdk", a.sdk]
    if a.verbose:
        cmd.append("-v")
    print(f"Bridge source: {bridge}", file=sys.stderr)
    if a.dry_run:
        print(" ".join(cmd))
        return
    subprocess.check_call(cmd, cwd=ROOT)


if __name__ == "__main__":
    main()
