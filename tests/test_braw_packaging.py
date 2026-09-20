"""Unit tests for BRAW fetch/install helpers (no proprietary SDK required)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_braw_bridge import _header_ok  # noqa: E402
from fetch_braw_sdk import _existing_sdk, _find_sdk_root, _is_sdk_root  # noqa: E402
from install_braw_into_bundle import (  # noqa: E402
    _assert_runtime_only,
    _is_forbidden_file,
    install,
)


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


def test_header_ok_accepts_windows_idl(tmp_path: Path) -> None:
    inc = tmp_path / "Include"
    inc.mkdir()
    assert not _header_ok(inc)
    (inc / "BlackmagicRawAPI.idl").write_text("import \"unknwn.idl\";\n")
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


def test_install_copies_runtime_and_refuses_headers(tmp_path: Path) -> None:
    build = tmp_path / "build"
    redist = build / "redistributable"
    redist.mkdir(parents=True)
    from install_braw_into_bundle import _bridge_name

    (build / _bridge_name()).write_bytes(b"bridge")
    (redist / "libBlackmagicRawAPI.so").write_bytes(b"api")
    (redist / "BlackmagicRawAPI.h").write_text("// must not ship\n")
    (redist / "Include").mkdir()
    (redist / "Include" / "BlackmagicRawAPI.h").write_text("// no\n")

    bundle = tmp_path / "main.dist"
    bundle.mkdir()
    dest = install(bundle, build_dir=build)
    assert dest is not None
    assert (dest / _bridge_name()).is_file()
    assert (dest / "libBlackmagicRawAPI.so").is_file()
    assert not (dest / "BlackmagicRawAPI.h").exists()
    assert not (dest / "Include").exists()
