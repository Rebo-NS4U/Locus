"""Exercise the normal launch entrypoint without phone commands or saved user state."""
import os
import sys
import tempfile
from unittest.mock import patch

root = tempfile.TemporaryDirectory(prefix='locus-startup-')
os.environ['XDG_STATE_HOME'] = root.name
os.environ['XDG_CACHE_HOME'] = root.name + '/cache'

from PySide6.QtWidgets import QApplication
from locus_linux.app import Window, main
from locus_linux.worker import Worker


def check_launch():
    app = QApplication.instance()
    app.processEvents()
    window = next(widget for widget in app.topLevelWidgets() if isinstance(widget, Window))
    assert window.isFullScreen(), 'Normal launches must open fullscreen'
    window.fullscreen_shortcut.activated.emit()
    assert not window.isFullScreen(), 'F11 must restore a normal window'
    window.fullscreen_shortcut.activated.emit()
    assert window.isFullScreen(), 'F11 must reenter fullscreen'
    window.activation_server.close()
    window.release_map()
    window.close()
    return 0


with patch.object(Worker, 'start'), patch.object(sys, 'argv', ['locus-linux', '--demo']), \
        patch.object(QApplication, 'exec', side_effect=check_launch):
    assert main() == 0
print('PASS: normal entrypoint opens fullscreen; F11 toggles window state without phone commands')
