"""Developer ID vs ad-hoc codesign plans, and the Release workflow wiring."""

from __future__ import annotations

import os
import plistlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from macos_codesign import (  # noqa: E402
    MACOS_BUNDLE_ID,
    _resources_link_target,
    assert_notarization_bundle_id,
    default_entitlements_path,
    plan_adhoc_verify,
    plan_dmg_sign,
    plan_gatekeeper_assess,
    plan_resign,
    plan_signature_verify,
    plan_verify,
    relocate_macos_data,
    validate_identity,
)

_IDENTITY = "Developer ID Application: Medeu Global LLC (83546T4BT8)"
_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"


def _mini_app(tmp_path: Path) -> Path:
    app = tmp_path / "EXR Converter.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / "exr_converter"
    exe.write_bytes(b"exe")
    exe.chmod(0o755)
    dylib = macos / "libfoo.dylib"
    dylib.write_bytes(b"dylib")
    return app


def _flat(cmds: list[list[str]]) -> str:
    return "\n".join(" ".join(cmd) for cmd in cmds)


def test_empty_identity_is_not_adhoc() -> None:
    with pytest.raises(SystemExit, match="empty"):
        validate_identity("  ")


def test_adhoc_plan_has_no_runtime_timestamp_or_entitlements(tmp_path: Path) -> None:
    app = _mini_app(tmp_path)
    cmds = plan_resign(
        app,
        identity="-",
        entitlements=tmp_path / "entitlements.plist",
        keychain=tmp_path / "signing.keychain-db",
    )
    flags = [token for cmd in cmds for token in cmd[:-1]]
    assert "--timestamp=none" in flags
    assert "--options" not in flags
    assert "runtime" not in flags
    assert "--entitlements" not in flags
    assert "--keychain" not in flags
    assert "--deep" not in flags
    assert cmds[-1][-1] == str(app)
    verify = plan_verify(app, identity="-")
    assert verify == plan_adhoc_verify(app)
    assert all("--deep" not in " ".join(cmd) for cmd in verify)


def test_developer_id_plan_uses_runtime_on_dylibs_and_entitlements_on_app(
    tmp_path: Path,
) -> None:
    app = _mini_app(tmp_path)
    entitlements = tmp_path / "entitlements.plist"
    entitlements.write_text("plist", encoding="utf-8")
    keychain = tmp_path / "signing.keychain-db"
    cmds = plan_resign(
        app,
        identity=_IDENTITY,
        entitlements=entitlements,
        keychain=keychain,
    )
    text = _flat(cmds)
    assert "--deep" not in text
    assert "--timestamp=none" not in text
    dylib = next(cmd for cmd in cmds if cmd[-1].endswith("libfoo.dylib"))
    assert dylib[dylib.index("--sign") + 1] == _IDENTITY
    assert "--options" in dylib and "runtime" in dylib
    assert "--timestamp" in dylib
    assert "--keychain" in dylib and str(keychain) in dylib
    assert "--entitlements" not in dylib
    exe = next(cmd for cmd in cmds if Path(cmd[-1]).name == "exr_converter")
    app_cmd = cmds[-1]
    assert app_cmd[-1] == str(app)
    for signed in (exe, app_cmd):
        assert "--entitlements" in signed
        assert str(entitlements) in signed
        assert "--options" in signed and "runtime" in signed
        assert "--timestamp" in signed
    verify = plan_verify(app, identity=_IDENTITY)
    assert verify == plan_signature_verify(app)
    assert verify[0][:5] == ["codesign", "--verify", "--deep", "--strict", "--verbose=2"]


def test_dmg_sign_refuses_adhoc_and_skips_hardened_runtime(tmp_path: Path) -> None:
    dmg = tmp_path / "EXR Converter.dmg"
    with pytest.raises(SystemExit, match="Developer ID"):
        plan_dmg_sign(dmg, identity="-")
    cmd = plan_dmg_sign(dmg, identity=_IDENTITY, keychain=tmp_path / "k")
    assert "--options" not in cmd
    assert "runtime" not in cmd
    assert "--timestamp" in cmd
    assert "--timestamp=none" not in cmd
    assert "--entitlements" not in cmd
    assert "--deep" not in cmd
    assert cmd[-1] == str(dmg)
    assess = " ".join(plan_gatekeeper_assess(dmg))
    assert assess.startswith("spctl -a -t open --context context:primary-signature -vv ")


