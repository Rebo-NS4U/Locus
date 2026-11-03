"""Verify that a viewed tile is reused across separate browser processes.

Uses only a local HTTP server and a temporary cache; no public tile requests.
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def browser(url):
    from PySide6.QtCore import QTimer, QUrl
    from PySide6.QtWidgets import QApplication
    from PySide6.QtWebEngineCore import QWebEnginePage
    from shiboken6 import delete
    from locus_linux.map_profile import create_map_profile

    app = QApplication([])
    profile = create_map_profile(app, demo=True)
    page = QWebEnginePage(profile)
    loaded = []
    page.loadFinished.connect(lambda ok: (loaded.append(ok), QTimer.singleShot(500, app.quit)))
    QTimer.singleShot(10000, app.quit)
    page.load(QUrl(url))
    app.exec()
    delete(page)
    delete(profile)
    assert loaded == [True], loaded


def verify():
    requests = []
    image = (Path(__file__).parents[1] / 'locus_linux/web/images/marker-icon.png').read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(image)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(image)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='locus-cache-') as directory:
            env = dict(os.environ, XDG_CACHE_HOME=directory, QT_QPA_PLATFORM='offscreen')
            url = f'http://127.0.0.1:{server.server_port}/tile.png'
            for _ in range(2):
                subprocess.run([sys.executable, __file__, url], env=env, check=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
            assert requests.count('/tile.png') == 1, requests
            print('PASS: two independent launches downloaded the tile only once')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    browser(sys.argv[1]) if len(sys.argv) > 1 else verify()
