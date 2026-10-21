#!/usr/bin/env python3
"""Build an unsigned Partner Center ``.msixupload`` of the Windows Nuitka app.

Partner Center ("MSIX or PWA app") re-signs the package. This script must not
code-sign the package or embed ``AppxSignature.p7x``.

The upload is a zip whose members sit at the archive root (Microsoft's
manual-upload layout):

* ``exr_converter_<version>_x64.msix`` — the unsigned package
* ``exr_converter_<version>_x64.appxsym`` — PDB symbols, only when the app
  directory contains ``.pdb`` files

The symbol member uses the ``.appxsym`` extension. A ``.msixsym`` member is
rejected by Partner Center. When Nuitka produced no PDBs the upload contains
only the ``.msix``, which is still a valid upload. PDB files are left out of
the MSIX layout. The Inno setup still reads the original app directory.

``--sign-msix`` is a separate step. It signs the loose ``.msix`` with
``signtool`` when the Azure Artifact Signing secrets are all set, and it
leaves that file unsigned otherwise. It refuses a ``.msixupload`` path. Call
it only after the upload has been written.

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
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
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
_SIGNING_ENV_KEYS = (
    "AZURE_TENANT_ID",
    "AZURE_CLIENT_ID",
    "AZURE_CLIENT_SECRET",
    "AZURE_ARTIFACT_SIGNING_ENDPOINT",
    "AZURE_ARTIFACT_SIGNING_ACCOUNT",
    "AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE",
)
_NUGET_EXE_URL = "https://dist.nuget.org/win-x86-commandline/v6.13.2/nuget.exe"
_ARTIFACT_SIGNING_CLIENT_VERSION = "1.0.146"
_TIMESTAMP_URL = "http://timestamp.acs.microsoft.com"
# Skip every DefaultAzureCredential type except EnvironmentCredential so a
# GitHub runner uses AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET
# and does not wait on interactive or managed-identity login.
_EXCLUDE_CREDENTIALS = (
    "ManagedIdentityCredential",
    "WorkloadIdentityCredential",
    "SharedTokenCacheCredential",
    "VisualStudioCredential",
    "VisualStudioCodeCredential",
    "AzureCliCredential",
    "AzurePowerShellCredential",
    "AzureDeveloperCliCredential",
    "InteractiveBrowserCredential",
)


class MsixIdentity(NamedTuple):
    name: str
    publisher: str
    publisher_display_name: str


class ArtifactSigningConfig(NamedTuple):
    """Account settings for signtool. The client secret stays in the environment."""

    endpoint: str
    account: str
    certificate_profile: str


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
    folder. The Inno setup reads ``app_dir`` unchanged. ``.pdb`` files stay in
    ``app_dir`` for an optional ``.appxsym`` and are not installed with the app.
    """
    layout.mkdir(parents=True, exist_ok=True)
    for dirpath, _dirnames, filenames in os.walk(app_dir, followlinks=False):
        relative = Path(dirpath).relative_to(app_dir)
        target_dir = layout / relative
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name.lower().endswith(".pdb"):
                continue
            source = Path(dirpath) / name
            target = target_dir / name
            if target.exists():
                continue
            try:
                os.link(source, target)
            except OSError:
                shutil.copy2(source, target)


def find_pdbs(app_dir: Path) -> list[Path]:
    """``.pdb`` files under *app_dir*, in stable relative-path order."""
    found: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(app_dir, followlinks=False):
        for name in filenames:
            if name.lower().endswith(".pdb"):
                found.append(Path(dirpath) / name)
    return sorted(found)


def upload_member_stem(version: str) -> str:
    """Inner archive name shared by the ``.msix`` and ``.appxsym`` members."""
    return f"exr_converter_{version}_x64"


