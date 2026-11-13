"""Transport strip: first / step-back / play-pause / step-forward / last."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..icons import set_button_icon
from ..style import set_style_property
from ..timeline_slider import TimelineSlider

BUTTON_SIZE = (26, 22)
PLAY_BUTTON_SIZE = (34, 26)


class ShuttleBar(QWidget):
    """Tiny transport strip that drives a :class:`TimelineSlider`.

    Optional :meth:`set_advance_callback` replaces the default per-tick step
    (used for cache-first playback that stalls until the next frame is in RAM).
    """

    playing_changed = Signal(bool)

    def __init__(
        self,
        timeline: TimelineSlider,
        fps: float = 24.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._timeline = timeline
        self._fps = max(1.0, fps)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(4)

        self._btn_first = self._make_btn("skip-back", "Go to first frame")
        self._btn_back = self._make_btn("step-back", "Step back one frame")
        self._btn_play = self._make_btn("play", "Play / pause")
        self._btn_play.setCheckable(True)
        # The main control reads first: a step larger than its neighbours.
        self._btn_play.setFixedSize(PLAY_BUTTON_SIZE[0], PLAY_BUTTON_SIZE[1])
        set_button_icon(self._btn_play, "play", size=16)
        self._btn_fwd = self._make_btn("step-forward", "Step forward one frame")
        self._btn_last = self._make_btn("skip-forward", "Go to last frame")

        # Focus stays off the buttons: Space / arrows belong to the player.
        for btn in (
            self._btn_first,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_last,
        ):
            layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter)

        self._btn_first.clicked.connect(self._on_first)
        self._btn_back.clicked.connect(self._on_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_fwd)
        self._btn_last.clicked.connect(self._on_last)

        self._timer = QTimer(self)
        self._advance_cb: Callable[[], None] | None = None
        self._timer.timeout.connect(self._on_timer_tick)
        self._refresh_timer_interval()

        # Stop playback when the user grabs the playhead.
        self._timeline.value_changed.connect(self._on_user_scrubbed)

    def set_advance_callback(self, callback: Callable[[], None] | None) -> None:
        """Replace the default per-tick advance with a custom callback."""
        self._advance_cb = callback

    def is_playing(self) -> bool:
        return self._btn_play.isChecked()

    def set_playing(self, playing: bool) -> None:
        self._btn_play.setChecked(bool(playing))

    def is_timer_active(self) -> bool:
        return self._timer.isActive()

    def stop_timer(self) -> None:
        self._timer.stop()

    def start_timer(self) -> None:
        self._timer.start()

    def set_fps(self, fps: float) -> None:
        self._fps = max(1.0, fps)
        self._refresh_timer_interval()

    @staticmethod
    def _make_btn(icon_name: str, tooltip: str) -> QPushButton:
        b = QPushButton()
        set_button_icon(b, icon_name, size=14)
        set_style_property(b, "variant", "tool")
        b.setFixedSize(BUTTON_SIZE[0], BUTTON_SIZE[1])
        b.setToolTip(tooltip)
        b.setAccessibleName(tooltip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def _refresh_timer_interval(self) -> None:
        self._timer.setInterval(max(10, int(round(1000.0 / self._fps))))

    def _on_first(self) -> None:
        self._timeline.set_value(self._timeline.first_frame)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_last(self) -> None:
        self._timeline.set_value(self._timeline.last_frame)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_back(self) -> None:
        self._timeline.set_value(self._timeline.value() - 1)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_fwd(self) -> None:
        self._timeline.set_value(self._timeline.value() + 1)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_play_toggled(self, checked: bool) -> None:
        set_button_icon(self._btn_play, "pause" if checked else "play", size=16)
        if checked:
            self._timer.start()
        else:
            self._timer.stop()
        self.playing_changed.emit(checked)

    def _on_user_scrubbed(self, _frame: int) -> None:
        if self._btn_play.isChecked() and self._timeline.is_dragging_playhead:
            self._btn_play.setChecked(False)

    def _on_timer_tick(self) -> None:
        if self._advance_cb is not None:
            self._advance_cb()
        else:
            self._advance()

    def _advance(self) -> None:
        cur = self._timeline.value()
        nxt = cur + 1
        if nxt > self._timeline.last_frame:
            nxt = self._timeline.first_frame
        self._timeline.set_value(nxt)
        self._timeline.value_changed.emit(nxt)


__all__ = ["ShuttleBar"]
