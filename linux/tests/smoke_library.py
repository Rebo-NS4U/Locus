"""GUI route persistence and recovery without starting a phone worker."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

root = tempfile.TemporaryDirectory(prefix='locus-library-')
os.environ['XDG_STATE_HOME'] = root.name
os.environ['XDG_CACHE_HOME'] = root.name + '/cache'

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtTest import QTest
from PySide6.QtCore import QTime
from locus_linux.app import Window, Worker
from locus_linux.library import RouteLibrary
from locus_linux.planning import RoutePlan

app = QApplication([])
path = Path(root.name) / 'locus-linux/demo-routes.json'
plan = RoutePlan()
plan.reset([(35, -111), (35.001, -111), (35.002, -111)])
plan.update(0, {'profile': 'walk_sidewalks', 'controls': [(35.0005, -111)]})
plan.update(1, {'profile': 'car_roads'})
library = RouteLibrary(path)
identifier = library.save_route('Campus', plan.serialize())
library.save_draft(plan.serialize(), 'Campus', identifier)
with patch.object(Worker, 'start'):
    first = Window(demo=True)
    first.busy = False
    assert first.active_route_id == identifier
    assert first.route_title.text() == 'Campus'
    assert len(first.stops) == 3
    assert first.route_plan.spec(1)['profile'] == 'car_roads'
    assert first.route_plan.spec(0)['profile'] == 'walk_auto'
    assert not first.route_plan.spec(0)['controls']
    first.route_list.setCurrentRow(2)
    first.departure_mode.setCurrentIndex(first.departure_mode.findData('timer'))
    first.departure_seconds.setValue(90)
    assert first.route_plan.pins[1]['departure'] == {'mode': 'timer', 'seconds': 90}
    first.departure_mode.setCurrentIndex(first.departure_mode.findData('clock'))
    first.departure_time.hour.setCurrentIndex(first.departure_time.hour.findData(4))
    first.departure_time.minute.setCurrentIndex(first.departure_time.minute.findData(30))
    first.departure_time.period.setCurrentText('PM')
    assert first.departure_time.text() == '4:30 PM'
    assert first.route_plan.pins[1]['departure'] == {'mode': 'clock', 'time': '16:30'}
    first.departure_time.setTime(QTime(0, 0))
    assert first.departure_time.text() == '12:00 AM'
    first.departure_time.setTime(QTime(12, 0))
    assert first.departure_time.text() == '12:00 PM'
    first.departure_time.setTime(QTime(16, 30))
    first.move_stop(0, 35.0001, -111)
    assert first.auto_plan_timer.isActive()
    first.ready = True
    with patch.object(first, 'plan_route') as plan_call:
        first.busy = True
        first.auto_plan_timer.start(1)
        QTest.qWait(50)
        plan_call.assert_not_called()
        first.busy = False
        first.auto_plan_timer.start(1)
        QTest.qWait(50)
        plan_call.assert_called_once()
    first.ready = False
    first.auto_plan_timer.stop()
    second = Window(demo=True)
    second.busy = False
    assert second.stops[0] == (35.0001, -111)
    assert second.route_title.text() == 'Campus'
    assert second.route_plan.pins[1]['departure'] == {'mode': 'clock', 'time': '16:30'}
    second.library_list.setCurrentRow(0)
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        second.delete_library_route()
    assert not second.route_library.data['routes']
    assert len(second.stops) == 3, 'Deleting a library entry retains the working route'
    assert second.active_route_id is None
    track = {'format': 'locus-track', 'version': 1, 'points': [(35, -111), (35.001, -111), (35.002, -111)]}
    second.apply_route_document(track)
    assert second.imported_track
    with patch.object(second, 'plan_route') as plan_call:
        second.refresh_automatic_route()
        plan_call.assert_not_called()
    second.route_title.setText('Imported walk')
    second.save_library_route()
    second.new_library_route()
    second.open_library_route()
    assert second.imported_track and len(second.route_points) == 3
    assert second.route_title.text() == 'Imported walk'
    for window in (second, first):
        window.close()
        app.aboutToQuit.disconnect(window.release_map)
        window.release_map()
app.processEvents()
root.cleanup()
print('PASS: GUI restart recovery, autosaved edits, delete retains draft, imported GPX library roundtrip')
