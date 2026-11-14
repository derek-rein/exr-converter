"""The running app's side of the file handoff: receive paths from later launches."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from ..core import handoff

log = logging.getLogger(__name__)

# A path list is a few hundred bytes; refuse anything absurd rather than buffer it.
_MAX_MESSAGE = 1 << 20


class InstanceServer(QObject):
    """Listens on :func:`handoff.endpoint` and emits the paths other launches send."""

    paths_received = Signal(list)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._on_connection)
        self._buffers: dict[QLocalSocket, bytearray] = {}

    def start(self) -> bool:
        """Start listening. False when another instance already owns the endpoint."""
        name = handoff.endpoint()
        # Windows lets a second server share a pipe name, so ask first.
        if handoff.is_running():
            return False
        if self._server.listen(name):
            return True
        # A crashed instance leaves its socket file behind: clear it and try once more.
        QLocalServer.removeServer(name)
        if self._server.listen(name):
            return True
        log.warning("Single-instance server could not listen: %s", self._server.errorString())
        return False

    def close(self) -> None:
        self._server.close()

    def _on_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                return
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._on_done(s))

    def _on_ready(self, sock: QLocalSocket) -> None:
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf += bytes(sock.readAll().data())
        if len(buf) > _MAX_MESSAGE:
            self._buffers.pop(sock, None)
            sock.abort()

    def _on_done(self, sock: QLocalSocket) -> None:
        buf = self._buffers.pop(sock, None)
        sock.deleteLater()
        if not buf:
            return
        paths = handoff.decode(bytes(buf))
        if paths:
            self.paths_received.emit(paths)
