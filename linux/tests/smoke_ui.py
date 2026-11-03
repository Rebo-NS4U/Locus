"""Native Qt/WebEngine end-to-end test with isolated state and fake phone/network."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

directory = tempfile.TemporaryDirectory(prefix="locus-ui-")
os.environ["XDG_STATE_HOME"] = directory.name

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from PySide6.QtTest import QTest
from locus_linux.app import Window
from locus_linux.routes import parse_gpx, export_gpx
from test_features import encode

points = [(51.5, -.12), (51.501, -.121), (51.502, -.122)]
gpx = Path(directory.name) / 'input.gpx'
gpx.write_bytes(export_gpx(points))
exported = Path(directory.name) / 'output.gpx'


def fake_request(url, params):
    if 'q' in params:
        return {'features': [{'geometry': {'coordinates': [-.12, 51.5]}, 'properties': {'name': 'Westminster', 'city': 'London'}}]}
    trip = {'status': 0, 'legs': [{'shape': encode(points)}], 'summary': {'time': 120}}
    return {'trip': trip, 'alternates': [{'trip': {**trip, 'summary': {'time': 150}}}]}


app = QApplication([])
window = Window(demo=True)
window.show()
loaded = False
stage = 0
started = time.monotonic()
marked = started
failure = None
errors = []
window.worker.failed.connect(lambda operation, error: errors.append((operation, error)))


def map_loaded(ok):
    global loaded
    loaded = ok


window.map.loadFinished.connect(map_loaded)


def check():
    global stage, failure, marked
    try:
        if time.monotonic() - started > 55:
            raise AssertionError(f"UI timeout at stage {stage}: {window.status.text()}")
        assert not errors, errors
        if stage == 0 and loaded and not window.busy:
            assert window.devices.currentData() == 'demo'
            assert window.colors['accent'].startswith('#')
            window.worker.services.requester = fake_request
            window.draw_stops.setChecked(True)
            window.map.page().runJavaScript("map.fire('click', {latlng: L.latLng(51.5, -0.12)})")
            stage = 1
        elif stage == 1 and len(window.stops) == 1:
            window.map.page().runJavaScript("map.fire('click', {latlng: L.latLng(51.502, -0.122)})")
            stage = 2
        elif stage == 2 and len(window.stops) == 2:
            window.draw_stops.setChecked(False)
            window.search.setText('Westminster')
            window.search_places()
            stage = 3
        elif stage == 3 and not window.busy:
            assert window.results.count() == 1
            window.choose_place(window.results.item(0).data(Qt.ItemDataRole.UserRole))
            assert window.worker.session.target is None, 'Search selection must only preview'
            window.show_setup()
            assert window.setup_dialog.isVisible()
            window.setup_dialog.accept()
            assert window.store.data['settings']['setup_seen']
            with patch.object(QInputDialog, 'getText', return_value=('London favorite', True)):
                window.save_favorite()
            assert window.saved['favorites'].count() == 1
            window.saved['favorites'].setCurrentRow(0)
            with patch.object(QInputDialog, 'getText', return_value=('Westminster favorite', True)):
                window.rename_place()
            assert window.store.data['favorites'][0]['name'] == 'Westminster favorite'
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(gpx), '')):
                window.import_gpx()
            assert window.route_points == points
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(exported), '')):
                window.export_gpx()
            assert parse_gpx(exported.read_bytes()) == points
            window.travel_mode.setCurrentText('Cycle')
            assert window.speed.value() == 23.4
            window.plan_route()
            stage = 4
        elif stage == 4 and not window.busy:
            assert window.route_options.count() == 2
            window.route_options.setCurrentIndex(1)
            assert window.route_points == points
            assert window.sidebar_scroll.horizontalScrollBar().maximum() == 0, 'Sidebar must fit without horizontal clipping'
            window.perform('prepare')
            stage = 5
        elif stage == 5 and not window.busy:
            assert 'Demo ready' in window.status.text()
            window.perform('apply')
            stage = 6
        elif stage == 6 and not window.busy:
            assert window.store.data['recents']
            assert window.worker.session.journal.exists()
            window.grab().save('/tmp/locus-linux-preview.png')
            window.tabs.setCurrentIndex(1)
            window.start_route()
            stage = 7
            marked = time.monotonic()
        elif stage == 7 and not window.busy and time.monotonic() - marked > 1.3:
            assert window.worker.session.progress > 0
            window.grab().save('/tmp/locus-linux-route.png')
            window.request('control', 'pause')
            stage = 8
        elif stage == 8 and not window.busy:
            assert window.worker.session.paused
            window.request('control', 'resume')
            stage = 9
        elif stage == 9 and not window.busy:
            assert not window.worker.session.paused
            window.request('control', 'hold')
            stage = 10
        elif stage == 10 and not window.busy:
            window.joystick(1, 0)
            marked = time.monotonic()
            stage = 11
        elif stage == 11 and time.monotonic() - marked > 1.3:
            assert window.worker.session.mode == 'joystick'
            window.release_keys()  # Also releases pointer motion on deactivation.
            stage = 12
        elif stage == 12 and window.worker.session.mode == 'fixed':
            window.tabs.setCurrentIndex(2)
            window.keyboard.setChecked(True)
            window.tabs.setFocus()
            QTest.keyPress(window, Qt.Key.Key_W)
            stage = 15
        elif stage == 15 and window.worker.session.mode == 'joystick':
            assert Qt.Key.Key_W in window.keys
            QTest.keyRelease(window, Qt.Key.Key_W)
            stage = 16
        elif stage == 16 and window.worker.session.mode == 'fixed':
            QTest.keyPress(window, Qt.Key.Key_D)
            stage = 17
        elif stage == 17 and window.worker.session.mode == 'joystick':
            window.tabs.setCurrentIndex(0)
            window.search.setFocus()
            QTest.keyRelease(window.search, Qt.Key.Key_D)
            stage = 18
        elif stage == 18 and window.worker.session.mode == 'fixed':
            QTest.keyClicks(window.search, 'WAD')
            assert not window.keys, 'Typing must not engage keyboard movement'
            window.perform('restore')
            stage = 13
        elif stage == 13 and not window.busy:
            assert not window.worker.session.journal.exists()
            window.connection.setCurrentIndex(1)
            stage = 14
        elif stage == 14 and not window.busy and window.worker.transport.mode == 'wifi':
            assert window.devices.currentData() == 'demo'
            window.saved['favorites'].setCurrentRow(0)
            window.remove_place()
            assert not window.store.data['favorites']
            timer.stop()
            print('PASS: native theme/map, drawn stops, search, favorite CRUD, recents, GPX roundtrip, alternate routes, speed presets, prepare/set, route start/pause/resume, joystick release, restore, Wi-Fi selection, clean quit')
            window.close()
    except Exception as error:
        failure = error
        timer.stop()
        window.worker.stop()


timer = QTimer()
timer.timeout.connect(check)
timer.start(100)
app.exec()
directory.cleanup()
if failure:
    raise failure
