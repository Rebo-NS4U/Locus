import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from locus_linux.engine import Session, DemoTransport, coordinates


class Transport(DemoTransport):
    def __init__(self):
        self.calls = []
        self.fail_set = self.fail_clear = False

    async def set(self, device, lat, lon):
        self.calls.append((device, lat, lon))
        if self.fail_set:
            raise ConnectionError("disconnected")

    async def clear(self, device):
        self.calls.append(("clear", device))
        if self.fail_clear:
            raise ConnectionError("disconnected")


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.journal = Path(self.directory.name) / "session.json"
        self.transport = Transport()
        self.messages = []
        self.session = Session(self.transport, self.journal, self.messages.append)

    async def asyncTearDown(self):
        if self.session.refresh:
            self.session.refresh.cancel()
            await asyncio.gather(self.session.refresh, return_exceptions=True)
        self.directory.cleanup()

    async def test_invalid_coordinates_never_touch_device(self):
        for lat, lon in [(91, 0), (0, -181), (float('nan'), 1), (1, float('inf')), (True, 0)]:
            with self.assertRaises(ValueError):
                await self.session.apply("phone", lat, lon)
        self.assertEqual(self.transport.calls, [])
        self.assertFalse(self.journal.exists())

    async def test_acknowledged_target_refreshes_and_restore_stops_updates(self):
        await self.session.apply("phone", 10, 20)
        await asyncio.sleep(1.1)
        self.assertGreaterEqual(len(self.transport.calls), 2)
        await self.session.restore("phone")
        count = len(self.transport.calls)
        await asyncio.sleep(1.1)
        self.assertEqual(len(self.transport.calls), count)
        self.assertFalse(self.journal.exists())

    async def test_failed_apply_keeps_uncertain_session(self):
        self.transport.fail_set = True
        with self.assertRaises(ConnectionError):
            await self.session.apply("phone", 10, 20)
        self.assertIsNone(self.session.target)
        self.assertEqual(json.loads(self.journal.read_text())["device"], "phone")

    async def test_failed_restore_keeps_recovery_record(self):
        await self.session.apply("phone", 10, 20)
        self.transport.fail_clear = True
        with self.assertRaises(ConnectionError):
            await self.session.restore("phone")
        self.assertTrue(self.journal.exists())

    async def test_other_phone_blocked_even_after_restart(self):
        self.session.record("original", [10, 20])
        for operation in [self.session.apply("different", 1, 2), self.session.restore("different")]:
            with self.assertRaises(RuntimeError):
                await operation
        self.assertEqual(self.transport.calls, [])

    async def test_corrupt_recovery_blocks_device_changes_without_erasing_journal(self):
        for content in ['{broken', '[]', '{}', '{"device": 3}',
                        '{"device": "phone", "transport": "other"}']:
            with self.subTest(content=content):
                self.journal.write_text(content)
                for operation in [self.session.apply('phone', 1, 2), self.session.restore('phone')]:
                    with self.assertRaisesRegex(RuntimeError, 'Recovery record'):
                        await operation
                self.assertEqual(self.journal.read_text(), content)
                self.assertEqual(self.transport.calls, [])

    async def test_refresh_failure_reports_unknown_and_stops(self):
        await self.session.apply("phone", 10, 20)
        self.transport.fail_set = True
        await asyncio.sleep(1.1)
        self.assertIsNone(self.session.target)
        self.assertIn("unknown", self.messages[0])
        self.assertTrue(self.journal.exists())

    async def test_cancelled_update_stops_refresh_and_retains_recovery(self):
        await self.session.apply("phone", 10, 20)

        async def cancelled_set(*args):
            raise asyncio.CancelledError()

        self.transport.set = cancelled_set
        with self.assertRaises(asyncio.CancelledError):
            await self.session.apply("phone", 30, 40)
        self.assertIsNone(self.session.target)
        self.assertTrue(self.journal.exists())


if __name__ == "__main__":
    unittest.main()
