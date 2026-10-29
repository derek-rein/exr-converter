"""Run a command loop in a child process, with a thread fallback.

Pure-Python background work (walking folders, grouping frames, fuzzy
scoring) holds the GIL. On a thread inside the GUI process every Qt → Python
callback on the GUI thread then waits for it, so the UI stutters even though
the work is "in the background". :class:`BackgroundWorker` runs such a loop in
a ``spawn`` child process instead and relays its events back from a small
thread that sleeps in a blocking ``Queue.get`` (which releases the GIL).

A *target* is a module-level function ``target(cmd_q, evt_q)`` that reads
command tuples from ``cmd_q`` until it gets ``("stop",)``, puts event tuples
on ``evt_q``, and finally puts ``("stopped",)``. The same function runs on a
thread with ``queue.Queue`` objects when a process cannot be started.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Target = Callable[[Any, Any], None]


def wait_for_command(cmd_q: Any, timeout: float | None) -> tuple:
    """``cmd_q.get`` that also returns ``("stop",)`` once the parent process is gone.

    ``timeout=0`` → ``get_nowait`` (raises ``queue.Empty``). ``None`` blocks,
    waking every second to notice an app that died without saying stop.
    """
    if timeout == 0:
        return cmd_q.get_nowait()
    if timeout is not None:
        return cmd_q.get(timeout=timeout)
    import multiprocessing

    parent = multiprocessing.parent_process()
    while True:
        try:
            return cmd_q.get(timeout=1.0)
        except queue.Empty:
            if parent is not None and not parent.is_alive():
                return ("stop",)


class BackgroundWorker:
    """Owns one command loop (process preferred) and relays its events.

    *deliver* is called on the relay thread with each event tuple; emit a Qt
    signal from it (queued to the GUI thread) rather than touching widgets.
    """

    def __init__(
        self,
        target: Target,
        deliver: Callable[[tuple], None],
        *,
        name: str,
        use_process: bool = True,
    ) -> None:
        self._deliver = deliver
        self._done = threading.Event()
        self._stop_requested = False
        self._clean_exit = False
        self._proc: Any = None
        self._thread: threading.Thread | None = None
        if use_process:
            try:
                import multiprocessing

                ctx = multiprocessing.get_context("spawn")
                self._cmd_q: Any = ctx.Queue()
                self._evt_q: Any = ctx.Queue()
                self._proc = ctx.Process(
                    target=target, args=(self._cmd_q, self._evt_q), name=name, daemon=True
                )
                self._proc.start()
            except Exception:
                log.warning("Could not start %s process; using a thread", name, exc_info=True)
                self._proc = None
        if self._proc is None:
            self._cmd_q = queue.Queue()
            self._evt_q = queue.Queue()
            self._thread = threading.Thread(
                target=target, args=(self._cmd_q, self._evt_q), name=name, daemon=True
            )
            self._thread.start()
        self._relay = threading.Thread(target=self._relay_loop, name=f"{name}Relay", daemon=True)
        self._relay.start()

    @property
    def uses_process(self) -> bool:
        return self._proc is not None

    def post(self, cmd: tuple) -> None:
        if self._done.is_set():
            return
        try:
            self._cmd_q.put(cmd)
        except (OSError, ValueError):
            pass

    def _worker_alive(self) -> bool:
        if self._proc is not None:
            return bool(self._proc.is_alive())
        return self._thread is not None and self._thread.is_alive()

    def is_alive(self) -> bool:
        return not self._done.is_set() and self._worker_alive()

    @property
    def crashed(self) -> bool:
        """True when the loop died without being asked to stop (e.g. the child
        process could not import its module). Callers can fall back to a thread."""
        return self._done.is_set() and not self._stop_requested and not self._clean_exit

    def _relay_loop(self) -> None:
        while not self._done.is_set():
            try:
                event = self._evt_q.get(timeout=0.25)
            except queue.Empty:
                if not self._worker_alive():
                    break
                continue
            except (EOFError, OSError, ValueError):
                break
            if not event or event[0] == "stopped":
                self._clean_exit = True
                break
            if self._done.is_set():
                break
            try:
                self._deliver(event)
            except Exception:
                log.exception("Background event handler failed")
        self._done.set()

    def stop(self, timeout: float = 2.0) -> bool:
        """Ask the loop to stop and wait up to *timeout*. True when it exited."""
        self._stop_requested = True
        try:
            self._cmd_q.put(("stop",))
        except (OSError, ValueError):
            pass
        self._done.set()
        if self._proc is not None:
            self._proc.join(timeout)
            if self._proc.is_alive():
                log.warning("Background process did not stop within %.1fs; terminating", timeout)
                self._proc.terminate()
                self._proc.join(1.0)
            alive = bool(self._proc.is_alive())
            for q in (self._cmd_q, self._evt_q):
                try:
                    q.close()
                    q.cancel_join_thread()
                except (AttributeError, OSError, ValueError):
                    pass
        else:
            t = self._thread
            if t is not None and t is not threading.current_thread():
                t.join(timeout)
            alive = t is not None and t.is_alive()
        if self._relay is not threading.current_thread():
            self._relay.join(1.0)
        return not alive