def test_bundle_id_must_be_reverse_dns(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    contents = app / "Contents"
    contents.mkdir(parents=True)
    plist = contents / "Info.plist"
    plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": "EXR Converter"}, fmt=plistlib.FMT_XML))
    with pytest.raises(SystemExit, match=MACOS_BUNDLE_ID):
        assert_notarization_bundle_id(app)
    plist.write_bytes(plistlib.dumps({"CFBundleIdentifier": MACOS_BUNDLE_ID}, fmt=plistlib.FMT_XML))
    assert_notarization_bundle_id(app)


def test_entitlements_match_ctypes_and_dlopen() -> None:
    with default_entitlements_path().open("rb") as handle:
        data = plistlib.load(handle)
    assert data["com.apple.security.cs.disable-library-validation"] is True
    assert data["com.apple.security.cs.allow-unsigned-executable-memory"] is True
    assert data["com.apple.security.cs.allow-jit"] is True
    assert "com.apple.security.app-sandbox" not in data
    assert "com.apple.security.cs.allow-dyld-environment-variables" not in data


def test_release_workflow_notarizes_with_adhoc_fallback() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "Using an ad-hoc signature and skipping notarization." in text
    assert "Developer ID Application: Medeu Global LLC (83546T4BT8)" in text
    assert "83546T4BT8" in text
    assert "openssl pkcs12 -export -legacy" in text
    assert "xcrun notarytool submit" in text
    assert text.count("xcrun notarytool submit") == 1
    assert "--timeout 30m" not in text
    assert 'NOTARY_WAIT="2h"' in text
    assert '--timeout "$NOTARY_WAIT"' in text
    assert "xcrun notarytool info" in text
    assert "polling notarytool info (not resubmitting)." in text
    assert "xcrun notarytool log" in text
    assert "xcrun stapler staple" in text
    assert "contains(matrix.os, 'macos') && 360 || 180" in text
    assert "codesign --verify --deep --strict --verbose=2" in text
    assert "spctl -a -t open --context context:primary-signature -vv" in text
    assert "codesign --force --deep" not in text
    assert "set-key-partition-list" in text
    assert "security delete-keychain" in text
    assert 'echo "$MACOS_CERT_P12_BASE64"' not in text
    assert 'echo "$MACOS_CERT_PASSWORD"' not in text
    assert 'echo "$ASC_API_KEY_P8"' not in text
    assert 'echo "$MACOS_SIGNING_IDENTITY"' not in text
    assert 'echo "$KEYCHAIN_PASS"' not in text
    assert "\nset -x\n" not in text
    sign_at = text.index("name: Re-sign macOS bundle")
    dmg_at = text.index("name: Create DMG (macOS)")
    notary_at = text.index("name: Notarize and staple macOS DMG")
    upload_at = text.index("name: Upload artifact")
    cosign_at = text.index("name: Sign all artifacts with Cosign")
    assert sign_at < dmg_at < notary_at < upload_at < cosign_at
    assert "--macos-signed-app-name=com.vfxtools.exrconverter" in text
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert f"MACOS_BUNDLE_ID := {MACOS_BUNDLE_ID}" in makefile
    assert "--macos-signed-app-name=$(MACOS_BUNDLE_ID)" in makefile


def _macho(path: Path, payload: bytes = b"\xfe\xed\xfa\xcf" + b"\x00" * 16) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def test_resources_link_target_is_posix() -> None:
    assert _resources_link_target(Path("pyproject.toml")) == "../Resources/pyproject.toml"
    nested = Path("PyOpenColorIO") / "bin" / "pyocioamf" / "config-aces-reference.yaml"
    target = _resources_link_target(nested)
    assert target == "../../../../Resources/PyOpenColorIO/bin/pyocioamf/config-aces-reference.yaml"
    assert "\\" not in target


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating symlinks needs extra privileges on Windows",
)
def test_relocate_macos_data_moves_non_macho_and_keeps_binaries(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    macos = app / "Contents" / "MacOS"
    _macho(macos / "exr_converter")
    _macho(macos / "libfoo.dylib")
    _macho(macos / "PySide6" / "Qt" / "lib" / "QtCore")
    _macho(macos / "PyOpenColorIO" / "PyOpenColorIO.so")
    yaml = macos / "PyOpenColorIO" / "bin" / "pyocioamf" / "config-aces-reference.yaml"
    yaml.parent.mkdir(parents=True, exist_ok=True)
    yaml.write_text("ocio: reference\n", encoding="utf-8")
    readme = macos / "PyOpenColorIO" / "README.md"
    readme.write_text("readme\n", encoding="utf-8")
    (macos / "pyproject.toml").write_text("[project]\nversion = '0.0.0'\n", encoding="utf-8")
    ocio = macos / "resources" / "ocio"
    ocio.mkdir(parents=True)
    (ocio / "aces-studio-v4.ocio").write_text("ocio: studio\n", encoding="utf-8")
    lut = ocio / "luts"
    lut.mkdir()
    (lut / "nlog_to_lin.spi1d").write_text("lut\n", encoding="utf-8")
    dist_info = macos / "PyOpenColorIO-2.5.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Metadata-Version: 2.1\n", encoding="utf-8")
    # Mixed dotted directory: Mach-O must stay reachable under the original name.
    qml = macos / "PySide6" / "Qt" / "qml" / "QtQuick.2"
    _macho(qml / "libqtquick2plugin.dylib")
    (qml / "qmldir").write_text("module QtQuick\n", encoding="utf-8")

    relocate_macos_data(app)

    resources = app / "Contents" / "Resources"
    assert (macos / "exr_converter").is_file() and not (macos / "exr_converter").is_symlink()
    assert (macos / "libfoo.dylib").is_file() and not (macos / "libfoo.dylib").is_symlink()
    qtcore = macos / "PySide6" / "Qt" / "lib" / "QtCore"
    assert qtcore.is_file() and not qtcore.is_symlink()

    bin_link = macos / "PyOpenColorIO" / "bin"
    assert bin_link.is_symlink()
    assert os.readlink(bin_link) == "../../Resources/PyOpenColorIO/bin"
    assert (bin_link / "pyocioamf" / "config-aces-reference.yaml").read_text(
        encoding="utf-8"
    ) == "ocio: reference\n"
    assert (
        resources / "PyOpenColorIO" / "bin" / "pyocioamf" / "config-aces-reference.yaml"
    ).is_file()

    readme_link = macos / "PyOpenColorIO" / "README.md"
    assert readme_link.is_symlink()
    assert os.readlink(readme_link) == "../../Resources/PyOpenColorIO/README.md"
    assert readme_link.read_text(encoding="utf-8") == "readme\n"

    project = macos / "pyproject.toml"
    assert project.is_symlink()
    assert os.readlink(project) == "../Resources/pyproject.toml"
    assert project.read_text(encoding="utf-8").startswith("[project]")

    ocio_link = macos / "resources"
    assert ocio_link.is_symlink()
    assert os.readlink(ocio_link) == "../Resources/resources"
    cfg = ocio_link / "ocio" / "aces-studio-v4.ocio"
    assert cfg.is_file()
    assert (cfg.parent / "luts" / "nlog_to_lin.spi1d").read_text(encoding="utf-8") == "lut\n"

    info = macos / "PyOpenColorIO-2.5.0.dist-info"
    assert info.is_symlink()
    assert (info / "METADATA").read_text(encoding="utf-8").startswith("Metadata-Version")

    quick = macos / "PySide6" / "Qt" / "qml" / "QtQuick.2"
    assert quick.is_symlink()
    assert os.readlink(quick) == "QtQuick__dot__2"
    plugin = quick / "libqtquick2plugin.dylib"
    assert plugin.is_file() and not plugin.is_symlink()
    assert (quick / "qmldir").is_symlink()
    assert (quick / "qmldir").read_text(encoding="utf-8") == "module QtQuick\n"
    real_dir = macos / "PySide6" / "Qt" / "qml" / "QtQuick__dot__2"
    assert real_dir.is_dir() and not real_dir.is_symlink()

    relocate_macos_data(app)
    assert os.readlink(project) == "../Resources/pyproject.toml"
    assert os.readlink(quick) == "QtQuick__dot__2"
    assert list(resources.rglob("aces-studio-v4.ocio")) == [
        resources / "resources" / "ocio" / "aces-studio-v4.ocio"
    ]

    cmds = plan_resign(app, identity=_IDENTITY, entitlements=tmp_path / "entitlements.plist")
    signed = [cmd[-1] for cmd in cmds]
    assert not any(path.endswith((".yaml", ".toml", ".md", "qmldir")) for path in signed)
    assert any(path.endswith("libfoo.dylib") for path in signed)
    assert any(path.endswith("libqtquick2plugin.dylib") for path in signed)
    assert any(path.endswith("exr_converter") for path in signed)


def test_release_workflow_can_notarize_without_publishing() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "macos_only:" in text
    assert "publish:" in text
    assert "macos-sign-test-" in text
    assert "fromJson(needs.build-matrix.outputs.include)" in text
    assert "inputs.macos_only != true" in text
    assert "No GitHub Release will be published." in text
    assert "-signed" in text
    # A signing test must not fall through into the publish job.
    marker = "  release:\n    needs: sign\n"
    assert marker in text
    release_if = text.split(marker, 1)[1].split("runs-on:", 1)[0]
    assert "inputs.macos_only != true" in release_if
    assert "inputs.publish == true" in release_if
