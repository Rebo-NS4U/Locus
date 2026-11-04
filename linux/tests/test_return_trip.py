import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from locus_linux.planning import RoutePlan
from locus_linux.services import Services
from locus_linux.library import RouteLibrary
from locus_linux.engine import Session
from test_engine import Transport
from test_planning import candidate


class ReturnTripTests(unittest.IsolatedAsyncioTestCase):
    async def test_return_is_routed_separately_in_reverse_mode_order(self):
        plan = RoutePlan()
        a, b, c = (0, 0), (0, .001), (0, .002)
        plan.reset([a, b, c])
        plan.update(0, {'profile': 'car_roads', 'speed_mode': 'manual', 'speed_kph': 18})
        plan.set_departure(1, {'mode': 'timer', 'seconds': 10})
        plan.set_departure(2, {'mode': 'manual'})
        plan.set_return_trip(True)
        service = Services()
        service.garage_connection = AsyncMock(return_value=None)
        service.indoor_connection = AsyncMock(return_value=None)
        calls = []
        async def route(points, mode, *unused):
            calls.append((points, mode))
            shape = [b, (.0001, .0005), a] if points == [b, a] else points
            return [{'points': shape}]
        service.route = route
        result = await service.mixed_route(plan.requests(), include_stops=False, there_and_back=True)
        self.assertEqual([mode for _, mode in calls], ['Drive', 'Walk', 'Walk', 'Drive'])
        self.assertEqual([points for points, _ in calls], [[a, b], [b, c], [c, b], [b, a]])
        plan.accept(result, plan.revision)
        combined = plan.assemble()
        self.assertEqual(combined['points'], [a, b, c, b, (.0001, .0005), a])
        self.assertEqual([event['stop'] for event in combined['planned_stops']], [2, 3, 2])
        self.assertEqual([(s['from_stop'], s['to_stop']) for s in plan.segments()], [(1, 2), (2, 3), (3, 2), (2, 1)])
        plan.update(0, {'profile': 'cycle_paths'})
        self.assertNotIn(plan.key(0), plan.results)
        self.assertNotIn(plan.key(3), plan.results)

    async def test_99_main_pins_support_all_196_trip_legs(self):
        plan = RoutePlan()
        plan.reset([(0, i / 10000) for i in range(99)])
        for i in range(98):
            plan.update(i, {'profile': 'offroad_walk'})
        plan.set_return_trip(True)
        service = Services(lambda *_: self.fail('Free test paths must stay offline'))
        result = await service.mixed_route(plan.requests(), there_and_back=True)
        self.assertEqual(len(result), 196)
        plan.accept(result, plan.revision)
        self.assertEqual(plan.assemble()['points'][0], plan.assemble()['points'][-1])

    async def test_save_restore_and_disable_round_trip(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .001)])
        plan.set_return_trip(True)
        plan.accept([[candidate(plan.points)], [candidate(plan.points[::-1])]], plan.revision)
        self.assertEqual(len(plan.assemble()['points']), 3)
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'routes.json'
            library = RouteLibrary(path)
            identifier = library.save_route('Return home', plan.serialize())
            restored = RoutePlan.deserialize(RouteLibrary(path).get(identifier)['document'])
            self.assertTrue(restored.there_and_back)
            self.assertEqual(restored.leg_count, 2)
        plan.set_return_trip(False)
        self.assertEqual(plan.assemble()['points'], plan.points)
        self.assertEqual(len(plan.results), 1)

    async def test_playback_returns_home_and_finishes_without_looping(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .00001)])
        plan.set_return_trip(True)
        plan.accept([[candidate(plan.points, 1)], [candidate(plan.points[::-1], 1)]], plan.revision)
        profile = plan.assemble()
        points = profile.pop('points')
        with tempfile.TemporaryDirectory() as root:
            session = Session(Transport(), Path(root) / 'session.json')
            try:
                await session.start_route('phone', points, profile=profile)
                await session.tick(1)
                self.assertNotEqual(session.target, points[0])
                for _ in range(5):
                    await session.tick(1)
                self.assertEqual(session.target, points[0])
                self.assertTrue(session.paused)
                self.assertAlmostEqual(session.progress, session.route.total)
            finally:
                await session.restore('phone')
                await asyncio.gather(session.refresh, return_exceptions=True)
