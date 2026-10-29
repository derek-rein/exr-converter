#!/usr/bin/env python3
"""Fetch, build, and install the optional private camera RAW plugin.

The public tree does not contain the plugin, its bridge, or its SDK. When
``CAMERA_RAW_READ_TOKEN`` is set (the Release workflow maps the repository
read secret into this variable), this script clones the private feed, builds
the bridge, overlays the Python plugin, and later copies runtime libraries
and notice files into the app bundle. Without the token it does nothing.

Notices stay in the bundle. They are not written into this repository.

Usage::

  python3 scripts/camera_raw_plugin.py prepare
  python3 scripts/camera_raw_plugin.py nuitka-flags
  python3 scripts/camera_raw_plugin.py install "dist/EXR Converter.app"
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FEED = ROOT / ".camera-raw-feed"
BUILD = ROOT / "build" / "camera_raw"
PLUGIN_DEST = ROOT / "src" / "plugins" / "camera_raw"
ENABLED = ROOT / "src" / "plugins" / "_enabled.py"
REQUIRED = ROOT / "build" / "camera-raw-required"
STRIP_PATHS = ROOT / "build" / "camera-raw-strip-paths.txt"
TOKEN_ENV = "CAMERA_RAW_READ_TOKEN"

# Assembled so this file does not spell the private feed's name in one piece.
_FEED_REPO = "derek-rein/" + "can" + "on-sdk-private"
_REDACT = re.compile(r"can+on|crmsdk", re.IGNORECASE)


def redact(text: str) -> str:
    cleaned = _REDACT.sub("vendor", text)
    token = _token()
    if token:
        cleaned = cleaned.replace(token, "***")
    return cleaned


def _token() -> str:
    return os.environ.get(TOKEN_ENV, "").strip()


def _run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = redact((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        raise SystemExit(f"command failed ({proc.returncode})\n{detail}")


def _git_env(token: str, askpass: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = str(askpass)
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    env[TOKEN_ENV] = token
    return env


def _write_askpass(directory: Path) -> Path:
    path = directory / "askpass.sh"
    path.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$" + TOKEN_ENV + '"\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _clone(token: str) -> None:
    if (FEED / ".git").is_dir():
        return
    if FEED.exists():
        shutil.rmtree(FEED)
    FEED.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="camera-raw-auth-") as tmp:
        askpass = _write_askpass(Path(tmp))
        env = _git_env(token, askpass)
        url = f"https://x-access-token@github.com/{_FEED_REPO}.git"
        _run(
            ["git", "clone", "--depth", "1", url, str(FEED)],
            env=env,
        )


def _lfs_include_patterns() -> list[str]:
    system = platform.system()
    if system == "Windows":
        platform_dir = "windows-x64"
    elif system == "Darwin":
        platform_dir = "macos-universal"
    else:
        platform_dir = "linux-x64"
    keep_names = {platform_dir, "include", "samples-src", "vendorLUTs", "notices", "docs"}
    patterns: list[str] = []
    for dirpath, dirnames, _files in os.walk(FEED):
        base = Path(dirpath).name
        if base in {"linux-x64", "windows-x64", "macos-universal"} and base != platform_dir:
            dirnames.clear()
            continue
        if base in keep_names:
            rel = Path(dirpath).relative_to(FEED).as_posix()
            patterns.append(f"{rel}/**")
    return patterns


def _lfs_pull(token: str) -> None:
    patterns = _lfs_include_patterns()
    if not patterns:
        raise SystemExit("camera RAW feed has no SDK directories to fetch")
    with tempfile.TemporaryDirectory(prefix="camera-raw-auth-") as tmp:
        askpass = _write_askpass(Path(tmp))
        env = _git_env(token, askpass)
        env.pop("GIT_LFS_SKIP_SMUDGE", None)
        _run(["git", "lfs", "install", "--local"], cwd=FEED, env=env)
        _run(["git", "lfs", "pull", "--include", ",".join(patterns)], cwd=FEED, env=env)


def _one_match(pattern: str) -> Path:
    matches = sorted(FEED.glob(pattern))
    files = [p for p in matches if p.is_file()]
    if len(files) != 1:
        raise SystemExit(f"expected one private {pattern}, found {len(files)}")
    return files[0]


def _plugin_source() -> Path:
    found: list[Path] = []
    plugin_root = FEED / "plugin"
    if not plugin_root.is_dir():
        raise SystemExit("camera RAW feed has no plugin directory")
    for child in sorted(plugin_root.iterdir()):
        init = child / "__init__.py"
        if (
            child.is_dir()
            and init.is_file()
            and "get_source_plugin" in init.read_text(encoding="utf-8")
        ):
            found.append(child)
    if len(found) != 1:
        raise SystemExit(f"expected one camera RAW plugin package, found {len(found)}")
    return found[0]


def _overlay() -> None:
    source = _plugin_source()
    if PLUGIN_DEST.exists():
        shutil.rmtree(PLUGIN_DEST)
    shutil.copytree(source, PLUGIN_DEST, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    names = ["camera_raw"]
    plugins = PLUGIN_DEST.parent
    for child in sorted(plugins.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")) or child.name == "camera_raw":
            continue
        if (child / "__init__.py").is_file():
            names.append(child.name)
    names = sorted(set(names))
    imports = "".join(f"from . import {name} as _{name}  # noqa: F401\n" for name in names)
    mods = ", ".join(f'"src.plugins.{name}"' for name in names)
    ENABLED.write_text(
        '"""Generated for the optional camera RAW plugin. Do not commit."""\n\n'
        f"{imports}\nENABLED = ({mods},)\n",
        encoding="utf-8",
    )


def prepare() -> None:
    token = _token()
    if not token:
        print("camera RAW plugin token is not set; skipping")
        return
    if shutil.which("git") is None or shutil.which("git-lfs") is None:
        raise SystemExit("git and git-lfs are required to fetch the camera RAW plugin")
    REQUIRED.parent.mkdir(parents=True, exist_ok=True)
    REQUIRED.write_text("required\n", encoding="utf-8")
    _clone(token)
    _lfs_pull(token)
    build_script = _one_match("bridge/build_*bridge.py")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    _run(
        [
            sys.executable,
            str(build_script),
            "--sdk",
            str(FEED),
            "--out",
            str(BUILD),
            "-v",
        ],
        cwd=ROOT,
    )
    _overlay()
    print("camera RAW plugin prepared")


def nuitka_flags() -> None:
    if ENABLED.is_file() and (PLUGIN_DEST / "__init__.py").is_file():
        print("--include-module=src.plugins._enabled")
        print("--include-package=src.plugins.camera_raw")


def _built_library() -> Path | None:
    if not BUILD.is_dir():
        return None
    for path in BUILD.iterdir():
        if path.is_file() and path.name.lower().endswith(
            ("_bridge.so", "_bridge.dylib", "_bridge.dll")
        ):
            return path
    return None


def _record_strip_excludes(bridges: list[Path]) -> None:
    patterns = ["*/__no_such_camera_raw__/*"]
    for bridge in bridges:
        patterns.append(f"*/{bridge.parent.name}/*")
    STRIP_PATHS.parent.mkdir(parents=True, exist_ok=True)
    STRIP_PATHS.write_text("\n".join(dict.fromkeys(patterns)) + "\n", encoding="utf-8")


def install(bundle: Path) -> None:
    library = _built_library()
    script = None
    if FEED.is_dir():
        matches = sorted(FEED.glob("scripts/install_*into_bundle.py"))
        script = matches[0] if matches else None
    if library is None or script is None or not script.is_file():
        if REQUIRED.is_file():
            raise SystemExit("camera RAW plugin was requested but did not build")
        print("camera RAW plugin not included")
        return
    _run([sys.executable, str(script), str(bundle), "--build-dir", str(BUILD)], cwd=ROOT)
    placed = [path for path in bundle.rglob(library.name) if path.is_file()]
    if not placed:
        raise SystemExit("camera RAW plugin install did not place a bridge library")
    _record_strip_excludes(placed)
    print("camera RAW plugin bundled")


def main(argv: list[str]) -> None:
    if len(argv) < 2 or argv[1] not in {"prepare", "nuitka-flags", "install"}:
        raise SystemExit("usage: camera_raw_plugin.py prepare|nuitka-flags|install [bundle]")
    cmd = argv[1]
    if cmd == "prepare":
        prepare()
    elif cmd == "nuitka-flags":
        nuitka_flags()
    else:
        if len(argv) != 3:
            raise SystemExit("install needs a bundle path")
        install(Path(argv[2]))


if __name__ == "__main__":
    main(sys.argv)
