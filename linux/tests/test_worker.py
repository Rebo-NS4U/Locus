import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QSignalSpy
from locus_linux.worker import Worker


class WorkerRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_corrupt_journal_does_not_abort_readiness_or_erase_evidence(self):
        for content in ['{broken', '[]', 'null', '{}', '{"device": 3}',
                        '{"device": "phone", "transport": "other"}']:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as folder:
                journal = Path(folder) / 'demo-session.json'
                journal.write_text(content)
                worker = Worker(True)
                ready = QSignalSpy(worker.ready)
                loop = asyncio.new_event_loop()
                loop.call_soon(loop.stop)
                with patch('locus_linux.worker.state_directory', return_value=Path(folder)), \
                        patch('locus_linux.worker.asyncio.new_event_loop', return_value=loop):
                    worker.run()
                self.assertEqual(ready.count(), 1)
                self.assertTrue(loop.is_closed())
                self.assertIsNotNone(worker.recovery_error)
                self.assertEqual(journal.read_text(), content)
                self.assertIsNone(worker.session.target)

    def test_valid_wifi_journal_restores_transport_without_applying_location(self):
        with tempfile.TemporaryDirectory() as folder:
            journal = Path(folder) / 'demo-session.json'
            journal.write_text(json.dumps({'device': 'phone', 'transport': 'wifi'}))
            worker = Worker(True)
            loop = asyncio.new_event_loop()
            loop.call_soon(loop.stop)
            with patch('locus_linux.worker.state_directory', return_value=Path(folder)), \
                    patch('locus_linux.worker.asyncio.new_event_loop', return_value=loop):
                worker.run()
            self.assertIsNone(worker.recovery_error)
            self.assertEqual(worker.transport.mode, 'wifi')
            self.assertIsNone(worker.session.target)
