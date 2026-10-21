"""Unsigned MSIX manifest, tile logos, and Release workflow wiring."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_msix import (  # noqa: E402
    _APP_EXECUTABLE,
    LOGOS,
    MsixIdentity,
    MsixIdentityMissing,
    assert_unsigned_msix,
    build_layout,
    build_manifest,
    identity_from_env,
    msix_version,
    read_project_version,
)

_NS = {
    "m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
    "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
    "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities",
}


def _identity(**overrides: str) -> MsixIdentity:
    data = {
        "MSIX_IDENTITY_NAME": "DerekVFX.EXRConverter",
        "MSIX_PUBLISHER": "CN=00000000-0000-0000-0000-000000000000",
        "MSIX_PUBLISHER_DISPLAY_NAME": "",
    }
    data.update(overrides)
    return identity_from_env(data)


def test_msix_version_forces_store_revision_zero() -> None:
    assert msix_version("0.12.3") == "0.12.3.0"
    assert msix_version("1.2.3rc1") == "1.2.3.0"
    assert msix_version(" 10.0.1 ") == "10.0.1.0"
    assert msix_version("1.2.3.9") == "1.2.3.0"
    with pytest.raises(ValueError):
        msix_version("1.2")
    with pytest.raises(ValueError):
        msix_version("1.2.70000")


def test_repo_version_maps() -> None:
    mapped = msix_version(read_project_version(ROOT / "pyproject.toml"))
    assert mapped.endswith(".0")
    assert mapped.count(".") == 3


def test_identity_defaults_and_skips() -> None:
    ident = _identity()
    assert ident.publisher_display_name == "DerekVFX"
    named = _identity(MSIX_PUBLISHER_DISPLAY_NAME="Derek VFX")
    assert named.publisher_display_name == "Derek VFX"
    with pytest.raises(MsixIdentityMissing) as missing:
        identity_from_env({})
    assert missing.value.missing == ["MSIX_IDENTITY_NAME", "MSIX_PUBLISHER"]
    with pytest.raises(ValueError, match="CN="):
        identity_from_env(
            {"MSIX_IDENTITY_NAME": "DerekVFX.EXRConverter", "MSIX_PUBLISHER": "DerekVFX"}
        )
    with pytest.raises(ValueError, match="package identity name"):
        identity_from_env({"MSIX_IDENTITY_NAME": "bad name", "MSIX_PUBLISHER": "CN=X"})


def test_manifest_is_full_trust_desktop_without_associations() -> None:
    ident = MsixIdentity("DerekVFX.EXRConverter", "CN=Foo & Bar", "DerekVFX")
    text = build_manifest(ident, "0.12.3.0", "#1B4F9C")
    root = ET.fromstring(text)
    identity = root.find("m:Identity", _NS)
    assert identity is not None
    assert identity.get("Name") == "DerekVFX.EXRConverter"
    assert identity.get("Publisher") == "CN=Foo & Bar"
    assert identity.get("Version") == "0.12.3.0"
    assert identity.get("ProcessorArchitecture") == "x64"
    family = root.find("m:Dependencies/m:TargetDeviceFamily", _NS)
    assert family is not None
    assert family.get("Name") == "Windows.Desktop"
    app = root.find("m:Applications/m:Application", _NS)
    assert app is not None
    assert app.get("Executable") == _APP_EXECUTABLE
    assert app.get("EntryPoint") == "Windows.FullTrustApplication"
    capability = root.find("m:Capabilities/rescap:Capability", _NS)
    assert capability is not None
    assert capability.get("Name") == "runFullTrust"
    assert "fileTypeAssociation" not in text
    assert "windows.protocol" not in text
    assert "runFullTrust" in text
    assert "AppxSignature" not in text
    assert "VFXTools" in text


def test_layout_logos_and_app_files(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "resources" / "ocio").mkdir(parents=True)
    (app / _APP_EXECUTABLE).write_bytes(b"MZ-not-a-real-exe")
    ocio = app / "resources" / "ocio" / "aces-studio-v4.ocio"
    ocio.write_text("ocio_profile_version: 2.5\n", encoding="utf-8")
    layout = tmp_path / "layout"
    manifest = build_layout(
        app,
        layout,
        icon=ROOT / "resources" / "icons" / "icon.png",
        identity=_identity(),
        version="0.12.3.0",
    )
    assert manifest.is_file()
    assert (layout / _APP_EXECUTABLE).read_bytes() == b"MZ-not-a-real-exe"
    packed_ocio = layout / "resources" / "ocio" / "aces-studio-v4.ocio"
    assert packed_ocio.read_text(encoding="utf-8").startswith("ocio_profile_version")
    for spec in LOGOS:
        with Image.open(layout / "Assets" / spec.filename) as logo:
            assert logo.size == (spec.width, spec.height)
    text = manifest.read_text(encoding="utf-8")
    assert "PublisherDisplayName>DerekVFX<" in text
    assert 'EntryPoint="Windows.FullTrustApplication"' in text


def test_assert_unsigned_rejects_signature(tmp_path: Path) -> None:
    signed = tmp_path / "signed.msix"
    unsigned = tmp_path / "unsigned.msix"

    with zipfile.ZipFile(unsigned, "w") as package:
        package.writestr("AppxBlockMap.xml", "<BlockMap/>")
        package.writestr("AppxManifest.xml", "<Package/>")
    assert_unsigned_msix(unsigned)
    with zipfile.ZipFile(signed, "w") as package:
        package.writestr("AppxBlockMap.xml", "<BlockMap/>")
        package.writestr("AppxSignature.p7x", b"signed")
    with pytest.raises(RuntimeError, match="unsigned"):
        assert_unsigned_msix(signed)


def test_release_workflow_packages_unsigned_msix_without_new_triggers() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    script = (ROOT / "scripts" / "package_msix.py").read_text(encoding="utf-8")
    assert "on:\n  push:\n    tags:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "schedule:" not in workflow
    assert "scripts/package_msix.py" in workflow
    assert "vars.MSIX_IDENTITY_NAME" in workflow
    assert "vars.MSIX_PUBLISHER" in workflow
    assert "vars.MSIX_PUBLISHER_DISPLAY_NAME" in workflow
    assert "exr_converter-windows-x86_64.msix" in workflow
    assert "exr_converter-windows-x86_64-msix" in workflow
    assert "installer.iss" in workflow
    assert "signtool" not in script.lower()
    assert "makeappx" in script.lower()
    # The setup executable upload is unchanged.
    assert "path: ${{ matrix.artifact }}.${{ matrix.ext }}" in workflow
