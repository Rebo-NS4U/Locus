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
    QLabel, QPushButton, QListWidgetItem, QComboBox, QDoubleSpinBox, QMessageBox, QLineEdit, QListWidget,
    QTabWidget, QGroupBox, QCheckBox, QSplitter, QFileDialog, QInputDialog,
    QSystemTrayIcon, QScrollArea, QGridLayout, QDialog, QSizePolicy, QToolButton, QMenu)
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from shiboken6 import delete
from .map_profile import create_map_profile

from .engine import Session, DemoTransport
from .device import IPhoneTransport
from .routes import SPEEDS, Route, parse_gpx, export_gpx
from .storage import Places
from .library import RouteLibrary
from .services import Services, PHOTON, VALHALLA, OVERPASS, endpoint
from .theme import apply_theme, read_colors
from .units import to_mph, to_kph, speed_text, METRES_PER_MILE
from .planning import RoutePlan, PROFILES, TRAVEL_MODES, travel_mode, automatic_specification
from .activation import activate_existing, listen_for_activation


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

    @Slot(float, float)
    def pick(self, latitude, longitude):
        self.picked.emit(latitude, longitude)

    @Slot(int, float, float)
    def moveStop(self, index, latitude, longitude):
        self.stop_moved.emit(index, latitude, longitude)

    @Slot(int)
    def selectLeg(self, index):
        self.segment_selected.emit(index)



class StatusLabel(QLabel):
    def setText(self, message):
        self.setToolTip(message)
        super().setText(message)


