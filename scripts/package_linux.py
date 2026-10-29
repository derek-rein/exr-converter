#!/usr/bin/env python3
"""Pack a Nuitka Linux dist as a relocatable tarball and an RPM.

The tarball root is ``exr-converter-<version>/`` with the Nuitka tree, a
launcher named ``exr-converter``, LICENSE, a short README, and a Rez
``package.py``. The RPM installs that same tree at ``/opt/exr-converter``,
symlinks ``/usr/bin/exr-converter``, and adds a desktop file and icon.

The launcher resolves its own directory and does not write next to itself.
Settings and caches stay on the XDG paths the app already uses.
"""

from __future__ import annotations

import argparse
import shutil
import stat
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "packaging" / "linux" / "exr-converter"
DESKTOP = ROOT / "packaging" / "linux" / "exr-converter.desktop"
LICENSE = ROOT / "LICENSE"
ICON = ROOT / "resources" / "icons" / "icon.png"

VENDOR = "Medeu Global LLC"
LICENSE_NAME = "MIT"
OPT_PREFIX = "/opt/exr-converter"
BINARY_NAME = "exr_converter"
LAUNCHER_NAME = "exr-converter"
HOMEPAGE = "https://vfx.systems/docs/exr-converter"

# Sonames provided by Rocky / RHEL 8 and 9 base packages. The GUI loads these
# from the host; the rest of the stack is inside the prefix.
RPM_DEPENDS = (
    "libEGL.so.1()(64bit)",
    "libGL.so.1()(64bit)",
    "libxkbcommon.so.0()(64bit)",
    "libxkbcommon-x11.so.0()(64bit)",
    "libX11.so.6()(64bit)",
    "libxcb.so.1()(64bit)",
    "libglib-2.0.so.0()(64bit)",
    "libfontconfig.so.1()(64bit)",
    "libfreetype.so.6()(64bit)",
    "libdbus-1.so.3()(64bit)",
    "libstdc++.so.6()(64bit)",
)

TARBALL_NAME = "exr_converter-linux-x86_64.tar.gz"
RPM_NAME = "exr_converter-linux-x86_64.rpm"


def project_version(root: Path = ROOT) -> str:
    with (root / "pyproject.toml").open("rb") as handle:
        version = tomllib.load(handle)["project"]["version"]
    if not isinstance(version, str) or not version:
        raise SystemExit("pyproject.toml [project].version is empty")
    return version


def folder_name(version: str) -> str:
    return f"exr-converter-{version}"


def readme_text(version: str) -> str:
    return f"""EXR Converter {version} for Linux x86_64

Relocatable build for RHEL, Rocky, and Alma 8 and newer (glibc 2.28+).
Copy this folder anywhere, including /opt, /software, or a read-only
network share. Nothing in this folder is written at runtime.

  ./exr-converter
  ./exr-converter --help

The command is `exr-converter`. The Nuitka binary beside it is `exr_converter`.

Settings: $XDG_CONFIG_HOME (default ~/.config, Qt org VFXTools / EXRConverter).
Presets:  $XDG_DATA_HOME (default ~/.local/share).
Caches:   $XDG_CACHE_HOME (default ~/.cache/exr-converter).

Rez: this folder includes package.py (name exr_converter, version {version}).
Put the folder on a package path, or set the package root to this directory.
`exr-converter` is appended to PATH.

Artists who want a single file can use the AppImage from the same release.
IT can install the RPM, which places this tree at /opt/exr-converter and
links /usr/bin/exr-converter.
"""


def rez_package_py(version: str) -> str:
    return (
        "# Rez package for the relocatable EXR Converter Linux build.\n"
        f'name = "exr_converter"\n'
        f'version = "{version}"\n'
        "\n"
        "def commands():\n"
        '    env.PATH.append("{root}")\n'
    )


def desktop_text(*, exec_line: str) -> str:
    text = DESKTOP.read_text(encoding="utf-8")
    if "Exec=exr-converter" not in text:
        raise SystemExit(f"{DESKTOP} is missing Exec=exr-converter")
    return text.replace("Exec=exr-converter", f"Exec={exec_line}", 1)


