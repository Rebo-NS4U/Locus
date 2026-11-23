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


class WorkerPlanningTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_round_trip_renews_deadline_for_each_segment(self):
        from locus_linux.services import Services

        worker = Worker(True)
        worker.loop = asyncio.get_running_loop()
        worker.operation_lock = asyncio.Lock()
        worker.services = Services()
        finished = worker.loop.create_future()
        worker.finished_operation.connect(lambda _, result: finished.set_result(result))
        worker.failed.connect(lambda _, message: finished.set_exception(RuntimeError(message)))
        progress = []
        worker.planning_progress.connect(lambda n, total: progress.append((n, total)))
        now = worker.loop.time()

        async def slow_route(points, *args):
            nonlocal now
            now += 100
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return [{'points': points}]

        worker.services.route = slow_route
        leg = {'start': (0, 0), 'end': (0, .001), 'spec': {'profile': 'cycle_roads'}}
        with patch.object(worker.loop, 'time', side_effect=lambda: now):
            worker.submit('mixed_route', [leg], 'https://example.org/route',
                          'https://example.org/overpass', False, True)
            result = await finished
        self.assertEqual(len(result), 2)
        self.assertEqual(progress, [(1, 2), (2, 2)])
