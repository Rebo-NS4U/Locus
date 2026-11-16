"""Exercise the normal launch entrypoint without phone commands or saved user state."""
import os
import sys
import tempfile
from unittest.mock import patch, Mock

root = tempfile.TemporaryDirectory(prefix='locus-startup-')
os.environ['XDG_STATE_HOME'] = root.name
os.environ['XDG_CACHE_HOME'] = root.name + '/cache'

from PySide6.QtWidgets import QApplication, QPushButton
from locus_linux.app import Window, main
from locus_linux.activation import opening_geometry
from locus_linux.worker import Worker


def check_launch():
    app = QApplication.instance()
    app.processEvents()
    window = next(widget for widget in app.topLevelWidgets() if isinstance(widget, Window))
    assert window.windowTitle() == 'Exora — DEMO'
    assert window.tray.toolTip() == 'Exora'
    assert app.applicationName() == 'Exora'
    assert app.desktopFileName() == 'locus-linux'
    # Exercise the actual new buttons and their Qt signal connections without
    # starting a phone worker or sending device commands.
    from test_planning import candidate
    window.route_plan.reset([(0, 0), (0, .001), (0, .002)])
    window.route_plan.accept([[candidate([(0, 0), (0, .001)])],
                              [candidate([(0, .001), (0, .002)])]], window.route_plan.revision)
    window.stops_changed()
    window.route_list.setCurrentRow(1)
    window.enable(True)
    buttons = {button.text(): button for button in window.findChildren(QPushButton)}
    with patch.object(window, 'request', Mock()) as request:
        buttons['Start from selected stop'].click()
        assert request.call_args.args[0] == 'start_route'
        assert request.call_args.args[-1] == window.route_profile['legs'][1]['first']
        request.reset_mock()
        buttons['Start'].click()
        assert request.call_args.args[-1] is None, 'Ordinary Start keeps first-stop timing'
        window.motion = {'mode': 'route'}
        buttons['Clear route'].click()
        request.assert_called_with('stop_here')
        window.busy = False
        state = {'connected': True, 'paused': True, 'position': (0, 0),
                 'progress': 0, 'total': 1, 'mode': 'route'}
        for mode in ('manual', 'timer', 'clock'):
            state['planned_stop'] = {'stop': 1, 'distance': 0, 'mode': mode,
                                     'remaining': 600, 'deadline': 1800000000}
            window.motion_changed(state)
            assert not window.continue_stop.isHidden()
            assert window.continue_stop.isEnabled()
            window.continue_stop.click()
            request.assert_called_with('control', 'depart')
        state['connected'] = False
        window.motion_changed(state)
        assert not window.continue_stop.isEnabled()
        state['planned_stop'] = None
        window.motion_changed(state)
        assert window.continue_stop.isHidden()
    window.motion = {}
    window.busy = False
    buttons['Clear route'].click()
    assert not window.stops and not window.route_points
    assert not window.isFullScreen() and not window.isMaximized(), 'Launch as a normal window'
    expected_size = opening_geometry(window.screen().availableGeometry()).size().expandedTo(window.minimumSize())
    assert window.size() == expected_size, 'Leave a visible border around the desktop-sized window'
    window.fullscreen_shortcut.activated.emit()
    assert window.isFullScreen(), 'F11 must still support optional fullscreen'
    window.fullscreen_shortcut.activated.emit()
    assert not window.isFullScreen(), 'F11 must restore a normal window'
    window.activation_server.close()
    window.release_map()
    window.close()
    return 0


with patch.object(Worker, 'start'), patch.object(sys, 'argv', ['exora', '--demo']), \
        patch.object(QApplication, 'exec', side_effect=check_launch):
    assert main() == 0
print('PASS: startup fills the available desktop as a normal window; optional F11 toggle works')
