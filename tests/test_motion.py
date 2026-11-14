"""Motion tokens and the pure helpers behind the animated widgets."""

from __future__ import annotations

import pytest

from src.gui import motion


def test_mix_hex_endpoints_and_midpoint() -> None:
    assert motion.mix_hex("#000000", "#ffffff", 0.0) == "#000000"
    assert motion.mix_hex("#000000", "#ffffff", 1.0) == "#ffffff"
    assert motion.mix_hex("#000000", "#ffffff", 0.5) == "#808080"


def test_mix_hex_clamps_out_of_range_progress() -> None:
    assert motion.mix_hex("#102030", "#405060", -1.0) == "#102030"
    assert motion.mix_hex("#102030", "#405060", 2.0) == "#405060"


def test_lerp() -> None:
    assert motion.lerp(10.0, 20.0, 0.25) == 12.5


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_turns_motion_off(value: str) -> None:
    assert motion.reduced_from_env({"EXR_CONVERTER_REDUCE_MOTION": value}) is True


@pytest.mark.parametrize("env", [{}, {"EXR_CONVERTER_REDUCE_MOTION": "0"}])
def test_env_leaves_motion_on(env: dict[str, str]) -> None:
    assert motion.reduced_from_env(env) is False


def test_no_display_means_no_motion() -> None:
    assert motion.reduced_from_env({"QT_QPA_PLATFORM": "offscreen"}) is True


def test_duration_is_zero_when_reduced() -> None:
    assert motion.duration(motion.FAST, reduced=True) == 0
    assert motion.duration(motion.FAST, reduced=False) == motion.FAST


def test_durations_stay_short() -> None:
    # Interface motion over ~300 ms reads as lag.
    assert 0 < motion.INSTANT < motion.FAST < motion.BASE < motion.SLOW <= 320


def test_indeterminate_segment_stays_inside_the_track() -> None:
    for step in range(0, 101):
        start, end = motion.indeterminate_segment(step / 100)
        assert 0.0 <= start <= end <= 1.0
    assert motion.indeterminate_segment(0.0)[1] == pytest.approx(0.0)
    assert motion.indeterminate_segment(1.0)[0] == pytest.approx(1.0)


def test_selecting_a_tab_does_not_move_the_others() -> None:
    """The selected label is bold; every tab reserves that width."""
    from PySide6.QtWidgets import QApplication

    from src.gui.animated_tabs import SlidingTabBar

    QApplication.instance() or QApplication([])
    import src.rc_resources  # noqa: F401
    from src.gui.style import load_stylesheet

    bar = SlidingTabBar()
    bar.setStyleSheet(load_stylesheet())  # the stylesheet is what makes the selected tab bold
    for name in ("Video → EXR", "EXR → Video", "Video → Proxy", "Transcode"):
        bar.addTab(name)
    bar.show()
    layouts = []
    for i in range(bar.count()):
        bar.setCurrentIndex(i)
        # Widths: a narrow test bar scrolls, which moves every left edge alike.
        layouts.append([bar.tabRect(j).width() for j in range(bar.count())])
    assert all(layout == layouts[0] for layout in layouts)
