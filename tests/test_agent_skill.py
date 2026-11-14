from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from src.core.agent_skill import SOURCE_COMMAND, Install, detect_install, render_skill

_TEMPLATE = (
    "---\nname: x\n---\n<!-- install:start -->\nold\n<!-- install:end -->\n"
    f"run {SOURCE_COMMAND} proxy -i a.mov\n"
)


def test_install_section_and_commands_are_replaced() -> None:
    install = Install(
        r'"C:\Apps\EXR Converter\exr_converter.exe"', r"C:\Apps\EXR Converter", "packaged"
    )
    text = render_skill(install, _TEMPLATE)
    assert "old" not in text and "install:start" not in text
    assert r"Installed app: `C:\Apps\EXR Converter`" in text
    assert SOURCE_COMMAND not in text
    assert r'run "C:\Apps\EXR Converter\exr_converter.exe" proxy -i a.mov' in text


def test_repo_skills_file_is_a_valid_skill() -> None:
    text = render_skill()
    assert text.startswith("---\nname: exr-converter\n")
    for sub in ("video2exr", "exr2video", "proxy", "transcode"):
        assert f"## {sub}" in text
    assert detect_install().command in text


def test_detect_install_from_source() -> None:
    install = detect_install()
    assert install.kind == "source"
    assert "main.py" in install.command


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_skills_dialog_copies_the_text(app) -> None:
    from src.gui.skills_dialog import SkillsDialog

    dlg = SkillsDialog()
    dlg.copy_to_clipboard()
    assert QApplication.clipboard().text() == dlg.skill_text()
    assert "exr-converter" in dlg.text_view.toPlainText()
