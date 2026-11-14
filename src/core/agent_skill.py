"""The Agent Skills text (``skills.md``) with this machine's install path filled in.

The file at the repository root is written for a source checkout. The app shows
it with the *Install* section replaced by the real command for this install, and
every ``uv run python main.py`` example rewritten to that command.
"""

from __future__ import annotations

import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from .app_paths import is_frozen_app, package_root_from_file

SKILL_FILE = "skills.md"
SOURCE_COMMAND = "uv run python main.py"
_INSTALL_RE = re.compile(r"<!-- install:start -->.*?<!-- install:end -->", re.DOTALL)


@dataclass(frozen=True)
class Install:
    """How to start this copy of the app from a shell."""

    command: str  # what goes before the subcommand
    location: str  # the executable, AppImage, or repository root
    kind: str  # "source" | "packaged"


def _quote(path: str | Path) -> str:
    text = str(path)
    if sys.platform == "win32":
        return f'"{text}"' if " " in text else text
    return shlex.quote(text)


def detect_install() -> Install:
    """Find the command that starts this app, for a source checkout or a binary."""
    packaged = is_frozen_app() or "__compiled__" in globals()
    if packaged:
        appimage = os.environ.get("APPIMAGE")
        exe = appimage if appimage and Path(appimage).exists() else sys.executable
        # Windows: the installer ships a launcher that waits for the app and
        # shows its output (the exe itself has no console).
        shim = Path(exe).with_name("exr-converter.cmd")
        run = shim if sys.platform == "win32" and shim.is_file() else exe
        return Install(_quote(run), str(Path(exe).resolve()), "packaged")
    root = package_root_from_file(__file__, parents=3)
    command = f"{_quote(sys.executable)} {_quote(root / 'main.py')}"
    return Install(command, str(root), "source")


def _read_template() -> str:
    root_file = package_root_from_file(__file__, parents=3) / SKILL_FILE
    try:
        return root_file.read_text(encoding="utf-8")
    except OSError:
        pass
    from PySide6.QtCore import QFile, QIODevice

    f = QFile(f":/{SKILL_FILE}")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        return ""
    try:
        return bytes(f.readAll().data()).decode("utf-8")
    finally:
        f.close()


def _install_section(install: Install) -> str:
    where = "Source checkout" if install.kind == "source" else "Installed app"
    return (
        "## Install\n\n"
        f"{where}: `{install.location}`\n\n"
        "Run every command with this prefix (the examples below show it in full):\n\n"
        f"```bash\n{install.command} <subcommand> ...\n```"
    )


def render_skill(install: Install | None = None, template: str | None = None) -> str:
    """The skill text for *install* (default: this machine)."""
    install = install or detect_install()
    text = _read_template() if template is None else template
    section = _install_section(install)
    # Callables keep backslashes in Windows paths from being read as escapes.
    text = _INSTALL_RE.sub(lambda _m: section, text)
    return text.replace(SOURCE_COMMAND, install.command)
