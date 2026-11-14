"""Characters that can't be in a file name never reach one."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QLineEdit

import src.rc_resources  # noqa: F401
from src.core.path_tokens import clean_filename_part
from src.core.proxy import default_proxy_output
from src.gui.filename_filter import BAD_PATTERN_CHARS, install_filename_filter
from src.gui.proxy_tab import ProxyTab
from src.gui.transcode_tab import TranscodeTab


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("_proxy", "_proxy"),
        ("a/b", "ab"),
        ("..\\..\\x", "....x"),
        ('a:b*c?d"e<f>g|h', "abcdefgh"),
        ("tail. ", "tail"),
        ("tab\there", "tabhere"),
        ("", ""),
    ],
)
def test_clean_filename_part(raw: str, clean: str) -> None:
    assert clean_filename_part(raw) == clean


def test_proxy_output_ignores_illegal_suffix_characters(tmp_path) -> None:
    out = default_proxy_output(tmp_path / "clip.mov", suffix="../evil:|x")
    assert out.parent == tmp_path / "Proxy"
    assert out.name.startswith("clip..evilx.")


def test_filter_strips_typed_pasted_and_set_text(qapp) -> None:
    edit = QLineEdit()
    install_filename_filter(edit)
    edit.setText("o/k:")  # typed, pasted or set: the signal is the same
    assert edit.text() == "ok"
    edit.setText("a|b")
    assert edit.text() == "ab"


def test_filter_cleans_a_value_already_in_the_field(qapp) -> None:
    edit = QLineEdit("old*value")
    install_filename_filter(edit, BAD_PATTERN_CHARS)
    assert edit.text() == "oldvalue"


def test_path_filter_leaves_posix_legal_paths(qapp) -> None:
    import sys

    from src.gui.filename_filter import BAD_PATH_CHARS

    edit = QLineEdit()
    install_filename_filter(edit, BAD_PATH_CHARS)
    edit.setText("/Volumes/Show/A|B")
    if sys.platform != "win32":
        assert edit.text() == "/Volumes/Show/A|B"
    edit.setText("/x\x01y")
    assert edit.text() == "/xy"


def test_proxy_suffix_field_rejects_separators(qapp, settings) -> None:
    tab = ProxyTab(settings)
    try:
        tab.suffix_edit.setText("_p/x")
        assert tab.suffix_edit.text() == "_px"
    finally:
        tab.shutdown()
        tab.deleteLater()


def test_transcode_name_keeps_folders_and_tokens_but_drops_wildcards(qapp, settings) -> None:
    tab = TranscodeTab(settings)
    try:
        tab.name_edit.setText("<reel>/<name>_x*?")
        assert tab.name_edit.text() == "<reel>/<name>_x"
    finally:
        tab.shutdown()
        tab.deleteLater()
