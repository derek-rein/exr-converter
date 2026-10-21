"""MSIX manifest, upload zip, optional signtool hook, and Release workflow wiring."""

from __future__ import annotations

import inspect
import io
import json
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
    artifact_signing_from_env,
    assert_unsigned_msix,
    assert_upload_contains_unsigned_msix,
    build_layout,
    build_manifest,
    build_msixupload,
    identity_from_env,
    msix_version,
    pack_msix,
    read_project_version,
    sign_loose_msix,
    signing_metadata,
    signtool_sign_command,
    upload_member_stem,
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


def test_layout_leaves_pdb_files_out_of_the_package(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "native").mkdir(parents=True)
    (app / _APP_EXECUTABLE).write_bytes(b"MZ")
    (app / "exr_converter.pdb").write_bytes(b"ROOT")
    (app / "native" / "helper.PDB").write_bytes(b"NESTED")
    layout = tmp_path / "layout"
    build_layout(
        app,
        layout,
        icon=ROOT / "resources" / "icons" / "icon.png",
        identity=_identity(),
        version="0.12.3.0",
    )
    packed = [
        path for path in layout.rglob("*") if path.is_file() and path.suffix.lower() == ".pdb"
    ]
    assert packed == []
    assert (app / "exr_converter.pdb").read_bytes() == b"ROOT"
    assert (app / "native" / "helper.PDB").read_bytes() == b"NESTED"


def _plain_msix(path: Path, payload: bytes = b"msix-bytes") -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("AppxBlockMap.xml", payload)


