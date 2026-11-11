"""Nuitka prep hides PyAV Cython sources that shadow extension modules."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_nuitka_av.py"


def _load():
    spec = importlib.util.spec_from_file_location("prepare_nuitka_av", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["prepare_nuitka_av"] = module
    spec.loader.exec_module(module)
    return module


def test_hides_only_cython_sources_beside_extensions(tmp_path: Path) -> None:
    module = _load()
    pkg = tmp_path / "av"
    audio = pkg / "audio"
    audio.mkdir(parents=True)
    (pkg / "__init__.py").write_text("from av import logging\n", encoding="utf-8")
    (pkg / "about.py").write_text("version = '1'\n", encoding="utf-8")
    (pkg / "utils.py").write_text(
        "import cython\nfrom cython.cimports import libav as lib\n",
        encoding="utf-8",
    )
    (pkg / "utils.abi3.so").write_bytes(b"ext")
    (audio / "__init__.py").write_text("from . import frame\n", encoding="utf-8")
    (audio / "frame.py").write_text(
        "import cython\nfrom cython.cimports.av.utils import avdict_to_dict\n",
        encoding="utf-8",
    )
    (audio / "frame.abi3.so").write_bytes(b"ext")

    hidden = module.hide_cython_companions(pkg)

    assert (pkg / "utils.abi3.so").is_file()
    assert not (pkg / "utils.py").exists()
    assert (pkg / "utils.py.cython-src").is_file()
    assert (pkg / "__init__.py").is_file()
    assert (pkg / "about.py").is_file()
    assert not (audio / "frame.py").exists()
    assert (audio / "frame.py.cython-src").is_file()
    assert (audio / "__init__.py").is_file()
    assert {path.name for path in hidden} == {
        "utils.py.cython-src",
        "frame.py.cython-src",
    }
