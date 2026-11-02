#!/usr/bin/env python3
"""Fetch, build, and install the optional ARRI (ARRIRAW / ARRICORE) plugin.

The public tree does not contain the ARRI plugin, its bridge, or the ARRI
Image SDK. When ``ARRI_SDK_READ_TOKEN`` is set (the Release workflow maps the
repository secret of the same name), this script clones the private feed,
pulls only this platform's SDK runtime and the MXF Library source, builds the
bridge, and overlays the Python plugin into ``src/plugins/arri`` (git-ignored).
``install`` then copies the bridge, the ARRI runtime libraries, and ARRI's
notice files into the app bundle. Without the token every command is a no-op
and the app keeps its "Camera RAW is not supported" message for ``.ari``.

Logs from public CI must never show SDK content, so tool output is captured
and a failure prints only the failing step.

Usage::

  python3 scripts/arri_plugin.py prepare
  python3 scripts/arri_plugin.py nuitka-flags
  python3 scripts/arri_plugin.py install "dist/EXR Converter.app"
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / ".arri-feed"
BUILD = ROOT / "build" / "arri"
WORK = ROOT / "build" / "arri-work"
PLUGINS = ROOT / "src" / "plugins"
PLUGIN_DEST = PLUGINS / "arri"
ENABLED = PLUGINS / "_enabled.py"
REQUIRED = ROOT / "build" / "arri-required"
STRIP_PATHS = ROOT / "build" / "arri-strip-paths.txt"
TOKEN_ENV = "ARRI_SDK_READ_TOKEN"
FEED_REPO = "derek-rein/arri-sdk-private"
IMAGE_SDK_VERSION = "9.2.0"


def _token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def redact(text: str) -> str:
    token = _token()
    return text.replace(token, "***") if token else text


def _run(
    step: str, cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None
) -> None:
    """Run quietly. Compiler and CMake output can quote ARRI SDK headers, which
    are confidential, so public logs only get the step name and exit code.
    Set ARRI_PLUGIN_VERBOSE=1 on a private machine to see the full output."""
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        msg = f"ARRI plugin: {step} failed ({proc.returncode})"
        if os.environ.get("ARRI_PLUGIN_VERBOSE") == "1":
            msg += "\n" + redact((proc.stdout or "") + "\n" + (proc.stderr or ""))
        else:
            msg += ". Output hidden; rerun the private ARRI review build to debug."
        raise SystemExit(msg)


def sdk_platform() -> str:
    """ARRI Image SDK platform folder for this runner."""
    env = os.environ.get("ARRI_SDK_PLATFORM", "").strip()
    if env:
        return env
    system = platform.system()
    if system == "Windows":
        return "windows-x64-msvc192"
    if system == "Darwin":
        arch = os.environ.get("ARRI_BRIDGE_ARCH") or platform.machine()
        return "macos-arm64-clang1200" if arch == "arm64" else "macos-x64-clang1200"
    # RHEL 8 build (glibc 2.28) matches the Rocky Linux 8 release build.
    return "linux-x64-rhel8-gcc85"


def lfs_include_patterns(platform_dir: str) -> list[str]:
    """Only this platform's SDK and the MXF Library source. No docs or test media."""
    return [
        f"vendor/arri/image-sdk/{IMAGE_SDK_VERSION}/{platform_dir}/**",
        "vendor/arri/mxf-library/**",
    ]


def _write_askpass(directory: Path) -> Path:
    # Git for Windows runs askpass helpers through its own sh, so one script fits all.
    path = directory / "askpass.sh"
    path.write_text("#!/bin/sh\nprintf '%s\\n' \"$" + TOKEN_ENV + '"\n', encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _git_env(token: str, askpass: Path, *, smudge: bool) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = str(askpass)
    env[TOKEN_ENV] = token
    if smudge:
        env.pop("GIT_LFS_SKIP_SMUDGE", None)
    else:
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
    return env


def _fetch(token: str) -> None:
    if FEED.exists():
        shutil.rmtree(FEED)
    FEED.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="arri-auth-") as tmp:
        askpass = _write_askpass(Path(tmp))
        url = f"https://x-access-token@github.com/{FEED_REPO}.git"
        _run(
            "clone",
            ["git", "clone", "--depth", "1", url, str(FEED)],
            env=_git_env(token, askpass, smudge=False),
        )
        env = _git_env(token, askpass, smudge=True)
        _run("lfs install", ["git", "lfs", "install", "--local"], cwd=FEED, env=env)
        include = ",".join(lfs_include_patterns(sdk_platform()))
        _run("lfs pull", ["git", "lfs", "pull", "--include", include], cwd=FEED, env=env)


