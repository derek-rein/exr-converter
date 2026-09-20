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
