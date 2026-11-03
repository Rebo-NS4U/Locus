"""Native Qt/WebEngine end-to-end test with isolated state and fake phone/network."""
import os
import tempfile
import time
import asyncio
from pathlib import Path
from unittest.mock import patch

directory = tempfile.TemporaryDirectory(prefix="locus-ui-")
os.environ["XDG_STATE_HOME"] = directory.name

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from PySide6.QtTest import QTest
from locus_linux.app import Window
from locus_linux.routes import parse_gpx, export_gpx
from locus_linux.services import decode_shape
from test_features import encode

points = [(51.5, -.12), (51.501, -.121), (51.502, -.122)]
gpx = Path(directory.name) / 'input.gpx'
gpx.write_bytes(export_gpx(points))
exported = Path(directory.name) / 'output.gpx'
project = Path(directory.name) / 'mixed.locus.json'


def fake_request(url, params):
    if url.endswith('trace_attributes'):
        shape = params['_json_body']['encoded_polyline']
        traced = decode_shape(shape)
        return {'shape': shape, 'edges': [{'begin_shape_index': 0, 'end_shape_index': len(traced) - 1, 'speed': 50, 'speed_limit': 30, 'surface': 'paved_smooth', 'end_node': {'traffic_signal': True}}]}
    if 'q' in params:
        return {'features': [{'geometry': {'coordinates': [-.12, 51.5]}, 'properties': {'name': 'Westminster', 'city': 'London'}}]}
    import json
    locations = json.loads(params['json'])['locations']
    route = [(p['lat'], p['lon']) for p in locations]
    if len(route) == 2:
        a, b = route
        route = [a, ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), b]
    trip = {'status': 0, 'legs': [{'shape': encode(route)}], 'summary': {'time': 120}}
    return {'trip': trip, 'alternates': [{'trip': {**trip, 'summary': {'time': 150}}}]}


app = QApplication([])
window = Window(demo=True)
window.show()
loaded = False
stage = 0
started = time.monotonic()
marked = started
failure = None
collapsed_width = None
errors = []
traffic_future = None
window.worker.failed.connect(lambda operation, error: errors.append((operation, error)))


def map_loaded(ok):
    global loaded
    loaded = ok


window.map.loadFinished.connect(map_loaded)