def write_enabled(plugins: Path | None = None) -> list[str]:
    """List every overlaid plugin package so one feed never hides another."""
    plugins = plugins or PLUGINS
    names = sorted(
        child.name
        for child in plugins.iterdir()
        if child.is_dir()
        and not child.name.startswith(("_", "."))
        and (child / "__init__.py").is_file()
    )
    imports = "".join(f"from . import {name} as _{name}  # noqa: F401\n" for name in names)
    mods = "".join(f'"src.plugins.{name}", ' for name in names)
    (plugins / "_enabled.py").write_text(
        '"""Generated for optional source plugins. Do not commit."""\n\n'
        f"{imports}\nENABLED = ({mods})\n",
        encoding="utf-8",
    )
    return names


def overlay(feed: Path | None = None, plugins: Path | None = None) -> None:
    feed = feed or FEED
    plugins = plugins or PLUGINS
    source = feed / "plugin" / "arri"
    if not (source / "__init__.py").is_file():
        raise SystemExit("ARRI plugin: feed has no plugin/arri package")
    dest = plugins / "arri"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    write_enabled(plugins)


def _assert_ignored() -> None:
    """The overlay must stay invisible to git in the public checkout."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "--", "src/plugins", ".arri-feed", "build"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except OSError:
        return
    if out:
        raise SystemExit("ARRI plugin: overlay files are visible to git; check .gitignore")


def prepare() -> None:
    token = _token()
    if not token:
        print("ARRI plugin token is not set; skipping")
        return
    if shutil.which("git") is None or shutil.which("git-lfs") is None:
        raise SystemExit("ARRI plugin: git and git-lfs are required")
    if shutil.which("cmake") is None:
        raise SystemExit("ARRI plugin: cmake is required")
    REQUIRED.parent.mkdir(parents=True, exist_ok=True)
    REQUIRED.write_text("required\n", encoding="utf-8")
    _fetch(token)
    if BUILD.exists():
        shutil.rmtree(BUILD)
    env = os.environ.copy()
    env["ARRI_SDK_PLATFORM"] = sdk_platform()
    env["ARRI_BRIDGE_WORK"] = str(WORK)
    _run(
        "bridge build",
        [
            sys.executable,
            str(FEED / "bridge" / "build_arri_bridge.py"),
            "--sdk",
            str(FEED),
            "--out",
            str(BUILD),
        ],
        cwd=ROOT,
        env=env,
    )
    overlay()
    _assert_ignored()
    print(f"ARRI plugin prepared ({sdk_platform()})")


def nuitka_flag_lines() -> list[str]:
    if ENABLED.is_file() and (PLUGIN_DEST / "__init__.py").is_file():
        return ["--include-module=src.plugins._enabled", "--include-package=src.plugins.arri"]
    return []


def nuitka_flags() -> None:
    """One flag per line, LF only (Windows text mode would add CR)."""
    lines = nuitka_flag_lines()
    if lines:
        sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("ascii"))
        sys.stdout.buffer.flush()


def _bridge() -> Path | None:
    if not BUILD.is_dir():
        return None
    for name in ("libarri_bridge.dylib", "libarri_bridge.dll", "libarri_bridge.so"):
        if (BUILD / name).is_file():
            return BUILD / name
    return None


def install(bundle: Path) -> None:
    bridge = _bridge()
    script = FEED / "scripts" / "install_arri_into_bundle.py"
    if bridge is None or not script.is_file():
        if REQUIRED.is_file():
            raise SystemExit("ARRI plugin was requested but did not build")
        print("ARRI plugin not included")
        return
    _run(
        "bundle install",
        [sys.executable, str(script), str(bundle), "--build-dir", str(BUILD)],
        cwd=ROOT,
    )
    if not any(p.is_file() for p in bundle.rglob(bridge.name)):
        raise SystemExit("ARRI plugin install did not place the bridge library")
    # ARRI runtime libraries ship in their original form: never strip them.
    STRIP_PATHS.parent.mkdir(parents=True, exist_ok=True)
    STRIP_PATHS.write_text("*/arri/*\n", encoding="utf-8")
    print("ARRI plugin bundled")


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[1] not in {"prepare", "nuitka-flags", "install"}:
        raise SystemExit("usage: arri_plugin.py prepare|nuitka-flags|install [bundle]")
    if argv[1] == "prepare":
        prepare()
    elif argv[1] == "nuitka-flags":
        nuitka_flags()
    else:
        if len(argv) != 3:
            raise SystemExit("install needs a bundle path")
        install(Path(argv[2]))


if __name__ == "__main__":
    main(sys.argv)