class Window(QMainWindow):
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
        self.map_loaded = False
        self.auto_plan_timer = QTimer(self)
        self.auto_plan_timer.setSingleShot(True)
        self.auto_plan_timer.setInterval(600)
        self.auto_plan_timer.timeout.connect(self.refresh_automatic_route)
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
        self.speed.setValue(to_mph(preferences.get("speed_kph", to_kph(self.speed.value()))))
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
        if self.route_library.data.get('draft'):
            draft = dict(self.route_library.data)
            self.route_title.setText(draft.get('draft_name') or 'Recovered draft')
            identifier = draft.get('draft_route_id')
            self.active_route_id = identifier if any(item['id'] == identifier for item in draft['routes']) else None
            self.apply_route_document(draft['draft'])
        if self.route_library.error:
            self.status.setText(self.route_library.error)
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
        root.setObjectName("shell")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 10)
        layout.setSpacing(10)
        toolbar = QWidget()
        toolbar.setObjectName("toolbar")
        header = QHBoxLayout(toolbar)
        header.setContentsMargins(12, 8, 12, 8)
        header.setSpacing(8)
        title = QLabel("Locus · Demo" if self.demo else "Locus")
        title.setObjectName("brand")
        title.setToolTip("Locus for Linux" + (" · DEMO: no phone commands" if self.demo else ""))
        header.addWidget(title)
        self.sidebar_toggle = QPushButton("Controls")
        self.sidebar_toggle.setCheckable(True)
        self.sidebar_toggle.setToolTip("Show/hide places, routes and movement controls (Ctrl+B)")
        header.addWidget(self.sidebar_toggle)
        self.sidebar_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        self.sidebar_shortcut.activated.connect(self.sidebar_toggle.toggle)
        self.connection_badge = StatusLabel("Not connected")
        self.connection_badge.setFixedWidth(240)
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
        layout.addWidget(toolbar)
        split = QSplitter()
        self.split = split
        split.setHandleWidth(12)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("inspector")
        self.tabs.setMinimumWidth(360)
        self.tabs.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
        self.tabs.setStyleSheet("QTabBar::tab { padding: 7px 6px; }")
        self.tabs.setUsesScrollButtons(False)
        self.tab_scrolls = []
        for widget, title in [(self.places_tab(), "Places"), (self.route_tab(), "Route"),
                              (self.movement_tab(), "Move"), (self.map_tab(), "Map"), (self.settings_tab(), "Settings")]:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            widget.setObjectName("inspectorPage")
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            scroll.setWidget(widget)
            self.tab_scrolls.append(scroll)
            if title == "Route":
                route_page = QWidget()
                route_layout = QVBoxLayout(route_page)
                route_layout.setContentsMargins(0, 0, 0, 0)
                route_layout.setSpacing(4)
                route_layout.addWidget(scroll, 1)
                route_layout.addWidget(self.route_footer)
                self.tabs.addTab(route_page, title)
            else:
                self.tabs.addTab(scroll, title)
        self.sidebar_width = 380
        self.tabs.hide()
        split.addWidget(self.tabs)
        self.map = QWebEngineView()
        self.map_profile = create_map_profile(self.map, demo=self.demo)
        self.map.setPage(QWebEnginePage(self.map_profile, self.map))
        QApplication.instance().aboutToQuit.connect(self.release_map)
        self.map.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        self.channel = QWebChannel(self.map.page())
        self.bridge = MapBridge()
        self.bridge.picked.connect(self.pick)
        self.bridge.stop_moved.connect(self.move_stop)
        self.bridge.segment_selected.connect(self.select_map_segment)
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
        statusbar = QWidget()
        statusbar.setObjectName("statusbar")
        footer = QHBoxLayout(statusbar)
        footer.setContentsMargins(10, 0, 10, 0)
        self.status = StatusLabel("Starting…")
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.status.setMaximumHeight(26)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setObjectName("status")
        footer.addWidget(self.status, 1)
        footer.addWidget(self.connection_badge)
        layout.addWidget(statusbar)
        self.setCentralWidget(root)

    @property
    def sidebar_scroll(self):
        return self.tab_scrolls[self.tabs.currentIndex()]

    def toggle_sidebar(self, visible):
        self.release_keys()
        if not visible and self.tabs.isVisible():
            self.sidebar_width = self.split.sizes()[0]
        self.tabs.setVisible(visible)
        if visible:
            self.split.setSizes([self.sidebar_width, max(400, self.split.width() - self.sidebar_width - 4)])
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
        self.button("Center map", self.preview, fields)
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
        legend = QLabel("Numbered pins mark route stops. Red STOP signs and traffic-light icons show detected driving controls after planning a mapped car route. Their tooltips describe the simulated stop. The solid dot marks the last acknowledged command, not an independent GPS reading. Verify the result in Maps on your iPhone.")
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
        box.setContentsMargins(4, 0, 4, 4)
        box.setSpacing(6)
        heading = QLabel('Build your next route')
        heading.setObjectName('pageTitle')
        box.addWidget(heading)
        hint = QLabel('Choose your stops. Let Locus find the way.')
        hint.setObjectName('subtitle')
        hint.setWordWrap(True)
        box.addWidget(hint)
        self.route_title = QLineEdit()
        self.route_title.setPlaceholderText('Name this route…')
        self.route_title.setAccessibleName('Route name')
        self.route_title.setObjectName('routeName')
        self.route_title.editingFinished.connect(self.remember_route_draft)
        box.addWidget(self.route_title)
        actions = QHBoxLayout()
        self.button('Save route', self.save_library_route, actions).setObjectName('primary')
        self.library_toggle = QPushButton('Saved routes')
        self.library_toggle.setCheckable(True)
        actions.addWidget(self.library_toggle)
        self.button('New', self.new_library_route, actions)
        box.addLayout(actions)
        self.library_panel = QGroupBox('Your library')
        library_layout = QVBoxLayout(self.library_panel)
        self.route_search = QLineEdit()
        self.route_search.setPlaceholderText('Find a saved route…')
        self.route_search.textChanged.connect(self.refresh_route_library)
        library_layout.addWidget(self.route_search)
        self.library_list = QListWidget()
        self.library_list.setMinimumHeight(200)
        self.library_list.setMaximumHeight(300)
        self.library_list.itemDoubleClicked.connect(lambda item: self.open_library_route())
        library_layout.addWidget(self.library_list)
        self.library_empty = QLabel('Save a route to keep it here.')
        self.library_empty.setObjectName('subtitle')
        self.library_empty.setWordWrap(True)
        library_layout.addWidget(self.library_empty)
        library_actions = QHBoxLayout()
        self.button('Open', self.open_library_route, library_actions)
        self.button('Rename', self.rename_library_route, library_actions)
        self.button('Delete', self.delete_library_route, library_actions)
        library_layout.addLayout(library_actions)
        self.library_toggle.toggled.connect(self.library_panel.setVisible)
        self.library_panel.hide()
        box.addWidget(self.library_panel)
        self.refresh_route_library()
        stops_box = self.group("Stops", box)
        stops_box.addWidget(QLabel("Click the map to add a stop"))
        self.route_stop_card = stops_box.parentWidget()
        self.route_list = QListWidget()
        self.route_list.setFixedHeight(210)
        self.route_list.setToolTip("Stops and their connecting segments in route order. Select a stop to insert, remove or reorder it; select a segment to choose Walking, Driving or Cycling.")
        self.route_list.currentRowChanged.connect(lambda: self.select_segment(self.selected_segment()))
        stops_box.addWidget(self.route_list)
        row = QHBoxLayout()
        for label, callback, tip in [
                ("Add", self.add_stop, "Append the selected coordinates as a stop"),
                ("Insert", self.insert_stop, "Insert the selected coordinates after the selected stop"),
                ("Remove", self.remove_stop, "Remove the selected stop")]:
            self.button(label, callback, row).setToolTip(tip)
        for label, direction in [("↑", -1), ("↓", 1)]:
            button = QToolButton()
            button.setText(label)
            button.setFixedWidth(28)
            button.setStyleSheet("padding: 7px 0;")
            button.setToolTip("Move selected stop " + ("up" if direction < 0 else "down"))
            button.setAccessibleName(button.toolTip())
            button.clicked.connect(lambda checked=False, step=direction: self.reorder_stop(step))
            row.addWidget(button)
        stops_box.addLayout(row)
        self.segment_group = QGroupBox("Travel between stops")
        editor = QVBoxLayout(self.segment_group)
        editor.addWidget(QLabel("Travel mode"))
        self.segment_travel = QComboBox()
        self.segment_travel.addItems(list(TRAVEL_MODES))
        self.segment_travel.setToolTip("Choose Walking, Driving or Cycling for this segment")
        editor.addWidget(self.segment_travel)
        self.segment_travel.currentTextChanged.connect(self.change_segment_travel)
        speed_row = QHBoxLayout()
        self.segment_speed_mode = QComboBox()
        self.segment_speed_mode.addItem("Auto speed", "auto")
        self.segment_speed_mode.addItem("Manual speed", "manual")
        self.segment_speed_mode.setToolTip("Auto driving uses mapped speeds/limits and turn geometry. Other profiles or missing road data use an estimate. This does not measure the phone's real driving speed.")
        speed_row.addWidget(self.segment_speed_mode)
        self.segment_speed = QDoubleSpinBox()
        self.segment_speed.setRange(.23, 223.69)
        self.segment_speed.setDecimals(2)
        self.segment_speed.setSuffix(" mph")
        speed_row.addWidget(self.segment_speed)
        editor.addLayout(speed_row)
        self.segment_info = QLabel("Select a segment to edit its routing and speed.")
        self.segment_info.setWordWrap(True)
        editor.addWidget(self.segment_info)
        self.segment_group.setEnabled(False)
        for signal in (self.segment_speed_mode.currentIndexChanged, self.segment_speed.valueChanged):
            signal.connect(self.update_segment)
        box.addWidget(self.segment_group)
        self.route_options = QComboBox()
        self.route_options.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.route_options.setMinimumContentsLength(12)
        self.route_options.currentIndexChanged.connect(self.choose_route)
        self.route_options.setPlaceholderText("Plan to compare paths")
        self.route_options.setToolTip("Alternative paths for the selected segment")
        editor.addWidget(self.route_options)
        note = QLabel("Add or drag stops and choose Walking, Driving or Cycling. Paths are chosen automatically.")
        note.setWordWrap(True)
        note.setObjectName("subtitle")
        box.addWidget(note)
        self.library_toggle.toggled.connect(lambda visible: self.route_stop_card.setVisible(not visible))
        self.library_toggle.toggled.connect(lambda visible: self.segment_group.setVisible(not visible))
        self.library_toggle.toggled.connect(lambda visible: note.setVisible(not visible))
        self.library_toggle.toggled.connect(lambda visible: heading.setText('Your route library' if visible else 'Build your next route'))
        self.library_toggle.toggled.connect(lambda visible: hint.setText('Saved locally. Ready for your next trip.' if visible else 'Choose your stops. Let Locus find the way.'))
        self.save_route_shortcut = QShortcut(QKeySequence.StandardKey.Save, self)
        self.save_route_shortcut.activated.connect(self.save_library_route)
        # Keep planning and playback reachable while the editor scrolls.
        self.route_footer = QWidget()
        footer = QVBoxLayout(self.route_footer)
        footer.setContentsMargins(4, 4, 4, 0)
        footer.setSpacing(4)
        row = QHBoxLayout()
        self.plan_button = self.button("Plan route", self.plan_route, row)
        self.plan_button.setObjectName("primary")
        self.plan_button.setEnabled(False)
        self.plan_button.setToolTip("Choose paths automatically for each segment’s travel mode")
        self.route_file_button = QToolButton()
        self.route_file_button.setText("Route ⋯")
        self.route_file_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.route_menu = QMenu(self.route_file_button)
        for label, callback in [("Import GPX…", self.import_gpx), ("Export GPX…", self.export_gpx),
                                ("Load project…", self.load_project), ("Export project…", self.save_project)]:
            self.route_menu.addAction(label, callback)
        self.route_menu.addSeparator()
        self.route_menu.addAction("Save a copy to library", lambda: self.save_library_route(as_copy=True))
        self.route_menu.addAction("Clear route", self.clear_route)
        self.route_file_button.setMenu(self.route_menu)
        row.addWidget(self.route_file_button)
        footer.addLayout(row)
        self.route_summary = StatusLabel("Add two stops, or import GPX from Route ⋯.")
        self.route_summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.route_summary.setFixedHeight(22)
        footer.addWidget(self.route_summary)
        self.loop_route = QCheckBox("Loop route")
        self.loop_route.setToolTip("Restart at the first point after reaching the last stop")
        footer.addWidget(self.loop_route)
        row = QHBoxLayout()
        self.button("Start", self.start_route, row, True).setObjectName("primary")
        self.button("Pause", lambda: self.request("control", "pause"), row, True)
        self.button("Resume", lambda: self.request("control", "resume"), row, True)
        footer.addLayout(row)
        self.progress = StatusLabel("Not following a route")
        self.progress.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.progress.setFixedHeight(self.progress.fontMetrics().lineSpacing() * 3 + 4)
        footer.addWidget(self.progress)
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
        self.speed.setRange(.23, 223.69)
        self.speed.setDecimals(2)
        self.speed.setSuffix(" mph")
        self.speed.setValue(to_mph(5.04))
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
        self.progress.setText(f"{'Paused / holding' if state['paused'] else 'Following route'} · {state['progress'] / METRES_PER_MILE:.2f} / {state['total'] / METRES_PER_MILE:.2f} mi · {to_mph(state.get('speed_kph', 0)):.1f} mph\n{speed_text(state.get('speed_source', ''))}" if state["total"] else "Not following a route")
        if state.get('traffic_stop'):
            stop = state['traffic_stop']
            label = 'Stop sign' if stop['kind'] == 'stop_sign' else 'Simulated red light'
            self.connection_badge.setText(f"{label} · {stop['remaining']:.0f}s")
            self.progress.setText(self.progress.text() + f"\n{label}: {stop['remaining']:.1f}s remaining")

    def configure_motion(self, *unused):
        if self.ready:
            self.worker.submit("configure", to_kph(self.speed.value()) / 3.6, self.variation.isChecked(), self.reconnect.isChecked())
            self.worker.submit('configure_traffic', self.traffic_stops.isChecked(), self.red_probability.value() / 100)

    def preset_speed(self, mode):
        if hasattr(self, "speed"):
            self.speed.setValue(to_mph(SPEEDS[mode] * 3.6))

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

    def select_coordinates(self, latitude, longitude):
        self.latitude.setValue(latitude)
        self.longitude.setValue(longitude)
        self.selected_name = ""

    def pick(self, latitude, longitude):
        self.select_coordinates(latitude, longitude)
        self.add_stop()

    def js(self, function, *args):
        if not self.map_loaded:
            return
        self.map.page().runJavaScript(function if not args else function + "(" + ",".join(json.dumps(arg) for arg in args) + ")")

    def preview(self):
        self.js("focusLocation", self.latitude.value(), self.longitude.value())

    def release_map(self):
        self.map_loaded = False
        # Flush the disk cache on exit; Qt requires pages to die first.
        delete(self.map.page())
        delete(self.map_profile)

    def map_ready(self, ok):
        self.map_loaded = ok
        if ok:
            if self.map.focusProxy():
                self.map.focusProxy().installEventFilter(self)
            self.js("setTheme", self.colors)
            self.map_typography()
            self.map_options()
            self.redraw_route()
            if self.stops:
                self.js("fitRoute()")

    def map_typography(self):
        font = QApplication.instance().font()
        self.js("setTypography", font.family(), font.pointSizeF())

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
            self.map_typography()

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
        self.select_coordinates(*place["point"])
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
                default = {"Drive": "car_roads", "Cycle": "cycle_paths", "Run": "run_paths"}.get(mode, "walk_auto")
                self.route_plan.add((self.latitude.value(), self.longitude.value()), default)
                self.stops_changed(select_newest=True)
            except ValueError as error:
                self.status.setText(speed_text(str(error)))

    def remove_stop(self):
        index = self.selected_stop()
        if index >= 0 and self.editable_route():
            self.route_plan.remove(index)
            self.stops_changed()

    def insert_stop(self):
        if self.editable_route():
            try:
                selected = self.selected_stop()
                index = selected + 1 if selected >= 0 else len(self.stops)
                default = {"Drive": "car_roads", "Cycle": "cycle_paths", "Run": "run_paths"}.get(self.travel_mode.currentText(), "walk_auto")
                self.route_plan.insert(index, (self.latitude.value(), self.longitude.value()), default)
                self.stops_changed(selected_row=max(0, 2 * index - 1))
            except ValueError as error:
                self.status.setText(speed_text(str(error)))

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
                self.status.setText(speed_text(str(error)))

    def reorder_stop(self, direction):
        index, target = self.selected_stop(), self.selected_stop() + direction
        if self.editable_route() and index >= 0 and 0 <= target < len(self.stops):
            self.route_plan.reorder(index, target)
            self.stops_changed()
            self.route_list.setCurrentRow(2 * target)

    def clear_route(self):
        if self.editable_route():
            self.route_plan.reset([])
            self.stops_changed()

    def selected_stop(self):
        row = self.route_list.currentRow()
        return row // 2 if row >= 0 else -1

    def selected_segment(self):
        row = self.route_list.currentRow()
        if row < 0 or len(self.stops) < 2:
            return -1
        return max(0, (row - 1) // 2)

    def stops_changed(self, *, select_newest=False, selected_row=None):
        if selected_row is None:
            selected_row = max(0, self.route_list.currentRow())
        self.stops = self.route_plan.points
        self.plan_button.setEnabled(len(self.stops) >= 2)
        self.imported_track = False
        if select_newest:
            selected_row = max(0, 2 * len(self.stops) - 3)
        self.route_list.blockSignals(True)
        self.route_list.clear()
        for i, point in enumerate(self.stops):
            self.route_list.addItem(f"Stop {i + 1:02d}   {point[0]:.5f}, {point[1]:.5f}")
            if i < len(self.stops) - 1:
                spec = self.route_plan.spec(i)
                ready = bool(self.route_plan.results.get(self.route_plan.key(i)))
                self.route_list.addItem(f"    {i + 1} → {i + 2} · {travel_mode(spec['profile'])} · {'ready' if ready else 'needs plan'}")
        if self.route_list.count():
            self.route_list.setCurrentRow(min(selected_row, self.route_list.count() - 1))
        self.route_list.blockSignals(False)
        self.select_segment(self.selected_segment())
        self.rebuild_route()
        self.remember_route_draft()
        self.schedule_automatic_route()

    def change_segment_travel(self, *unused):
        if not self.segment_loading:
            self.update_segment(mode_changed=True)

    def select_segment(self, index):
        valid = 0 <= index < len(self.stops) - 1
        self.segment_group.setEnabled(valid)
        self.segment_loading = True
        self.route_options.blockSignals(True)
        self.route_options.clear()
        self.alternatives = []
        if valid:
            spec = self.route_plan.spec(index)
            self.segment_travel.setCurrentText(travel_mode(spec["profile"]))
            self.segment_speed_mode.setCurrentIndex(self.segment_speed_mode.findData(spec["speed_mode"]))
            self.segment_speed.setValue(to_mph(spec["speed_kph"]))
            self.segment_speed.setEnabled(spec["speed_mode"] == "manual")
            self.segment_speed.setVisible(spec["speed_mode"] == "manual")
            key = self.route_plan.key(index)
            self.alternatives = self.route_plan.results.get(key, [])
            for i, option in enumerate(self.alternatives):
                self.route_options.addItem(f"Path {i + 1} · {option['distance'] / METRES_PER_MILE:.2f} mi · ~{option['seconds'] / 60:.1f} min")
            self.route_options.setCurrentIndex(self.route_plan.selected.get(key, 0))
            text = "Automatic path · "
            if self.alternatives:
                option = self.alternatives[self.route_plan.selected.get(key, 0)]
                low, high = to_mph(min(option["speeds"]) * 3.6), to_mph(max(option["speeds"]) * 3.6)
                text += f"targets {low:.1f}–{high:.1f} mph\n"
                text += speed_text(option.get("speed_warning", option["sources"][0]))
                signs = sum(stop['kind'] == 'stop_sign' for stop in option.get('stops', []))
                lights = sum(stop['kind'] == 'traffic_light' for stop in option.get('stops', []))
                text += f'\n{signs} stop signs · {lights} traffic lights'
                if option.get('garage_warning'):
                    text += '\n' + option['garage_warning']
                if option.get('traffic_warning'):
                    text += '\n' + option['traffic_warning']
            else:
                text += "Needs planning. Paths are chosen for this travel mode; automatic driving speed uses road data."
            self.segment_info.setText(text)
        self.route_options.blockSignals(False)
        self.segment_loading = False
        self.redraw_route()

    def update_segment(self, *unused, mode_changed=False):
        index = self.selected_segment()
        if self.segment_loading or index < 0:
            return
        if not self.editable_route():
            self.select_segment(index)
            return
        old = self.route_plan.spec(index)
        spec = {**old, "profile": TRAVEL_MODES[self.segment_travel.currentText()] if mode_changed else old["profile"], "speed_mode": self.segment_speed_mode.currentData(), "speed_kph": to_kph(self.segment_speed.value()) if self.segment_speed_mode.currentData() == "manual" else old["speed_kph"]}
        if old["profile"] != spec["profile"] and spec["speed_mode"] == "auto":
            spec["speed_kph"] = PROFILES[spec["profile"]]["speed"]
        try:
            self.route_plan.update(index, spec)
            self.stops_changed()
        except ValueError as error:
            self.status.setText(speed_text(str(error)))
            self.select_segment(index)

    def select_map_segment(self, index):
        if 0 <= index < len(self.stops) - 1:
            self.sidebar_toggle.setChecked(True)
            self.tabs.setCurrentIndex(1)
            self.route_list.setCurrentRow(2 * index + 1)

    def rebuild_route(self):
        self.route_points, self.route_profile = [], None
        try:
            combined = self.route_plan.assemble()
            self.route_points = combined.pop("points")
            self.route_profile = combined
            self.show_route_summary("Mixed route")
        except ValueError:
            self.route_summary.setText(f"{len(self.stops)} stops · ready to plan" if len(self.stops) >= 2 else "Add at least two stops to build a route.")
            self.redraw_route()

    def redraw_route(self):
        self.js("setRoute", self.route_points, self.stops, [] if self.imported_track else self.route_plan.segments(), self.selected_segment())

    def use_drawn_route(self):
        if self.editable_route():
            profile = "offroad_car" if self.travel_mode.currentText() == "Drive" else "offroad_walk"
            for index in range(len(self.stops) - 1):
                self.route_plan.update(index, {**self.route_plan.spec(index), "profile": profile})
            self.stops_changed()
            self.plan_route()

    def show_route_summary(self, source):
        route = Route(self.route_points)
        self.route_summary.setText(f"{source} · {route.total / METRES_PER_MILE:.2f} mi · {len(route.points)} points")
        self.redraw_route()

    def schedule_automatic_route(self):
        if not self.imported_track and len(self.stops) >= 2 and any(
                not self.route_plan.results.get(self.route_plan.key(i)) for i in range(len(self.stops) - 1)):
            self.auto_plan_timer.start()
        else:
            self.auto_plan_timer.stop()

    def refresh_automatic_route(self):
        if self.imported_track or len(self.stops) < 2 or self.closing:
            return
        if self.busy or not self.ready:
            self.auto_plan_timer.start()
            return
        if self.editable_route():
            self.plan_route()

    def plan_route(self):
        self.auto_plan_timer.stop()
        if not self.busy and self.editable_route():
            self.planning_revision = self.route_plan.revision
            self.planning_document = self.route_plan
            self.request("mixed_route", self.route_plan.requests(), self.store.data["settings"].get("valhalla", VALHALLA), self.store.data['settings'].get('overpass', OVERPASS), self.traffic_stops.isChecked())

    def choose_route(self, index):
        if 0 <= index < len(self.alternatives) and self.editable_route():
            segment = self.selected_segment()
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

    def current_route_document(self):
        if self.imported_track:
            return {'format': 'locus-track', 'version': 1, 'points': self.route_points.copy()}
        return self.route_plan.serialize()

    def remember_route_draft(self):
        if self.restoring_route:
            return
        try:
            self.route_library.save_draft(self.current_route_document(), self.route_title.text(), self.active_route_id)
        except (OSError, ValueError) as error:
            self.status.setText(f'Could not save draft: {error}')

    def refresh_route_library(self, *unused):
        selected = self.library_list.currentItem()
        identifier = selected.data(Qt.ItemDataRole.UserRole) if selected else self.active_route_id
        self.library_list.clear()
        query = self.route_search.text().strip().casefold()
        for entry in self.route_library.data['routes']:
            if query not in entry['name'].casefold():
                continue
            value = entry['document']
            detail = f"{len(value['points'])} track points" if value['format'] == 'locus-track' else f"{len(value['pins'])} stops · editable route"
            item = QListWidgetItem(entry['name'] + '\n' + detail)
            item.setData(Qt.ItemDataRole.UserRole, entry['id'])
            self.library_list.addItem(item)
            if entry['id'] == identifier:
                self.library_list.setCurrentItem(item)
        self.library_empty.setVisible(self.library_list.count() == 0)
        self.library_empty.setText('No routes match your search.' if query else 'Save a route to keep it here.')
        if self.library_list.count() and self.library_list.currentRow() < 0:
            self.library_list.setCurrentRow(0)

    def save_library_route(self, checked=False, *, as_copy=False):
        try:
            name = self.route_title.text().strip()
            if not name or as_copy:
                name, accepted = QInputDialog.getText(self, 'Save route', 'Route name:', text=name + (' copy' if as_copy and name else ''))
                if not accepted:
                    return
            identifier = self.route_library.save_route(name, self.current_route_document(), None if as_copy else self.active_route_id)
            self.active_route_id = identifier
            self.route_title.setText(name)
            self.refresh_route_library()
            self.remember_route_draft()
            self.status.setText(f'Saved “{name}” to your route library.')
        except (OSError, ValueError, StopIteration) as error:
            self.status.setText(f'Could not save route: {error}')

    def apply_route_document(self, value):
        self.restoring_route = True
        try:
            if value['format'] == 'locus-track':
                points = Route(value['points']).points
                self.route_plan.reset([points[0], points[-1]])
                self.stops_changed()
                self.route_points, self.route_profile, self.imported_track = points, None, True
                self.show_route_summary('Saved track')
            else:
                plan = RoutePlan.deserialize(value) if len(value['pins']) >= 2 else RoutePlan()
                if len(value['pins']) == 1:
                    plan.add(value['pins'][0]['point'])
                for index in range(len(plan.pins) - 1):
                    plan.update(index, automatic_specification(plan.spec(index)))
                self.route_plan = plan
                self.stops_changed()
        finally:
            self.restoring_route = False
        self.remember_route_draft()

    def open_library_route(self, *unused):
        item = self.library_list.currentItem()
        if not item or not self.editable_route():
            return
        try:
            entry = self.route_library.get(item.data(Qt.ItemDataRole.UserRole))
            self.apply_route_document(entry['document'])
            self.active_route_id = entry['id']
            self.route_title.setText(entry['name'])
            self.remember_route_draft()
            self.library_toggle.setChecked(False)
            self.js('fitRoute()')
            self.status.setText('Route opened. Paths refresh automatically. Press Start when ready.')
        except (OSError, ValueError, KeyError, StopIteration) as error:
            self.status.setText(f'Could not open route: {error}')

    def rename_library_route(self, *unused):
        item = self.library_list.currentItem()
        if item:
            identifier = item.data(Qt.ItemDataRole.UserRole)
            entry = self.route_library.get(identifier)
            name, accepted = QInputDialog.getText(self, 'Rename route', 'Route name:', text=entry['name'])
            if accepted:
                try:
                    self.route_library.rename(identifier, name)
                    if self.active_route_id == identifier:
                        self.route_title.setText(name.strip())
                        self.remember_route_draft()
                    self.refresh_route_library()
                except (OSError, ValueError) as error:
                    self.status.setText(str(error))

    def delete_library_route(self, *unused):
        item = self.library_list.currentItem()
        if item:
            identifier = item.data(Qt.ItemDataRole.UserRole)
            entry = self.route_library.get(identifier)
            if QMessageBox.question(self, 'Delete saved route', f'Delete “{entry["name"]}” from your library?') != QMessageBox.StandardButton.Yes:
                return
            try:
                self.route_library.delete(identifier)
                if self.active_route_id == identifier:
                    self.active_route_id = None
                    self.remember_route_draft()
                self.refresh_route_library()
            except (OSError, ValueError) as error:
                self.status.setText(str(error))

    def new_library_route(self, *unused):
        if self.editable_route():
            self.active_route_id = None
            self.route_title.clear()
            self.clear_route()
            self.library_toggle.setChecked(False)

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
                self.status.setText("Route project saved with stops, travel modes and speed choices.")
        except (OSError, ValueError) as error:
            self.status.setText(speed_text(str(error)))

    def load_project(self):
        if not self.editable_route():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load editable route", "", "Locus route (*.locus.json)")
        if path:
            try:
                if Path(path).stat().st_size > 8_000_000:
                    raise ValueError("Route project is too large")
                plan = RoutePlan.deserialize(json.loads(Path(path).read_text()))
                # Old shape hints must not constrain automatic path selection.
                for index in range(len(plan.pins) - 1):
                    plan.update(index, automatic_specification(plan.spec(index)))
                self.active_route_id = None
                self.route_title.setText(Path(path).stem)
                self.route_plan = plan
                self.stops_changed()
                self.status.setText("Route project loaded. Refreshing paths automatically.")
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
                self.active_route_id = None
                self.route_title.setText(Path(path).stem)
                self.route_plan.reset([points[0], points[-1]])
                self.stops_changed()
                self.route_points = points
                self.route_profile = None
                self.imported_track = True
                self.remember_route_draft()
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
            self.status.setText(speed_text(str(error)))

    def save_settings(self):
        try:
            self.store.data["settings"].update(photon=endpoint(self.photon.text().strip()), valhalla=endpoint(self.valhalla.text().strip()),
                reconnect=self.reconnect.isChecked(), notifications=self.notifications.isChecked(), travel_mode=self.travel_mode.currentText(),
                speed_kph=to_kph(self.speed.value()), variation=self.variation.isChecked(), night_map=self.map_style.currentIndex() == 0, follow=self.follow.isChecked(),
                traffic_stops=self.traffic_stops.isChecked(), red_probability=self.red_probability.value(), overpass=endpoint(self.overpass.text().strip()))
            self.store.save()
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
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
