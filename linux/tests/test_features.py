import asyncio
import json
import math
import tempfile
import unittest
from pathlib import Path

from locus_linux.engine import Session
from locus_linux.routes import Route, distance, offset, parse_gpx, export_gpx
from locus_linux.services import Services, endpoint, decode_shape
from locus_linux.storage import Places
from test_engine import Transport


def encode(points):
    output, previous = '', [0, 0]
    for point in points:
        for i, value in enumerate(point):
            number = round(value * 1e6)
            delta, previous[i] = number - previous[i], number
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                output += chr((32 | (delta & 31)) + 63)
                delta >>= 5
            output += chr(delta + 63)
    return output


class GeometryTests(unittest.TestCase):
    def test_route_follows_distance_across_unequal_segments(self):
        route = Route([(0, 0), (0, .001), (0, .01)])
        midpoint = route.at(route.total / 2)
        self.assertAlmostEqual(midpoint[1], .005, places=7)
        self.assertEqual(route.at(route.total), (0, .01))

    def test_dateline_and_poles(self):
        route = Route([(0, 179.9), (0, -179.9)])
        self.assertAlmostEqual(abs(route.at(route.total / 2)[1]), 180)
        self.assertLess(route.total, 23000)
        self.assertTrue(all(math.isfinite(x) for x in offset((90, 0), 10, 10)))

    def test_gpx_namespace_order_and_escaped_names_roundtrip(self):
        points = [(51.5, -.12), (51.5001, -.1201)]
        data = export_gpx(points, 'A&B <route>')
        self.assertEqual(parse_gpx(data), points)
        self.assertIn(b'A&amp;B &lt;route&gt;', data)
        self.assertEqual(parse_gpx(b"<gpx><rte><rtept lon='2' lat='1'/><rtept lon='3' lat='2'/></rte></gpx>"), [(1, 2), (2, 3)])

    def test_gpx_rejects_gaps_entities_invalid_and_oversized(self):
        for data in [b'<!DOCTYPE gpx><gpx/>', b'<gpx><trkseg/><trkseg/></gpx>', b'<gpx><wpt lat="91" lon="1"/></gpx>', b'x' * 8_000_001]:
            with self.assertRaises(ValueError):
                parse_gpx(data)

    def test_shape_decode_roundtrip_and_malformed(self):
        points = [(51.5, -.12), (51.501234, -.121234), (-30, 150)]
        self.assertEqual(decode_shape(encode(points)), points)
        with self.assertRaises(ValueError):
            decode_shape('~')


class MotionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.transport = Transport()
        self.messages = []
        self.session = Session(self.transport, Path(self.directory.name) / 'session.json', self.messages.append)
        await self.session.configure(10, False, True)

    async def asyncTearDown(self):
        if self.session.refresh:
            self.session.refresh.cancel()
            await asyncio.gather(self.session.refresh, return_exceptions=True)
        self.directory.cleanup()

    async def test_joystick_diagonal_normalizes_and_release_holds(self):
        await self.session.apply('phone', 0, 0)
        await self.session.joystick(1, 1)
        await self.session.tick(1)
        self.assertAlmostEqual(distance((0, 0), self.session.target), 10, places=3)
        await self.session.joystick(0, 0)
        held = self.session.target
        await self.session.tick(1)
        self.assertEqual(held, self.session.target)

    async def test_route_pause_resume_arrival_and_no_post_restore_commands(self):
        await self.session.start_route('phone', [(0, 0), (0, .001)])
        await self.session.tick(1)
        await self.session.control('pause')
        held = self.session.target
        await self.session.tick(1)
        self.assertEqual(held, self.session.target)
        await self.session.control('resume')
        for _ in range(12):
            await self.session.tick(1)
        self.assertEqual(self.session.target, (0, .001))
        self.assertTrue(self.session.paused)
        await self.session.restore('phone')
        count = len(self.transport.calls)
        await self.session.tick(1)
        self.assertEqual(count, len(self.transport.calls))

    async def test_sleep_pauses_and_reconnection_never_resumes_route(self):
        await self.session.start_route('phone', [(0, 0), (0, .01)])
        await self.session.tick(30)
        self.assertTrue(self.session.paused)
        self.assertEqual(self.session.progress, 0)
        await self.session.control('resume')
        self.transport.fail_set = True
        await self.session.tick(1, now=10)
        self.assertIsNone(self.session.target)
        self.transport.fail_set = False
        count = len(self.transport.calls)
        await self.session.tick(1, now=11)
        self.assertEqual(count, len(self.transport.calls))
        await self.session.tick(1, now=15)
        self.assertIsNotNone(self.session.target)
        self.assertTrue(self.session.paused)
        self.assertTrue(all(call[0] == 'phone' for call in self.transport.calls))

    async def test_loop_restarts_and_motion_validation(self):
        await self.session.start_route('phone', [(0, 0), (0, .00001)], loop=True)
        await self.session.tick(1)
        self.assertEqual(self.session.progress, self.session.route.total)
        await self.session.configure(0.5, False, False)
        await self.session.tick(1)
        self.assertAlmostEqual(self.session.progress, .5)
        with self.assertRaises(ValueError):
            await self.session.configure(float('nan'), False, False)
        with self.assertRaises(ValueError):
            await self.session.joystick(2, 0)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_validation_cache_and_route_costing(self):
        requests = []
        points = [(51.5, -.12), (51.51, -.12)]

        def fake(url, params):
            requests.append(params)
            if 'q' in params:
                return {'features': [{'geometry': {'coordinates': [-.12, 51.5]}, 'properties': {'name': 'Bridge', 'city': 'London'}}]}
            return {'trip': {'status': 0, 'legs': [{'shape': encode(points)}], 'summary': {'time': 400}}}

        service = Services(fake)
        self.assertEqual((await service.search('London'))[0]['point'], points[0])
        await service.search('London')
        self.assertEqual(len(requests), 1)
        options = await service.route(points, 'Cycle')
        self.assertEqual(json.loads(requests[-1]['json'])['costing'], 'bicycle')
        self.assertEqual(options[0]['points'], points)
        with self.assertRaises(ValueError):
            await service.search('a')
        with self.assertRaises(ValueError):
            await service.route([points[0]], 'Walk')

    async def test_provider_errors_and_https_validation(self):
        for url in ['http://example.org/', 'https://user:password@example.org/', 'https://example.org/?q=secret']:
            with self.assertRaises(ValueError):
                endpoint(url)
        service = Services(lambda url, params: {'error': 'No path'})
        with self.assertRaisesRegex(ValueError, 'No path'):
            await service.route([(0, 0), (1, 1)], 'Walk')


class PersistenceTests(unittest.TestCase):
    def test_favorites_recents_rename_remove_and_permissions(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'places.json'
            places = Places(path)
            places.add('favorites', 'First', (1, 2))
            places.add('favorites', 'Updated', (1, 2))
            self.assertEqual(len(places.data['favorites']), 1)
            places.rename('favorites', 0, 'Renamed')
            self.assertEqual(Places(path).data['favorites'][0]['name'], 'Renamed')
            for i in range(25):
                places.add('recents', str(i), (i, 0))
            self.assertEqual(len(Places(path).data['recents']), 20)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            places.remove('favorites', 0)
            self.assertFalse(Places(path).data['favorites'])


if __name__ == '__main__':
    unittest.main()
