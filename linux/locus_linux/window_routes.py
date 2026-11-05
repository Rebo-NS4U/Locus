# SPDX-License-Identifier: GPL-3.0-or-later
"""Route editing, planning and playback actions."""
from PySide6.QtCore import QTime
from .routes import Route
from .services import VALHALLA, OVERPASS
from .units import to_mph, to_kph, speed_text, METRES_PER_MILE
from .planning import PROFILES, TRAVEL_MODES, travel_mode


class RouteEditorMixin:
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
        self.remove_stop_at(self.selected_stop())

    def remove_stop_at(self, index):
        if 0 <= index < len(self.stops) and self.editable_route():
            self.route_plan.remove(index)
            self.stops_changed()

    def insert_stop(self):
        if self.editable_route():
            try:
                selected = self.selected_stop()
                index = selected + 1 if selected >= 0 else len(self.stops)
                default = {"Drive": "car_roads", "Cycle": "cycle_paths", "Run": "run_paths"}.get(self.travel_mode.currentText(), "walk_auto")
                self.route_plan.insert(index, (self.latitude.value(), self.longitude.value()), default)
                self.stops_changed(selected_row=index)
            except ValueError as error:
                self.status.setText(speed_text(str(error)))

    def move_stop(self, index, latitude, longitude):
        from .engine import coordinates
        if not self.editable_route():
            self.redraw_route()
            return
        if self.return_edit.isChecked() and index >= len(self.stops):
            index = 2 * len(self.stops) - 2 - index
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
            self.route_list.setCurrentRow(target)

    def clear_route(self):
        if self.editable_route():
            self.route_plan.reset([])
            self.stops_changed()

    def selected_stop(self):
        row = self.route_list.currentRow()
        index = row
        if index >= len(self.stops) and self.return_edit.isChecked():
            index = 2 * len(self.stops) - 2 - index
        return index

    def _editor_stops(self):
        if self.route_plan.there_and_back and self.return_edit.isChecked() and len(self.stops) >= 2:
            return self.stops + self.stops[-2::-1]
        return self.stops

    def selected_segment(self):
        row = self.route_list.currentRow()
        if row < 0 or len(self.stops) < 2:
            return -1
        if row >= len(self.route_plan.visits) - 1:
            return -1
        index = row
        if self.route_plan.there_and_back and index >= len(self.stops) - 1:
            index = 2 * (len(self.stops) - 1) - 1 - index
        return index

    def stops_changed(self, *, select_newest=False, selected_row=None):
        if selected_row is None:
            selected_row = max(0, self.route_list.currentRow())
        self.stops = self.route_plan.points
        self.plan_button.setEnabled(len(self.stops) >= 2)
        self.imported_track = False
        self.source_track_points = []
        self.there_and_back.blockSignals(True)
        self.there_and_back.setChecked(self.route_plan.there_and_back)
        self.there_and_back.blockSignals(False)
        self.return_edit.setVisible(self.route_plan.there_and_back and len(self.stops) >= 2)
        if not self.route_plan.there_and_back:
            self.return_edit.setChecked(False)
        if select_newest:
            selected_row = len(self.stops) - 1
        self.route_list.blockSignals(True)
        self.route_list.clear()
        display_stops = self._editor_stops()
        for i, point in enumerate(display_stops):
            pin_index = i if i < len(self.stops) else 2 * len(self.stops) - 2 - i
            rule = self.route_plan.pins[pin_index].get('departure', {'mode': 'none'})
            seconds = rule.get('seconds', 0)
            duration = next((f'{seconds / factor:g} {unit}' for factor, unit in
                             [(86400, 'days'), (3600, 'hours'), (60, 'minutes'), (1, 'seconds')]
                             if seconds >= factor and seconds % factor == 0), f'{seconds:g} seconds')
            timing = {'none': 'Pass through', 'manual': 'Wait for Continue',
                      'timer': f'Wait {duration}',
                      'clock': f"Leave at {rule.get('time', '')}"}[rule['mode']]
            onward = 'End of trip'
            if i < len(self.route_plan.visits) - 1:
                leg_index = i if i < len(self.stops) - 1 else 2 * (len(self.stops) - 1) - 1 - i
                spec = self.route_plan.spec(leg_index)
                ready = bool(self.route_plan.results.get(self.route_plan.key(leg_index)))
                destination = self.route_plan.visits[i + 1]
                onward = f"To stop {destination + 1:02d} · {travel_mode(spec['profile'])} · {'Ready' if ready else 'Needs planning'}"
            title = f"{'Return · ' if i >= len(self.stops) else ''}Stop {pin_index + 1:02d}"
            if i == 0:
                title += ' · Start'
            elif i == len(self.route_plan.visits) - 1:
                title += ' · Finish'
            elif self.route_plan.there_and_back and i == len(self.stops) - 1:
                title += ' · Turnaround'
            self.add_stop_card(pin_index + 1, title, f'{point[0]:.5f}, {point[1]:.5f}', timing, onward)
        if self.route_list.count():
            self.route_list.setCurrentRow(min(selected_row, self.route_list.count() - 1))
        self.route_list.blockSignals(False)
        self.select_segment(self.selected_segment())
        self.highlight_active_leg(self.motion.get("active_leg"), force=True, planned_stop=self.motion.get("planned_stop"))
        self.rebuild_route()
        self.remember_route_draft()

    def change_timer_unit(self, *unused):
        if self.departure_loading:
            return
        self.departure_seconds.blockSignals(True)
        self.departure_seconds.setMaximum(604800 // int(self.departure_unit.currentData()))
        self.departure_seconds.blockSignals(False)
        self.update_departure()

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
                crossings = sum(stop['kind'] == 'crosswalk' for stop in option.get('stops', []))
                text += f'\n{signs} signs · {lights} lights · {crossings} crosswalks'
                if option.get('garage_warning'):
                    text += '\n' + option['garage_warning']
                if option.get('traffic_warning'):
                    text += '\n' + option['traffic_warning']
            else:
                text += "Needs planning. Paths are chosen for this travel mode; automatic driving speed uses road data."
            self.segment_info.setText(text)
        self.route_options.blockSignals(False)
        self.segment_loading = False
        self.select_departure()
        item = self.route_list.currentItem()
        self.selection_context.setText(item.text().split('\n')[0] if item else 'Select a stop to edit its timing and next leg.')
        self.segment_group.setTitle('Onward travel' if valid else 'No onward leg · end of trip')
        self.redraw_route()

    def select_departure(self):
        index = self.selected_stop()
        valid = 0 <= index < len(self.route_plan.pins) and not self.imported_track
        self.departure_group.setEnabled(valid)
        self.departure_loading = True
        if valid:
            rule = self.route_plan.pins[index].get('departure', {'mode': 'none'})
            self.departure_group.setTitle(f'Departure from stop {index + 1}')
            self.departure_mode.setCurrentIndex(self.departure_mode.findData(rule['mode']))
            seconds = int(rule.get('seconds', 60))
            multiplier = int(self.departure_unit.currentData())
            if seconds % multiplier:
                multiplier = 1
            self.departure_unit.setCurrentIndex(self.departure_unit.findData(multiplier))
            self.departure_seconds.setMaximum(604800 // multiplier)
            self.departure_seconds.setValue(max(1, seconds // multiplier))
            self.departure_time.setTime(QTime.fromString(rule.get('time', '09:00'), 'HH:mm'))
            self.departure_seconds.setVisible(rule['mode'] == 'timer')
            self.departure_unit.setVisible(rule['mode'] == 'timer')
            self.departure_time.setVisible(rule['mode'] == 'clock')
        self.departure_loading = False

    def update_departure(self, *unused):
        if self.departure_loading or self.imported_track:
            return
        index = self.selected_stop()
        if not 0 <= index < len(self.route_plan.pins):
            return
        if not self.editable_route():
            self.select_departure()
            return
        rule = {'mode': self.departure_mode.currentData()}
        if rule['mode'] == 'timer':
            rule['seconds'] = self.departure_seconds.value() * int(self.departure_unit.currentData())
        elif rule['mode'] == 'clock':
            rule['time'] = self.departure_time.time().toString('HH:mm')
        self.route_plan.set_departure(index, rule)
        self.stops_changed()

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
        if 0 <= index < self.route_plan.leg_count:
            self.sidebar_toggle.setChecked(True)
            self.tabs.setCurrentIndex(1)
            if index >= len(self.stops) - 1:
                self.return_edit.setChecked(True)
            self.studio_sections.setCurrentIndex(0)
            self.route_list.setCurrentRow(index)

    def rebuild_route(self):
        self.route_points, self.route_profile = [], None
        if len(self.stops) == 1:
            self.route_summary.setText('Single point · Start sets this location.')
            self.redraw_route()
            return
        try:
            combined = self.route_plan.assemble()
            self.route_points = combined.pop("points")
            self.route_profile = combined
            self.show_route_summary("There and back" if self.route_plan.there_and_back else "Mixed route")
        except ValueError:
            self.route_summary.setText(f"{len(self.stops)} stops · ready to plan" if len(self.stops) >= 2 else "Add at least two stops to build a route.")
            self.redraw_route()

    def redraw_route(self):
        if self.imported_track and self.route_profile:
            pieces = [{'points': self.route_points[leg['first']:leg['last'] + 1], 'ready': True,
                       'color': self.colors['accent'], 'label': 'GPX', **leg} for leg in self.route_profile.get('legs', [])]
        else:
            pieces = self.route_plan.segments()
        self.js('setRoute', self.route_points, self.stops, pieces, self.selected_segment())

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

    def plan_route(self):
        if not self.busy and self.editable_route():
            self.planning_revision = self.route_plan.revision
            self.planning_document = self.route_plan
            self.request("mixed_route", self.route_plan.requests(), self.store.data["settings"].get("valhalla", VALHALLA), self.store.data['settings'].get('overpass', OVERPASS), self.traffic_stops.isChecked(), self.route_plan.there_and_back)

    def choose_route(self, index):
        if 0 <= index < len(self.alternatives) and self.editable_route():
            segment = self.selected_segment()
            if segment >= 0:
                self.route_plan.select(segment, index)
                self.rebuild_route()
                self.select_segment(segment)

    def refresh_imported_trip(self):
        points = self.source_track_points
        self.route_points = points + points[-2::-1] if self.route_plan.there_and_back else points.copy()
        Route(self.route_points)
        count = len(points)
        legs = [{'first': 0, 'last': count - 1, 'from_stop': 1, 'to_stop': 2, 'mode': 'GPX'}]
        if self.route_plan.there_and_back:
            legs.append({'first': count - 1, 'last': len(self.route_points) - 1, 'from_stop': 2, 'to_stop': 1, 'mode': 'GPX'})
        self.route_profile = {'legs': legs}
        self.show_route_summary('There and back · GPX' if self.route_plan.there_and_back else 'Imported GPX')

    def change_return_trip(self, enabled):
        previous = self.route_plan.there_and_back
        if not self.editable_route():
            self.there_and_back.blockSignals(True)
            self.there_and_back.setChecked(previous)
            self.there_and_back.blockSignals(False)
            return
        try:
            self.route_plan.set_return_trip(enabled)
            if self.imported_track:
                self.refresh_imported_trip()
                self.remember_route_draft()
            else:
                self.stops_changed()
        except ValueError as error:
            self.route_plan.set_return_trip(previous)
            self.there_and_back.blockSignals(True)
            self.there_and_back.setChecked(previous)
            self.there_and_back.blockSignals(False)
            if self.imported_track:
                self.refresh_imported_trip()
            self.status.setText(str(error))

    def toggle_return_editor(self, enabled):
        if self.route_plan.there_and_back:
            self.stops_changed()

    def start_route(self):
        if not self.imported_track and len(self.stops) == 1:
            self.select_coordinates(*self.stops[0])
            self.perform('apply')
            return
        if len(self.route_points) < 2:
            self.status.setText("Plan the segments, or import a GPX path, before starting a route.")
            return
        self.configure_motion()
        self.request("start_route", self.devices.currentData(), self.route_points.copy(), self.loop_route.isChecked(), self.route_profile)
