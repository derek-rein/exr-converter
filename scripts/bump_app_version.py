#!/usr/bin/env python3
"""Bump [project].version in an app pyproject.toml and sync APP_VERSION in src/constants.py.

Used by the repo root Makefile for namespaced tags: exr_converter/v1.2.3, slate_maker/v0.4.0.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

VERSION_LINE = re.compile(r"^version\s*=\s*\"([^\"]+)\"\s*$", re.MULTILINE)
APP_VERSION_LINE = re.compile(
    r"^(APP_VERSION\s*=\s*\")([^\"]+)(\"\s*)$",
    re.MULTILINE,
)

APPS: dict[str, dict[str, Path | str]] = {
    "exr_converter": {
        "tag_prefix": "exr_converter",
        "pyproject": REPO_ROOT / "apps/exr_converter/pyproject.toml",
        "constants": REPO_ROOT / "apps/exr_converter/src/constants.py",
    },
    "slate_maker": {
        "tag_prefix": "slate_maker",
        "pyproject": REPO_ROOT / "apps/slate_maker/pyproject.toml",
        "constants": REPO_ROOT / "apps/slate_maker/src/constants.py",
    },
}


def parse_semver(s: str) -> tuple[int, int, int]:
    parts = s.strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"expected semver x.y.z, got {s!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_semver(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def read_pyproject_version(pyproject: Path) -> str:
    text = pyproject.read_text(encoding="utf-8")
    m = VERSION_LINE.search(text)
    if not m:
        raise SystemExit(f"no version = line found in {pyproject}")
    return m.group(1)


def write_pyproject_version(pyproject: Path, new_version: str, dry_run: bool) -> None:
    text = pyproject.read_text(encoding="utf-8")
    new_text, n = VERSION_LINE.subn(f'version = "{new_version}"', text, count=1)
    if n != 1:
        raise SystemExit(f"failed to replace version in {pyproject}")
    if not dry_run:
        pyproject.write_text(new_text, encoding="utf-8")


def write_app_version(constants: Path, new_version: str, dry_run: bool) -> None:
    text = constants.read_text(encoding="utf-8")
    new_text, n = APP_VERSION_LINE.subn(
        lambda m: f'{m.group(1)}{new_version}{m.group(3)}',
        text,
        count=1,
    )
    if n != 1:
        raise SystemExit(
            f"expected exactly one APP_VERSION = \"...\" line in {constants}",
        )
    if not dry_run:
        constants.write_text(new_text, encoding="utf-8")


def show_export(app: str) -> None:
    """Print VERSION / TAG / APP from current pyproject (no bump)."""
    cfg = APPS[app]
    pyproject = cfg["pyproject"]
    tag_prefix = str(cfg["tag_prefix"])
    current = read_pyproject_version(pyproject)
    tag = f"{tag_prefix}/v{current}"
    print(f'VERSION="{current}"')
    print(f'TAG="{tag}"')
    print(f'APP="{app}"')


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_bump = sub.add_parser("bump", help="increment semver and sync constants.py")
    p_bump.add_argument(
        "app",
        choices=sorted(APPS.keys()),
        help="application directory name under apps/",
    )
    p_bump.add_argument(
        "part",
        choices=("patch", "minor", "major"),
        help="which segment to increment",
    )
    p_bump.add_argument(
        "--dry-run",
        action="store_true",
        help="print new version but do not write files",
    )

    p_show = sub.add_parser(
        "show",
        help="print VERSION/TAG from current pyproject (no file changes)",
    )
    p_show.add_argument("app", choices=sorted(APPS.keys()))

    args = p.parse_args()

    if args.cmd == "show":
        show_export(args.app)
        return

    cfg = APPS[args.app]
    pyproject = cfg["pyproject"]
    constants = cfg["constants"]
    tag_prefix = str(cfg["tag_prefix"])

    if not pyproject.is_file():
        raise SystemExit(f"missing {pyproject}")
    if not constants.is_file():
        raise SystemExit(f"missing {constants}")

    current = read_pyproject_version(pyproject)
    new_version = bump_semver(current, args.part)
    tag = f"{tag_prefix}/v{new_version}"

    print(f"{args.app}: {current} → {new_version} ({args.part})")
    print(f"tag: {tag}")

    write_pyproject_version(pyproject, new_version, args.dry_run)
    write_app_version(constants, new_version, args.dry_run)

    if args.dry_run:
        print("(dry-run: no files modified)")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
