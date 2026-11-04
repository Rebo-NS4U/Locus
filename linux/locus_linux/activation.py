# SPDX-License-Identifier: GPL-3.0-or-later
"""Ask an existing instance to show its window without touching phone state."""
from PySide6.QtCore import Qt
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def activate_existing(name):
    socket = QLocalSocket()
    socket.connectToServer(name)
    connected = socket.waitForConnected(1000)
    if connected:
        socket.disconnectFromServer()
    return connected


def listen_for_activation(name, window):
    # Only the holder of the application's lock may remove a stale socket.
    QLocalServer.removeServer(name)
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def show_window():
        while server.hasPendingConnections():
            socket = server.nextPendingConnection()
            socket.disconnectFromServer()
            socket.deleteLater()
        window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
        window.show()
        window.raise_()
        window.activateWindow()
        if window.windowHandle():
            window.windowHandle().requestActivate()

    server.newConnection.connect(show_window)
    if not server.listen(name):
        raise RuntimeError("Cannot listen for Locus activation: " + server.errorString())
    return server
