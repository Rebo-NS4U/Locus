"""Single instance activation must show the existing window."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import tempfile
import unittest
from PySide6.QtWidgets import QApplication, QWidget
from locus_linux.activation import activate_existing, listen_for_activation


class ActivationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_repeated_launch_shows_hidden_window(self):
        with tempfile.TemporaryDirectory() as directory:
            name = directory + "/activation"
            self.assertFalse(activate_existing(name))
            window = QWidget()
            server = listen_for_activation(name, window)
            try:
                for _ in range(2):
                    window.hide()
                    self.assertTrue(activate_existing(name))
                    self.app.processEvents()
                    self.assertTrue(window.isVisible())
            finally:
                server.close()
                window.close()
