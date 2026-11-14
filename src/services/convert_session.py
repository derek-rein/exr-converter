"""The lifecycle of one single-file convert, as an explicit state machine.

The main window used to infer this from three loose fields (``_busy``, ``_thread``,
``_worker``), so "is a convert running?" had three different answers depending on
which line asked: the flag was cleared when the worker reported, while the thread
was still winding down. One phase now answers all of them.

    IDLE --start()--> RUNNING --request_cancel()--> CANCELLING
                          |                              |
                          +-------- finish() ------------+
                                        |
                                        v
                          FINISHING --thread_closed()--> IDLE

``finish()`` is the worker's terminal report (done, failed or cancelled);
``thread_closed()`` is the thread's ``finished`` signal. Convert may start again
only from IDLE, so a new run cannot begin while the old thread is still closing.
"""

from __future__ import annotations

from enum import Enum


class ConvertPhase(Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"  # cancel asked for, the worker has not reported yet
    FINISHING = "finishing"  # the worker reported; its thread is closing


class ConvertSession:
    """Holds the phase and refuses transitions that make no sense."""

    def __init__(self) -> None:
        self.phase = ConvertPhase.IDLE

    # -- questions the UI asks -------------------------------------------------

    @property
    def is_idle(self) -> bool:
        return self.phase is ConvertPhase.IDLE

    @property
    def is_working(self) -> bool:
        """A convert is producing output: progress is shown and results still come."""
        return self.phase in (ConvertPhase.RUNNING, ConvertPhase.CANCELLING)

    @property
    def can_start(self) -> bool:
        return self.phase is ConvertPhase.IDLE

    @property
    def can_cancel(self) -> bool:
        """Cancel is offered once, while running; asking again changes nothing."""
        return self.phase is ConvertPhase.RUNNING

    # -- transitions -----------------------------------------------------------

    def start(self) -> bool:
        """IDLE -> RUNNING. False (and no change) when a convert is already active."""
        if not self.can_start:
            return False
        self.phase = ConvertPhase.RUNNING
        return True

    def request_cancel(self) -> bool:
        """RUNNING -> CANCELLING. False when there is nothing left to cancel."""
        if not self.can_cancel:
            return False
        self.phase = ConvertPhase.CANCELLING
        return True

    def finish(self) -> bool:
        """The worker reported done, failed or cancelled. RUNNING/CANCELLING -> FINISHING."""
        if not self.is_working:
            return False
        self.phase = ConvertPhase.FINISHING
        return True

    def thread_closed(self) -> None:
        """The thread ended. Any phase -> IDLE (a thread that dies early ends the run)."""
        self.phase = ConvertPhase.IDLE
