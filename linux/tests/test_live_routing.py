import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from locus_linux.engine import Session
from locus_linux.routes import Route
from locus_linux.services import Services
from locus_linux.traffic import TrafficSchedule, attach_controls
from test_engine import Transport


class LiveRoutingTests(unittest.TestCase):
    def test_active_leg_at_boundary_and_during_stop_arrival(self):
        route = Route([(0, 0), (0, .001), (0, .002), (0, .001), (0, 0)], legs=[
            {'first': 0, 'last': 1, 'from_stop': 1, 'to_stop': 2},
            {'first': 1, 'last': 2, 'from_stop': 2, 'to_stop': 3},
            {'first': 2, 'last': 3, 'from_stop': 3, 'to_stop': 2},
            {'first': 3, 'last': 4, 'from_stop': 2, 'to_stop': 1}])
        self.assertEqual(route.leg_at(route.lengths[1])['index'], 1)
        self.assertEqual(route.leg_at(route.lengths[1], arriving=True)['index'], 0)
        self.assertEqual(route.leg_at(route.total)['to_stop'], 1)

    def test_light_queue_is_random_before_light_and_never_before_current_leg(self):
        controls = [{'distance': 100, 'kind': 'traffic_light', 'min_distance': 90}]
        offsets = set()
        for seed in range(30):
            event = TrafficSchedule(controls, red_probability=1, rng=random.Random(seed)).upcoming
            self.assertGreaterEqual(event['distance'], 90)
            self.assertLessEqual(event['distance'], 97)
            offsets.add(round(event['queue_distance'], 2))
        self.assertGreater(len(offsets), 8)
        controls.insert(0, {'distance': 95, 'kind': 'stop_sign'})
        schedule = TrafficSchedule(controls, red_probability=1, rng=random.Random(2))
        self.assertGreater(schedule.events[1]['distance'], schedule.events[0]['distance'])

    def test_crosswalks_pause_sometimes_and_have_bounded_waits(self):
        stops = [{'distance': 100, 'kind': 'crosswalk'}]
        chosen = 0
        for seed in range(100):
            schedule = TrafficSchedule(stops, rng=random.Random(seed))
            if schedule.upcoming:
                chosen += 1
                self.assertTrue(2 <= schedule.upcoming['seconds'] <= 8)
        self.assertTrue(15 < chosen < 45)
        self.assertFalse(TrafficSchedule(stops, enabled=False).events)

    def test_crosswalk_is_attached_to_actual_traversed_way(self):
        option = {'points': [(0, 0), (0, .002)], 'speeds': [10], 'sources': ['road'],
                  'driving': [True], 'motorized': [True]}
        trace = {'edges': [{'way_id': 1, 'begin_shape_index': 0, 'end_shape_index': 1}]}
        elements = [{'type': 'way', 'id': 1, 'nodes': [10]},
                    {'type': 'node', 'id': 10, 'lat': 0, 'lon': .001, 'tags': {'highway': 'crossing'}}]
        attach_controls(option, trace, elements)
        self.assertEqual(option['stops'], [{'index': 1, 'kind': 'crosswalk'}])
        self.assertEqual(len(option['speeds']), len(option['points']) - 1)

    def test_fast_curve_lookup_matches_braking_reference_on_dense_geometry(self):
        import math
        route = Route([(.000002 * (i % 2), .000005 * i) for i in range(500)],
                      speeds=[5 + i % 8 for i in range(499)], driving=[True] * 499)
        for meters in (0, .3, 20.123, 80, route.lengths[250], route.total - .01):
            index = route.edge_at(meters)
            expected = route.speeds[index]
            for edge in range(index + 1, len(route.points) - 1):
                ahead = route.lengths[edge] - meters
                if ahead > 150:
                    break
                expected = min(expected, math.sqrt(route.curve_caps[edge] ** 2 + 6 * ahead))
            self.assertAlmostEqual(route.desired_speed(meters), expected, places=6)

    def test_curve_geometry_is_not_recomputed_during_playback(self):
        route = Route([(0, 0), (0, .001), (.001, .001)], speeds=[20, 20], driving=[True, True])
        with patch('locus_linux.routes.distance', side_effect=AssertionError('Repeated geometry work')):
            for i in range(100):
                route.desired_speed(i / 10)


class LiveSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.session = Session(Transport(), Path(self.root.name) / 'session.json', traffic_rng=random.Random(7))

    async def asyncTearDown(self):
        if self.session.refresh:
            self.session.refresh.cancel()
        self.root.cleanup()

    async def test_smooth_variable_vehicle_speed_never_exceeds_mapped_target(self):
        await self.session.configure(10, True, False)
        await self.session.start_route('phone', [(0, 0), (0, .1)], profile={
            'speeds': [10], 'sources': ['mapped'], 'driving': [True], 'motorized': [True]})
        speeds = []
        with patch('locus_linux.engine.random.uniform', side_effect=lambda a, b: a + (b - a) * .25):
            for _ in range(30):
                before = self.session.motion_speed
                await self.session.tick(1)
                speeds.append(self.session.motion_speed)
                self.assertLessEqual(self.session.motion_speed, 10)
                self.assertLessEqual(abs(self.session.motion_speed - before), 3.00001)
        self.assertGreater(len({round(v, 3) for v in speeds[10:]}), 4)
        self.assertGreater(speeds[1], speeds[0])

    async def test_red_light_holds_before_line_and_settings_do_not_repeat_it(self):
        await self.session.configure_traffic(True, 1)
        await self.session.start_route('phone', [(0, 0), (0, .001), (0, .003)], profile={
            'speeds': [10, 10], 'sources': ['road'] * 2, 'driving': [True] * 2,
            'stops': [{'index': 1, 'kind': 'traffic_light'}]})
        for _ in range(100):
            await self.session.tick(1)
            if self.session.traffic.waiting:
                break
        self.assertIsNotNone(self.session.traffic.waiting)
        self.assertTrue(3 <= self.session.snapshot()['traffic_stop']['queue_distance'] <= 20)
        self.assertLess(self.session.progress, self.session.route.lengths[1])
        await self.session.configure_traffic(True, .9)
        self.assertFalse(self.session.traffic.events)


class PlannerReuseTests(unittest.IsolatedAsyncioTestCase):
    async def test_unchanged_legs_reuse_results_and_changed_leg_only_is_rerouted(self):
        service = Services()
        calls = []
        async def route(points, *unused):
            calls.append(points)
            return [{'points': points}]
        service.route = route
        legs = [{'start': (0, i / 1000), 'end': (0, (i + 1) / 1000),
                 'spec': {'profile': 'walk_trails'}} for i in range(10)]
        first = await service.mixed_route(legs)
        self.assertEqual(len(calls), 10)
        first[0][0]['points'][0] = (2, 2)
        second = await service.mixed_route(legs)
        self.assertEqual(len(calls), 10)
        self.assertEqual(second[0][0]['points'][0], (0, 0))
        legs[4]['spec'] = {'profile': 'walk_trails', 'speed_mode': 'manual', 'speed_kph': 6}
        await service.mixed_route(legs)
        self.assertEqual(len(calls), 11)
        await service.mixed_route(legs, include_stops=False)
        self.assertEqual(len(calls), 21, 'Traffic-control choices are part of the cache key')
