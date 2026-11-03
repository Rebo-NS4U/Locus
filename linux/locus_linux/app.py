# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot, Qt, QLockFile
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QMessageBox)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

from .engine import Session, DemoTransport
from .device import IPhoneTransport


class Worker(QThread):
    finished_operation = Signal(str, object)
    failed = Signal(str)
    interrupted = Signal(str)
    ready = Signal()

    def __init__(self, demo):
        super().__init__()
        self.demo = demo

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        data = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "locus-linux"
        self.transport = DemoTransport() if self.demo else IPhoneTransport()
        self.session = Session(self.transport, data / ("demo-session.json" if self.demo else "session.json"), self.interrupted.emit)
        self.operation_lock = asyncio.Lock()
        self.ready.emit()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, operation, *args):
        async def execute():
            try:
                async with self.operation_lock, asyncio.timeout(180):
                    if operation == "discover":
                        result = await self.transport.discover()
                    elif operation == "prepare":
                        result = await self.transport.prepare(*args)
                    elif operation == "apply":
                        result = await self.session.apply(*args)
                    elif operation == "restore":
                        result = await self.session.restore(*args)
                    else:
                        raise ValueError("Unknown operation")
                self.finished_operation.emit(operation, result)
            except Exception as error:
                self.failed.emit(str(error) or type(error).__name__)
        asyncio.run_coroutine_threadsafe(execute(), self.loop)

    def stop(self):
        async def cleanup():
            if self.session.refresh:
                self.session.refresh.cancel()
                await asyncio.gather(self.session.refresh, return_exceptions=True)
            try:
                await asyncio.wait_for(self.transport.close(), 10)
            finally:
                self.loop.stop()
        asyncio.run_coroutine_threadsafe(cleanup(), self.loop)


class MapBridge(QObject):
    picked = Signal(float, float)

    @Slot(float, float)
    def pick(self, latitude, longitude):
        self.picked.emit(latitude, longitude)


class Window(QMainWindow):
    def __init__(self, demo):
        super().__init__()
        self.setWindowTitle("Locus for Linux" + (" — DEMO" if demo else " — Preview"))
        self.resize(1180, 800)
        self.busy = True
        self.closing = False
        self.exit_after_restore = False
        self.worker = Worker(demo)
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel("Locus  ·  Linux preview" + ("  ·  DEMO: no phone commands" if demo else ""))
        title.setStyleSheet("font-size: 24px; font-weight: 600; padding: 12px;")
        layout.addWidget(title)
        row = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.setMinimumWidth(300)
        row.addWidget(self.devices, 1)
        self.buttons = []
        for label, operation in [("Scan USB", "discover"), ("Prepare iPhone", "prepare"), ("Set location", "apply"), ("Restore location", "restore")]:
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, op=operation: self.perform(op))
            row.addWidget(button)
            self.buttons.append(button)
        layout.addLayout(row)
        fields = QHBoxLayout()
        self.latitude, self.longitude = QDoubleSpinBox(), QDoubleSpinBox()
        for label, field, limit, value in [("Latitude", self.latitude, 90, 40.7128), ("Longitude", self.longitude, 180, -74.006)]:
            field.setRange(-limit, limit)
            field.setDecimals(6)
            field.setValue(value)
            fields.addWidget(QLabel(label))
            fields.addWidget(field)
        pin = QPushButton("Preview coordinates")
        pin.clicked.connect(self.preview)
        fields.addWidget(pin)
        layout.addLayout(fields)
        self.map = QWebEngineView()
        self.map.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.channel = QWebChannel(self.map.page())
        self.bridge = MapBridge()
        self.bridge.picked.connect(self.pick)
        self.channel.registerObject("locus", self.bridge)
        self.map.page().setWebChannel(self.channel)
        self.map.setUrl(QUrl.fromLocalFile(str(Path(__file__).parent / "web/map.html")))
        layout.addWidget(self.map, 1)
        self.status = QLabel("Starting…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setStyleSheet("padding: 12px;")
        layout.addWidget(self.status)
        note = QLabel("Click the map to preview a pin; Set location applies it. USB · iOS 17.4+ · Developer Mode. Map tiles use OpenStreetMap.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.setCentralWidget(root)
        self.worker.ready.connect(self.started)
        self.worker.finished_operation.connect(self.completed)
        self.worker.failed.connect(self.failure)
        self.worker.interrupted.connect(self.status.setText)
        self.worker.finished.connect(self.worker_stopped)
        self.enable(False)
        self.worker.start()

    def started(self):
        self.busy = False
        self.perform("discover")

    def enable(self, enabled):
        self.devices.setEnabled(enabled)
        for button in self.buttons:
            button.setEnabled(enabled)

    def perform(self, operation):
        if self.busy:
            return
        self.busy = True
        self.enable(False)
        self.status.setText("Working…" + (" Unlock the phone and accept Trust if prompted. First preparation downloads Apple's developer image." if operation == "prepare" else ""))
        args = [] if operation == "discover" else [self.devices.currentData()]
        if operation == "apply":
            args += [self.latitude.value(), self.longitude.value()]
        self.worker.submit(operation, *args)

    def completed(self, operation, result):
        self.busy = False
        self.enable(True)
        if operation == "discover":
            self.devices.clear()
            for device, name in result:
                self.devices.addItem(name, device)
            recovery = self.worker.session.journal.exists()
            self.status.setText(("Unresolved saved session: select its iPhone and Restore location. " if recovery else "") +
                ("Select your phone, then Prepare iPhone." if result else "No USB iPhone found. Connect a data cable, unlock the phone, and check that usbmuxd is running."))
        else:
            self.status.setText(result)
        if operation == "restore" and self.exit_after_restore:
            self.close()

    def failure(self, message):
        self.busy = False
        self.exit_after_restore = False
        self.enable(True)
        self.status.setText("Operation failed: " + message + " — If a session was active, its location is unknown. Reconnect and Restore location.")

    def pick(self, latitude, longitude):
        self.latitude.setValue(latitude)
        self.longitude.setValue(longitude)

    def preview(self):
        self.map.page().runJavaScript(f"setPin({json.dumps(self.latitude.value())}, {json.dumps(self.longitude.value())})")

    def closeEvent(self, event):
        if not self.worker.isRunning():
            event.accept()
            return
        event.ignore()
        if self.busy or self.closing:
            return
        if self.worker.session.journal.exists():
            answer = QMessageBox.question(self, "Unresolved iPhone session", "Restore the selected iPhone before quitting?\nChoose No to quit with location unknown and retain the recovery record.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes)
            if answer == QMessageBox.StandardButton.Cancel:
                return
            if answer == QMessageBox.StandardButton.Yes:
                self.exit_after_restore = True
                self.perform("restore")
                return
        self.closing = True
        self.enable(False)
        self.worker.stop()

    def worker_stopped(self):
        self.close()


def main():
    parser = argparse.ArgumentParser(description="Locus native Linux preview")
    parser.add_argument("--demo", action="store_true", help="Use fake hardware; sends no phone commands")
    options = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Locus Linux")
    app.setOrganizationName("Locus")
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "locus-linux"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = QLockFile(str(state / ("demo.lock" if options.demo else "app.lock")))
    if not lock.tryLock(0):
        QMessageBox.information(None, "Locus is already open", "Switch to the existing Locus window to manage your iPhone.")
        return 1
    window = Window(options.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
