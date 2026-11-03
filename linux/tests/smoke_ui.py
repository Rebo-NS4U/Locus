"""Real Qt/WebEngine integration test; demo transport only, isolated state."""
import os
import tempfile
import time

directory = tempfile.TemporaryDirectory(prefix="locus-ui-")
os.environ["XDG_STATE_HOME"] = directory.name

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from locus_linux.app import Window

app = QApplication([])
window = Window(demo=True)
window.show()
loaded = False
stage = 0
started = time.monotonic()
applied_at = None
failure = None


def map_loaded(ok):
    global loaded
    loaded = ok


window.map.loadFinished.connect(map_loaded)


def check():
    global stage, failure, applied_at
    try:
        if time.monotonic() - started > 35:
            raise AssertionError(f"UI timeout at stage {stage}: {window.status.text()}")
        if stage == 0 and loaded and not window.busy:
            assert window.devices.currentData() == "demo"
            window.map.page().runJavaScript("map.fire('click', {latlng: L.latLng(51.5, -0.12)})")
            stage = 1
        elif stage == 1 and window.latitude.value() == 51.5:
            assert window.longitude.value() == -0.12
            window.perform("prepare")
            stage = 2
        elif stage == 2 and not window.busy:
            assert "Demo ready" in window.status.text(), window.status.text()
            window.perform("apply")
            stage = 3
        elif stage == 3 and not window.busy:
            assert "acknowledged" in window.status.text(), window.status.text()
            assert window.worker.session.journal.exists()
            window.grab().save("/tmp/locus-linux-preview.png")
            applied_at = time.monotonic()
            stage = 4
        elif stage == 4 and time.monotonic() - applied_at > 1.2:
            window.perform("restore")
            stage = 5
        elif stage == 5 and not window.busy:
            assert "Clear command sent" in window.status.text(), window.status.text()
            assert not window.worker.session.journal.exists()
            timer.stop()
            print("PASS: native window, map click bridge, USB demo discovery, prepare, apply, refresh, restore, clean quit")
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
