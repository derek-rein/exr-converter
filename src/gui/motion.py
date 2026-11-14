"""Motion tokens and small animation helpers.

Rules the helpers encode, so call sites do not have to remember them:

* Short. Nothing over ~300 ms; most things are 120-200 ms.
* Ease out. Things arrive fast and settle; nothing eases in from rest.
* Interruptible. Starting an animation replaces the running one from the
  value on screen, so rapid input never queues motion.
* Optional. ``reduced()`` is true when the user turned motion off, when the
  environment asks for it, or when there is no display (tests); every helper
  then jumps to the end state.

The first block is pure (no Qt) and unit-tested; the rest drives Qt.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

# -- Tokens (milliseconds) ----------------------------------------------------
INSTANT = 90  # press feedback
FAST = 140  # hover, small fades, page cross-fade
BASE = 200  # indicators that travel, progress catching up
SLOW = 280  # panels and drawers that change size

ENV_VAR = "EXR_CONVERTER_REDUCE_MOTION"

_TRUTHY = {"1", "true", "yes", "on"}


# -- Pure helpers ---------------------------------------------------------------


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def mix_hex(a: str, b: str, t: float) -> str:
    """Blend two ``#rrggbb`` colours; *t* is clamped to 0..1."""
    t = min(1.0, max(0.0, t))
    ca = [int(a[i : i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(lerp(x, y, t)):02x}" for x, y in zip(ca, cb, strict=True))


def reduced_from_env(env: Mapping[str, str]) -> bool:
    """True when the environment says not to animate."""
    if env.get(ENV_VAR, "").strip().lower() in _TRUTHY:
        return True
    return env.get("QT_QPA_PLATFORM", "") == "offscreen"


def duration(ms: int, *, reduced: bool) -> int:
    return 0 if reduced else ms


def indeterminate_segment(phase: float, length: float = 0.3) -> tuple[float, float]:
    """Start and end (0..1 of the track) of the travelling busy segment.

    The segment enters from the left edge, crosses, and leaves at the right,
    so it is clipped to the track at both ends of the cycle.
    """
    head = phase * (1.0 + length)
    return max(0.0, head - length), min(1.0, head)


# -- Qt side --------------------------------------------------------------------

_reduced_override: bool | None = None


def set_reduced(value: bool | None) -> None:
    """Force motion off (``True``) or on (``False``); ``None`` follows the environment."""
    global _reduced_override  # noqa: PLW0603
    _reduced_override = value


def reduced() -> bool:
    if _reduced_override is not None:
        return _reduced_override
    return reduced_from_env(os.environ)


def ms(value: int) -> int:
    """Duration token adjusted for the reduced-motion setting."""
    return duration(value, reduced=reduced())


def ease_out():
    """Strong decelerating curve, cubic-bezier(0.23, 1, 0.32, 1).

    Qt's stock OutCubic is too gentle to read as intentional at these
    durations; this one moves most of the distance in the first third.
    """
    from PySide6.QtCore import QEasingCurve, QPointF

    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(QPointF(0.23, 1.0), QPointF(0.32, 1.0), QPointF(1.0, 1.0))
    return curve


class Animator:
    """One interruptible float animation owned by a widget.

    ``to(target)`` animates from the value currently shown, so reversing
    mid-flight (hover in, hover out) never jumps.

    It runs on its own ``QTimer`` and ``QElapsedTimer``: one clock per
    animator, the easing applied by hand, and a last tick that always lands
    exactly on the target. Like any timer it must be started from the GUI
    thread; Qt prints "Timers cannot be started from another thread" if not.
    """

    _TICK_MS = 16

    def __init__(
        self,
        owner,
        on_change: Callable[[float], None],
        *,
        value: float = 0.0,
        duration_ms: int = FAST,
    ) -> None:
        from PySide6.QtCore import QElapsedTimer, Qt, QTimer

        self._value = float(value)
        self._start = self._value
        self._target = self._value
        self._length = 0
        self._on_change = on_change
        self._duration = duration_ms
        self._curve = ease_out()
        self._clock = QElapsedTimer()
        self._timer = QTimer(owner)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def value(self) -> float:
        return self._value

    @property
    def target(self) -> float:
        return self._target

    def is_running(self) -> bool:
        return self._timer.isActive()

    def _emit(self, value: float) -> None:
        self._value = float(value)
        self._on_change(self._value)

    def _tick(self) -> None:
        progress = self._clock.elapsed() / self._length if self._length > 0 else 1.0
        if progress >= 1.0:
            self._timer.stop()
            self._emit(self._target)
            return
        eased = self._curve.valueForProgress(progress)
        self._emit(lerp(self._start, self._target, eased))

    def set(self, value: float) -> None:
        """Jump without animating."""
        self._timer.stop()
        self._target = float(value)
        self._emit(value)

    def to(self, target: float, duration_ms: int | None = None) -> None:
        self._timer.stop()
        self._target = float(target)
        length = ms(self._duration if duration_ms is None else duration_ms)
        if length <= 0 or abs(target - self._value) < 1e-6:
            self._emit(target)
            return
        self._start = self._value
        self._length = length
        self._clock.start()
        self._timer.start()
