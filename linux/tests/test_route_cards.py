"""Route card interaction without a phone or WebEngine session."""
import unittest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QLabel, QCheckBox
from PySide6.QtTest import QTest
from locus_linux.route_panel import RoutePanelMixin
from locus_linux.window_routes import RouteEditorMixin
from locus_linux.planning import RoutePlan
from locus_linux.window_map import MapInteractionMixin


class Editor(RoutePanelMixin, RouteEditorMixin, MapInteractionMixin):
    def __init__(self):
        self.route_list = QListWidget()
        self.route_plan = RoutePlan()
        for point in [(40, -74), (41, -74), (42, -74)]:
            self.route_plan.add(point, 'walk_auto')
        self.stops = self.route_plan.points
        self.changed = False
        self.active_leg_signature = None
        self.active_leg_label = QLabel()
        self.return_edit = QCheckBox()
        self.colors = {'accent': '#ffb77e'}

    def js(self, *args):
        pass

    def editable_route(self):
        return True

    def stops_changed(self):
        self.changed = True
        self.stops = self.route_plan.points


class RouteCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_unselected_card_deletes_its_own_stop_with_one_click(self):
        editor = Editor()
        for i in range(3):
            editor.add_stop_card(i + 1, f'Stop {i + 1}', '', '', '')
        editor.route_list.setCurrentRow(0)
        editor.route_list.show()
        self.app.processEvents()
        card = editor.route_list.itemWidget(editor.route_list.item(1))
        QTest.mouseClick(card.findChild(QPushButton), Qt.MouseButton.LeftButton)
        self.assertEqual(editor.stops, [(40, -74), (42, -74)])
        self.assertTrue(editor.changed)
        editor.route_list.close()

    def test_card_body_selects_row(self):
        editor = Editor()
        editor.add_stop_card(1, 'Stop 1', '', '', '')
        editor.add_stop_card(2, 'Stop 2', '', '', '')
        editor.route_list.setCurrentRow(0)
        card = editor.route_list.itemWidget(editor.route_list.item(1))
        QTest.mouseClick(card, Qt.MouseButton.LeftButton)
        self.assertEqual(editor.route_list.currentRow(), 1)

    def test_playback_highlight_moves_between_cards_and_clears(self):
        editor = Editor()
        for i in range(3):
            editor.add_stop_card(i + 1, f'Stop {i + 1}', '', '', '')
        cards = [editor.route_list.itemWidget(editor.route_list.item(i)) for i in range(3)]
        editor.highlight_active_leg({'index': 0, 'from_stop': 1, 'to_stop': 2})
        self.assertEqual([card.property('activeLeg') for card in cards], [True, True, False])
        self.assertIn('#ffb77e', cards[0].styleSheet())
        editor.highlight_active_leg({'index': 1, 'from_stop': 2, 'to_stop': 3})
        self.assertEqual([card.property('activeLeg') for card in cards], [False, True, True])
        editor.highlight_active_leg(None)
        self.assertEqual([card.property('activeLeg') for card in cards], [False, False, False])
