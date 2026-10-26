#!/usr/bin/env python3
"""Replace ELF files whose program headers patchelf left unloadable.

Nuitka rewrites RPATH with patchelf. On the auditwheel libraries shipped
inside NumPy (OpenBLAS, libgfortran, libquadmath) and PyAV (hashed libxcb),
that rewrite produces a LOAD segment whose file offset is not congruent
with its virtual address. glibc then refuses to map the file.

The wheel copies are already relocatable (RPATH ``$ORIGIN``) and Nuitka
places each of them next to the binary, so the wheel file still loads.
Copy it back over the broken bundle file. Other ELF files are left as
Nuitka wrote them.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

_ELF_MAGIC = b"\x7fELF"


def _iter_elf(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    found: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as handle:
                magic = handle.read(4)
        except OSError:
            continue
        if magic == _ELF_MAGIC:
            found.append(path)
    return found


def load_segments_aligned(path: Path) -> bool:
    """False when a LOAD offset is not congruent with its virtual address."""
    try:
        text = subprocess.check_output(
            ["readelf", "-W", "-l", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return True
    for line in text.splitlines():
        parts = line.split()
        # Align is the last column. Flags may be "R E", so do not count fields.
        if not parts or parts[0] != "LOAD" or len(parts) < 6:
            continue
        try:
            offset = int(parts[1], 16)
            virt = int(parts[2], 16)
            align = int(parts[-1], 16)
        except ValueError:
            continue
        if align and (offset % align) != (virt % align):
            return False
    return True


def _index_sources(roots: list[Path], dest_root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    dest_resolved = dest_root.resolve()
    for root in roots:
        if not root.exists():
            continue
        for path in _iter_elf(root):
            try:
                path.resolve().relative_to(dest_resolved)
                inside_dest = True
            except ValueError:
                inside_dest = False
            if inside_dest:
                continue
            found.setdefault(path.name, path)
    return found


def restore_misaligned(dest: Path, sources: list[Path]) -> list[Path]:
    """Copy healthy same-named ELF files over misaligned ones. Returns them."""
    index = _index_sources(sources, dest if dest.is_dir() else dest.parent)
    restored: list[Path] = []
    for path in _iter_elf(dest):
        if load_segments_aligned(path):
            continue
        source = index.get(path.name)
        if source is None or not load_segments_aligned(source):
            raise SystemExit(
                f"misaligned ELF {path} has no healthy source named {path.name} "
                f"under {', '.join(str(item) for item in sources)}"
            )
        shutil.copy2(source, path)
        if not load_segments_aligned(path):
            raise SystemExit(f"restored {path} from {source} is still misaligned")
        print(f"repair_linux_bundle_elf: restored {path.name} from {source}")
        restored.append(path)
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dest", type=Path, help="Nuitka dist directory")
    parser.add_argument(
        "sources",
        type=Path,
        nargs="+",
        help="Directories that hold the original wheels (site-packages)",
    )
    args = parser.parse_args()
    if not args.dest.exists():
        print(f"ERROR: {args.dest} does not exist", file=sys.stderr)
        return 1
    restored = restore_misaligned(args.dest, args.sources)
    print(f"repair_linux_bundle_elf: {len(restored)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