def _copy_dist(dist: Path, dest: Path) -> None:
    shutil.copytree(dist, dest, symlinks=True)
    binary = dest / BINARY_NAME
    if not binary.exists():
        raise SystemExit(f"{dist} has no {BINARY_NAME} binary")
    mode = binary.stat().st_mode
    binary.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def stage_tree(dist: Path, stage_root: Path, version: str) -> Path:
    """Return the versioned folder inside ``stage_root``."""
    if not LAUNCHER.is_file():
        raise SystemExit(f"missing launcher {LAUNCHER}")
    if not LICENSE.is_file():
        raise SystemExit(f"missing {LICENSE}")
    if not ICON.is_file():
        raise SystemExit(f"missing {ICON}")
    folder = stage_root / folder_name(version)
    if folder.exists():
        shutil.rmtree(folder)
    _copy_dist(dist, folder)
    launcher_dest = folder / LAUNCHER_NAME
    shutil.copyfile(LAUNCHER, launcher_dest)
    launcher_dest.chmod(0o755)
    shutil.copyfile(LICENSE, folder / "LICENSE")
    (folder / "README").write_text(readme_text(version), encoding="utf-8")
    (folder / "package.py").write_text(rez_package_py(version), encoding="utf-8")
    (folder / "exr-converter.desktop").write_text(
        desktop_text(exec_line=LAUNCHER_NAME),
        encoding="utf-8",
    )
    shutil.copyfile(ICON, folder / "exr-converter.png")
    return folder


def make_tarball(folder: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    executables = {f"{folder.name}/{LAUNCHER_NAME}", f"{folder.name}/{BINARY_NAME}"}

    def _entry(info: tarfile.TarInfo) -> tarfile.TarInfo:
        # Keep the entry points executable even when staged on a filesystem
        # without POSIX modes (Windows test runners).
        if info.isfile() and info.name in executables:
            info.mode |= 0o755
        return info

    with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as archive:
        archive.add(folder, arcname=folder.name, recursive=True, filter=_entry)


def nfpm_config(folder: Path, version: str, work: Path) -> Path:
    desktop = work / "exr-converter.desktop"
    desktop.write_text(desktop_text(exec_line=f"{OPT_PREFIX}/{LAUNCHER_NAME}"), encoding="utf-8")
    icon = work / "exr-converter.png"
    shutil.copyfile(ICON, icon)
    depends = "\n".join(f'  - "{item}"' for item in RPM_DEPENDS)
    text = f"""name: exr-converter
arch: amd64
platform: linux
version: {version}
release: "1"
maintainer: {VENDOR}
vendor: {VENDOR}
license: {LICENSE_NAME}
homepage: {HOMEPAGE}
description: |
  Convert video and OpenEXR sequences with OpenColorIO.
  Relocatable build for RHEL, Rocky, and Alma 8 and newer (glibc 2.28).
contents:
  - src: {folder}
    dst: {OPT_PREFIX}
    type: tree
  - src: {desktop}
    dst: /usr/share/applications/exr-converter.desktop
  - src: {icon}
    dst: /usr/share/icons/hicolor/256x256/apps/exr-converter.png
  - src: {OPT_PREFIX}/{LAUNCHER_NAME}
    dst: /usr/bin/exr-converter
    type: symlink
depends:
{depends}
"""
    path = work / "nfpm.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def make_rpm(folder: Path, version: str, dest: Path, nfpm: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    work = dest.parent / ".nfpm"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    config = nfpm_config(folder, version, work)
    subprocess.check_call(
        [str(nfpm), "package", "--config", str(config), "--packager", "rpm", "--target", str(dest)]
    )


def package_distribution(
    dist: Path,
    out_dir: Path,
    *,
    version: str | None = None,
    nfpm: Path | None = None,
) -> tuple[Path, Path]:
    version = version or project_version()
    out_dir.mkdir(parents=True, exist_ok=True)
    stage_root = out_dir / ".stage"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage_root.mkdir(parents=True)
    folder = stage_tree(dist, stage_root, version)
    tarball = out_dir / TARBALL_NAME
    make_tarball(folder, tarball)
    if nfpm is None:
        raise SystemExit("nfpm is required to build the RPM")
    rpm = out_dir / RPM_NAME
    make_rpm(folder, version, rpm, nfpm)
    shutil.rmtree(stage_root)
    return tarball, rpm


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, required=True, help="Nuitka dist/main.dist")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--version", default="")
    parser.add_argument("--nfpm", type=Path, required=True, help="Path to the nfpm binary")
    args = parser.parse_args()
    version = args.version.strip() or project_version()
    tarball, rpm = package_distribution(
        args.dist.resolve(),
        args.out_dir.resolve(),
        version=version,
        nfpm=args.nfpm.resolve(),
    )
    # Report sizes for the release log.
    for path in (tarball, rpm):
        size = path.stat().st_size
        print(f"{path.name}: {size} bytes ({size / (1024 * 1024):.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
