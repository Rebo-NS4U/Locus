"""Selected route visits and clear actions without a real phone."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from locus_linux.window_routes import RouteEditorMixin


class RouteActionTests(unittest.TestCase):
    def test_selected_visit_uses_geometry_boundary_including_return_and_finish(self):
        for row, expected in [(0, 0), (1, 4), (2, 9), (3, 13), (4, 18)]:
            with self.subTest(row=row):
                window = SimpleNamespace(
                    imported_track=False, stops=[(0, 0)] * 3,
                    route_list=SimpleNamespace(currentRow=lambda: row),
                    route_profile={'legs': [{'first': a, 'last': b} for a, b in
                                            [(0, 4), (4, 9), (9, 13), (13, 18)]]},
                    start_route=Mock(), status=Mock())
                RouteEditorMixin.start_selected_stop(window)
                window.start_route.assert_called_once_with(start_index=expected)

    def test_unplanned_or_imported_route_does_not_start_selected(self):
        for imported, profile, row in [(False, None, 0), (True, {'legs': [{}]}, 0),
                                        (False, {'legs': [{}]}, -1)]:
            window = SimpleNamespace(imported_track=imported, stops=[(0, 0)] * 2,
                                     route_profile=profile, route_list=SimpleNamespace(currentRow=lambda: row),
                                     start_route=Mock(), status=Mock())
            RouteEditorMixin.start_selected_stop(window)
            window.start_route.assert_not_called()
            window.status.setText.assert_called_once()

    def test_clear_active_route_stops_before_clearing_editor(self):
        window = SimpleNamespace(motion={'mode': 'route'}, request=Mock(),
                                 editable_route=Mock(), route_plan=Mock())
        RouteEditorMixin.clear_route(window)
        window.request.assert_called_once_with('stop_here')
        window.route_plan.reset.assert_not_called()

    def test_clear_idle_route_resets_working_document_and_library_identity(self):
        window = SimpleNamespace(motion={'mode': 'fixed'}, editable_route=lambda: True,
                                 active_route_id='saved-route', route_title=Mock(),
                                 route_plan=Mock(), stops_changed=Mock())
        RouteEditorMixin.clear_route(window)
        window.route_plan.reset.assert_called_once_with([])
        window.route_title.clear.assert_called_once()
        window.stops_changed.assert_called_once()
        self.assertIsNone(window.active_route_id)
