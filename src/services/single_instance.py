"""Single-instance coordination through a Qt local socket."""

from __future__ import annotations

import hashlib
import os
import sys

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


class _ActivationEmitter(QObject):
    activationRequested = pyqtSignal()


_activation_emitter = _ActivationEmitter()
activationRequested = _activation_emitter.activationRequested


def build_instance_key() -> str:
    """Return the stable local-server name for this application directory."""

    if getattr(sys, "frozen", False):
        application_directory = os.path.dirname(sys.executable)
    else:
        application_directory = os.path.dirname(os.path.abspath(__file__))

    normalized_path = os.path.abspath(application_directory).lower()
    digest = hashlib.sha1(normalized_path.encode("utf-8")).hexdigest()
    return f"StickerGenie-{digest}"


def ensure_single_instance(app: QObject) -> bool:
    """Become the primary instance, or notify the existing instance."""

    key = build_instance_key()
    socket = QLocalSocket()
    socket.connectToServer(key)
    if socket.waitForConnected(250):
        socket.write(b"\x01")
        socket.flush()
        socket.waitForBytesWritten(250)
        socket.disconnectFromServer()
        return False

    QLocalServer.removeServer(key)
    server = QLocalServer(app)
    if not server.listen(key):
        return False

    def handle_activation() -> None:
        while server.hasPendingConnections():
            client = server.nextPendingConnection()
            if client is None:
                continue
            client.readAll()
            client.disconnectFromServer()
            _activation_emitter.activationRequested.emit()

    server.newConnection.connect(handle_activation)
    # Keep the server alive for the lifetime of the QApplication.
    setattr(app, "_single_instance_server", server)
    return True
