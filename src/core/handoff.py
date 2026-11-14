"""Hand a double-clicked file to the copy of the app that is already running.

The first launch starts a local server (``src/services/single_instance.py``);
a later launch finds it, sends the paths, and exits without starting Qt. This
module is the client half and the wire format, with no Qt import, so a second
double-click costs a Python start and one socket write.

Wire format: one UTF-8 JSON object per connection, ``{"v": 1, "paths": [...]}``,
then the client closes. There is no reply: a connection that opens is enough,
and a stale socket or pipe refuses at once.

Endpoint: a Unix socket in the runtime directory (macOS and Linux), a named pipe
on Windows. Both are per user.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Iterable

PROTOCOL_VERSION = 1


def _user_tag() -> str:
    if sys.platform == "win32":
        name = os.environ.get("USERNAME") or "user"
        return "".join(c for c in name if c.isalnum() or c in "-_") or "user"
    return str(os.getuid())


def endpoint() -> str:
    """The socket path (macOS, Linux) or pipe name (Windows) for this user."""
    # The override lets tests and a second dev copy use a private endpoint.
    base = os.environ.get("EXR_CONVERTER_HANDOFF") or f"exr-converter-{_user_tag()}"
    if sys.platform == "win32":
        return base  # QLocalServer maps it to \\.\pipe\<name>
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    directory = runtime if runtime and os.path.isdir(runtime) else tempfile.gettempdir()
    return os.path.join(directory, f"{base}.sock")


def encode(paths: Iterable[str]) -> bytes:
    return json.dumps({"v": PROTOCOL_VERSION, "paths": [str(p) for p in paths]}).encode("utf-8")


def decode(data: bytes) -> list[str]:
    """The paths in a message, or an empty list for anything that is not one."""
    try:
        message = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return []
    if not isinstance(message, dict) or message.get("v") != PROTOCOL_VERSION:
        return []
    paths = message.get("paths")
    if not isinstance(paths, list):
        return []
    return [p for p in paths if isinstance(p, str) and p]


def is_running() -> bool:
    """True when an instance is already listening on this user's endpoint."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            if kernel32.WaitNamedPipeW(rf"\\.\pipe\{endpoint()}", 1):
                return True
            # ERROR_SEM_TIMEOUT: the pipe exists, but every instance is busy.
            return ctypes.get_last_error() == 121
        import socket

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            sock.connect(endpoint())
        return True
    except OSError:
        return False


def _send_to_pipe(pipe_path: str, payload: bytes, *, timeout: float) -> bool:
    """Windows: write *payload* to the server's pipe, giving up after *timeout* seconds.

    Qt's pipes have no buffer, so a write returns only when the server reads it;
    a server whose event loop is busy would hold the client indefinitely. The
    write runs on a thread so the wait has a limit.
    """
    import threading

    outcome: list[bool] = []

    def write() -> None:
        try:
            with open(pipe_path, "r+b", buffering=0) as pipe:
                pipe.write(payload)
            outcome.append(True)
        except OSError:
            outcome.append(False)

    thread = threading.Thread(target=write, name="exr-handoff", daemon=True)
    thread.start()
    thread.join(timeout)
    return bool(outcome and outcome[0])


def send_paths(paths: Iterable[str], *, timeout: float = 0.5) -> bool:
    """Give *paths* to a running instance. True if one took them, False if none is running."""
    payload = encode(paths)
    try:
        if sys.platform == "win32":
            return _send_to_pipe(rf"\\.\pipe\{endpoint()}", payload, timeout=max(timeout, 3.0))
        import socket

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(endpoint())
            sock.sendall(payload)
        return True
    except OSError:
        return False
