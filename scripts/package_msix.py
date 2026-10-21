#!/usr/bin/env python3
"""Build an unsigned MSIX of the Windows Nuitka app for Microsoft Store submission.

Partner Center ("MSIX or PWA app") re-signs the package. This script must not
code-sign the package or embed ``AppxSignature.p7x``.

Identity comes from repository variables (not secrets):

* ``MSIX_IDENTITY_NAME`` — Partner Center package identity name
* ``MSIX_PUBLISHER`` — the ``CN=...`` publisher string, copied exactly
* ``MSIX_PUBLISHER_DISPLAY_NAME`` — optional; defaults to ``DerekVFX``

When the required variables are unset the script prints a warning and exits 0
so the setup ``.exe`` still ships. The manifest version is ``[project].version``
from ``pyproject.toml`` mapped to ``a.b.c.0`` (the Store requires the last
segment to be 0).

Tile logos are generated from ``resources/icons/icon.png`` at pack time.
Requires Pillow (``uv run --with pillow`` or the dev group) and, to produce
the ``.msix``, ``makeappx.exe`` from the Windows 10 SDK.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple
from xml.sax.saxutils import escape

from PIL import Image

# Store package identity name: 3–50 characters, letters, digits, ".", "-".
_IDENTITY_NAME_RE = re.compile(r"^[A-Za-z0-9][-.A-Za-z0-9]{2,49}$")
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")
_APP_EXECUTABLE = "exr_converter.exe"
_DEFAULT_PUBLISHER_DISPLAY_NAME = "DerekVFX"
_MIN_VERSION = "10.0.19041.0"
_MAX_VERSION_TESTED = "10.0.26100.0"


class MsixIdentity(NamedTuple):
    name: str
    publisher: str
    publisher_display_name: str


class MsixIdentityMissing(Exception):
    """Required identity variables are unset. Packaging should be skipped."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        names = ", ".join(missing)
        super().__init__(
            "Skipping MSIX: repository variables not set "
            f"({names}). Settings → Secrets and variables → Actions → Variables. "
            "The Windows setup .exe is unchanged."
        )


class LogoSpec(NamedTuple):
    filename: str
    width: int
    height: int


# Sizes MakeAppx checks for the unqualified (scale-100) tile assets.
LOGOS: tuple[LogoSpec, ...] = (
    LogoSpec("StoreLogo.png", 50, 50),
    LogoSpec("Square44x44Logo.png", 44, 44),
    LogoSpec("Square150x150Logo.png", 150, 150),
    LogoSpec("Wide310x150Logo.png", 310, 150),
)


def msix_version(pyproject_version: str) -> str:
    """Map semver ``a.b.c`` to the Store version ``a.b.c.0``.

    Partner Center requires four numeric segments, each 0–65535, and the
    revision (last segment) must be 0. A prerelease suffix is ignored.
    """
    match = _VERSION_RE.match(pyproject_version.strip())
    if match is None:
        raise ValueError(
            f"Cannot map {pyproject_version!r} to an MSIX version. "
            "Expected a.b.c from pyproject.toml."
        )
    parts = [int(match.group(index)) for index in range(1, 4)]
    for number in parts:
        if number > 65535:
            raise ValueError(
                f"MSIX version segment {number} exceeds 65535 in {pyproject_version!r}."
            )
    return f"{parts[0]}.{parts[1]}.{parts[2]}.0"


def read_project_version(path: Path) -> str:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise ValueError(f"No [project].version in {path}")
    return version.strip()


def identity_from_env(env: Mapping[str, str]) -> MsixIdentity:
    """Read Store identity from the environment.

    Raises :class:`MsixIdentityMissing` when a required variable is blank, and
    ``ValueError`` when a value is set but not usable in the manifest.
    """
    name = env.get("MSIX_IDENTITY_NAME", "").strip()
    publisher = env.get("MSIX_PUBLISHER", "").strip()
    display = env.get("MSIX_PUBLISHER_DISPLAY_NAME", "").strip() or _DEFAULT_PUBLISHER_DISPLAY_NAME
    missing = [
        key
        for key, value in (
            ("MSIX_IDENTITY_NAME", name),
            ("MSIX_PUBLISHER", publisher),
        )
        if not value
    ]
    if missing:
        raise MsixIdentityMissing(missing)
    if _IDENTITY_NAME_RE.match(name) is None:
        raise ValueError(
            "MSIX_IDENTITY_NAME must be 3–50 letters, digits, dots, or hyphens "
            f"(Partner Center package identity name). Got {name!r}."
        )
    if not publisher.startswith("CN=") or publisher == "CN=":
        raise ValueError(
            "MSIX_PUBLISHER must be the Partner Center publisher string starting with 'CN='. "
            f"Got {publisher!r}."
        )
    if len(display) > 256:
        raise ValueError("MSIX_PUBLISHER_DISPLAY_NAME is longer than 256 characters.")
    return MsixIdentity(name, publisher, display)


