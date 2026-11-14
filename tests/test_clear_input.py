"""Right-click Clear input on the Video → EXR / EXR → Video tabs."""

from __future__ import annotations

import pytest

import src.rc_resources  # noqa: F401
from src.gui.convert_tab import ConvertTab
from tests.support.integration import write_synthetic_video


@pytest.fixture
def tab(qapp, settings):
    t = ConvertTab("video2exr", settings)
    yield t
    t.deleteLater()


@pytest.mark.integration
def test_clear_input_resets_source_and_card(tab, tmp_path) -> None:
    clip = tmp_path / "a.mov"
    write_synthetic_video(clip)
    assert tab.set_input(str(clip))
    assert tab._source is not None
    assert not tab.source_card.is_empty()

    tab.clear_input()

    assert tab.input_path.text() == ""
    assert tab._source is None
    assert tab.source_card.is_empty()


def test_clear_input_when_already_empty_is_harmless(tab) -> None:
    tab.clear_input()
    assert not tab.is_ready()
    assert tab.source_card.is_empty()
