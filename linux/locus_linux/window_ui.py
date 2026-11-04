# SPDX-License-Identifier: GPL-3.0-or-later
"""Window layout and control construction."""
from pathlib import Path
from PySide6.QtCore import QUrl, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QTabWidget, QGroupBox, QCheckBox, QSplitter, QScrollArea, QGridLayout, QSizePolicy, QToolButton, QMenu
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from .map_profile import create_map_profile
from .routes import SPEEDS
from .services import PHOTON, VALHALLA, OVERPASS
from .units import to_mph
from .widgets import MapBridge, StatusLabel
from .route_panel import RoutePanelMixin


class WindowUiMixin(RoutePanelMixin):
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
        try:
            self.store.update_settings(sidebar_visible=visible)
        except (OSError, ValueError) as error:
            self.status.setText(f'Could not save sidebar preference: {error}')
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
        self.traffic_stops = QCheckBox("Auto stops at signs, lights and crosswalks")
        self.traffic_stops.setChecked(self.store.data['settings'].get('traffic_stops', True))
        self.traffic_stops.setToolTip('Stop signs: 2–5 seconds. Crosswalks: 30% chance of a 2–8 second yield. Red lights: 8–65 seconds with a randomized 10–66 ft queue when space permits. These are simulated, not live conditions.')
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
