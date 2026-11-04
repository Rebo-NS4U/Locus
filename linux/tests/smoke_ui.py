"""Native Qt/WebEngine end-to-end test with isolated state and fake phone/network."""
import os
import tempfile
import time
import asyncio
from pathlib import Path
from unittest.mock import patch

directory = tempfile.TemporaryDirectory(prefix="locus-ui-")
os.environ["XDG_STATE_HOME"] = directory.name
os.environ["XDG_CACHE_HOME"] = directory.name + "/cache"

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
from PySide6.QtTest import QTest
from locus_linux.app import Window
from locus_linux.routes import parse_gpx, export_gpx
from locus_linux.services import decode_shape
from locus_linux.units import to_mph, to_kph
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
window.auto_plan_timer.setInterval(60000)
window.show()
loaded = False
stage = 0
started = time.monotonic()
marked = started
failure = None
collapsed_width = None
errors = []
gesture_results = []
traffic_future = None
active_map_check = []
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
            profile = window.map.page().profile()
            assert not profile.isOffTheRecord()
            assert profile.httpCacheType() == profile.HttpCacheType.DiskHttpCache
            assert profile.httpCacheMaximumSize() == 256 * 1024 * 1024
            assert profile.cachePath() == directory.name + '/cache/locus-linux/demo-map/http'
            assert 'LocusLinux/' in profile.httpUserAgent()
            assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            # The shell must retain alpha for compositor blur, while controls
            # and map content remain fully readable (no whole-window opacity).
            assert window.centralWidget().objectName() == 'shell'
            shell_pixel = window.grab().toImage().pixelColor(10, window.height() // 2)
            assert 0 < shell_pixel.alpha() < 255, shell_pixel.getRgb()
            for widget in (window.sidebar_toggle, window.status, window.tabs.tabBar(),
                           window.speed, window.segment_group, window.connection_badge):
                assert widget.font().family() == app.font().family()
                assert widget.font().pointSizeF() == app.font().pointSizeF()
                assert widget.font().weight() == app.font().weight()
            assert window.traffic_stops.isChecked()
            window.red_probability.setValue(100)
            assert not window.sidebar_scroll.isVisible()
            # The navigation rail remains reachable when the inspector is hidden.
            assert window.map.width() >= window.width() * .80
            window.navigation_buttons[1].click()
            assert window.tabs.currentIndex() == 1 and window.tabs.isVisible()
            window.sidebar_toggle.setChecked(False)
            app.processEvents()
            assert window.map.height() >= window.height() * .75
            collapsed_width = window.map.width()
            window.worker.services.requester = fake_request
            window.map.page().runJavaScript("""
                (() => {
                    const fixture = {points: [[51.5, -.12], [51.501, -.121]], ready: true,
                        color: palette.accent, label: 'Test road', stops: [
                            {point: [51.5, -.12], kind: 'stop_sign'},
                            {point: [51.501, -.121], kind: 'traffic_light'}]};
                    setRoute([], [], [fixture]);
                    const signs = routeLine.getLayers().filter(layer => layer instanceof L.Marker);
                    const visibleSigns = signs.length === 2
                        && signs[0].getElement().textContent === 'STOP'
                        && signs[1].getElement().querySelectorAll('i').length === 3;
                    setTheme(palette); // Theme updates must support icon markers, too.
                    setRoute([], [], []);
                    const signsCleared = signs.every(sign => !map.hasLayer(sign));
                    const zoom = map.getZoom();
                    const event = {latlng: L.latLng(51.5, -0.12)};
                    map.fire('click', event);
                    map.fire('click', {...event, originalEvent: {detail: 2}});
                    map.fire('dblclick', event);
                    const layers = [];
                    map.eachLayer(layer => layers.push(layer));
                    return visibleSigns && signsCleared && !map.doubleClickZoom.enabled() && map.getZoom() === zoom
                        && !layers.some(layer => layer instanceof L.CircleMarker);
                })()
            """, gesture_results.append)
            stage = 1
        elif stage == 1 and len(window.stops) == 1 and gesture_results:
            assert gesture_results == [True], 'One click must add one route pin without zooming or a preview circle'
            assert window.worker.session.target is None, 'Map clicks must not apply phone location'
            assert not window.sidebar_toggle.isChecked(), 'Pin placement must work with Controls hidden'
            window.sidebar_toggle.setChecked(True)
            app.processEvents()
            assert window.map.width() <= collapsed_width - 300
            tab_geometry = window.tabs.tabBar().geometry()
            for index in range(window.tabs.count()):
                window.tabs.setCurrentIndex(index)
                app.processEvents()
                page = window.sidebar_scroll
                page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
                app.processEvents()
                assert window.tabs.tabBar().geometry() == tab_geometry, 'Navigation must stay pinned'
                assert window.map.width() >= 280, 'Every workspace must leave a usable map'
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
            assert window.sidebar_scroll.height() > 80, 'Studio views must remain scrollable in a small window'
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
            window.search.setText('Westminster')
            window.search_places()
            stage = 3
        elif stage == 3 and not window.busy:
            assert window.results.count() == 1
            window.choose_place(window.results.item(0).data(Qt.ItemDataRole.UserRole))
            assert len(window.stops) == 2, 'Search selection must not add a route pin'
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
            assert abs(window.speed.value() - to_mph(23.4)) < .005
            assert window.speed.suffix() == " mph"
            assert window.segment_speed.suffix() == " mph"
            window.save_settings()
            assert abs(window.store.data['settings']['speed_kph'] - 23.4) < .01
            window.segment_travel.setCurrentText('Cycling')
            window.plan_route()
            stage = 4
        elif stage == 4 and not window.busy:
            assert abs(window.worker.session.speed - to_kph(window.speed.value()) / 3.6) < .0001
            assert window.route_options.count() == 2
            window.route_options.setCurrentIndex(1)
            assert window.route_points == points
            assert window.sidebar_scroll.horizontalScrollBar().maximum() == 0, 'Sidebar must fit without horizontal clipping'
            assert [window.segment_travel.itemText(i) for i in range(window.segment_travel.count())] == ['Walking', 'Driving', 'Cycling']
            window.segment_travel.setCurrentText('Walking')
            assert window.route_plan.spec(0)['profile'] == 'walk_auto'
            window.segment_travel.setCurrentText('Cycling')
            assert window.route_plan.spec(0)['profile'] == 'cycle_paths'
            window.segment_travel.setCurrentText('Driving')
            assert window.route_plan.spec(0)['profile'] == 'car_roads'
            assert not hasattr(window, 'draw_bends')
            assert not hasattr(window.bridge, 'moveBend')
            window.select_coordinates(51.501, -.121)
            window.route_list.setCurrentRow(0)
            window.insert_stop()
            assert len(window.stops) == 3
            assert window.selected_segment() == 0
            assert window.route_list.currentRow() == 1
            assert window.route_list.count() == 5
            assert window.route_plan.spec(0)['profile'] == 'car_roads'
            window.route_list.setCurrentRow(3)
            window.segment_travel.setCurrentText('Walking')
            window.select_coordinates(51.503, -.123)
            window.add_stop()
            assert window.selected_segment() == 2
            assert window.route_list.currentRow() == 5
            window.segment_travel.setCurrentText('Driving')
            window.segment_speed_mode.setCurrentIndex(window.segment_speed_mode.findData('manual'))
            window.segment_speed.setValue(12)
            window.save_settings()
            assert abs(window.store.data['settings']['speed_kph'] - 23.4) < .01
            window.plan_route()
            stage = 42
        elif stage == 42 and not window.busy:
            assert len(window.route_plan.results) == 3
            assert any('limit 30' in source for source in window.route_profile['sources'])
            assert abs(window.route_plan.spec(2)['speed_kph'] - to_kph(12)) < .0001
            assert 'mph' in window.segment_info.text()
            assert 'km/h' not in window.segment_info.text()
            window.route_title.setText('Campus loop')
            window.save_library_route()
            identifier = window.active_route_id
            assert identifier and len(window.route_library.data['routes']) == 1
            window.save_library_route()
            assert len(window.route_library.data['routes']) == 1, 'Save updates the current library route'
            window.library_toggle.setChecked(True)
            window.route_search.setText('not a match')
            assert window.library_list.count() == 0
            window.route_search.setText('campus')
            assert window.library_list.count() == 1
            with patch.object(QInputDialog, 'getText', return_value=('Campus commute', True)):
                window.rename_library_route()
            window.new_library_route()
            assert not window.stops
            window.open_library_route()
            assert len(window.stops) == 4
            assert window.route_title.text() == 'Campus commute'
            assert window.active_route_id == identifier
            from locus_linux.library import RouteLibrary
            persisted = RouteLibrary(window.route_library.path)
            assert persisted.data['draft_route_id'] == identifier
            assert persisted.data['draft_name'] == 'Campus commute'
            assert len(persisted.data['draft']['pins']) == 4
            window.tabs.setCurrentIndex(1)
            window.library_toggle.setChecked(True)
            app.processEvents()
            window.grab().save('/tmp/locus-modern-library.png')
            window.library_toggle.setChecked(False)
            app.processEvents()
            window.grab().save('/tmp/locus-modern-editor.png')
            with patch.object(QFileDialog, 'getSaveFileName', return_value=(str(project), '')):
                window.save_project()
            assert project.exists()
            # Legacy projects must not retain invisible manual shape constraints.
            import json
            legacy = json.loads(project.read_text())
            legacy['legs'][0]['controls'] = [[51.51, -.13]]
            project.write_text(json.dumps(legacy))
            with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(project), '')):
                window.load_project()
            assert [window.route_plan.spec(i)['profile'] for i in range(3)] == ['car_roads', 'walk_auto', 'car_roads']
            assert all(not window.route_plan.spec(i)['controls'] for i in range(3))
            window.save_settings()
            assert abs(window.store.data['settings']['speed_kph'] - 23.4) < .01
            window.plan_route()
            stage = 43
        elif stage == 43 and not window.busy:
            window.route_list.setCurrentRow(1)
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
            assert window.map.width() >= window.width() * .80
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
            assert result['active_leg']['from_stop'] == 1
            assert 'stop 1 → 2' in window.active_leg_label.text()
            assert all(window.route_list.item(row).font().bold() for row in (0, 1, 2))
            window.map.page().runJavaScript("Boolean(activeLeg && activeLeg.index === 0 && legLines[0].options.weight === 8 && document.querySelectorAll('.route-stop-active').length === 2)", active_map_check.append)
            stage = 71
        elif stage == 71 and active_map_check:
            assert active_map_check == [True], active_map_check
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
