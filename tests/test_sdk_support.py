"""Optional decoder SDKs: missing is expected from source, and messages stay short."""

from __future__ import annotations

import logging
from pathlib import Path

from src.core.braw import BRAWUnavailableError
from src.core.r3d import R3DUnavailableError
from src.core.sdk_support import (
    OptionalSdkError,
    log_missing_bridge,
    missing_bridge_message,
)


def test_unavailable_errors_are_optional_sdk_errors() -> None:
    assert issubclass(R3DUnavailableError, OptionalSdkError)
    assert issubclass(BRAWUnavailableError, OptionalSdkError)


def test_missing_bridge_message_is_one_line() -> None:
    msg = missing_bridge_message("R3D", "docs/r3d.md")
    assert "Tried:" not in msg
    assert "\n" not in msg
    assert "docs/r3d.md" in msg


def test_log_missing_bridge_keeps_paths_on_debug(caplog) -> None:
    log = logging.getLogger("test.sdk_support")
    with caplog.at_level(logging.DEBUG, logger="test.sdk_support"):
        msg = log_missing_bridge(
            log,
            kind="R3D",
            docs="docs/r3d.md",
            candidates=[Path("/nope/r3d_bridge.dll")],
            frozen=False,
        )
    assert msg == missing_bridge_message("R3D", "docs/r3d.md")
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any("candidates=" in r.getMessage() for r in caplog.records)
