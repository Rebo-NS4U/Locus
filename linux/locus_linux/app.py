# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QUrl, Signal, Slot, Qt, QLockFile, QTimer, QEvent
from PySide6.QtGui import QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QDoubleSpinBox, QMessageBox, QLineEdit, QListWidget,
    QTabWidget, QGroupBox, QCheckBox, QSplitter, QFileDialog, QInputDialog,
    QSystemTrayIcon, QScrollArea, QGridLayout, QDialog, QSizePolicy, QToolButton, QMenu)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings

from .engine import Session, DemoTransport
from .device import IPhoneTransport
from .routes import SPEEDS, Route, parse_gpx, export_gpx
from .storage import Places
from .services import Services, PHOTON, VALHALLA, OVERPASS, endpoint
from .theme import apply_theme, read_colors
from .planning import RoutePlan, PROFILES


def state_directory():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "locus-linux"


class Worker(QThread):
    finished_operation = Signal(str, object)
    failed = Signal(str, str)
    interrupted = Signal(str)
    state_changed = Signal(object)
    ready = Signal()

    def __init__(self, demo):
        super().__init__()
        self.demo = demo

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.transport = DemoTransport() if self.demo else IPhoneTransport()
        self.session = Session(self.transport, state_directory() / ("demo-session.json" if self.demo else "session.json"),
                               self.interrupted.emit, self.state_changed.emit)
        if self.session.journal.exists():
            saved_mode = json.loads(self.session.journal.read_text()).get("transport", "usb")
            if saved_mode in ("usb", "wifi"):
                self.transport.mode = saved_mode
        self.services = Services()
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
                    elif operation in ("prepare", "enable_wifi", "set_mode"):
                        if self.session.journal.exists():
                            raise ValueError("Restore the current session before changing device setup or transport.")
                        result = await getattr(self.transport, operation)(*args)
                    elif operation in ("apply", "restore", "configure", "configure_traffic", "start_route", "control", "joystick"):
                        result = await getattr(self.session, operation)(*args)
                    elif operation in ("search", "mixed_route"):
                        result = await getattr(self.services, operation)(*args)
                    else:
                        raise ValueError("Unknown operation")
                self.finished_operation.emit(operation, result)
            except Exception as error:
                self.failed.emit(operation, str(error) or type(error).__name__)
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
    stop_moved = Signal(int, float, float)
    segment_selected = Signal(int)
    bend_moved = Signal(int, int, float, float)
    bend_deleted = Signal(int, int)

    @Slot(float, float)
    def pick(self, latitude, longitude):
        self.picked.emit(latitude, longitude)

    @Slot(int, float, float)
    def moveStop(self, index, latitude, longitude):
        self.stop_moved.emit(index, latitude, longitude)

    @Slot(int)
    def selectLeg(self, index):
        self.segment_selected.emit(index)

    @Slot(int, int, float, float)
    def moveBend(self, segment, bend, latitude, longitude):
        self.bend_moved.emit(segment, bend, latitude, longitude)

    @Slot(int, int)
    def deleteBend(self, segment, bend):
        self.bend_deleted.emit(segment, bend)


class StatusLabel(QLabel):
    def setText(self, message):
        self.setToolTip(message)
        super().setText(message)


