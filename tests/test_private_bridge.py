"""Private R3D / BRAW bridge builds never echo their output into public CI logs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "private_bridge.py"


def _load():
    spec = importlib.util.spec_from_file_location("private_bridge", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["private_bridge"] = module
    spec.loader.exec_module(module)
    return module


def _cmd(code: int) -> list[str]:
    return [
        sys.executable,
        "-c",
        "import sys; print('SECRET HEADER LINE tok123'); "
        f"print('warn', file=sys.stderr); sys.exit({code})",
    ]


def test_success_prints_only_a_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.delenv(module.VERBOSE_ENV, raising=False)
    module.run_build("R3D", _cmd(0), cwd=tmp_path, token_env="R3D_SDK_READ_TOKEN")
    out = capsys.readouterr()
    assert "SECRET HEADER LINE" not in out.out + out.err
    assert "R3D bridge built" in out.err


def test_failure_hides_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    module = _load()
    monkeypatch.delenv(module.VERBOSE_ENV, raising=False)
    with pytest.raises(SystemExit) as exc:
        module.run_build("BRAW", _cmd(2), cwd=tmp_path, token_env="BRAW_SDK_READ_TOKEN")
    out = capsys.readouterr()
    assert "BRAW bridge build failed (2)" in str(exc.value)
    assert "SECRET HEADER LINE" not in str(exc.value) + out.out + out.err


def test_verbose_shows_output_with_token_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = _load()
    monkeypatch.setenv(module.VERBOSE_ENV, "1")
    monkeypatch.setenv("R3D_SDK_READ_TOKEN", "tok123")
    module.run_build("R3D", _cmd(0), cwd=tmp_path, token_env="R3D_SDK_READ_TOKEN")
    err = capsys.readouterr().err
    assert "SECRET HEADER LINE ***" in err
    assert "tok123" not in err
