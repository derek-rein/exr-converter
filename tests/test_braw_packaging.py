"""Unit tests for BRAW fetch/install helpers (no proprietary SDK required)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_braw_bridge import (  # noqa: E402
    _find_iid_c,
    _header_ok,
    _prepare_windows_include,
)
from fetch_braw_sdk import (  # noqa: E402
    _existing_sdk,
    _find_sdk_root,
    _is_sdk_root,
    _token,
)
from install_braw_into_bundle import (  # noqa: E402
    _assert_no_framework_under_macos,
    _assert_runtime_only,
    _bridge_name,
    _is_forbidden_file,
    install,
    resolve_install_root,
)
from macos_codesign import (  # noqa: E402
    framework_ambiguity_reasons,
    normalize_versioned_framework,
    plan_adhoc_resign,
    plan_adhoc_verify,
)
from packaging_util import copytree_preserve_symlinks  # noqa: E402

_SYMLINK_OK = sys.platform != "win32"


def test_token_uses_braw_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("BRAW_SDK_READ_TOKEN", "braw-pat")
    assert _token() == "braw-pat"


def test_token_does_not_use_r3d_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BRAW_SDK_READ_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("R3D_SDK_READ_TOKEN", "r3d-pat")

    def _no_gh(*_a: object, **_k: object) -> str:
        raise OSError("no gh")

    monkeypatch.setattr("fetch_braw_sdk.subprocess.check_output", _no_gh)
    assert _token() == ""


def test_is_sdk_root_linux_layout(tmp_path: Path) -> None:
    root = tmp_path / "BlackmagicRAWSDK-6.0"
    header = root / "Linux" / "Include" / "BlackmagicRawAPI.h"
    header.parent.mkdir(parents=True)
    header.write_text("// stub\n")
    assert _is_sdk_root(root)
    assert _find_sdk_root(tmp_path) == root
    assert _existing_sdk(tmp_path) == root


def test_is_sdk_root_windows_idl_layout(tmp_path: Path) -> None:
    root = tmp_path / "BlackmagicRAWSDK-6.0"
    win = root / "Win" / "Include"
    win.mkdir(parents=True)
    (win / "BlackmagicRawAPI.idl").write_text("// idl stub\n")
    (win / "BlackmagicRawAPIDispatch.h").write_text("// dispatch\n")
    assert _is_sdk_root(root)
    assert _find_sdk_root(tmp_path) == root


def test_is_sdk_root_include_at_top(tmp_path: Path) -> None:
    header = tmp_path / "Include" / "BlackmagicRawAPI.h"
    header.parent.mkdir()
    header.write_text("// stub\n")
    assert _is_sdk_root(tmp_path)
    assert _find_sdk_root(tmp_path) == tmp_path


def test_find_iid_c_prefers_official_midl_name(tmp_path: Path) -> None:
    assert _find_iid_c(tmp_path) is None
    other = tmp_path / "extra_i.c"
    other.write_text("/* other */\n")
    assert _find_iid_c(tmp_path) == other
    official = tmp_path / "BlackmagicRawAPI_i.c"
    official.write_text("/* iids */\n")
    assert _find_iid_c(tmp_path) == official


def test_prepare_windows_include_reuses_existing_iid(tmp_path: Path) -> None:
    include = tmp_path / "inc"
    include.mkdir()
    (include / "BlackmagicRawAPI.h").write_text("// header\n")
    (include / "BlackmagicRawAPI.idl").write_text("// idl\n")
    (include / "BlackmagicRawAPIDispatch.h").write_text("// dispatch\n")
    (include / "BlackmagicRawAPIDispatch.cpp").write_text("// dispatch cpp\n")
    (include / "BlackmagicRawAPI_i.c").write_text("/* iids */\n")
    gen = _prepare_windows_include(include, tmp_path / "out")
    assert (gen / "BlackmagicRawAPI.h").is_file()
    assert (gen / "BlackmagicRawAPI_i.c").read_text() == "/* iids */\n"
    assert _find_iid_c(gen) == gen / "BlackmagicRawAPI_i.c"


def test_windows_build_script_compiles_midl_iid_source() -> None:
    """Win link needs BlackmagicRawAPI_i.c — MIDL header only declares the IIDs."""
    src = (ROOT / "scripts" / "build_braw_bridge.py").read_text()
    assert '"/iid"' in src
    assert "BlackmagicRawAPI_i.c" in src
    assert "Missing BlackmagicRawAPI_i.c" in src


def test_bridge_source_has_macos_windows_sdk_shims() -> None:
    """Guard against Linux-only COM assumptions that break Mac/Win Release."""
    src = (ROOT / "native" / "braw" / "braw_bridge.cpp").read_text()
    assert "BlackmagicRawAPIDispatch.h" in src
    assert "using Variant = VARIANT" in src
    assert "iid_equals" in src
    assert "IUnknownUUID" in src
    assert "CFStringRef ver" in src
    assert "BSTR ver" in src
    assert "BOOL cpu_ok" in src
    assert "CreateBlackmagicRawFactoryInstanceFromPath(path.get())" in src


def test_header_ok_accepts_windows_idl(tmp_path: Path) -> None:
    inc = tmp_path / "Include"
    inc.mkdir()
    assert not _header_ok(inc)
    (inc / "BlackmagicRawAPI.idl").write_text('import "unknwn.idl";\n')
    (inc / "BlackmagicRawAPIDispatch.h").write_text("#pragma once\n")
    assert _header_ok(inc)
    (inc / "BlackmagicRawAPI.h").write_text("// generated\n")
    assert _header_ok(inc)


def test_find_sdk_root_missing(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="BlackmagicRawAPI.h"):
        _find_sdk_root(tmp_path)


def test_forbidden_file_helpers(tmp_path: Path) -> None:
    assert _is_forbidden_file(tmp_path / "BlackmagicRawAPI.h")
    assert _is_forbidden_file(tmp_path / "foo.lib")
    assert not _is_forbidden_file(tmp_path / "libBlackmagicRawAPI.so")
    assert not _is_forbidden_file(tmp_path / "BlackmagicRawAPI.dll")


def test_assert_runtime_only_refuses_headers(tmp_path: Path) -> None:
    dest = tmp_path / "braw"
    dest.mkdir()
    (dest / "libbraw_bridge.so").write_bytes(b"x")
    (dest / "libBlackmagicRawAPI.so").write_bytes(b"y")
    _assert_runtime_only(dest)

    (dest / "BlackmagicRawAPI.h").write_text("// leaked\n")
    with pytest.raises(SystemExit, match="forbidden SDK artifacts"):
        _assert_runtime_only(dest)


def test_install_skips_without_bridge(tmp_path: Path) -> None:
    bundle = tmp_path / "dist"
    bundle.mkdir()
    assert install(bundle, build_dir=tmp_path / "missing-build") is None


def test_resolve_install_root_macos_app_uses_frameworks(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    assert resolve_install_root(app) == (app / "Contents" / "Frameworks").resolve()


def test_resolve_install_root_plain_dist(tmp_path: Path) -> None:
    dist = tmp_path / "main.dist"
    dist.mkdir()
    assert resolve_install_root(dist) == dist.resolve()


def _stage_runtime_build(tmp_path: Path, *, with_framework: bool = False) -> Path:
    build = tmp_path / "build"
    redist = build / "redistributable"
    redist.mkdir(parents=True)
    (build / _bridge_name()).write_bytes(b"bridge")
    (redist / "libBlackmagicRawAPI.so").write_bytes(b"api")
    (redist / "BlackmagicRawAPI.h").write_text("// must not ship\n")
    (redist / "Include").mkdir()
    (redist / "Include" / "BlackmagicRawAPI.h").write_text("// no\n")
    if with_framework:
        fw = redist / "BlackmagicRawAPI.framework" / "Versions" / "A"
        fw.mkdir(parents=True)
        (fw / "BlackmagicRawAPI").write_bytes(b"macho")
        (fw / "Headers").mkdir()
        (fw / "Headers" / "BlackmagicRawAPI.h").write_text("// stripped\n")
    return build


def test_install_copies_runtime_and_refuses_headers(tmp_path: Path) -> None:
    build = _stage_runtime_build(tmp_path)
    bundle = tmp_path / "main.dist"
    bundle.mkdir()
    dest = install(bundle, build_dir=build)
    assert dest is not None
    assert dest == (bundle / "braw").resolve()
    assert (dest / _bridge_name()).is_file()
    assert (dest / "libBlackmagicRawAPI.so").is_file()
    assert not (dest / "BlackmagicRawAPI.h").exists()
    assert not (dest / "Include").exists()


def test_install_macos_app_puts_framework_under_contents_frameworks(tmp_path: Path) -> None:
    """A .framework under Contents/MacOS makes codesign --deep ambiguous."""
    build = _stage_runtime_build(tmp_path, with_framework=True)
    app = tmp_path / "EXR Converter.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    dest = install(app, build_dir=build)
    assert dest is not None
    assert dest == (app / "Contents" / "Frameworks" / "braw").resolve()
    assert (dest / _bridge_name()).is_file()
    assert (dest / "BlackmagicRawAPI.framework" / "Versions" / "A" / "BlackmagicRawAPI").is_file()
    assert not (dest / "BlackmagicRawAPI.framework" / "Versions" / "A" / "Headers").exists()
    assert not list((app / "Contents" / "MacOS").rglob("*.framework"))
    _assert_no_framework_under_macos(app)


def test_assert_no_framework_under_macos_refuses_nested_framework(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    fw = app / "Contents" / "MacOS" / "braw" / "BlackmagicRawAPI.framework"
    fw.mkdir(parents=True)
    with pytest.raises(SystemExit, match="Contents/MacOS"):
        _assert_no_framework_under_macos(app)


def _ambiguous_braw_framework(root: Path) -> Path:
    """SDK 6.0-like tree after shutil.copytree flattened the symlinks.

    codesign --deep reports *bundle format is ambiguous* on this layout:
    real Versions/Current, real top-level Resources/, and Contents/.
    GPU decoder bits live in Libraries/ — normalize must keep them.
    """
    fw = root / "BlackmagicRawAPI.framework"
    ver_a = fw / "Versions" / "A"
    current = fw / "Versions" / "Current"
    for base in (ver_a, current):
        (base / "Resources").mkdir(parents=True)
        (base / "BlackmagicRawAPI").write_bytes(b"macho-a")
        (base / "Resources" / "Info.plist").write_text("<plist/>\n")
        (base / "Libraries").mkdir()
        (base / "Libraries" / "DecoderMetal").write_bytes(b"metal")
        (base / "Libraries" / "libInstructionSetServicesAVX2.dylib").write_bytes(b"avx2")
    (fw / "Resources").mkdir()
    (fw / "Resources" / "Info.plist").write_text("<plist/>\n")
    (fw / "Contents" / "MacOS").mkdir(parents=True)
    (fw / "Contents" / "Info.plist").write_text("<plist/>\n")
    (fw / "Contents" / "MacOS" / "BlackmagicRawAPI").write_bytes(b"macho-contents")
    (fw / "BlackmagicRawAPI").write_bytes(b"macho-top")
    return fw


@pytest.mark.skipif(not _SYMLINK_OK, reason="framework symlink copy is a macOS packaging concern")
def test_copytree_preserve_symlinks(tmp_path: Path) -> None:
    src = tmp_path / "src" / "BlackmagicRawAPI.framework" / "Versions"
    src.mkdir(parents=True)
    (src / "A").mkdir()
    (src / "A" / "BlackmagicRawAPI").write_bytes(b"macho")
    (src / "Current").symlink_to("A")
    dest = tmp_path / "dest" / "BlackmagicRawAPI.framework" / "Versions"
    copytree_preserve_symlinks(src, dest)
    assert (dest / "Current").is_symlink()
    assert (dest / "Current").readlink() == Path("A")
    assert (dest / "A" / "BlackmagicRawAPI").read_bytes() == b"macho"


def test_framework_ambiguity_reasons_sdk6_flattened(tmp_path: Path) -> None:
    fw = _ambiguous_braw_framework(tmp_path)
    reasons = " ".join(framework_ambiguity_reasons(fw))
    assert "Contents/" in reasons
    assert "Versions/" in reasons
    assert "Current" in reasons
    assert "Resources/" in reasons


@pytest.mark.skipif(not _SYMLINK_OK, reason="framework symlink restore needs POSIX")
def test_normalize_versioned_framework_keeps_gpu_libs(tmp_path: Path) -> None:
    fw = _ambiguous_braw_framework(tmp_path)
    actions = normalize_versioned_framework(fw)
    assert actions
    current = fw / "Versions" / "Current"
    assert current.is_symlink()
    assert current.readlink() == Path("A")
    assert (fw / "Resources").is_symlink()
    assert (fw / "BlackmagicRawAPI").is_symlink()
    assert not (fw / "Contents").exists()
    metal = fw / "Versions" / "A" / "Libraries" / "DecoderMetal"
    avx = fw / "Versions" / "A" / "Libraries" / "libInstructionSetServicesAVX2.dylib"
    assert metal.is_file()
    assert avx.is_file()
    assert not framework_ambiguity_reasons(fw)


@pytest.mark.skipif(not _SYMLINK_OK, reason="resign plan is exercised on a POSIX framework tree")
def test_plan_adhoc_resign_never_uses_deep(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "exr_converter").write_bytes(b"exe")
    (macos / "exr_converter").chmod(0o755)
    fw = _ambiguous_braw_framework(app / "Contents" / "Frameworks" / "braw")
    normalize_versioned_framework(fw)
    cmds = plan_adhoc_resign(app)
    flat = [" ".join(c) for c in cmds]
    assert all("--deep" not in c for c in flat)
    assert any(c[-1].endswith("BlackmagicRawAPI.framework") for c in cmds)
    assert any(c[-1].endswith("exr_converter") for c in cmds)
    assert cmds[-1][-1] == str(app)
    verify = plan_adhoc_verify(app)
    assert all("--deep" not in " ".join(c) for c in verify)


def test_plan_adhoc_resign_refuses_framework_under_macos(tmp_path: Path) -> None:
    app = tmp_path / "EXR Converter.app"
    fw = app / "Contents" / "MacOS" / "braw" / "BlackmagicRawAPI.framework"
    fw.mkdir(parents=True)
    with pytest.raises(SystemExit, match="Contents/MacOS"):
        plan_adhoc_resign(app)


@pytest.mark.skipif(not _SYMLINK_OK, reason="macOS .app framework install uses POSIX symlinks")
def test_install_normalizes_flattened_macos_framework(tmp_path: Path) -> None:
    build = tmp_path / "build"
    redist = build / "redistributable"
    redist.mkdir(parents=True)
    (build / _bridge_name()).write_bytes(b"bridge")
    _ambiguous_braw_framework(redist)
    app = tmp_path / "EXR Converter.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    dest = install(app, build_dir=build)
    assert dest is not None
    fw = dest / "BlackmagicRawAPI.framework"
    assert fw.is_dir()
    assert (fw / "Versions" / "Current").is_symlink()
    assert (fw / "Versions" / "A" / "Libraries" / "DecoderMetal").is_file()
    assert not (fw / "Contents").exists()
    assert not list((app / "Contents" / "MacOS").rglob("*.framework"))


def test_release_workflow_does_not_codesign_deep() -> None:
    text = (ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "scripts/macos_codesign.py" in text
    assert "codesign --force --deep" not in text
    assert "codesign --verify --deep" not in text
