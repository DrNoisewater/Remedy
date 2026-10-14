"""Single-instance support: a second launch hands off to the running Remedy window and exits.

The first instance listens on a named local socket (a named pipe on Windows). A later launch
connects, asks the first instance to show itself, and quits before any window appears.
"""
from __future__ import annotations

import getpass

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .core.utils import IS_WINDOWS, log


def _key() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    # one instance per Windows user; elevated and normal launches share it
    return f"NoisewaterLabs.Remedy.{user}"


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.key = _key()
        self.server = None

    def notify_running(self) -> bool:
        """If Remedy is already running, ask it to come to the front. Returns True if it was."""
        sock = QLocalSocket()
        sock.connectToServer(self.key)
        if not sock.waitForConnected(400):
            return False
        if IS_WINDOWS:
            try:  # let the running instance take focus (Windows blocks background apps otherwise)
                import ctypes
                ctypes.windll.user32.AllowSetForegroundWindow(-1)
            except Exception:  # noqa: BLE001
                pass
        sock.write(b"show\n")
        sock.flush()
        sock.waitForBytesWritten(400)
        sock.disconnectFromServer()
        return True

    def listen(self) -> None:
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.WorldAccessOption)  # reachable from elevated/normal launches
        if not self.server.listen(self.key):
            QLocalServer.removeServer(self.key)  # clear a stale socket left by a crash
            if not self.server.listen(self.key):
                log.warning("Single-instance listener unavailable: %s", self.server.errorString())
                return
        self.server.newConnection.connect(self._on_connection)

    def close(self) -> None:
        """Stop listening (used right before relaunching as administrator)."""
        if self.server is not None:
            self.server.close()

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.disconnected.connect(conn.deleteLater)
            self.activated.emit()