def write_appxsym(pdbs: list[Path], app_dir: Path, dest: Path) -> None:
    """Zip *pdbs* into an ``.appxsym``. Paths are relative to *app_dir*."""
    if not pdbs:
        raise ValueError("An .appxsym requires at least one PDB file.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest.unlink()
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for pdb in pdbs:
            archive.write(pdb, pdb.relative_to(app_dir).as_posix())


def build_msixupload(msix: Path, output: Path, *, app_dir: Path, version: str) -> Path:
    """Zip *msix* into a Partner Center ``.msixupload``.

    Members are at the zip root, the same layout as Explorer's
    Send to → Compressed folder on the files themselves (not on a parent
    directory). The package member is stored, not deflated again. The symbol
    member is included only when :func:`find_pdbs` finds files.
    """
    if not msix.is_file():
        raise RuntimeError(f"Missing MSIX to wrap: {msix}")
    if output.resolve() == msix.resolve():
        raise ValueError("The .msixupload path and the .msix path must differ.")
    stem = upload_member_stem(version)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    pdbs = find_pdbs(app_dir)
    with tempfile.TemporaryDirectory(prefix="exr-appxsym-") as tmp:
        with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
            archive.write(msix, f"{stem}.msix", compress_type=zipfile.ZIP_STORED)
            if pdbs:
                symbols = Path(tmp) / f"{stem}.appxsym"
                write_appxsym(pdbs, app_dir, symbols)
                archive.write(symbols, symbols.name, compress_type=zipfile.ZIP_DEFLATED)
    if pdbs:
        print(
            f"MSIX upload includes {len(pdbs)} PDB file(s) in {stem}.appxsym",
            flush=True,
        )
    else:
        print(
            "MSIX upload has no .appxsym (no PDB files under the app directory). "
            "Partner Center accepts an upload that contains only the .msix.",
            flush=True,
        )
    return output


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


def _zip_entry_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as package:
        return [entry.replace("\\", "/").lower() for entry in package.namelist()]


def assert_unsigned_msix(path: Path) -> None:
    """Fail if MakeAppx (or a later step) embedded an Appx signature."""
    names = _zip_entry_names(path)
    if any(name.endswith("appxsignature.p7x") for name in names):
        raise RuntimeError(
            f"{path} contains AppxSignature.p7x. Partner Center must receive an unsigned MSIX."
        )
    if not any(name == "appxblockmap.xml" for name in names):
        raise RuntimeError(f"{path} is missing AppxBlockMap.xml.")


def artifact_signing_from_env(env: Mapping[str, str]) -> ArtifactSigningConfig | None:
    """Return Artifact Signing settings, or None when no signing secrets are set.

    A partial set is an error so a release does not silently ship an unsigned
    package after only some of the secrets were added. The client secret is
    checked for presence and is not copied into the returned config.
    """
    values = {key: env.get(key, "").strip() for key in _SIGNING_ENV_KEYS}
    if not any(values.values()):
        return None
    missing = [key for key, value in values.items() if not value]
    if missing:
        names = ", ".join(missing)
        raise ValueError(
            "Azure Artifact Signing secrets are incomplete ("
            f"{names}). Set all of them, or none. The loose .msix is signed only "
            "when every secret is present."
        )
    return ArtifactSigningConfig(
        endpoint=values["AZURE_ARTIFACT_SIGNING_ENDPOINT"],
        account=values["AZURE_ARTIFACT_SIGNING_ACCOUNT"],
        certificate_profile=values["AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE"],
    )


def signing_metadata(config: ArtifactSigningConfig) -> dict[str, object]:
    """``metadata.json`` for signtool's Artifact Signing dlib.

    EnvironmentCredential stays enabled. The client secret is not written here.
    """
    return {
        "Endpoint": config.endpoint,
        "CodeSigningAccountName": config.account,
        "CertificateProfileName": config.certificate_profile,
        "ExcludeCredentials": list(_EXCLUDE_CREDENTIALS),
    }


def assert_loose_msix_path(path: Path) -> None:
    """The only file signtool may sign is a loose ``.msix``."""
    if path.name.lower().endswith(".msixupload") or path.suffix.lower() != ".msix":
        raise ValueError(
            f"Refusing to sign {path.name}. signtool may sign the loose .msix only. "
            "The .msixupload stays unsigned so Partner Center can re-sign it."
        )


def signtool_sign_command(signtool: Path, dlib: Path, metadata: Path, msix: Path) -> list[str]:
    """signtool command for one loose ``.msix``. The upload is not an argument."""
    assert_loose_msix_path(msix)
    if dlib.name.lower() != "azure.codesigning.dlib.dll":
        raise ValueError(f"Expected Azure.CodeSigning.Dlib.dll, got {dlib.name}.")
    return [
        str(signtool),
        "sign",
        "/v",
        "/debug",
        "/fd",
        "SHA256",
        "/tr",
        _TIMESTAMP_URL,
        "/td",
        "SHA256",
        "/dlib",
        str(dlib),
        "/dmdf",
        str(metadata),
        str(msix),
    ]


def assert_upload_contains_unsigned_msix(path: Path) -> None:
    """The ``.msix`` stored inside the upload must not contain ``AppxSignature.p7x``."""
    with zipfile.ZipFile(path) as outer:
        members = [
            name
            for name in outer.namelist()
            if name.lower().endswith(".msix") and not name.endswith("/")
        ]
        if len(members) != 1:
            raise RuntimeError(
                f"{path} should contain exactly one .msix member, found {members!r}."
            )
        payload = outer.read(members[0])
    with zipfile.ZipFile(io.BytesIO(payload)) as package:
        names = [entry.replace("\\", "/").lower() for entry in package.namelist()]
    if any(name.endswith("appxsignature.p7x") for name in names):
        raise RuntimeError(
            f"{path} contains a signed .msix. Partner Center re-signs the upload. "
            "Sign only the loose self-distribution .msix, after the upload is written."
        )
    if not any(name == "appxblockmap.xml" for name in names):
        raise RuntimeError(f"The .msix inside {path} is missing AppxBlockMap.xml.")


def assert_signed_msix(path: Path) -> None:
    """The loose self-distribution package has an Appx signature."""
    assert_loose_msix_path(path)
    names = _zip_entry_names(path)
    if not any(name.endswith("appxsignature.p7x") for name in names):
        raise RuntimeError(f"{path} has no AppxSignature.p7x after signtool.")


def _sdk_version_key(path: Path) -> tuple[int, ...]:
    folder = path.parent.parent.name
    numbers: list[int] = []
    for piece in folder.split("."):
        if not piece.isdigit():
            break
        numbers.append(int(piece))
    return tuple(numbers)


def find_signtool() -> Path | None:
    """Newest x64 ``signtool.exe`` from the Windows SDK, or None off Windows."""
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
            found.extend(root.glob("*/x64/signtool.exe"))
    if not found:
        return None
    return max(found, key=_sdk_version_key)


def _download(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "exr-converter-msix"})
    with urllib.request.urlopen(request, timeout=120) as response, dest.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def install_artifact_signing_dlib(dest: Path) -> Path:
    """Install ``Microsoft.ArtifactSigning.Client`` and return the x64 dlib."""
    dest.mkdir(parents=True, exist_ok=True)
    nuget = dest / "nuget.exe"
    if not nuget.is_file():
        _download(_NUGET_EXE_URL, nuget)
    subprocess.run(
        [
            str(nuget),
            "install",
            "Microsoft.ArtifactSigning.Client",
            "-Version",
            _ARTIFACT_SIGNING_CLIENT_VERSION,
            "-OutputDirectory",
            str(dest),
            "-NonInteractive",
            "-Verbosity",
            "quiet",
        ],
        check=True,
    )
    preferred = (
        dest
        / f"Microsoft.ArtifactSigning.Client.{_ARTIFACT_SIGNING_CLIENT_VERSION}"
        / "bin"
        / "x64"
        / "Azure.CodeSigning.Dlib.dll"
    )
    if preferred.is_file():
        return preferred
    matches = [
        path
        for path in dest.rglob("Azure.CodeSigning.Dlib.dll")
        if path.parent.name.lower() == "x64"
    ]
    if not matches:
        raise RuntimeError(
            "Azure.CodeSigning.Dlib.dll was not found after installing "
            f"Microsoft.ArtifactSigning.Client {_ARTIFACT_SIGNING_CLIENT_VERSION}."
        )
    return matches[0]