def check():
    global stage, failure, marked, collapsed_width, traffic_future
    try:
        if time.monotonic() - started > 65:
            raise AssertionError(f"UI timeout at stage {stage}: {window.status.text()}")
        assert not errors, errors
        if stage == 0 and loaded and not window.busy:
            assert window.devices.currentData() == 'demo'
            assert window.colors['accent'].startswith('#')
            assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            # The shell must retain alpha for compositor blur, while controls
            # and map content remain fully readable (no whole-window opacity).
            assert window.centralWidget().objectName() == 'shell'
            shell_pixel = window.grab().toImage().pixelColor(10, window.height() // 2)
            assert 0 < shell_pixel.alpha() < 255, shell_pixel.getRgb()
            assert window.traffic_stops.isChecked()
            window.red_probability.setValue(100)
            assert not window.sidebar_scroll.isVisible()
            assert window.map.width() >= window.width() * .95
            assert window.map.height() >= window.height() * .85
            collapsed_width = window.map.width()
            window.sidebar_toggle.setChecked(True)
            window.worker.services.requester = fake_request
            window.draw_stops.setChecked(True)
            window.map.page().runJavaScript("map.fire('click', {latlng: L.latLng(51.5, -0.12)})")
            stage = 1
        elif stage == 1 and len(window.stops) == 1:
            assert window.map.width() <= collapsed_width - 300
            tab_geometry = window.tabs.tabBar().geometry()
            map_geometry = window.map.geometry()
            for index in range(window.tabs.count()):
                window.tabs.setCurrentIndex(index)
                app.processEvents()
                page = window.sidebar_scroll
                page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                app.processEvents()
                assert window.tabs.tabBar().geometry() == tab_geometry, 'Navigation must stay pinned'
                assert window.map.geometry() == map_geometry, 'Switching pages must not shift the map'
                assert page.horizontalScrollBar().maximum() == 0, 'Controls must fit the panel'
            window.tabs.setCurrentIndex(1)
            app.processEvents()
            footer_geometry = window.route_footer.geometry()
            for value in (0, window.sidebar_scroll.verticalScrollBar().maximum()):
                window.sidebar_scroll.verticalScrollBar().setValue(value)
                app.processEvents()
                assert window.route_footer.geometry() == footer_geometry, 'Route actions must stay pinned'
                assert window.plan_button.isVisible()
            original_size = window.size()
            window.resize(900, 600)
            app.processEvents()
            assert window.route_footer.geometry().bottom() < window.tabs.currentWidget().height()
            assert window.sidebar_scroll.height() > 150, 'Editor must remain usable in a small window'
            assert window.progress.height() >= window.progress.fontMetrics().lineSpacing() * 3
            window.resize(original_size)
            window.tabs.setCurrentIndex(0)
            window.split.setSizes([420, window.split.width() - 424])
            app.processEvents()
            panel_width = window.split.sizes()[0]
            window.sidebar_toggle.setChecked(False)
            app.processEvents()
            window.sidebar_toggle.setChecked(True)
            app.processEvents()
            assert abs(window.split.sizes()[0] - panel_width) <= 1, 'Reopening must preserve panel width'
            badge_geometry = window.connection_badge.geometry()
            window.connection_badge.setText('A much longer connection status that must not move the controls')
            app.processEvents()
            assert window.connection_badge.geometry() == badge_geometry

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
            window.segment_profile.setCurrentIndex(window.segment_profile.findData('car_roads'))
            window.draw_bends.setChecked(True)
            window.map.page().runJavaScript("map.fire('click', {latlng: L.latLng(51.5005, -0.1207)})")
            stage = 40
        elif stage == 40 and len(window.route_plan.spec(0)['controls']) == 1:
            window.map.page().runJavaScript("bendMarkers[0].setLatLng([51.5006,-0.1206]); bendMarkers[0].fire('dragend')")
            stage = 41
        elif stage == 41 and all(abs(a - b) < 1e-8 for a, b in zip(window.route_plan.spec(0)['controls'][0], (51.5006, -.1206))):
            window.draw_bends.setChecked(False)
            window.pick(51.501, -.121)
            window.stop_list.setCurrentRow(0)
            window.insert_stop()
            assert len(window.stops) == 3
            assert window.route_plan.spec(0)['profile'] == 'car_roads'
            window.segment_list.setCurrentRow(1)
            window.segment_profile.setCurrentIndex(window.segment_profile.findData('walk_trails'))
            window.pick(51.503, -.123)
            window.add_stop()
            window.segment_list.setCurrentRow(2)
            window.segment_profile.setCurrentIndex(window.segment_profile.findData('offroad_car'))
            window.segment_speed_mode.setCurrentIndex(window.segment_speed_mode.findData('manual'))
            window.segment_speed.setValue(12)
            window.plan_route()
            stage = 42
        elif stage == 42 and not window.busy:
            assert len(window.route_plan.results) == 3
            assert any('limit 30' in source for source in window.route_profile['sources'])
            assert window.route_plan.spec(2)['speed_kph'] == 12
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(project), '')):
                window.save_project()
            assert project.exists()
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(project), '')):
                window.load_project()
            assert [window.route_plan.spec(i)['profile'] for i in range(3)] == ['car_roads', 'walk_trails', 'offroad_car']
            window.plan_route()
            stage = 43
        elif stage == 43 and not window.busy:
            window.segment_list.setCurrentRow(0)
            assert window.route_options.count() == 2
            window.perform('prepare')
            before = len(window.stops)
            window.add_stop()
            assert len(window.stops) == before, 'A pending phone operation must block route edits'
            stage = 5
        elif stage == 5 and not window.busy:
            assert 'Demo ready' in window.status.text()
            window.perform('apply')
            stage = 6
        elif stage == 6 and not window.busy:
            assert window.store.data['recents']
            assert window.worker.session.journal.exists()
            window.sidebar_toggle.setChecked(False)
            window.tabs.setCurrentIndex(1)
            window.start_route()
            stage = 7
            marked = time.monotonic()
        elif stage == 7 and not window.busy and time.monotonic() - marked > 1.3:
            assert window.worker.session.progress > 0
            assert window.map.width() >= window.width() * .95
            window.grab().save('/tmp/locus-linux-map-space.png')
            window.sidebar_toggle.setChecked(True)
            async def reach_control():
                for _ in range(100):
                    await window.worker.session.tick(1)
                    if window.worker.session.snapshot().get('traffic_stop'):
                        return window.worker.session.snapshot()
                raise AssertionError('Traffic light was not reached')
            traffic_future = asyncio.run_coroutine_threadsafe(reach_control(), window.worker.loop)
            stage = 70
        elif stage == 70 and traffic_future.done() and 'red light' in window.connection_badge.text().lower():
            result = traffic_future.result()
            assert result['traffic_stop']['kind'] == 'traffic_light'
            assert result['speed_kph'] == 0
            assert 'red light' in window.connection_badge.text().lower()
            window.grab().save('/tmp/locus-linux-traffic-stop.png')
            window.request('control', 'pause')
            stage = 8
        elif stage == 8 and not window.busy:
            assert window.worker.session.paused
            window.grab().save('/tmp/locus-linux-route.png')
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
