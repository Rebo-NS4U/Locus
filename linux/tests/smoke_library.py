"""GUI route persistence and recovery without starting a phone worker."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

root = tempfile.TemporaryDirectory(prefix='locus-library-')
os.environ['XDG_STATE_HOME'] = root.name
os.environ['XDG_CACHE_HOME'] = root.name + '/cache'

from PySide6.QtWidgets import QApplication, QMessageBox
from locus_linux.app import Window, Worker
from locus_linux.library import RouteLibrary
from locus_linux.planning import RoutePlan

app = QApplication([])
path = Path(root.name) / 'locus-linux/demo-routes.json'
plan = RoutePlan()
plan.reset([(35, -111), (35.001, -111), (35.002, -111)])
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
    first.move_stop(0, 35.0001, -111)
    second = Window(demo=True)
    second.busy = False
    assert second.stops[0] == (35.0001, -111)
    assert second.route_title.text() == 'Campus'
    second.library_list.setCurrentRow(0)
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
        second.delete_library_route()
    assert not second.route_library.data['routes']
    assert len(second.stops) == 3, 'Deleting a library entry retains the working route'
    assert second.active_route_id is None
    track = {'format': 'locus-track', 'version': 1, 'points': [(35, -111), (35.001, -111), (35.002, -111)]}
    second.apply_route_document(track)
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
