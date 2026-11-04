"""Open corrupt demo state, report errors and quit without losing recovery data."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

directory = tempfile.TemporaryDirectory(prefix='locus-recovery-')
os.environ['XDG_STATE_HOME'] = directory.name
os.environ['XDG_CACHE_HOME'] = directory.name + '/cache'

from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox
from PySide6.QtTest import QTest
from locus_linux.app import Window


def wait_for(condition):
    deadline = time.monotonic() + 10
    while not condition() and time.monotonic() < deadline:
        QTest.qWait(20)
    assert condition(), 'Timed out waiting for demo worker'


state = Path(directory.name) / 'locus-linux'
state.mkdir()
places, journal = state / 'demo-places.json', state / 'demo-session.json'
places.write_text('{broken places')
journal.write_text('{broken journal')
app = QApplication([])
window = Window(demo=True)
window.show()
try:
    wait_for(lambda: window.ready and not window.busy)
    assert 'Recovery record' in window.status.text()
    assert window.store.error and window.worker.recovery_error
    assert window.devices.count() == 1
    with patch.object(QInputDialog, 'getText', return_value=('New favorite', True)):
        window.save_favorite()
    assert 'Could not save favorite' in window.status.text()
    assert window.store.data['favorites'] == []
    with patch.object(window.worker.transport, 'set', new_callable=AsyncMock) as send:
        window.perform('apply')
        wait_for(lambda: not window.busy)
        assert 'Recovery record' in window.status.text()
        send.assert_not_called()
    assert places.read_text() == '{broken places'
    assert journal.read_text() == '{broken journal'
finally:
    window.busy = False
    with patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.No):
        window.close()
    wait_for(lambda: not window.worker.isRunning())
    app.aboutToQuit.disconnect(window.release_map)
    window.release_map()
    app.processEvents()
assert journal.read_text() == '{broken journal'
directory.cleanup()
print('PASS: corrupt state opens, errors are visible, phone changes are blocked, original files survive clean quit')
