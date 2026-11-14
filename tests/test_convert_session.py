from __future__ import annotations

from src.services.convert_session import ConvertPhase, ConvertSession


def test_a_normal_run() -> None:
    s = ConvertSession()
    assert s.is_idle and s.can_start and not s.is_working and not s.can_cancel
    assert s.start()
    assert s.phase is ConvertPhase.RUNNING and s.is_working and s.can_cancel
    assert s.finish()
    # The worker has reported, but its thread is still closing: not idle yet.
    assert s.phase is ConvertPhase.FINISHING
    assert not s.is_idle and not s.can_start and not s.is_working and not s.can_cancel
    s.thread_closed()
    assert s.is_idle and s.can_start


def test_cannot_start_twice() -> None:
    s = ConvertSession()
    assert s.start()
    assert not s.start()
    assert s.phase is ConvertPhase.RUNNING


def test_cannot_start_while_the_old_thread_closes() -> None:
    s = ConvertSession()
    s.start()
    s.finish()
    assert not s.start()


def test_cancel_once_then_the_worker_reports() -> None:
    s = ConvertSession()
    s.start()
    assert s.request_cancel()
    assert s.phase is ConvertPhase.CANCELLING
    assert s.is_working and not s.can_cancel  # progress stays, a second Cancel is refused
    assert not s.request_cancel()
    assert s.finish()
    assert s.phase is ConvertPhase.FINISHING


def test_nothing_to_cancel_or_finish_when_idle() -> None:
    s = ConvertSession()
    assert not s.request_cancel()
    assert not s.finish()
    assert s.is_idle


def test_a_thread_that_dies_early_ends_the_run() -> None:
    s = ConvertSession()
    s.start()
    s.thread_closed()
    assert s.is_idle
