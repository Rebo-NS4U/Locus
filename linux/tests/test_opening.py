"""Opening preferences and saved single locations without a phone session."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QApplication, QDoubleSpinBox, QCheckBox, QComboBox, QLabel
from locus_linux.activation import opening_geometry
from locus_linux.app import Window
from locus_linux.library import RouteLibrary
from locus_linux.planning import RoutePlan
from locus_linux.storage import Places
from locus_linux.window_map import MapInteractionMixin
from locus_linux.window_routes import RouteEditorMixin
from locus_linux.window_ui import WindowUiMixin


class SettingsEditor(WindowUiMixin):
    configure_motion = Mock()
    save_settings = Window.save_settings


class OpeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_opening_geometry_retains_border_on_offset_monitor(self):
        available = QRect(1920, 30, 1920, 1050)
        result = opening_geometry(available)
        self.assertEqual(result, QRect(1932, 42, 1896, 1026))
        self.assertTrue(available.contains(result))

    def test_settings_copy_save_and_restore_opening_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = SettingsEditor()
            editor.store = Places(Path(directory) / 'places.json')
            editor.latitude, editor.longitude = QDoubleSpinBox(), QDoubleSpinBox()
            for field, limit, value in [(editor.latitude, 90, 35), (editor.longitude, 180, -111)]:
                field.setRange(-limit, limit)
                field.setValue(value)
            for name in ('variation', 'traffic_stops', 'follow'):
                setattr(editor, name, QCheckBox())
            for name, text in [('travel_mode', 'Walk'), ('map_style', 'Night')]:
                field = QComboBox()
                field.addItem(text)
                setattr(editor, name, field)
            editor.speed, editor.red_probability = QDoubleSpinBox(), QDoubleSpinBox()
            editor.speed.setValue(3)
            editor.status = QLabel()
            page = editor.settings_tab()
            editor.copy_opening_location()
            editor.save_settings()
            settings = Places(editor.store.path).data['settings']
            self.assertEqual((settings['default_latitude'], settings['default_longitude']), (35, -111))
            page.deleteLater()
            restored = SettingsEditor()
            restored.store = Places(editor.store.path)
            page = restored.settings_tab()
            self.assertEqual((restored.default_latitude.value(), restored.default_longitude.value()), (35, -111))
            page.deleteLater()

    def test_invalid_opening_coordinates_do_not_replace_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            places = Places(Path(directory) / 'places.json')
            places.update_settings(default_latitude=35, default_longitude=-111)
            before = places.path.read_bytes()
            for key, value in [('default_latitude', 91), ('default_longitude', -181),
                               ('default_latitude', float('nan')), ('default_longitude', '0')]:
                with self.assertRaises(ValueError):
                    places.update_settings(**{key: value})
                self.assertEqual(places.path.read_bytes(), before)

    def test_map_load_uses_saved_opening_location_with_a_draft(self):
        window = Mock()
        window.store.data = {'settings': {'default_latitude': 35, 'default_longitude': -111}}
        window.stops = [(40, -74)]
        MapInteractionMixin.map_ready(window, True)
        window.js.assert_any_call('focusLocation', 35, -111)
        self.assertNotIn(('fitRoute()',), [call.args for call in window.js.call_args_list])

    def test_single_point_survives_save_reopen_and_start(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'routes.json'
            plan = RoutePlan()
            plan.add((35, -111))
            identifier = RouteLibrary(path).save_route('Home', plan.serialize())
            document = RouteLibrary(path).get(identifier)['document']
            restored = RoutePlan.deserialize(document)
            self.assertEqual(restored.points, [(35, -111)])
            self.assertEqual(restored.leg_count, 0)
            window = SimpleNamespace(imported_track=False, stops=restored.points,
                                     select_coordinates=Mock(), perform=Mock(), request=Mock())
            RouteEditorMixin.start_route(window)
            window.select_coordinates.assert_called_once_with(35, -111)
            window.perform.assert_called_once_with('apply')
            window.request.assert_not_called()
            with self.assertRaises(ValueError):
                RouteLibrary(path).save_route('Empty', RoutePlan().serialize())