def test_msixupload_without_pdbs_contains_only_the_msix(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    msix = tmp_path / "plain.msix"
    _plain_msix(msix)
    upload = tmp_path / "out.msixupload"
    build_msixupload(msix, upload, app_dir=app, version="0.12.3.0")
    stem = upload_member_stem("0.12.3.0")
    with zipfile.ZipFile(upload) as package:
        names = package.namelist()
        assert names == [f"{stem}.msix"]
        assert "/" not in names[0]
        info = package.getinfo(names[0])
        assert info.compress_type == zipfile.ZIP_STORED
        assert not any(name.endswith(".appxsym") or name.endswith(".msixsym") for name in names)


def test_msixupload_includes_appxsym_when_pdbs_exist(tmp_path: Path) -> None:
    app = tmp_path / "app"
    (app / "native").mkdir(parents=True)
    (app / "native" / "exr_converter.pdb").write_bytes(b"SYMBOLS")
    msix = tmp_path / "plain.msix"
    _plain_msix(msix, b"package-body")
    upload = tmp_path / "out.msixupload"
    build_msixupload(msix, upload, app_dir=app, version="0.12.3.0")
    stem = upload_member_stem("0.12.3.0")
    with zipfile.ZipFile(upload) as package:
        assert package.namelist() == [f"{stem}.msix", f"{stem}.appxsym"]
        assert package.getinfo(f"{stem}.msix").compress_type == zipfile.ZIP_STORED
        assert package.getinfo(f"{stem}.appxsym").compress_type == zipfile.ZIP_DEFLATED
        assert package.read(f"{stem}.msix") == msix.read_bytes()
        symbol_bytes = package.read(f"{stem}.appxsym")
    with zipfile.ZipFile(io.BytesIO(symbol_bytes)) as symbols:
        assert symbols.namelist() == ["native/exr_converter.pdb"]
        assert symbols.read("native/exr_converter.pdb") == b"SYMBOLS"


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


_SIGNING_ENV = {
    "AZURE_TENANT_ID": "tenant",
    "AZURE_CLIENT_ID": "client",
    "AZURE_CLIENT_SECRET": "super-secret",
    "AZURE_ARTIFACT_SIGNING_ENDPOINT": "https://eus.codesigning.azure.net",
    "AZURE_ARTIFACT_SIGNING_ACCOUNT": "exr-account",
    "AZURE_ARTIFACT_SIGNING_CERTIFICATE_PROFILE": "exr-profile",
}


def test_artifact_signing_secrets_are_all_or_nothing() -> None:
    assert artifact_signing_from_env({}) is None
    with pytest.raises(ValueError, match="incomplete"):
        artifact_signing_from_env({"AZURE_TENANT_ID": "tenant"})
    config = artifact_signing_from_env(_SIGNING_ENV)
    assert config is not None
    assert config.account == "exr-account"
    metadata = signing_metadata(config)
    blob = json.dumps(metadata)
    assert "super-secret" not in blob
    assert "EnvironmentCredential" not in metadata["ExcludeCredentials"]
    assert "InteractiveBrowserCredential" in metadata["ExcludeCredentials"]


def test_signtool_command_signs_only_the_loose_msix(tmp_path: Path) -> None:
    msix = tmp_path / "app.msix"
    command = signtool_sign_command(
        Path("signtool.exe"),
        Path("Azure.CodeSigning.Dlib.dll"),
        tmp_path / "metadata.json",
        msix,
    )
    assert command[1] == "sign"
    assert command[-1] == str(msix)
    assert "msixupload" not in " ".join(command).lower()
    assert "super-secret" not in " ".join(command)
    assert "/dlib" in command
    with pytest.raises(ValueError, match="loose"):
        signtool_sign_command(
            Path("signtool.exe"),
            Path("Azure.CodeSigning.Dlib.dll"),
            tmp_path / "metadata.json",
            tmp_path / "app.msixupload",
        )


def test_rewriting_the_loose_msix_leaves_the_upload_unsigned(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    msix = tmp_path / "plain.msix"
    _plain_msix(msix)
    upload = tmp_path / "out.msixupload"
    build_msixupload(msix, upload, app_dir=app, version="0.12.3.0")
    sealed = upload.read_bytes()
    assert_upload_contains_unsigned_msix(upload)
    msix.write_bytes(b"signed-replacement")
    assert upload.read_bytes() == sealed
    assert_upload_contains_unsigned_msix(upload)


def test_upload_guard_rejects_a_signed_inner_package(tmp_path: Path) -> None:
    app = tmp_path / "app"
    app.mkdir()
    msix = tmp_path / "signed.msix"
    with zipfile.ZipFile(msix, "w") as package:
        package.writestr("AppxBlockMap.xml", "<BlockMap/>")
        package.writestr("AppxSignature.p7x", b"signed")
    upload = tmp_path / "out.msixupload"
    build_msixupload(msix, upload, app_dir=app, version="0.12.3.0")
    with pytest.raises(RuntimeError, match="signed"):
        assert_upload_contains_unsigned_msix(upload)


def test_sign_loose_msix_skips_when_secrets_are_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / "app"
    app.mkdir()
    msix = tmp_path / "plain.msix"
    _plain_msix(msix)
    original = msix.read_bytes()
    upload = tmp_path / "out.msixupload"
    build_msixupload(msix, upload, app_dir=app, version="0.12.3.0")
    for key in _SIGNING_ENV:
        monkeypatch.delenv(key, raising=False)
    assert sign_loose_msix(msix, upload) == 0
    assert msix.read_bytes() == original
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant")
    assert sign_loose_msix(msix, upload) == 1
    assert msix.read_bytes() == original


def test_release_workflow_publishes_both_msix_assets_without_new_triggers() -> None:
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
    assert "exr_converter-windows-x86_64.msixupload" in workflow
    assert "--msix exr_converter-windows-x86_64.msix" in workflow
    assert "--sign-msix exr_converter-windows-x86_64.msix" in workflow
    assert "--upload exr_converter-windows-x86_64.msixupload" in workflow
    assert "--sign-msix exr_converter-windows-x86_64.msixupload" not in workflow
    pack_at = workflow.index("--output exr_converter-windows-x86_64.msixupload")
    sign_at = workflow.index("--sign-msix exr_converter-windows-x86_64.msix")
    upload_at = workflow.index("name: exr_converter-windows-x86_64-msixupload")
    publish_at = workflow.index("msstore publish \\\n")
    assert pack_at < sign_at < upload_at < publish_at
    assert "Skipping Microsoft Store publish" in workflow
    assert "The first Store submission must be created in Partner Center" in workflow
    assert "secrets.MSSTORE_TENANT_ID" in workflow
    assert "secrets.MSSTORE_CLIENT_ID" in workflow
    assert "secrets.MSSTORE_CLIENT_SECRET" in workflow
    assert "secrets.MSSTORE_SELLER_ID" in workflow
    assert "vars.MSSTORE_APP_ID" in workflow
    assert "microsoft/microsoft-store-apppublisher@" in workflow
    assert "version: v0.4.3" in workflow
    assert "--appId" in workflow
    assert "inputs.macos_only != true" in workflow
    assert "exr_converter-windows-x86_64-msix" in workflow
    assert "exr_converter-windows-x86_64-msixupload" in workflow
    assert 'downloaded/exr_converter-windows-x86_64-msix"' in workflow
    assert "downloaded/exr_converter-windows-x86_64-msixupload" in workflow
    assert "release-assets/exr_converter-windows-x86_64.msixupload" in workflow
    assert "release-assets/exr_converter-windows-x86_64.msix" in workflow
    assert "artifacts/*.msix\n" in workflow
    assert "artifacts/*.msixupload" in workflow
    for secret in _SIGNING_ENV:
        assert f"secrets.{secret}" in workflow
    assert "installer.iss" in workflow
    assert "name: exr_converter-windows-x86_64-setup" in workflow
    assert "signtool" not in inspect.getsource(pack_msix).lower()
    assert "signtool" not in inspect.getsource(build_msixupload).lower()
    assert "signtool" in inspect.getsource(signtool_sign_command).lower()
    assert "makeappx" in script.lower()
    assert ".appxsym" in script
    # The setup executable upload is unchanged.
    assert "path: ${{ matrix.artifact }}.${{ matrix.ext }}" in workflow
