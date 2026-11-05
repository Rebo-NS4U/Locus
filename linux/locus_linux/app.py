# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import sys
from pathlib import Path

from PySide6.QtCore import Qt, QLockFile, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QApplication, QMainWindow, QVBoxLayout, QLabel, QPushButton, QMessageBox, QSystemTrayIcon, QDialog)

from .storage import Places
from .library import RouteLibrary
from .services import endpoint
from .theme import apply_theme
from .units import to_mph, to_kph, speed_text
from .planning import RoutePlan
from .activation import activate_existing, listen_for_activation, opening_geometry


from .worker import Worker as Worker, state_directory as state_directory
from .widgets import MapBridge as MapBridge, DepartureTimePicker as DepartureTimePicker, StatusLabel as StatusLabel
from .window_ui import WindowUiMixin
from .window_routes import RouteEditorMixin
from .window_library import RouteLibraryMixin
from .window_map import MapInteractionMixin


class Window(WindowUiMixin, RouteEditorMixin, RouteLibraryMixin, MapInteractionMixin, QMainWindow):
    def __init__(self, demo):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.demo = demo
        self.colors = apply_theme(QApplication.instance())
        self.font_stamp = 0
        self.setWindowTitle("Locus for Linux" + (" — DEMO" if demo else ""))
        self.setWindowIcon(QIcon(str(Path(__file__).parent / "web/locus.svg")))
        self.resize(1440, 920)
        self.setMinimumSize(900, 600)
        self.busy, self.ready, self.closing, self.exit_after_restore = True, False, False, False
        self.pending_operation = None
        self.stops, self.route_points, self.alternatives = [], [], []
        self.route_plan = RoutePlan()
        self.route_library = RouteLibrary(state_directory() / ('demo-routes.json' if demo else 'routes.json'))
        self.active_route_id = None
        self.restoring_route = False
        self.route_profile = None
        self.planning_revision = None
        self.planning_document = None
        self.segment_loading = False
        self.departure_loading = False
        self.map_loaded = False
        self.auto_plan_timer = QTimer(self)
        self.auto_plan_timer.setSingleShot(True)
        self.auto_plan_timer.setInterval(600)
        self.auto_plan_timer.timeout.connect(self.refresh_automatic_route)
        self.imported_track = False
        self.source_track_points = []
        self.motion = {"mode": "fixed", "connected": False}
        self.active_leg_signature = None
        self.keys = set()
        self.joystick_engaged = False
        self.pending_recent = None
        self.selected_name = ""
        self.store = Places(state_directory() / ("demo-places.json" if demo else "places.json"))
        self.worker = Worker(demo)
        self.foreground_buttons = []
        self.build_ui()
        preferences = self.store.data["settings"]
        self.select_coordinates(preferences.get('default_latitude', 40.7128), preferences.get('default_longitude', -74.006))
        self.travel_mode.setCurrentText(preferences.get("travel_mode", "Walk"))
        self.speed.setValue(to_mph(preferences.get("speed_kph", to_kph(self.speed.value()))))
        self.variation.setChecked(preferences.get("variation", True) if preferences.get("motion_revision") == 1 else True)
        self.map_style.setCurrentIndex(0 if preferences.get("night_map", True) else 1)
        self.follow.setChecked(preferences.get("follow", True))
        self.sidebar_toggle.setChecked(preferences.get("sidebar_visible", False))
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip("Locus for Linux")
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.worker.ready.connect(self.started)
        self.worker.finished_operation.connect(self.completed)
        self.worker.failed.connect(self.failure)
        self.worker.interrupted.connect(self.interrupted)
        self.worker.state_changed.connect(self.motion_changed)
        self.worker.finished.connect(self.close)
        self.theme_timer = QTimer(self)
        self.theme_timer.timeout.connect(self.refresh_theme)
        self.theme_timer.start(2000)
        # Scope the filter to widgets, not every QObject: a global Python filter
        # can re-enter PySide wrapper creation during WebEngine request setup.
        self.installEventFilter(self)
        self.map.installEventFilter(self)
        QApplication.instance().focusChanged.connect(self.focus_changed)
        self.enable(False)
        if self.route_library.data.get('draft'):
            draft = dict(self.route_library.data)
            self.route_title.setText(draft.get('draft_name') or 'Recovered draft')
            identifier = draft.get('draft_route_id')
            self.active_route_id = identifier if any(item['id'] == identifier for item in draft['routes']) else None
            self.apply_route_document(draft['draft'])
        if self.state_warnings():
            self.status.setText(self.state_warnings())
        self.worker.start()

    def state_warnings(self):
        return ' '.join(error for error in (self.store.error, self.route_library.error,
                                            self.worker.recovery_error) if error)

    def started(self):
        self.ready, self.busy = True, False
        self.connection.blockSignals(True)
        self.connection.setCurrentIndex(self.connection.findData(self.worker.transport.mode))
        self.connection.blockSignals(False)
        self.configure_motion()
        self.perform("discover")
        if not self.demo and not self.store.data["settings"].get("setup_seen"):
            QTimer.singleShot(400, self.show_setup)

    def show_setup(self):
        if hasattr(self, "setup_dialog") and self.setup_dialog.isVisible():
            self.setup_dialog.raise_()
            return
        self.setup_dialog = QDialog(self)
        self.setup_dialog.setWindowTitle("Set up Locus")
        self.setup_dialog.resize(560, 480)
        layout = QVBoxLayout(self.setup_dialog)
        for title, body in [
            ("1 · Connect and trust", "Connect your iPhone with a USB data cable, unlock it and accept Trust This Computer. Linux needs usbmuxd and libimobiledevice installed."),
            ("2 · Enable developer access", "Use iOS 17.4 or newer. Enable Developer Mode in Settings → Privacy & Security and restart if prompted. Scan, select the phone and choose Prepare iPhone. First preparation downloads Apple's developer image."),
            ("3 · Pick a location", "Click the map to add route pins. Search or enter coordinates to select a location. Set location applies it. Verify the result in Maps on the phone."),
            ("4 · Move or connect wirelessly", "Use Move for the joystick. In Route, choose Walking, Driving or Cycling for each pin-to-pin segment, then Plan route. Wi-Fi: restore first, enable it over USB, unplug and scan in Wi-Fi mode."),
            ("5 · Restore before leaving", "Restore real location before disconnecting or quitting. If the connection drops, the location is unknown. Reconnect the same phone to restore; a phone restart may be needed if simulation stays stuck.")]:
            heading = QLabel(title)
            label = QLabel(body)
            label.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(label)
        button = QPushButton("Got it")
        button.clicked.connect(self.setup_dialog.accept)
        layout.addWidget(button)
        def seen():
            try:
                self.store.update_settings(setup_seen=True)
            except (OSError, ValueError) as error:
                self.status.setText(f'Could not save settings: {error}')
        self.setup_dialog.accepted.connect(seen)
        self.setup_dialog.show()

    def enable(self, enabled):
        self.devices.setEnabled(enabled)
        self.connection.setEnabled(enabled)
        for button in self.foreground_buttons:
            button.setEnabled(enabled)

    def request(self, operation, *args):
        if self.busy or not self.ready:
            return
        self.release_keys()
        self.busy = True
        self.pending_operation = operation
        self.enable(False)
        self.status.setText("Working…" + (" Unlock the phone and accept Trust if prompted. First preparation downloads Apple's developer image." if operation == "prepare" else ""))
        self.worker.submit(operation, *args)

    def perform(self, operation):
        args = [] if operation == "discover" else [self.devices.currentData()]
        if operation == "apply":
            args += [self.latitude.value(), self.longitude.value()]
            self.pending_recent = {"name": self.selected_name, "point": args[1:]}
        self.request(operation, *args)

    def completed(self, operation, result):
        if operation in ("configure", "configure_traffic", "joystick"):
            return
        self.busy = False
        self.pending_operation = None
        self.enable(True)
        if operation == "discover":
            selected = self.devices.currentData()
            self.devices.clear()
            for device, name in result:
                self.devices.addItem(name, device)
            index = self.devices.findData(selected)
            if index >= 0:
                self.devices.setCurrentIndex(index)
            recovery = self.worker.session.journal.exists()
            warning = self.state_warnings()
            self.status.setText((warning + ' ' if warning else '') +
                ("Unresolved session: select its iPhone and Restore location. " if recovery and not self.worker.recovery_error else "") +
                ("Select your phone, then Prepare iPhone." if result else "No iPhone found. Connect and unlock it; for Wi-Fi, enable it over USB first and use the same network."))
        elif operation == "search":
            self.populate(self.results, result)
            self.status.setText(f"{len(result)} places found. Select one to center the map.")
        elif operation == "mixed_route":
            try:
                if self.route_plan is not self.planning_document:
                    raise ValueError("Route changed while planning. Plan again to use your edits.")
                self.route_plan.accept(result, self.planning_revision)
                self.stops_changed()
                self.js("fitRoute()")
                self.status.setText("Paths chosen automatically. Select a colored segment to change its travel mode.")
            except ValueError as error:
                self.status.setText(speed_text(str(error)))
                self.schedule_automatic_route()
        elif operation == "set_mode":
            self.devices.clear()
            self.status.setText(result)
            self.perform("discover")
        else:
            self.status.setText(result)
            if operation == "apply" and self.pending_recent:
                try:
                    self.store.add("recents", **self.pending_recent)
                    self.refresh_places()
                except (OSError, ValueError) as error:
                    self.status.setText(f'{result} Could not save recent place: {error}')
                finally:
                    self.pending_recent = None
        if operation == "restore" and self.exit_after_restore:
            self.close()

    def failure(self, operation, message):
        if operation not in ("configure", "configure_traffic", "joystick"):
            self.busy = False
            self.pending_operation = None
            self.exit_after_restore = False
            self.enable(True)
        if operation == "set_mode":
            self.connection.blockSignals(True)
            self.connection.setCurrentIndex(self.connection.findData(self.worker.transport.mode))
            self.connection.blockSignals(False)
        self.status.setText(message)

    def interrupted(self, message):
        self.status.setText(message)
        if self.notifications.isChecked() and not self.isActiveWindow() and self.tray.isVisible():
            self.tray.showMessage("Locus connection", message, QSystemTrayIcon.MessageIcon.Warning, 8000)


    def save_settings(self):
        try:
            self.store.update_settings(photon=endpoint(self.photon.text().strip()), valhalla=endpoint(self.valhalla.text().strip()),
                reconnect=self.reconnect.isChecked(), notifications=self.notifications.isChecked(), travel_mode=self.travel_mode.currentText(),
                motion_revision=1, speed_kph=to_kph(self.speed.value()), variation=self.variation.isChecked(), night_map=self.map_style.currentIndex() == 0, follow=self.follow.isChecked(),
                traffic_stops=self.traffic_stops.isChecked(), red_probability=self.red_probability.value(), overpass=endpoint(self.overpass.text().strip()),
                default_latitude=self.default_latitude.value(), default_longitude=self.default_longitude.value())
            self.configure_motion()
            self.status.setText("Settings saved.")
        except (OSError, ValueError) as error:
            self.status.setText(speed_text(str(error)))

    def closeEvent(self, event):
        if not self.worker.isRunning():
            self.tray.hide()
            event.accept()
            return
        event.ignore()
        if self.busy or self.closing:
            return
        self.release_keys()
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


def main():
    parser = argparse.ArgumentParser(description="Locus native Linux desktop")
    parser.add_argument("--demo", action="store_true", help="Use fake hardware; sends no phone commands")
    parser.add_argument("--install-launcher", action="store_true", help="Install an application-menu entry for this environment")
    options = parser.parse_args()
    if options.install_launcher:
        from .install import install_launcher
        print(install_launcher())
        return 0
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Locus Linux")
    app.setDesktopFileName("locus-linux")
    app.setOrganizationName("Locus")
    state = state_directory()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = QLockFile(str(state / ("demo.lock" if options.demo else "app.lock")))
    activation_name = str(state / ("demo-activation" if options.demo else "activation"))
    if not lock.tryLock(0):
        if activate_existing(activation_name):
            return 0
        QMessageBox.information(None, "Locus is already open", "Switch to the existing Locus window to manage your iPhone.")
        return 1
    window = Window(options.demo)
    window.activation_server = listen_for_activation(activation_name, window)
    window.setGeometry(opening_geometry(window.screen().availableGeometry()))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