def _xml(value: str) -> str:
    return escape(value, {'"': "&quot;"})


def _edge_color(src: Image.Image) -> tuple[int, int, int, int]:
    pixel = src.convert("RGBA").getpixel((0, 0))
    if isinstance(pixel, int):
        return (pixel, pixel, pixel, 255)
    red, green, blue = int(pixel[0]), int(pixel[1]), int(pixel[2])
    alpha = int(pixel[3]) if len(pixel) > 3 else 255
    return (red, green, blue, alpha)


def tile_background(src: Image.Image) -> str:
    """``#RRGGBB`` from the icon corner, used as the Start-menu tile color."""
    red, green, blue, _alpha = _edge_color(src)
    return f"#{red:02X}{green:02X}{blue:02X}"


def render_logo(
    src: Image.Image,
    width: int,
    height: int,
    background: tuple[int, int, int, int],
) -> Image.Image:
    """Scale *src* into ``width``×``height``. Wide tiles letterbox on *background*."""
    image = src.convert("RGBA")
    if width == height:
        return image.resize((width, height), Image.Resampling.LANCZOS)
    scale = min(width / image.width, height / image.height)
    thumb_w = max(1, round(image.width * scale))
    thumb_h = max(1, round(image.height * scale))
    thumb = image.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), background)
    canvas.paste(thumb, ((width - thumb_w) // 2, (height - thumb_h) // 2), thumb)
    return canvas


def write_logos(icon_path: Path, assets_dir: Path) -> str:
    """Write the manifest tile PNGs. Returns the ``#RRGGBB`` tile background."""
    src = Image.open(icon_path)
    background = _edge_color(src)
    assets_dir.mkdir(parents=True, exist_ok=True)
    for spec in LOGOS:
        logo = render_logo(src, spec.width, spec.height, background)
        if logo.size != (spec.width, spec.height):
            raise RuntimeError(
                f"{spec.filename} rendered at {logo.size}, expected {spec.width}×{spec.height}"
            )
        logo.save(assets_dir / spec.filename, format="PNG")
    return tile_background(src)


def build_manifest(identity: MsixIdentity, version: str, background: str) -> str:
    """Appx manifest for a full-trust desktop app.

    No file associations or protocol handlers: the GUI opens media from inside
    the app (``--open``), and a bare path on argv is not a supported launch mode.
    The bundled OCIO config is read from ``resources/ocio`` inside the package.
    """
    name = _xml(identity.name)
    publisher = _xml(identity.publisher)
    display = _xml(identity.publisher_display_name)
    # Virtualization exclusions keep QSettings, presets, and the TrustMark cache
    # in the real user profile (the same folders the Inno setup uses). The
    # package directory itself stays read-only.
    return f"""<?xml version="1.0" encoding="utf-8"?>
<Package
  xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:uap5="http://schemas.microsoft.com/appx/manifest/uap/windows10/5"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"
  xmlns:virtualization="http://schemas.microsoft.com/appx/manifest/virtualization/windows10"
  IgnorableNamespaces="uap uap5 rescap virtualization">
  <Identity
    Name="{name}"
    Publisher="{publisher}"
    Version="{version}"
    ProcessorArchitecture="x64" />
  <Properties>
    <DisplayName>EXR Converter</DisplayName>
    <PublisherDisplayName>{display}</PublisherDisplayName>
    <Logo>Assets\\StoreLogo.png</Logo>
  </Properties>
  <Dependencies>
    <TargetDeviceFamily
      Name="Windows.Desktop"
      MinVersion="{_MIN_VERSION}"
      MaxVersionTested="{_MAX_VERSION_TESTED}" />
  </Dependencies>
  <Resources>
    <Resource Language="en-us" />
  </Resources>
  <Applications>
    <Application
      Id="EXRConverter"
      Executable="{_APP_EXECUTABLE}"
      EntryPoint="Windows.FullTrustApplication">
      <uap:VisualElements
        DisplayName="EXR Converter"
        Description="Convert video and OpenEXR with OpenColorIO."
        BackgroundColor="{background}"
        Square150x150Logo="Assets\\Square150x150Logo.png"
        Square44x44Logo="Assets\\Square44x44Logo.png">
        <uap:DefaultTile Wide310x150Logo="Assets\\Wide310x150Logo.png" />
      </uap:VisualElements>
      <Extensions>
        <uap5:Extension Category="windows.appExecutionAlias">
          <uap5:AppExecutionAlias>
            <uap5:ExecutionAlias Alias="{_APP_EXECUTABLE}" />
          </uap5:AppExecutionAlias>
        </uap5:Extension>
      </Extensions>
    </Application>
  </Applications>
  <Capabilities>
    <rescap:Capability Name="runFullTrust" />
  </Capabilities>
  <virtualization:FileSystemWriteVirtualization>
    <virtualization:ExcludedDirectories>
      <virtualization:ExcludedDirectory>$(KnownFolder:RoamingAppData)\\VFXTools</virtualization:ExcludedDirectory>
      <virtualization:ExcludedDirectory>$(KnownFolder:LocalAppData)\\VFXTools</virtualization:ExcludedDirectory>
    </virtualization:ExcludedDirectories>
  </virtualization:FileSystemWriteVirtualization>
  <virtualization:RegistryWriteVirtualization>
    <virtualization:ExcludedKeys>
      <virtualization:ExcludedKey>HKEY_CURRENT_USER\\Software\\VFXTools</virtualization:ExcludedKey>
    </virtualization:ExcludedKeys>
  </virtualization:RegistryWriteVirtualization>
</Package>
"""


def stage_app(app_dir: Path, layout: Path) -> None:
    """Hardlink the built app into *layout* (copy if the link fails).

    Hardlinks keep the MSIX staging tree from doubling a multi-gigabyte Nuitka
    folder. The Inno setup reads ``app_dir`` unchanged.
    """
    layout.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(app_dir, followlinks=False):
        relative = Path(dirpath).relative_to(app_dir)
        target_dir = layout / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            source = Path(dirpath) / name
            target = target_dir / name
            if target.exists():
                continue
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)


def build_layout(
    app_dir: Path,
    layout: Path,
    *,
    icon: Path,
    identity: MsixIdentity,
    version: str,
) -> Path:
    """Assemble the MakeAppx input folder. Returns the manifest path."""
    exe = app_dir / _APP_EXECUTABLE
    if not exe.is_file():
        raise RuntimeError(f"Missing {_APP_EXECUTABLE} under {app_dir}.")
    stage_app(app_dir, layout)
    background = write_logos(icon, layout / "Assets")
    manifest = layout / "AppxManifest.xml"
    manifest.write_text(build_manifest(identity, version, background), encoding="utf-8")
    return manifest


def find_makeappx() -> Path | None:
    """Newest ``makeappx.exe`` from the Windows SDK, or None off Windows."""
    if sys.platform != "win32":
        return None
    roots: list[Path] = []
    for key in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(key, "")
        if base:
            roots.append(Path(base) / "Windows Kits" / "10" / "bin")
    sdk = os.environ.get("WindowsSdkDir", "").strip()
    if sdk:
        roots.append(Path(sdk) / "bin")
    found: list[Path] = []
    for root in roots:
        if root.is_dir():
            found.extend(root.glob("*/x64/makeappx.exe"))
    if not found:
        return None
    return sorted(found)[-1]


def pack_msix(makeappx: Path, layout: Path, output: Path) -> None:
    """Pack *layout* with MakeAppx. Does not sign."""
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    command = [
        str(makeappx),
        "pack",
        "/d",
        str(layout),
        "/p",
        str(output),
        "/o",
        "/h",
        "SHA256",
    ]
    print(" ".join(command), flush=True)
    subprocess.run(command, check=True)


def assert_unsigned_msix(path: Path) -> None:
    """Fail if MakeAppx (or a later step) embedded an Appx signature."""
    with zipfile.ZipFile(path) as package:
        names = [entry.replace("\\", "/").lower() for entry in package.namelist()]
    if any(name.endswith("appxsignature.p7x") for name in names):
        raise RuntimeError(
            f"{path} contains AppxSignature.p7x. Partner Center must receive an unsigned MSIX."
        )
    if not any(name == "appxblockmap.xml" for name in names):
        raise RuntimeError(f"{path} is missing AppxBlockMap.xml.")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-dir",
        type=Path,
        required=True,
        help="Nuitka dist folder containing exr_converter.exe.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Destination .msix path.")
    parser.add_argument("--icon", type=Path, default=Path("resources/icons/icon.png"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        identity = identity_from_env(os.environ)
    except MsixIdentityMissing as exc:
        print(f"::warning::{exc}", file=sys.stderr)
        return 0

    version = msix_version(read_project_version(args.pyproject))
    if not args.app_dir.is_dir():
        print(f"::error::App directory not found: {args.app_dir}", file=sys.stderr)
        return 1
    if not args.icon.is_file():
        print(f"::error::Icon not found: {args.icon}", file=sys.stderr)
        return 1
    makeappx = find_makeappx()
    if makeappx is None:
        print(
            "::error::makeappx.exe not found. Install the Windows 10 SDK on the runner.",
            file=sys.stderr,
        )
        return 1

    print(
        f"MSIX identity {identity.name} publisher-display {identity.publisher_display_name} "
        f"version {version}",
        flush=True,
    )
    with tempfile.TemporaryDirectory(prefix="exr-msix-") as tmp:
        layout = Path(tmp) / "layout"
        manifest = build_layout(
            args.app_dir,
            layout,
            icon=args.icon,
            identity=identity,
            version=version,
        )
        print(manifest.read_text(encoding="utf-8"), flush=True)
        pack_msix(makeappx, layout, args.output)
    assert_unsigned_msix(args.output)
    print(f"Wrote unsigned MSIX {args.output}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
