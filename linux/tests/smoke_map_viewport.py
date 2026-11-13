"""Exercise map layout and zoom in a real browser, without phone or tile requests."""
import json
from pathlib import Path

from PySide6.QtCore import QEventLoop, QPointF, QTimer, QUrl, Qt
from PySide6.QtGui import QNativeGestureEvent, QPointingDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from locus_linux.map_profile import configure_map_browser


class Offline(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        if info.requestUrl().scheme() in ('http', 'https'):
            info.block(True)


configure_map_browser()
app = QApplication([])
view = QWebEngineView()
interceptor = Offline(view)
view.page().profile().setUrlRequestInterceptor(interceptor)
channel = QWebChannel(view.page())
view.page().setWebChannel(channel)
view.resize(1000, 700)
view.show()
loaded = []
view.loadFinished.connect(loaded.append)
view.load(QUrl.fromLocalFile(str(Path(__file__).parents[1] / 'locus_linux/web/map.html')))
for _ in range(100):
    QTest.qWait(50)
    if loaded:
        break
assert loaded == [True], loaded


def evaluate(script):
    loop = QEventLoop()
    results = []
    def receive(value):
        results.append(value)
        loop.quit()
    view.page().runJavaScript('JSON.stringify(' + script + ')', receive)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert results, 'JavaScript callback timed out'
    return json.loads(results[0])


def geometry():
    return evaluate('''(() => {
      const box = map.getContainer().getBoundingClientRect();
      const controls = ['.leaflet-control-zoom', '.leaflet-control-attribution'];
      return {width: map.getSize().x, height: map.getSize().y,
        center: map.latLngToContainerPoint(map.getCenter()), scale: visualViewport.scale,
        controlsVisible: controls.every(selector => {
          const r = document.querySelector(selector).getBoundingClientRect();
          return r.left >= box.left && r.right <= box.right && r.top >= box.top && r.bottom <= box.bottom;
        })};
    })()''')


initial_center = evaluate('[map.getCenter().lat, map.getCenter().lng]')
# Resize only the map cell: Leaflet's window resize handler cannot detect this.
for width, height in [(500, 400), (850, 600), (320, 300)]:
    evaluate(f"(() => {{ map.getContainer().style.width='{width}px'; map.getContainer().style.height='{height}px'; return true; }})()")
    QTest.qWait(250)
    result = geometry()
    assert (result['width'], result['height']) == (width, height), result
    assert result['controlsVisible'], result
    assert abs(result['center']['x'] - width / 2) <= 1, result
    assert abs(result['center']['y'] - height / 2) <= 1, result
    center = evaluate('[map.getCenter().lat, map.getCenter().lng]')
    assert max(abs(a - b) for a, b in zip(center, initial_center)) < .0001, center

# Touchpad zoom must change Leaflet's tile zoom, keeping browser magnification at 1.
zoom = evaluate('map.getZoom()')
target = view.focusProxy()
pos = QPointF(160, 150)
for kind, value in [(Qt.NativeGestureType.BeginNativeGesture, 0),
                    (Qt.NativeGestureType.ZoomNativeGesture, 1),
                    (Qt.NativeGestureType.EndNativeGesture, 0)]:
    app.sendEvent(target, QNativeGestureEvent(kind, QPointingDevice.primaryPointingDevice(),
                                            2, pos, pos, pos, value, QPointF()))
QTest.qWait(700)
assert evaluate('map.getZoom()') > zoom
assert geometry()['scale'] == 1
assert geometry()['controlsVisible']
view.close()
print('PASS: map cell resizing preserves size, center and visible controls; pinch changes tile zoom without magnifying the page')
