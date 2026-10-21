#!/usr/bin/env python3
"""Build the macOS Quick Look and thumbnail appexes and embed them in the app.

Compile with ``swiftc`` (no Xcode project). Each appex is an executable whose
entry point is ``NSExtensionMain``, linked to system frameworks only. Swift
stays dynamic (``/usr/lib/swift``); do not pass ``-static-stdlib``.

Usage:
    python scripts/build_quicklook_appex.py --compile-check
    python scripts/build_quicklook_appex.py --app "dist/EXR Converter.app"
"""

from __future__ import annotations

import argparse
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.quicklook_formats import (  # noqa: E402
    merge_type_declarations,
    supported_content_types,
)

QL_ROOT = ROOT / "packaging" / "macos" / "quicklook"
CORE_SOURCES = sorted((QL_ROOT / "Sources" / "EXRQLCore").glob("*.swift"))
ENTITLEMENTS = QL_ROOT / "Entitlements" / "appex.entitlements"
MAX_EXE_BYTES = 6_000_000

PREVIEW_ID = "com.vfxtools.exrconverter.quicklook-preview"
THUMB_ID = "com.vfxtools.exrconverter.quicklook-thumbnail"
MIN_OS = "12.0"


def host_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x86_64"
    raise SystemExit(f"unsupported architecture {machine!r}")


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        data = tomllib.load(handle)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit("pyproject.toml is missing [project].version")
    return version.strip()


def _base_frameworks(sdk: Path) -> list[str]:
    names = [
        "Foundation",
        "CoreGraphics",
        "CoreText",
        "ImageIO",
        "CoreImage",
        "UniformTypeIdentifiers",
        "Cocoa",
    ]
    qlui = sdk / "System" / "Library" / "Frameworks" / "QuickLookUI.framework"
    if qlui.is_dir():
        names.append("QuickLookUI")
    return names


def compile_executable(
    *,
    module: str,
    sources: list[Path],
    frameworks: list[str],
    arch: str,
    destination: Path,
) -> None:
    sdk = Path(
        subprocess.check_output(
            ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
            text=True,
        ).strip()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "xcrun",
        "--sdk",
        "macosx",
        "swiftc",
        "-swift-version",
        "5",
        "-parse-as-library",
        "-emit-executable",
        "-Osize",
        "-whole-module-optimization",
        "-target",
        f"{arch}-apple-macos{MIN_OS}",
        "-sdk",
        str(sdk),
        "-module-name",
        module,
        "-Xlinker",
        "-e",
        "-Xlinker",
        "_NSExtensionMain",
        "-o",
        str(destination),
    ]
    for framework in [*_base_frameworks(sdk), *frameworks]:
        cmd.extend(["-framework", framework])
    cmd.extend(str(path) for path in sources)
    print(" ".join(cmd), flush=True)
    subprocess.check_call(cmd)


def assert_lightweight(executable: Path) -> None:
    kind = subprocess.check_output(["file", str(executable)], text=True)
    if "Mach-O" not in kind:
        raise SystemExit(f"{executable} is not Mach-O:\n{kind}")
    size = executable.stat().st_size
    if size > MAX_EXE_BYTES:
        raise SystemExit(
            f"{executable.name} is {size} bytes (limit {MAX_EXE_BYTES}). "
            "The appex must link the system Swift runtime, not -static-stdlib."
        )
    linked = subprocess.check_output(["otool", "-L", str(executable)], text=True)
    bad: list[str] = []
    for line in linked.splitlines()[1:]:
        dep = line.strip().split()[0] if line.strip() else ""
        if not dep or dep == str(executable):
            continue
        if dep.startswith("/usr/") or dep.startswith("/System/"):
            continue
        bad.append(dep)
    if bad:
        raise SystemExit(f"{executable.name} links non-system libraries:\n" + "\n".join(bad))
    print(f"{executable.name}: {size} bytes", flush=True)


def extension_info_plist(
    *,
    bundle_id: str,
    executable: str,
    display_name: str,
    version: str,
    principal_class: str,
    extension_point: str,
    preview: bool,
) -> dict[str, object]:
    attributes: dict[str, object] = {
        "QLSupportedContentTypes": supported_content_types(),
    }
    if preview:
        attributes["QLIsDataBasedPreview"] = True
        attributes["QLSupportsSearchableItems"] = False
    else:
        attributes["QLThumbnailMinimumDimension"] = 16
    info: dict[str, object] = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": display_name,
        "CFBundleExecutable": executable,
        "CFBundleIdentifier": bundle_id,
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": display_name,
        "CFBundlePackageType": "XPC!",
        "CFBundleShortVersionString": version,
        "CFBundleSupportedPlatforms": ["MacOSX"],
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": MIN_OS,
        "NSExtension": {
            "NSExtensionAttributes": attributes,
            "NSExtensionPointIdentifier": extension_point,
            "NSExtensionPrincipalClass": principal_class,
        },
    }
    return merge_type_declarations(info)


