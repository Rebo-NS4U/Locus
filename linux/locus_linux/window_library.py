# SPDX-License-Identifier: GPL-3.0-or-later
"""Saved route library and GPX/project file actions."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem, QMessageBox, QFileDialog, QInputDialog
from .routes import Route, parse_gpx, export_gpx
from .units import speed_text
from .planning import RoutePlan, automatic_specification


class RouteLibraryMixin:
    def current_route_document(self):
        if self.imported_track:
            return {'format': 'locus-track', 'version': 1, 'points': self.source_track_points.copy(), 'there_and_back': self.route_plan.there_and_back}
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
            if value['format'] == 'locus-route' and len(value['pins']) == 1:
                detail = 'Single point · saved location'
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
                self.route_plan.set_return_trip(value.get('there_and_back', False))
                self.stops_changed()
                self.source_track_points, self.imported_track = points, True
                self.refresh_imported_trip()
                self.select_departure()
            else:
                plan = RoutePlan.deserialize(value) if len(value['pins']) >= 2 else RoutePlan()
                if len(value['pins']) == 1:
                    plan.add(value['pins'][0]['point'])
                    plan.set_departure(0, value['pins'][0].get('departure'))
                plan.set_return_trip(value.get('there_and_back', False))
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
            if len(self.stops) == 1:
                self.select_coordinates(*self.stops[0])
                self.status.setText('Saved point opened. Press Start to set this location.')
            else:
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
            if not data["pins"]:
                raise ValueError("Add at least one pin before saving a project")
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
                self.source_track_points = points
                self.imported_track = True
                self.refresh_imported_trip()
                self.select_departure()
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