class Window(QMainWindow):
    def __init__(self, demo):
        super().__init__()
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
        self.route_profile = None
        self.planning_revision = None
        self.planning_document = None
        self.segment_loading = False
        self.imported_track = False
        self.motion = {"mode": "fixed", "connected": False}
        self.keys = set()
        self.joystick_engaged = False
        self.pending_recent = None
        self.selected_name = ""
        self.store = Places(state_directory() / ("demo-places.json" if demo else "places.json"))
        self.worker = Worker(demo)
        self.foreground_buttons = []
        self.build_ui()
        preferences = self.store.data["settings"]
        self.travel_mode.setCurrentText(preferences.get("travel_mode", "Walk"))
        self.speed.setValue(preferences.get("speed_kph", self.speed.value()))
        self.variation.setChecked(preferences.get("variation", False))
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
        self.worker.start()

    def button(self, label, callback, layout, foreground=False):
        button = QPushButton(label)
        button.clicked.connect(callback)
        layout.addWidget(button)
        if foreground:
            self.foreground_buttons.append(button)
        return button

    def group(self, title, parent):
        group = QGroupBox(title)
        layout = QVBoxLayout(group)
        parent.addWidget(group)
        return layout

    def build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(5)
        header = QHBoxLayout()
        header.setSpacing(6)
        title = QLabel("Locus · Demo" if self.demo else "Locus")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        title.setToolTip("Locus for Linux" + (" · DEMO: no phone commands" if self.demo else ""))
        header.addWidget(title)
        self.sidebar_toggle = QPushButton("Controls")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setToolTip("Show/hide places, routes and movement controls (Ctrl+B)")
        header.addWidget(self.sidebar_toggle)
        self.sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_shortcut.activated.connect(self.sidebar_toggle.toggle)
        self.connection_badge = QLabel("Not connected")
        self.connection = QComboBox()
        self.connection.addItem("USB", "usb")
        self.connection.addItem("Wi-Fi", "wifi")
        self.connection.setToolTip("Phone connection transport")
        self.connection.currentIndexChanged.connect(self.change_transport)
        header.addWidget(self.connection)
        self.devices = QComboBox()
        self.devices.setMinimumWidth(140)
        self.devices.setMaximumWidth(220)
        self.devices.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.devices.setMinimumContentsLength(12)
        self.devices.setToolTip("Selected iPhone")
        header.addWidget(self.devices, 1)
        self.device_menu_button = QToolButton()
        self.device_menu_button.setText("⋯")
        self.device_menu_button.setToolTip("Scan, prepare, wireless setup and help")
        self.device_menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.device_menu = QMenu(self.device_menu_button)
        for label, operation in [("Scan", "discover"), ("Prepare iPhone", "prepare"), ("Enable Wi-Fi over USB", "enable_wifi")]:
            action = self.device_menu.addAction(label)
            action.triggered.connect(lambda checked=False, op=operation: self.perform(op))
            self.foreground_buttons.append(action)
        self.device_menu.addSeparator()
        self.device_menu.addAction("Setup guide", self.show_setup)
        self.device_menu_button.setMenu(self.device_menu)
        header.addWidget(self.device_menu_button)
        header.addStretch(1)
        self.button("Set location", lambda: self.perform("apply"), header, True).setObjectName("primary")
        self.button("Hold", lambda: self.request("control", "hold"), header, True).setToolTip("Stop movement and hold the current simulated location")
        self.button("Restore", lambda: self.perform("restore"), header, True).setToolTip("Clear simulated location and close the phone connection")
        layout.addLayout(header)
        split = QSplitter()
        self.split = split
        split.setHandleWidth(4)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(0, 0, 4, 0)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QWidget { font-size: 13px; } QTabBar::tab { padding: 7px 6px; }")
        for widget, title in [(self.places_tab(), "Places"), (self.route_tab(), "Route"),
                              (self.movement_tab(), "Move"), (self.map_tab(), "Map"), (self.settings_tab(), "Settings")]:
            self.tabs.addTab(widget, title)
        side.addWidget(self.tabs)
        scroll = QScrollArea()
        self.sidebar_scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setWidget(sidebar)
        scroll.setMinimumWidth(340)
        scroll.hide()
        sidebar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        split.addWidget(scroll)
        self.map = QWebEngineView()
        self.map.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.channel = QWebChannel(self.map.page())
        self.bridge = MapBridge()
        self.bridge.picked.connect(self.pick)
        self.bridge.stop_moved.connect(self.move_stop)
        self.bridge.segment_selected.connect(self.select_map_segment)
        self.bridge.bend_moved.connect(self.move_bend)
        self.bridge.bend_deleted.connect(self.delete_bend)
        self.channel.registerObject("locus", self.bridge)
        self.map.page().setWebChannel(self.channel)
        self.map.loadFinished.connect(self.map_ready)
        self.map.setUrl(QUrl.fromLocalFile(str(Path(__file__).parent / "web/map.html")))
        split.addWidget(self.map)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setCollapsible(1, False)
        split.setCollapsible(0, False)
        self.sidebar_toggle.toggled.connect(self.toggle_sidebar)
        layout.addWidget(split, 1)
        footer = QHBoxLayout()
        self.status = StatusLabel("Starting…")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.status.setMaximumHeight(26)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("status")
        footer.addWidget(self.status, 1)
        self.connection_badge.setStyleSheet("font-size: 12px;")
        footer.addWidget(self.connection_badge)
        layout.addLayout(footer)
        self.setCentralWidget(root)

    def toggle_sidebar(self, visible):
        self.release_keys()
        self.sidebar_scroll.setVisible(visible)
        if visible:
            self.split.setSizes([340, max(400, self.split.width() - 344)])
        self.store.data["settings"]["sidebar_visible"] = visible
        self.store.save()
        QTimer.singleShot(100, lambda: self.js("map.invalidateSize({pan:false})"))

    def map_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        fields = self.group("Coordinates", box)
        self.latitude, self.longitude = QDoubleSpinBox(), QDoubleSpinBox()
        for label, field, limit, value in [("Latitude", self.latitude, 90, 40.7128), ("Longitude", self.longitude, 180, -74.006)]:
            field.setRange(-limit, limit)
            field.setDecimals(6)
            field.setValue(value)
            fields.addWidget(QLabel(label))
            fields.addWidget(field)
        self.button("Preview pin", self.preview, fields)
        group = self.group("Map display", box)
        self.map_style = QComboBox()
        self.map_style.addItems(["Night map", "Street map"])
        self.map_style.currentIndexChanged.connect(self.map_options)
        group.addWidget(self.map_style)
        self.follow = QCheckBox("Follow movement")
        self.follow.setChecked(True)
        self.follow.toggled.connect(self.map_options)
        group.addWidget(self.follow)
        self.button("Fit route", lambda: self.js("fitRoute()"), group)
        legend = QLabel("The ring marks your preview pin. The solid dot marks the last acknowledged command, not an independent GPS reading. Verify the result in Maps on your iPhone.")
        legend.setWordWrap(True)
        legend.setObjectName("subtitle")
        box.addWidget(legend)
        box.addStretch()
        return page

    def places_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        group = self.group("Find a place", box)
        row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setMinimumWidth(60)
        self.search.setPlaceholderText("City, address or landmark")
        self.search.returnPressed.connect(self.search_places)
        row.addWidget(self.search, 1)
        self.button("Search", self.search_places, row, True)
        group.addLayout(row)
        self.results = QListWidget()
        self.results.setMaximumHeight(170)
        self.results.itemClicked.connect(lambda item: self.choose_place(item.data(Qt.ItemDataRole.UserRole)))
        group.addWidget(self.results)
        self.saved_tabs = QTabWidget()
        self.saved = {}
        for key, label in [("favorites", "Favorites"), ("recents", "Recent")]:
            widget = QListWidget()
            widget.itemClicked.connect(lambda item: self.choose_place(item.data(Qt.ItemDataRole.UserRole)))
            self.saved[key] = widget
            self.saved_tabs.addTab(widget, label)
        box.addWidget(self.saved_tabs, 1)
        row = QHBoxLayout()
        for label, callback in [("Save pin", self.save_favorite), ("Rename", self.rename_place), ("Remove", self.remove_place)]:
            self.button(label, callback, row)
        box.addLayout(row)
        self.refresh_places()
        return page

    def route_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.draw_stops = QCheckBox("Add map clicks as route stops")
        box.addWidget(self.draw_stops)
        self.stop_list = QListWidget()
        self.stop_list.setMaximumHeight(110)
        box.addWidget(self.stop_list)
        for actions in [[("Add pin", self.add_stop), ("Remove", self.remove_stop), ("Clear", self.clear_route)],
                        [("Move up", lambda: self.reorder_stop(-1)), ("Move down", lambda: self.reorder_stop(1)), ("Insert pin", self.insert_stop)],
                        [("Plan segments", self.plan_route), ("All free path", self.use_drawn_route)]]:
            row = QHBoxLayout()
            for label, callback in actions:
                self.button(label, callback, row)
            box.addLayout(row)
        self.segment_list = QListWidget()
        self.segment_list.setMaximumHeight(100)
        self.segment_list.currentRowChanged.connect(self.select_segment)
        box.addWidget(QLabel("Segments between pins"))
        box.addWidget(self.segment_list)
        self.segment_group = QGroupBox("Selected segment")
        editor = QVBoxLayout(self.segment_group)
        self.segment_profile = QComboBox()
        self.segment_profile.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.segment_profile.setMinimumContentsLength(12)
        for key, profile in PROFILES.items():
            self.segment_profile.addItem(profile["label"], key)
        editor.addWidget(self.segment_profile)
        speed_row = QHBoxLayout()
        self.segment_speed_mode = QComboBox()
        self.segment_speed_mode.addItem("Auto speed", "auto")
        self.segment_speed_mode.addItem("Manual speed", "manual")
        self.segment_speed_mode.setToolTip("Auto driving uses mapped speeds/limits and turn geometry. Other profiles or missing road data use an estimate. This does not measure the phone's real driving speed.")
        speed_row.addWidget(self.segment_speed_mode)
        self.segment_speed = QDoubleSpinBox()
        self.segment_speed.setRange(.36, 360)
        self.segment_speed.setDecimals(2)
        self.segment_speed.setSuffix(" km/h")
        speed_row.addWidget(self.segment_speed)
        editor.addLayout(speed_row)
        self.draw_bends = QCheckBox("Map clicks add bends to this segment")
        self.draw_bends.toggled.connect(lambda checked: self.draw_stops.setChecked(False) if checked else None)
        self.draw_stops.toggled.connect(lambda checked: self.draw_bends.setChecked(False) if checked else None)
        editor.addWidget(self.draw_bends)
        bend_row = QHBoxLayout()
        self.button("Add bend", self.add_bend, bend_row).setToolTip("Use the preview pin as an intermediate bend")
        self.button("Undo", self.undo_bend, bend_row).setToolTip("Remove the last bend")
        self.button("Clear", self.clear_bends, bend_row).setToolTip("Remove this segment's bends")
        editor.addLayout(bend_row)
        self.segment_info = QLabel("Select a segment to edit its routing and speed.")
        self.segment_info.setWordWrap(True)
        editor.addWidget(self.segment_info)
        self.segment_group.setEnabled(False)
        for signal in (self.segment_profile.currentIndexChanged, self.segment_speed_mode.currentIndexChanged, self.segment_speed.valueChanged):
            signal.connect(self.update_segment)
        box.addWidget(self.segment_group)
        self.route_options = QComboBox()
        self.route_options.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.route_options.setMinimumContentsLength(12)
        self.route_options.currentIndexChanged.connect(self.choose_route)
        box.addWidget(self.route_options)
        self.route_summary = QLabel("Add two stops, or import a GPX route.")
        self.route_summary.setWordWrap(True)
        box.addWidget(self.route_summary)
        row = QHBoxLayout()
        self.button("Import GPX", self.import_gpx, row)
        self.button("Export GPX", self.export_gpx, row)
        box.addLayout(row)
        row = QHBoxLayout()
        self.button("Save route project", self.save_project, row)
        self.button("Load project", self.load_project, row)
        box.addLayout(row)
        self.loop_route = QCheckBox("Loop route (restart at first point)")
        box.addWidget(self.loop_route)
        row = QHBoxLayout()
        self.button("Start route", self.start_route, row, True).setObjectName("primary")
        self.button("Pause", lambda: self.request("control", "pause"), row, True)
        self.button("Resume", lambda: self.request("control", "resume"), row, True)
        box.addLayout(row)
        self.progress = QLabel("Not following a route")
        box.addWidget(self.progress)
        note = QLabel("Choose a profile per segment. Drag numbered pins or bend markers to reshape it, then Plan segments. Mapped trails/sidewalks/tracks are preferences based on OpenStreetMap; free paths follow your bends exactly.")
        note.setWordWrap(True)
        note.setObjectName("subtitle")
        box.addWidget(note)
        box.addStretch()
        return page

    def movement_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        group = self.group("Joystick / imported GPX speed", box)
        self.travel_mode = QComboBox()
        self.travel_mode.addItems(SPEEDS)
        self.travel_mode.currentTextChanged.connect(self.preset_speed)
        group.addWidget(self.travel_mode)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.36, 360)
        self.speed.setDecimals(2)
        self.speed.setSuffix(" km/h")
        self.speed.setValue(5.04)
        self.speed.valueChanged.connect(self.configure_motion)
        group.addWidget(self.speed)
        self.variation = QCheckBox("Natural speed variation (±10%)")
        self.variation.toggled.connect(self.configure_motion)
        group.addWidget(self.variation)
        group = self.group("Driving stops", box)
        self.traffic_stops = QCheckBox("Auto stops at mapped signs and lights")
        self.traffic_stops.setChecked(self.store.data['settings'].get('traffic_stops', True))
        self.traffic_stops.setToolTip('Stop signs pause for 2–5 seconds. Simulated red lights wait 8–65 seconds. Replan to fetch traffic controls; signal phases are not live.')
        self.traffic_stops.toggled.connect(self.configure_motion)
        group.addWidget(self.traffic_stops)
        self.red_probability = QDoubleSpinBox()
        self.red_probability.setRange(0, 100)
        self.red_probability.setDecimals(0)
        self.red_probability.setSingleStep(5)
        self.red_probability.setSuffix('% of lights red')
        self.red_probability.setValue(self.store.data['settings'].get('red_probability', 45))
        self.red_probability.valueChanged.connect(self.configure_motion)
        group.addWidget(self.red_probability)
        group = self.group("Joystick", box)
        directions = QGridLayout()
        for label, x, y, row, col in [("↖", -1, 1, 0, 0), ("↑", 0, 1, 0, 1), ("↗", 1, 1, 0, 2),
                ("←", -1, 0, 1, 0), ("→", 1, 0, 1, 2), ("↙", -1, -1, 2, 0), ("↓", 0, -1, 2, 1), ("↘", 1, -1, 2, 2)]:
            button = QPushButton(label)
            button.setMinimumHeight(44)
            button.pressed.connect(lambda east=x, north=y: self.joystick(east, north))
            button.released.connect(lambda: self.joystick(0, 0))
            directions.addWidget(button, row, col)
        hold = QPushButton("■")
        hold.clicked.connect(lambda: self.request("control", "hold"))
        directions.addWidget(hold, 1, 1)
        group.addLayout(directions)
        self.keyboard = QCheckBox("Enable WASD / arrow keys")
        self.keyboard.toggled.connect(self.release_keys)
        group.addWidget(self.keyboard)
        note = QLabel("Set a location first. Hold a direction to move; release to hold position. Keyboard controls stay off while typing or editing coordinates.")
        note.setWordWrap(True)
        group.addWidget(note)
        box.addStretch()
        return page

    def settings_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        group = self.group("Connection and recovery", box)
        self.reconnect = QCheckBox("Reconnect the same iPhone after a drop")
        self.reconnect.setChecked(self.store.data["settings"].get("reconnect", True))
        self.reconnect.toggled.connect(self.configure_motion)
        group.addWidget(self.reconnect)
        self.notifications = QCheckBox("Notify when a connection drops")
        self.notifications.setChecked(self.store.data["settings"].get("notifications", True))
        group.addWidget(self.notifications)
        label = QLabel("After reconnecting, routes remain paused. Saved sessions never restart automatically after reopening Locus.")
        label.setWordWrap(True)
        group.addWidget(label)
        group = self.group("Map services", box)
        group.addWidget(QLabel("Photon search endpoint"))
        self.photon = QLineEdit(self.store.data["settings"].get("photon", PHOTON))
        group.addWidget(self.photon)
        group.addWidget(QLabel("Valhalla route endpoint"))
        self.valhalla = QLineEdit(self.store.data["settings"].get("valhalla", VALHALLA))
        group.addWidget(self.valhalla)
        group.addWidget(QLabel('Overpass endpoint (larger segments)'))
        self.overpass = QLineEdit(self.store.data['settings'].get('overpass', OVERPASS))
        group.addWidget(self.overpass)
        self.button("Save settings", self.save_settings, group)
        for text in ["Search sends text to Photon. Route/speed lookup sends paths to Valhalla. Stop lookup sends road/node IDs to OpenStreetMap for small segments, or to Overpass for larger ones. Map views fetch OpenStreetMap tiles. Phone identifiers stay local.",
                     "Appearance follows your Qt desktop colors and font. iOS 17.4+ and Developer Mode are required."]:
            label = QLabel(text)
            label.setWordWrap(True)
            group.addWidget(label)
        box.addStretch()
        return page

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
            ("3 · Pick a location", "Search, click the map or enter coordinates. These only preview a pin. Set location applies it. Verify the result in Maps on the phone."),
            ("4 · Move or connect wirelessly", "Use Move for the joystick. In Route, set a profile and Auto/manual speed for each pin-to-pin segment, add or drag bends, then Plan segments. Wi-Fi: restore first, enable it over USB, unplug and scan in Wi-Fi mode."),
            ("5 · Restore before leaving", "Restore real location before disconnecting or quitting. If the connection drops, the location is unknown. Reconnect the same phone to restore; a phone restart may be needed if simulation stays stuck.")]:
            heading = QLabel(title)
            heading.setStyleSheet("font-weight: 600;")
            label = QLabel(body)
            label.setWordWrap(True)
            layout.addWidget(heading)
            layout.addWidget(label)
        button = QPushButton("Got it")
        button.clicked.connect(self.setup_dialog.accept)
        layout.addWidget(button)
        def seen():
            self.store.data["settings"]["setup_seen"] = True
            self.store.save()
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
            self.status.setText(("Unresolved session: select its iPhone and Restore location. " if recovery else "") +
                ("Select your phone, then Prepare iPhone." if result else "No iPhone found. Connect and unlock it; for Wi-Fi, enable it over USB first and use the same network."))
        elif operation == "search":
            self.populate(self.results, result)
            self.status.setText(f"{len(result)} places found. Select one to preview.")
        elif operation == "mixed_route":
            try:
                if self.route_plan is not self.planning_document:
                    raise ValueError("Route changed while planning. Plan again to use your edits.")
                self.route_plan.accept(result, self.planning_revision)
                self.stops_changed()
                self.js("fitRoute()")
                self.status.setText("Segments planned. Select a colored segment to change its profile, bends, speed or alternative.")
            except ValueError as error:
                self.status.setText(str(error))
        elif operation == "set_mode":
            self.devices.clear()
            self.status.setText(result)
            self.perform("discover")
        else:
            self.status.setText(result)
            if operation == "apply" and self.pending_recent:
                self.store.add("recents", **self.pending_recent)
                self.refresh_places()
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

    def motion_changed(self, state):
        self.motion = state
        self.connection_badge.setText(("Demo session" if self.demo else "Location active") if state["connected"] else "Location unknown / inactive")
        self.connection_badge.setStyleSheet(f"color: {self.colors['accent'] if state['connected'] else self.colors['muted']};")
        self.js("setAcknowledged", state["position"], state["connected"])
        self.progress.setText(f"{'Paused / holding' if state['paused'] else 'Following route'} · {state['progress'] / 1000:.2f} / {state['total'] / 1000:.2f} km · {state.get('speed_kph', 0):.1f} km/h\n{state.get('speed_source', '')}" if state["total"] else "Not following a route")
        if state.get('traffic_stop'):
            stop = state['traffic_stop']
            label = 'Stop sign' if stop['kind'] == 'stop_sign' else 'Simulated red light'
            self.connection_badge.setText(f"{label} · {stop['remaining']:.0f}s")
            self.progress.setText(self.progress.text() + f"\n{label}: {stop['remaining']:.1f}s remaining")

    def configure_motion(self, *unused):
        if self.ready:
            self.worker.submit("configure", self.speed.value() / 3.6, self.variation.isChecked(), self.reconnect.isChecked())
            self.worker.submit('configure_traffic', self.traffic_stops.isChecked(), self.red_probability.value() / 100)

    def preset_speed(self, mode):
        if hasattr(self, "speed"):
            self.speed.setValue(SPEEDS[mode] * 3.6)

    def joystick(self, east, north):
        if self.ready and not self.busy:
            self.joystick_engaged = bool(east or north)
            self.worker.submit("joystick", east, north)

    def release_keys(self, *unused):
        if self.keys or self.joystick_engaged:
            self.keys.clear()
            self.joystick(0, 0)

    def focus_changed(self, previous, current):
        # A held key may be released in a text field after focus moves. Stop
        # immediately rather than waiting for that field to forward KeyRelease.
        self.release_keys()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.ApplicationDeactivate, QEvent.Type.WindowDeactivate, QEvent.Type.FocusOut):
            self.release_keys()
        # These filters are installed only on this window/map. Qt delivers
        # physical keys to the focused window; deactivation releases movement.
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease) and self.keyboard.isChecked():
            if isinstance(QApplication.focusWidget(), (QLineEdit, QDoubleSpinBox, QComboBox)):
                return False
            vectors = {Qt.Key.Key_W: (0, 1), Qt.Key.Key_Up: (0, 1), Qt.Key.Key_A: (-1, 0), Qt.Key.Key_Left: (-1, 0),
                       Qt.Key.Key_S: (0, -1), Qt.Key.Key_Down: (0, -1), Qt.Key.Key_D: (1, 0), Qt.Key.Key_Right: (1, 0)}
            key = event.key()
            if key in vectors and not event.isAutoRepeat():
                if event.type() == QEvent.Type.KeyPress:
                    self.keys.add(key)
                else:
                    self.keys.discard(key)
                self.joystick(max(-1, min(1, sum(vectors[k][0] for k in self.keys))), max(-1, min(1, sum(vectors[k][1] for k in self.keys))))
                return True
        return super().eventFilter(watched, event)

    def change_transport(self):
        if self.ready:
            self.request("set_mode", self.connection.currentData())

    def pick(self, latitude, longitude):
        self.latitude.setValue(latitude)
        self.longitude.setValue(longitude)
        self.selected_name = ""
        if self.draw_bends.isChecked():
            self.add_bend()
        elif self.draw_stops.isChecked():
            self.add_stop()

    def js(self, function, *args):
        self.map.page().runJavaScript(function if not args else function + "(" + ",".join(json.dumps(arg) for arg in args) + ")")

    def preview(self):
        self.js("setPin", self.latitude.value(), self.longitude.value())

    def map_ready(self, ok):
        if ok:
            if self.map.focusProxy():
                self.map.focusProxy().installEventFilter(self)
            self.js("setTheme", self.colors)
            self.map_options()
            self.redraw_route()

    def map_options(self, *unused):
        if hasattr(self, "map"):
            self.js("setOptions", self.map_style.currentIndex() == 0, self.follow.isChecked())

    def refresh_theme(self):
        colors = read_colors()
        config = Path.home() / ".config/qt6ct/qt6ct.conf"
        stamp = config.stat().st_mtime_ns if config.exists() else 0
        if colors != self.colors or stamp != self.font_stamp:
            self.font_stamp = stamp
            self.colors = apply_theme(QApplication.instance(), colors)
            self.js("setTheme", colors)

    def search_places(self):
        self.request("search", self.search.text(), self.store.data["settings"].get("photon", PHOTON))

    def populate(self, widget, places):
        widget.clear()
        for place in places:
            widget.addItem(place["name"])
            widget.item(widget.count() - 1).setData(Qt.ItemDataRole.UserRole, place)

    def refresh_places(self):
        for key, widget in self.saved.items():
            self.populate(widget, self.store.data[key])

    def choose_place(self, place):
        self.pick(*place["point"])
        self.selected_name = place["name"]
        self.preview()

    def save_favorite(self):
        name, ok = QInputDialog.getText(self, "Save favorite", "Place name", text=self.selected_name)
        if ok:
            self.store.add("favorites", name, (self.latitude.value(), self.longitude.value()))
            self.refresh_places()

    def selected_saved(self):
        key = "favorites" if self.saved_tabs.currentIndex() == 0 else "recents"
        return key, self.saved[key].currentRow()

    def rename_place(self):
        key, index = self.selected_saved()
        if index >= 0:
            name, ok = QInputDialog.getText(self, "Rename place", "Place name", text=self.store.data[key][index]["name"])
            if ok and name.strip():
                self.store.rename(key, index, name)
                self.refresh_places()

    def remove_place(self):
        key, index = self.selected_saved()
        if index >= 0:
            self.store.remove(key, index)
            self.refresh_places()

    def editable_route(self):
        if self.busy and self.pending_operation != "mixed_route":
            self.status.setText("Wait for the current phone operation before editing the route.")
            return False
        if self.motion.get("mode") == "route" and self.ready and self.worker.session.journal.exists():
            self.status.setText("Hold / stop movement before editing this route.")
            return False
        return True

    def add_stop(self):
        if self.editable_route():
            try:
                mode = self.travel_mode.currentText()
                default = {"Drive": "car_roads", "Cycle": "cycle_paths", "Run": "run_paths"}.get(mode, "walk_sidewalks")
                self.route_plan.add((self.latitude.value(), self.longitude.value()), default)
                self.stops_changed()
            except ValueError as error:
                self.status.setText(str(error))

    def remove_stop(self):
        index = self.stop_list.currentRow()
        if index >= 0 and self.editable_route():
            self.route_plan.remove(index)
            self.stops_changed()

    def insert_stop(self):
        if self.editable_route():
            try:
                selected = self.stop_list.currentRow()
                index = selected + 1 if selected >= 0 else len(self.stops)
                default = {"Drive": "car_roads", "Cycle": "cycle_paths", "Run": "run_paths"}.get(self.travel_mode.currentText(), "walk_sidewalks")
                self.route_plan.insert(index, (self.latitude.value(), self.longitude.value()), default)
                self.stops_changed()
                self.stop_list.setCurrentRow(index)
            except ValueError as error:
                self.status.setText(str(error))

    def move_stop(self, index, latitude, longitude):
        from .engine import coordinates
        if not self.editable_route():
            self.redraw_route()
            return
        if 0 <= index < len(self.stops):
            try:
                self.route_plan.move(index, coordinates(latitude, longitude))
                self.stops_changed()
            except ValueError as error:
                self.status.setText(str(error))

    def reorder_stop(self, direction):
        index, target = self.stop_list.currentRow(), self.stop_list.currentRow() + direction
        if self.editable_route() and index >= 0 and 0 <= target < len(self.stops):
            self.route_plan.reorder(index, target)
            self.stops_changed()
            self.stop_list.setCurrentRow(target)

    def clear_route(self):
        if self.editable_route():
            self.route_plan.reset([])
            self.stops_changed()

    def stops_changed(self):
        selected = max(0, self.segment_list.currentRow())
        self.stops = self.route_plan.points
        self.imported_track = False
        self.stop_list.clear()
        for i, point in enumerate(self.stops):
            self.stop_list.addItem(f"{i + 1:02d}   {point[0]:.5f}, {point[1]:.5f}")
        self.segment_list.blockSignals(True)
        self.segment_list.clear()
        for i in range(len(self.stops) - 1):
            spec = self.route_plan.spec(i)
            ready = bool(self.route_plan.results.get(self.route_plan.key(i)))
            self.segment_list.addItem(f"{i + 1} → {i + 2} · {PROFILES[spec['profile']]['label']} · {'ready' if ready else 'needs plan'}")
        if self.segment_list.count():
            self.segment_list.setCurrentRow(min(selected, self.segment_list.count() - 1))
        self.segment_list.blockSignals(False)
        self.select_segment(self.segment_list.currentRow())
        self.rebuild_route()

    def select_segment(self, index):
        valid = 0 <= index < len(self.stops) - 1
        self.segment_group.setEnabled(valid)
        self.segment_loading = True
        self.route_options.blockSignals(True)
        self.route_options.clear()
        self.alternatives = []
        if valid:
            spec = self.route_plan.spec(index)
            self.segment_profile.setCurrentIndex(self.segment_profile.findData(spec["profile"]))
            self.segment_speed_mode.setCurrentIndex(self.segment_speed_mode.findData(spec["speed_mode"]))
            self.segment_speed.setValue(spec["speed_kph"])
            self.segment_speed.setEnabled(spec["speed_mode"] == "manual")
            self.segment_speed.setVisible(spec["speed_mode"] == "manual")
            key = self.route_plan.key(index)
            self.alternatives = self.route_plan.results.get(key, [])
            for i, option in enumerate(self.alternatives):
                self.route_options.addItem(f"Path {i + 1} · {option['distance'] / 1000:.2f} km · ~{option['seconds'] / 60:.1f} min")
            self.route_options.setCurrentIndex(self.route_plan.selected.get(key, 0))
            text = f"{len(spec['controls'])} bends · "
            if self.alternatives:
                option = self.alternatives[self.route_plan.selected.get(key, 0)]
                low, high = min(option["speeds"]) * 3.6, max(option["speeds"]) * 3.6
                text += f"targets {low:.1f}–{high:.1f} km/h\n"
                text += option.get("speed_warning", option["sources"][0])
                signs = sum(stop['kind'] == 'stop_sign' for stop in option.get('stops', []))
                lights = sum(stop['kind'] == 'traffic_light' for stop in option.get('stops', []))
                text += f'\n{signs} stop signs · {lights} traffic lights'
                if option.get('traffic_warning'):
                    text += '\n' + option['traffic_warning']
            else:
                text += "Needs planning. Auto driving uses road data; free off-road uses an estimated speed."
            self.segment_info.setText(text)
        self.route_options.blockSignals(False)
        self.segment_loading = False
        self.redraw_route()

    def update_segment(self, *unused):
        index = self.segment_list.currentRow()
        if self.segment_loading or index < 0:
            return
        if not self.editable_route():
            self.select_segment(index)
            return
        old = self.route_plan.spec(index)
        spec = {**old, "profile": self.segment_profile.currentData(), "speed_mode": self.segment_speed_mode.currentData(), "speed_kph": self.segment_speed.value()}
        if old["profile"] != spec["profile"] and spec["speed_mode"] == "auto":
            spec["speed_kph"] = PROFILES[spec["profile"]]["speed"]
        try:
            self.route_plan.update(index, spec)
            self.stops_changed()
        except ValueError as error:
            self.status.setText(str(error))
            self.select_segment(index)

    def add_bend(self):
        index = self.segment_list.currentRow()
        if index < 0 or not self.editable_route():
            return
        spec = self.route_plan.spec(index)
        spec["controls"].append((self.latitude.value(), self.longitude.value()))
        self.change_bends(index, spec)

    def undo_bend(self):
        index = self.segment_list.currentRow()
        if index >= 0 and self.editable_route():
            spec = self.route_plan.spec(index)
            spec["controls"] = spec["controls"][:-1]
            self.change_bends(index, spec)

    def clear_bends(self):
        index = self.segment_list.currentRow()
        if index >= 0 and self.editable_route():
            self.change_bends(index, {**self.route_plan.spec(index), "controls": []})

    def move_bend(self, segment, bend, latitude, longitude):
        from .engine import coordinates
        if not self.editable_route():
            self.redraw_route()
            return
        if 0 <= segment < len(self.stops) - 1:
            spec = self.route_plan.spec(segment)
            if 0 <= bend < len(spec["controls"]):
                try:
                    spec["controls"][bend] = coordinates(latitude, longitude)
                    self.change_bends(segment, spec)
                except ValueError as error:
                    self.status.setText(str(error))

    def change_bends(self, index, spec):
        try:
            self.route_plan.update(index, spec)
            self.stops_changed()
        except ValueError as error:
            self.status.setText(str(error))

    def delete_bend(self, segment, bend):
        if self.editable_route() and 0 <= segment < len(self.stops) - 1:
            spec = self.route_plan.spec(segment)
            if 0 <= bend < len(spec["controls"]):
                spec["controls"].pop(bend)
                self.change_bends(segment, spec)

    def select_map_segment(self, index):
        if 0 <= index < self.segment_list.count():
            self.sidebar_toggle.setChecked(True)
            self.tabs.setCurrentIndex(1)
            self.segment_list.setCurrentRow(index)

    def rebuild_route(self):
        self.route_points, self.route_profile = [], None
        try:
            combined = self.route_plan.assemble()
            self.route_points = combined.pop("points")
            self.route_profile = combined
            self.show_route_summary("Mixed route")
        except ValueError:
            self.route_summary.setText("Choose a profile for each segment, then Plan segments.")
            self.redraw_route()

    def redraw_route(self):
        self.js("setRoute", self.route_points, self.stops, [] if self.imported_track else self.route_plan.segments(), self.segment_list.currentRow())

    def use_drawn_route(self):
        if self.editable_route():
            profile = "offroad_car" if self.travel_mode.currentText() == "Drive" else "offroad_walk"
            for index in range(len(self.stops) - 1):
                self.route_plan.update(index, {**self.route_plan.spec(index), "profile": profile})
            self.stops_changed()
            self.plan_route()

    def show_route_summary(self, source):
        route = Route(self.route_points)
        self.route_summary.setText(f"{source} · {route.total / 1000:.2f} km · {len(route.points)} points")
        self.redraw_route()

    def plan_route(self):
        if not self.busy and self.editable_route():
            self.planning_revision = self.route_plan.revision
            self.planning_document = self.route_plan
            self.request("mixed_route", self.route_plan.requests(), self.store.data["settings"].get("valhalla", VALHALLA), self.store.data['settings'].get('overpass', OVERPASS), self.traffic_stops.isChecked())

    def choose_route(self, index):
        if 0 <= index < len(self.alternatives) and self.editable_route():
            segment = self.segment_list.currentRow()
            if segment >= 0:
                self.route_plan.select(segment, index)
                self.rebuild_route()
                self.select_segment(segment)

    def start_route(self):
        if len(self.route_points) < 2:
            self.status.setText("Plan the segments, or import a GPX path, before starting a route.")
            return
        self.configure_motion()
        self.request("start_route", self.devices.currentData(), self.route_points.copy(), self.loop_route.isChecked(), self.route_profile)

    def save_project(self):
        try:
            if self.imported_track:
                raise ValueError("Use Export GPX to save this imported track; route projects save editable pin-to-pin settings.")
            data = self.route_plan.serialize()
            if len(data["pins"]) < 2:
                raise ValueError("Add at least two pins before saving a project")
            path, _ = QFileDialog.getSaveFileName(self, "Save editable route", "route.locus.json", "Locus route (*.locus.json)")
            if path:
                Path(path).write_text(json.dumps(data, indent=2))
                self.status.setText("Route project saved with segment profiles, bends and speed choices.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def load_project(self):
        if not self.editable_route():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load editable route", "", "Locus route (*.locus.json)")
        if path:
            try:
                if Path(path).stat().st_size > 8_000_000:
                    raise ValueError("Route project is too large")
                plan = RoutePlan.deserialize(json.loads(Path(path).read_text()))
                self.route_plan = plan
                self.stops_changed()
                self.status.setText("Route project loaded. Plan segments to rebuild the paths.")
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                self.status.setText(f"Could not load project: {error}")

    def import_gpx(self):
        if not self.editable_route():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import route", "", "GPX routes (*.gpx)")
        if path:
            try:
                if Path(path).stat().st_size > 8_000_000:
                    raise ValueError("GPX is too large (8 MB limit).")
                points = parse_gpx(Path(path).read_bytes())
                self.route_plan.reset([points[0], points[-1]])
                self.stops_changed()
                self.route_points = points
                self.route_profile = None
                self.imported_track = True
                self.show_route_summary("Imported GPX")
                self.js("fitRoute()")
            except (OSError, ValueError, KeyError, ET.ParseError) as error:
                self.status.setText(f"Could not import GPX: {error}")

    def export_gpx(self):
        try:
            data = export_gpx(self.route_points)
            path, _ = QFileDialog.getSaveFileName(self, "Export route", "locus-route.gpx", "GPX routes (*.gpx)")
            if path:
                Path(path).write_bytes(data)
                self.status.setText("GPX route exported.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

    def save_settings(self):
        try:
            self.store.data["settings"].update(photon=endpoint(self.photon.text().strip()), valhalla=endpoint(self.valhalla.text().strip()),
                reconnect=self.reconnect.isChecked(), notifications=self.notifications.isChecked(), travel_mode=self.travel_mode.currentText(),
                speed_kph=self.speed.value(), variation=self.variation.isChecked(), night_map=self.map_style.currentIndex() == 0, follow=self.follow.isChecked(),
                traffic_stops=self.traffic_stops.isChecked(), red_probability=self.red_probability.value(), overpass=endpoint(self.overpass.text().strip()))
            self.store.save()
            self.configure_motion()
            self.status.setText("Settings saved.")
        except (OSError, ValueError) as error:
            self.status.setText(str(error))

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
    if not lock.tryLock(0):
        QMessageBox.information(None, "Locus is already open", "Switch to the existing Locus window to manage your iPhone.")
        return 1
    window = Window(options.demo)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