def sign_loose_msix(msix: Path, upload: Path) -> int:
    """Sign *msix* when Artifact Signing secrets are set. Never signs *upload*."""
    assert_loose_msix_path(msix)
    if not msix.is_file():
        print(f"::error::MSIX not found: {msix}", file=sys.stderr)
        return 1
    if not upload.is_file():
        print(f"::error::MSIX upload not found: {upload}", file=sys.stderr)
        return 1
    assert_upload_contains_unsigned_msix(upload)
    try:
        config = artifact_signing_from_env(os.environ)
    except ValueError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    if config is None:
        print(
            "::warning::Self-distribution MSIX is unsigned. Windows will not install it "
            "until it has a signature from a certificate the PC trusts. Set Azure Artifact "
            "Signing secrets (AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, "
            "AZURE_ARTIFACT_SIGNING_ENDPOINT, AZURE_ARTIFACT_SIGNING_ACCOUNT, "
            "AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE) to sign "
            f"{msix.name} with signtool. {upload.name} is never signed.",
            file=sys.stderr,
        )
        return 0
    if sys.platform != "win32":
        print("::error::signtool signing requires Windows.", file=sys.stderr)
        return 1
    signtool = find_signtool()
    if signtool is None:
        print(
            "::error::signtool.exe not found. Install the Windows SDK "
            "(10.0.22621 or newer) on the runner.",
            file=sys.stderr,
        )
        return 1
    sealed = upload.read_bytes()
    with tempfile.TemporaryDirectory(prefix="exr-msix-sign-") as tmp:
        tmp_path = Path(tmp)
        dlib = install_artifact_signing_dlib(tmp_path)
        metadata_path = tmp_path / "metadata.json"
        metadata_path.write_text(
            json.dumps(signing_metadata(config), indent=2) + "\n",
            encoding="utf-8",
        )
        command = signtool_sign_command(signtool, dlib, metadata_path, msix)
        print(
            "Signing the loose MSIX with signtool. The .msixupload is not an argument.",
            flush=True,
        )
        print(" ".join(command), flush=True)
        subprocess.run(command, check=True)
    if upload.read_bytes() != sealed:
        raise RuntimeError(f"signtool modified {upload.name}. The upload must stay unsigned.")
    assert_upload_contains_unsigned_msix(upload)
    assert_signed_msix(msix)
    print(f"Signed {msix}. {upload.name} is still unsigned.", flush=True)
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--app-dir",
        type=Path,
        help="Nuitka dist folder containing exr_converter.exe.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Destination .msixupload (zip of the .msix and optional .appxsym).",
    )
    parser.add_argument(
        "--msix",
        type=Path,
        default=None,
        help="Also keep the plain unsigned .msix at this path.",
    )
    parser.add_argument(
        "--sign-msix",
        type=Path,
        default=None,
        help="Sign this loose .msix with signtool when Artifact Signing secrets are set.",
    )
    parser.add_argument(
        "--upload",
        type=Path,
        default=None,
        help="Existing .msixupload to leave unsigned while --sign-msix runs.",
    )
    parser.add_argument("--icon", type=Path, default=Path("resources/icons/icon.png"))
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    args = parser.parse_args(argv)
    if args.sign_msix is not None:
        if args.upload is None:
            parser.error("--upload is required with --sign-msix")
        return args
    if args.app_dir is None or args.output is None:
        parser.error("--app-dir and --output are required to pack an MSIX")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.sign_msix is not None:
        return sign_loose_msix(args.sign_msix, args.upload)
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
        msix_path = args.msix if args.msix is not None else Path(tmp) / "package.msix"
        pack_msix(makeappx, layout, msix_path)
        assert_unsigned_msix(msix_path)
        build_msixupload(msix_path, args.output, app_dir=args.app_dir, version=version)
    print(f"Wrote unsigned MSIX upload {args.output}", flush=True)
    if args.msix is not None:
        print(f"Wrote unsigned MSIX {args.msix}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"::error::{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
