from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from src.gui.drag_mode import Drag
from src.gui.timeline_slider import TimelineSlider


def test_a_second_button_during_a_pan_does_not_start_a_playhead_drag(qapp) -> None:
    slider = TimelineSlider()
    slider.resize(400, slider.height())
    slider.show()
    pos = QPoint(200, 10)
    QTest.mousePress(slider, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, pos)
    assert slider._drag is Drag.PAN
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert slider._drag is Drag.PAN
    assert not slider.is_dragging_playhead
    QTest.mouseRelease(slider, Qt.MouseButton.MiddleButton, Qt.KeyboardModifier.NoModifier, pos)
    assert slider._drag is Drag.NONE


def test_left_press_drags_the_playhead_and_release_ends_it(qapp) -> None:
    slider = TimelineSlider()
    slider.resize(400, slider.height())
    slider.show()
    pos = QPoint(100, 10)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert slider.is_dragging_playhead
    QTest.mouseRelease(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pos)
    assert slider._drag is Drag.NONE and not slider.is_dragging_playhead
