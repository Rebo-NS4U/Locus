# SPDX-License-Identifier: GPL-3.0-or-later
"""Map display, movement input and saved place actions."""
import json
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox, QLineEdit, QInputDialog
from shiboken6 import delete
from .routes import SPEEDS
from .services import PHOTON
from .theme import apply_theme, read_colors
from .units import to_mph, to_kph, speed_text, METRES_PER_MILE


class MapInteractionMixin:
    def motion_changed(self, state):
        self.motion = state
        self.highlight_active_leg(state.get("active_leg"), planned_stop=state.get("planned_stop"))
        self.connection_badge.setText(("Demo session" if self.demo else "Location active") if state["connected"] else "Location unknown / inactive")
        self.connection_badge.setStyleSheet(f"color: {self.colors['accent'] if state['connected'] else self.colors['muted']};")
        self.js("setAcknowledged", state["position"], state["connected"])
        self.progress.setText(f"{'Paused / holding' if state['paused'] else 'Following route'} · {state['progress'] / METRES_PER_MILE:.2f} / {state['total'] / METRES_PER_MILE:.2f} mi · {to_mph(state.get('speed_kph', 0)):.1f} mph\n{speed_text(state.get('speed_source', ''))}" if state["total"] else "Not following a route")
        planned = state.get('planned_stop')
        self.continue_stop.setVisible(bool(planned and planned['mode'] == 'manual'))
        self.continue_stop.setEnabled(bool(planned and not state['paused'] and state['connected'] and not self.busy))
        if planned:
            label = f"At stop {planned['stop']} · "
            if planned['mode'] == 'manual':
                label += 'waiting for Continue'
            elif planned['mode'] == 'timer':
                label += f"{planned['remaining']:.0f}s remaining"
            else:
                label += 'departure ' + datetime.fromtimestamp(planned['deadline']).strftime('%a %I:%M %p')
            self.progress.setText(label)
        if state.get('traffic_stop'):
            stop = state['traffic_stop']
            label = 'Stop sign' if stop['kind'] == 'stop_sign' else 'Crosswalk yield' if stop['kind'] == 'crosswalk' else f"Red light · queue {stop.get('queue_distance', 0) * 3.28084:.0f} ft back"
            self.connection_badge.setText(f"{label} · {stop['remaining']:.0f}s")
            self.progress.setText(self.progress.text() + f"\n{label}: {stop['remaining']:.1f}s remaining")

    def highlight_active_leg(self, leg, force=False, planned_stop=None):
        destination = planned_stop['stop'] if planned_stop else leg['to_stop'] if leg else None
        row = destination - 1 if destination is not None else None
        if leg and self.return_edit.isChecked():
            row = 0 if planned_stop and planned_stop['distance'] == 0 else leg['index'] + 1
        signature = (leg['index'] if leg else None, row, bool(planned_stop))
        if signature == self.active_leg_signature and not force:
            return
        self.active_leg_signature = signature
        self.js('setActiveLeg', leg)
        self.active_leg_label.setVisible(bool(leg))
        if leg:
            self.active_leg_label.setText(f"Active: stop {leg['from_stop']} → {leg['to_stop']} · {leg.get('mode', '')}")
        rows = {row} if row is not None else set()
        if row is not None and 0 <= row < self.route_list.count():
            self.route_list.setCurrentRow(row)
            self.route_list.scrollToItem(self.route_list.item(row))
        color = QColor(self.colors['accent'])
        color.setAlpha(70)
        for row in range(self.route_list.count()):
            item = self.route_list.item(row)
            active = row in rows
            item.setBackground(QBrush(color) if active else QBrush())
            card = self.route_list.itemWidget(item)
            if card:
                # Item widgets cover the native item paint on some Qt themes.
                # Put the playback indicator on the visible card itself.
                border = self.colors['accent'] if active else 'transparent'
                card.setProperty('activeLeg', active)
                card.setStyleSheet(f'QFrame#stopCard {{ border: 2px solid {border}; border-radius: 8px; }}')
            font = item.font()
            font.setBold(active)
            item.setFont(font)

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
            settings = self.store.data['settings']
            if 'default_latitude' in settings or 'default_longitude' in settings:
                self.js('focusLocation', settings.get('default_latitude', 40.7128), settings.get('default_longitude', -74.006))
            elif self.stops:
                self.js('focusLocation', *self.stops[0])

    def map_typography(self):
        font = QApplication.instance().font()
        self.js("setTypography", font.family(), font.pointSizeF())

    def map_options(self, *unused):
        if hasattr(self, "map"):
            self.js("setOptions", self.map_style.currentIndex() == 0, self.follow.isChecked())

    def refresh_theme(self):
        colors = read_colors()
        config = Path.home() / ".config/qt6ct/qt6ct.conf"
        shell_config = Path.home() / '.config/rebos_shell/config/theme.json'
        stamp = (config.stat().st_mtime_ns if config.exists() else 0,
                 shell_config.stat().st_mtime_ns if shell_config.exists() else 0)
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
            try:
                self.store.add("favorites", name, (self.latitude.value(), self.longitude.value()))
                self.refresh_places()
            except (OSError, ValueError) as error:
                self.status.setText(f'Could not save favorite: {error}')

    def selected_saved(self):
        key = "favorites" if self.saved_tabs.currentIndex() == 0 else "recents"
        return key, self.saved[key].currentRow()

    def rename_place(self):
        key, index = self.selected_saved()
        if index >= 0:
            name, ok = QInputDialog.getText(self, "Rename place", "Place name", text=self.store.data[key][index]["name"])
            if ok and name.strip():
                try:
                    self.store.rename(key, index, name)
                    self.refresh_places()
                except (OSError, ValueError) as error:
                    self.status.setText(f'Could not rename place: {error}')

    def remove_place(self):
        key, index = self.selected_saved()
        if index >= 0:
            try:
                self.store.remove(key, index)
                self.refresh_places()
            except (OSError, ValueError) as error:
                self.status.setText(f'Could not remove place: {error}')
