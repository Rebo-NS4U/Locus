"""Map placement inserts pins in order and preserves surrounding legs."""
import unittest
from types import SimpleNamespace
from locus_linux.planning import RoutePlan
from locus_linux.window_map import MapInteractionMixin
from locus_linux.window_routes import RouteEditorMixin


class Editor(RouteEditorMixin, MapInteractionMixin):
    def __init__(self, insert=True, selected=0):
        self.route_plan = RoutePlan()
        for point in [(40, -74), (41, -74), (42, -74)]:
            self.route_plan.add(point)
        self.stops = self.route_plan.points
        self.row = selected
        self.insert_pins = SimpleNamespace(isChecked=lambda: insert)
        self.travel_mode = SimpleNamespace(currentText=lambda: 'Walking')
        self.status = SimpleNamespace(setText=lambda text: None)
        self.allowed = True

    def select_coordinates(self, latitude, longitude):
        self.latitude = SimpleNamespace(value=lambda: latitude)
        self.longitude = SimpleNamespace(value=lambda: longitude)

    def selected_stop(self):
        return self.row

    def editable_route(self):
        return self.allowed

    def stops_changed(self, *, selected_row=None, select_newest=False):
        self.stops = self.route_plan.points
        self.row = len(self.stops) - 1 if select_newest else selected_row


class PinInsertionTests(unittest.TestCase):
    def test_map_inserts_consecutive_pins_and_inherits_settings(self):
        editor = Editor()
        original_ids = [pin['id'] for pin in editor.route_plan.pins]
        editor.route_plan.settings[editor.route_plan.key(0)]['profile'] = 'car_roads'
        editor.pick(40.2, -74)
        editor.pick(40.4, -74)
        self.assertEqual(editor.stops, [(40, -74), (40.2, -74), (40.4, -74), (41, -74), (42, -74)])
        self.assertEqual(editor.row, 2)
        self.assertEqual([editor.route_plan.spec(i)['profile'] for i in range(3)], ['car_roads'] * 3)
        self.assertEqual([editor.route_plan.pins[i]['id'] for i in (0, 3, 4)], original_ids)

    def test_default_mode_appends(self):
        editor = Editor(insert=False)
        editor.pick(40.5, -74)
        self.assertEqual(editor.stops[-1], (40.5, -74))
        self.assertEqual(editor.stops[1], (41, -74))

    def test_no_selection_appends(self):
        editor = Editor(selected=-1)
        editor.pick(43, -74)
        self.assertEqual(editor.stops[-1], (43, -74))

    def test_edit_guard_blocks_insertion(self):
        editor = Editor()
        editor.allowed = False
        editor.pick(40.5, -74)
        self.assertEqual(len(editor.stops), 3)