def write_appex(
    *,
    dest: Path,
    bundle_id: str,
    executable_name: str,
    display_name: str,
    module: str,
    extra_source: Path,
    extra_frameworks: list[str],
    extension_point: str,
    principal_class: str,
    preview: bool,
    arch: str,
    version: str,
) -> Path:
    if dest.exists():
        shutil.rmtree(dest)
    macos = dest / "Contents" / "MacOS"
    exe = macos / executable_name
    compile_executable(
        module=module,
        sources=[*CORE_SOURCES, extra_source],
        frameworks=extra_frameworks,
        arch=arch,
        destination=exe,
    )
    assert_lightweight(exe)
    info = extension_info_plist(
        bundle_id=bundle_id,
        executable=executable_name,
        display_name=display_name,
        version=version,
        principal_class=principal_class,
        extension_point=extension_point,
        preview=preview,
    )
    plist_path = dest / "Contents" / "Info.plist"
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_XML)
    ad_hoc_sign(dest)
    return dest


def ad_hoc_sign(appex: Path) -> None:
    """Seal the appex so the bundle layout is valid before Developer ID re-sign."""
    if not ENTITLEMENTS.is_file():
        raise SystemExit(f"missing appex entitlements: {ENTITLEMENTS}")
    subprocess.check_call(
        [
            "codesign",
            "--force",
            "--sign",
            "-",
            "--timestamp=none",
            "--entitlements",
            str(ENTITLEMENTS),
            str(appex),
        ]
    )
    subprocess.check_call(["codesign", "--verify", "--strict", "--verbose=2", str(appex)])


def build_pair(staging: Path, arch: str) -> tuple[Path, Path]:
    if not CORE_SOURCES:
        raise SystemExit(f"no Swift sources in {QL_ROOT / 'Sources' / 'EXRQLCore'}")
    version = project_version()
    preview = write_appex(
        dest=staging / "EXRConverterPreview.appex",
        bundle_id=PREVIEW_ID,
        executable_name="EXRConverterPreview",
        display_name="EXR Converter Quick Look",
        module="EXRConverterPreview",
        extra_source=QL_ROOT / "Sources" / "EXRQLPreview" / "PreviewProvider.swift",
        extra_frameworks=["QuickLook"],
        extension_point="com.apple.quicklook.preview",
        principal_class="EXRConverterPreview.PreviewProvider",
        preview=True,
        arch=arch,
        version=version,
    )
    thumb = write_appex(
        dest=staging / "EXRConverterThumbnail.appex",
        bundle_id=THUMB_ID,
        executable_name="EXRConverterThumbnail",
        display_name="EXR Converter Thumbnails",
        module="EXRConverterThumbnail",
        extra_source=QL_ROOT / "Sources" / "EXRQLThumbnail" / "ThumbnailProvider.swift",
        extra_frameworks=["QuickLookThumbnailing"],
        extension_point="com.apple.quicklook.thumbnail",
        principal_class="EXRConverterThumbnail.ThumbnailProvider",
        preview=False,
        arch=arch,
        version=version,
    )
    return preview, thumb


def install_into_app(app: Path, arch: str) -> None:
    if not (app / "Contents").is_dir():
        raise SystemExit(f"not an app bundle: {app}")
    staging = app / "Contents" / "PlugIns"
    staging.mkdir(parents=True, exist_ok=True)
    build_pair(staging, arch)
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    if not isinstance(info, dict):
        raise SystemExit(f"Info.plist is not a dictionary: {plist_path}")
    merged = merge_type_declarations(info)
    with plist_path.open("wb") as handle:
        plistlib.dump(merged, handle, fmt=plistlib.FMT_XML)
    for appex in sorted(staging.glob("*.appex")):
        print(f"embedded {appex}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("arm64", "x86_64"), default=None)
    parser.add_argument("--app", type=Path, default=None, help="Embed into this .app")
    parser.add_argument(
        "--compile-check",
        action="store_true",
        help="Compile both appexes into a temp directory and discard them",
    )
    args = parser.parse_args()
    if sys.platform != "darwin":
        raise SystemExit("Quick Look appexes build on macOS only")
    arch = args.arch or host_arch()
    if args.compile_check:
        with tempfile.TemporaryDirectory(prefix="exr-quicklook-") as tmp:
            build_pair(Path(tmp), arch)
        return
    if args.app is None:
        raise SystemExit("pass --app or --compile-check")
    install_into_app(args.app, arch)


if __name__ == "__main__":
    main()
