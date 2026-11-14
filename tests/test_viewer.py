from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest

from src.core import handoff  # noqa: F401
from src.core.exr_io import write_exr
from src.gui.viewer import ViewerHost, media_kind_for
from src.launch import UnknownOptions, needs_cli, parse_gui_args
from tests.test_handoff import send_while_pumping

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)


@pytest.fixture
def exr_sequence(tmp_path: Path) -> Path:
    folder = tmp_path / "shot"
    folder.mkdir()
    for frame in range(1001, 1006):
        write_exr(
            str(folder / f"plate.{frame:04d}.exr"),
            np.full((8, 12, 3), 0.18, dtype=np.float32),
            compression="zip",
        )
    return folder


# -- launch routing ---------------------------------------------------------


@pytest.mark.parametrize(
    "argv", [["video2exr", "-i", "a.mov"], ["--help"], ["-V"], ["--workers", "2", "proxy"]]
)
def test_subcommands_and_help_use_the_full_parser(argv: list[str]) -> None:
    assert needs_cli(argv)


@pytest.mark.parametrize(
    "argv", [[], ["--open", "x.mov"], ["--smoke-test"], ["/a/plate.1001.exr"], ["--view", "a.exr"]]
)
def test_gui_launches_skip_the_full_parser(argv: list[str]) -> None:
    assert not needs_cli(argv)


def test_bare_paths_and_view_flags_are_files_to_view() -> None:
    args = parse_gui_args(["--view", "/a/one.exr", "/b/two.dpx"])
    assert args.paths == ["/a/one.exr", "/b/two.dpx"] and args.view
    plain = parse_gui_args(["--open", "x.mov", "--mode", "video2exr"])
    assert plain.paths == [] and not plain.view
    assert (plain.open, plain.mode) == ("x.mov", "video2exr")


def test_unknown_flags_are_left_to_the_full_parser() -> None:
    with pytest.raises(UnknownOptions):
        parse_gui_args(["--no-such-flag"])


def test_media_kinds() -> None:
    assert media_kind_for("/a/plate.1001.exr") == "sequence"
    assert media_kind_for("/a/clip.R3D") == "video"
    assert media_kind_for("/a/clip.braw") == "video"
    assert media_kind_for("/a/notes.txt") == ""


# -- weight -----------------------------------------------------------------


def test_the_viewer_path_does_not_load_the_converter() -> None:
    code = (
        "import sys\n"
        "import src.launch, src.gui.session, src.gui.viewer\n"
        "heavy = ['av', 'src.cli', 'src.gui.window', 'src.gui.convert_tab',\n"
        "         'onnxruntime', 'src.core.trustmark']\n"
        "print(','.join(m for m in heavy if m in sys.modules))\n"
    )
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "", f"viewer path imported: {out.stdout.strip()}"


# -- opening a file ---------------------------------------------------------


def _wait_for(qapp, predicate, ms: int = 4000) -> bool:
    waited = 0
    while waited < ms:
        if predicate():
            return True
        QTest.qWait(50)
        waited += 50
    return predicate()


def test_double_click_opens_on_the_clicked_frame(qapp, settings, exr_sequence: Path) -> None:
    host = ViewerHost(settings)
    windows = host.open_paths([str(exr_sequence / "plate.1003.exr")])
    assert len(windows) == 1
    window = windows[0]
    try:
        assert _wait_for(qapp, lambda: window.player().frame() == 1003)
        assert window.player().shot_frames() == [1001, 1002, 1003, 1004, 1005]
    finally:
        window.close()


def test_a_second_file_reuses_the_window(qapp, settings, exr_sequence: Path) -> None:
    host = ViewerHost(settings)
    first = host.open_paths([str(exr_sequence / "plate.1001.exr")])[0]
    try:
        again = host.open_paths([str(exr_sequence / "plate.1004.exr")])[0]
        assert again is first
        assert _wait_for(qapp, lambda: first.player().frame() == 1004)
    finally:
        first.close()


def test_files_that_are_not_media_are_skipped(qapp, settings, tmp_path: Path) -> None:
    note = tmp_path / "notes.txt"
    note.write_text("x")
    assert ViewerHost(settings).open_paths([str(note)]) == []


def test_step_up_reports_the_path(qapp, settings, exr_sequence: Path) -> None:
    seen: list[str] = []
    host = ViewerHost(settings, step_up=seen.append)
    window = host.open_paths([str(exr_sequence / "plate.1002.exr")])[0]
    try:
        window.open_in_app.emit(window.path)
        assert seen == [str(exr_sequence / "plate.1002.exr")]
    finally:
        window.close()


def test_a_second_launch_opens_a_viewer_in_the_running_app(
    qapp, settings, exr_sequence: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXR_CONVERTER_HANDOFF", f"exr-test-{uuid.uuid4().hex[:10]}")
    from src.gui.app import ExrApplication
    from src.gui.session import AppSession

    app = qapp
    if not isinstance(app, ExrApplication):
        pytest.skip("the shared test QApplication is a plain QApplication")
    session = AppSession(app, settings)
    assert session.start_server()
    try:
        assert send_while_pumping([str(exr_sequence / "plate.1005.exr")])
        assert _wait_for(qapp, lambda: bool(session.viewer_windows()))
    finally:
        for window in session.viewer_windows():
            window.close()
