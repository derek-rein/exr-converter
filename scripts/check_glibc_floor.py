#!/usr/bin/env python3
"""Fail if a Linux build needs a newer glibc than the studio floor.

Scans ELF files for GNU version *needs* (not definitions) and compares the
highest ``GLIBC_x.y`` with a ceiling. Rocky Linux / RHEL 8 is glibc 2.28.
``GLIBCXX`` / ``CXXABI`` are printed always and can be capped too: the system
``libstdc++`` on Rocky 8's GCC 8 is ``GLIBCXX_3.4.25`` and ``CXXABI_1.3.11``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ELF_MAGIC = b"\x7fELF"
_VERSION_RE = re.compile(r"Name:\s+((?:GLIBC|GLIBCXX|CXXABI)_[\d.]+)")


def parse_version_needs(text: str) -> set[str]:
    """Return symbol versions from readelf ``Version needs`` sections only."""
    found: set[str] = set()
    in_needs = False
    for line in text.splitlines():
        if "Version needs section" in line:
            in_needs = True
            continue
        if in_needs and "Version definition section" in line:
            in_needs = False
            continue
        if not in_needs:
            continue
        match = _VERSION_RE.search(line)
        if match:
            found.add(match.group(1))
    return found


def version_tuple(name: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", name))


def version_family(name: str) -> str:
    return name.split("_", 1)[0]


def exceeds(name: str, ceiling: tuple[int, ...]) -> bool:
    return version_tuple(name) > ceiling


def symbol_exceeds(name: str, ceilings: dict[str, tuple[int, ...] | None]) -> bool:
    ceiling = ceilings.get(version_family(name))
    if ceiling is None:
        return False
    return exceeds(name, ceiling)


def exceeding_symbols(needs: set[str], ceilings: dict[str, tuple[int, ...] | None]) -> list[str]:
    return sorted(name for name in needs if symbol_exceeds(name, ceilings))


def is_optional_gpu_library(path: Path) -> bool:
    """True for an OpenCL helper the decoder can run without.

    The core RED and Blackmagic libraries are not OpenCL-named, so they stay
    even when this filter is on. CUDA libraries stay too.
    """
    return "opencl" in path.name.lower()


def drop_optional_gpu(
    root: Path, ceilings: dict[str, tuple[int, ...] | None]
) -> list[tuple[Path, list[str]]]:
    """Delete optional OpenCL libraries that need a newer glibc than *ceilings*."""
    dropped: list[tuple[Path, list[str]]] = []
    for path in iter_elf_files(root):
        if not is_optional_gpu_library(path):
            continue
        over = exceeding_symbols(readelf_needs(path), ceilings)
        if not over:
            continue
        path.unlink()
        dropped.append((path, over))
    return dropped


def readelf_needs(path: Path) -> set[str]:
    try:
        out = subprocess.check_output(
            ["readelf", "-V", "-W", str(path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return set()
    return parse_version_needs(out)


def iter_elf_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as handle:
                magic = handle.read(4)
        except OSError:
            continue
        if magic == _ELF_MAGIC:
            files.append(path)
    return files


def scan(root: Path) -> dict[str, tuple[str, Path]]:
    """Map GNU family -> (highest version name, path that required it)."""
    best: dict[str, tuple[str, Path]] = {}
    for path in iter_elf_files(root):
        for name in readelf_needs(path):
            family = version_family(name)
            current = best.get(family)
            if current is None or version_tuple(name) > version_tuple(current[0]):
                best[family] = (name, path)
    return best


def _parse_ceiling(text: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in text.split("."))
    if len(parts) < 2:
        raise argparse.ArgumentTypeError(f"expected a version like 2.28, got {text!r}")
    return parts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="ELF file or directory (Nuitka dist)")
    parser.add_argument(
        "--max-glibc",
        type=_parse_ceiling,
        default=(2, 28),
        help="Highest allowed GLIBC_x.y (default 2.28)",
    )
    parser.add_argument(
        "--max-glibcxx",
        type=_parse_ceiling,
        default=None,
        help="Optional GLIBCXX_3.4.N ceiling, for example 3.4.25",
    )
    parser.add_argument(
        "--max-cxxabi",
        type=_parse_ceiling,
        default=None,
        help="Optional CXXABI ceiling, for example 1.3.11",
    )
    parser.add_argument(
        "--drop-optional-gpu",
        action="store_true",
        help="Remove OpenCL libraries that exceed the ceiling, then recheck",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.exists():
        print(f"ERROR: {root} does not exist", file=sys.stderr)
        return 1
    ceilings = {
        "GLIBC": args.max_glibc,
        "GLIBCXX": args.max_glibcxx,
        "CXXABI": args.max_cxxabi,
    }
    if args.drop_optional_gpu:
        for path, over in drop_optional_gpu(root, ceilings):
            try:
                rel = path.relative_to(root if root.is_dir() else root.parent)
            except ValueError:
                rel = path
            print(f"Dropped optional GPU library {rel} ({', '.join(over)})")
    best = scan(root)
    if not best:
        print(f"ERROR: no ELF version needs under {root}", file=sys.stderr)
        return 1

    failed = False
    for path in iter_elf_files(root):
        over = exceeding_symbols(readelf_needs(path), ceilings)
        if not over:
            continue
        try:
            rel = path.relative_to(root if root.is_dir() else root.parent)
        except ValueError:
            rel = path
        for name in over:
            family = version_family(name)
            ceiling = ceilings[family]
            limit = ".".join(str(part) for part in ceiling) if ceiling is not None else "none"
            print(
                f"ERROR: {name} exceeds {family} {limit} ({rel})",
                file=sys.stderr,
            )
            failed = True
    for family in ("GLIBC", "GLIBCXX", "CXXABI"):
        item = best.get(family)
        if item is None:
            continue
        name, path = item
        ceiling = ceilings[family]
        rel = path
        try:
            rel = path.relative_to(root if root.is_dir() else root.parent)
        except ValueError:
            rel = path
        limit = ".".join(str(part) for part in ceiling) if ceiling is not None else "none"
        print(f"{family}: {name} in {rel} (ceiling {limit})")
    if failed:
        return 1
    glibc = best.get("GLIBC")
    if glibc is not None:
        print(f"OK: glibc floor {glibc[0].split('_', 1)[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
