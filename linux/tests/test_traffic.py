import asyncio
import copy
import random
import tempfile
import unittest
from pathlib import Path

from locus_linux.engine import Session
from locus_linux.planning import RoutePlan
from locus_linux.routes import Route, distance
from locus_linux.services import Services, encode_shape
from locus_linux.traffic import TrafficSchedule, attach_controls
from test_engine import Transport


def option(points):
    count = len(points) - 1
    return {'points': list(points), 'speeds': [10.] * count, 'sources': ['road'] * count,
            'driving': [True] * count, 'motorized': [True] * count}


class PlacementTests(unittest.TestCase):
    def test_stop_sign_inserted_on_correct_way_and_direction(self):
        points = [(0, 0), (0, .002)]
        trace = {'edges': [{'begin_shape_index': 0, 'end_shape_index': 1, 'way_id': 10, 'forward': True}]}
        elements = [{'type': 'way', 'id': 10, 'nodes': [1, 2]},
                    {'type': 'node', 'id': 1, 'lat': 0, 'lon': .001, 'tags': {'highway': 'stop', 'direction': 'forward'}},
                    {'type': 'node', 'id': 2, 'lat': 0, 'lon': .0015, 'tags': {'highway': 'stop', 'direction': 'backward'}},
                    {'type': 'node', 'id': 3, 'lat': 0, 'lon': .0005, 'tags': {'highway': 'stop'}}]
        result = option(points)
        attach_controls(result, trace, elements)
        self.assertEqual(len(result['stops']), 1)
        stop = result['stops'][0]
        self.assertEqual(stop['kind'], 'stop_sign')
        self.assertLess(distance(result['points'][stop['index']], (0, .001)), .01)
        self.assertEqual(len(result['speeds']), len(result['points']) - 1)

    def test_adjacent_road_signal_is_not_used_and_provider_lights_are(self):
        points = [(0, 0), (0, .001), (0, .002)]
        result = option(points)
        trace = {'edges': [{'begin_shape_index': 0, 'end_shape_index': 1, 'way_id': 10, 'forward': True, 'end_node': {'traffic_signal': True}},
                           {'begin_shape_index': 1, 'end_shape_index': 2, 'way_id': 10, 'forward': True}]}
        attach_controls(result, trace, [{'type': 'way', 'id': 20, 'nodes': [1]},
            {'type': 'node', 'id': 1, 'lat': 0, 'lon': .0005, 'tags': {'highway': 'stop'}}])
        self.assertEqual(result['stops'], [{'index': 1, 'kind': 'traffic_light'}])

    def test_combining_segments_and_anchor_keep_stop_indexes_correct(self):
        first = option([(0, .00001), (0, .001), (0, .002)])
        first['stops'] = [{'index': 1, 'kind': 'stop_sign'}]
        Services.anchor(first, (0, 0), (0, .002))
        self.assertEqual(first['stops'][0]['index'], 2)
        self.assertEqual(first['points'][2], (0, .001))
        plan = RoutePlan(); plan.reset([(0, 0), (0, .002), (0, .004)])
        second = option([(0, .002), (0, .003), (0, .004)])
        second['stops'] = [{'index': 1, 'kind': 'traffic_light'}]
        plan.accept([[first], [second]], plan.revision)
        profile = plan.assemble()
        route = Route(**profile)
        self.assertEqual([stop['kind'] for stop in route.stops], ['stop_sign', 'traffic_light'])
        self.assertAlmostEqual(route.stops[1]['distance'], distance((0, 0), (0, .003)), places=3)

    def test_directional_stop_line_does_not_duplicate_intersection_center_light(self):
        points = [(0, 0), (0, .001), (0, .002)]
        trace = {'edges': [{'begin_shape_index': 0, 'end_shape_index': 1, 'way_id': 10, 'forward': True, 'end_node': {'traffic_signal': True}}]}
        elements = [{'type': 'way', 'id': 10, 'nodes': [1]},
                    {'type': 'node', 'id': 1, 'lat': 0, 'lon': .0009, 'tags': {'highway': 'traffic_signals', 'direction': 'forward'}}]
        result = option(points)
        attach_controls(result, trace, elements)
        self.assertEqual(len(result['stops']), 1)
        location = result['points'][result['stops'][0]['index']]
        self.assertLess(distance(location, (0, .0009)), .01)
        elements[1]['tags']['direction'] = 'backward'
        result = option(points)
        attach_controls(result, trace, elements)
        self.assertFalse(result['stops'])


class ScheduleTests(unittest.TestCase):
    def test_random_durations_are_bounded_and_light_decision_once(self):
        controls = [{'distance': 10, 'kind': 'stop_sign'}, {'distance': 100, 'kind': 'traffic_light'}]
        waits = set()
        red = 0
        for seed in range(200):
            schedule = TrafficSchedule(controls, rng=random.Random(seed))
            self.assertEqual(schedule.events[0]['kind'], 'stop_sign')
            seconds = schedule.events[0]['seconds']
            self.assertTrue(2 <= seconds <= 5)
            waits.add(round(seconds, 2))
            if len(schedule.events) > 1:
                red += 1
                self.assertTrue(8 <= schedule.events[1]['seconds'] <= 65)
            saved = copy.deepcopy(schedule.events)
            self.assertEqual(schedule.upcoming, saved[0])
            self.assertEqual(schedule.events, saved)
        self.assertGreater(len(waits), 50)
        self.assertTrue(55 < red < 125, red)

    def test_disabled_and_green_lights_never_create_halts(self):
        controls = [{'distance': 20, 'kind': 'traffic_light'}]
        self.assertFalse(TrafficSchedule(controls, enabled=False).events)
        self.assertFalse(TrafficSchedule(controls, red_probability=0).events)
        self.assertEqual(len(TrafficSchedule(controls, red_probability=1).events), 1)

    def test_duplicate_junction_is_served_once_and_old_controls_are_skipped(self):
        controls = [{'distance': 10, 'kind': 'traffic_light'}, {'distance': 10.5, 'kind': 'traffic_light'},
                    {'distance': 11, 'kind': 'stop_sign'}, {'distance': 100, 'kind': 'stop_sign'}]
        schedule = TrafficSchedule(controls, red_probability=1, rng=random.Random(3))
        self.assertEqual(len(schedule.events), 2)
        self.assertEqual(schedule.events[0]['kind'], 'traffic_light')
        self.assertEqual(len(TrafficSchedule(controls, after=50).events), 1)


class TrafficMotionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.transport = Transport()
        self.session = Session(self.transport, Path(self.directory.name) / 'session.json', traffic_rng=random.Random(4))
        self.profile = {'speeds': [10, 10], 'sources': ['road'] * 2, 'driving': [True] * 2, 'motorized': [True] * 2,
                        'stops': [{'index': 1, 'kind': 'stop_sign'}]}
        self.points = [(0, 0), (0, .00003), (0, .002)]

    async def asyncTearDown(self):
        if self.session.refresh:
            self.session.refresh.cancel()
            await asyncio.gather(self.session.refresh, return_exceptions=True)
        self.directory.cleanup()

    async def arrive(self):
        for _ in range(20):
            await self.session.tick(1)
            if self.session.traffic.waiting:
                return
        self.fail('Never reached traffic control')

    async def test_no_overshoot_hold_refresh_and_smooth_restart(self):
        await self.session.start_route('phone', self.points, profile=self.profile)
        await self.arrive()
        boundary = self.session.route.lengths[1]
        self.assertAlmostEqual(self.session.progress, boundary)
        self.assertEqual(self.session.motion_speed, 0)
        original = self.session.target
        count = len(self.transport.calls)
        while self.session.traffic.waiting:
            await self.session.tick(1)
            self.assertEqual(self.session.target, original)
        self.assertGreater(len(self.transport.calls), count)
        await self.session.tick(1)
        self.assertGreater(self.session.progress, boundary)
        self.assertLessEqual(self.session.motion_speed, 2.00001)

    async def test_pause_and_disconnect_do_not_consume_wait(self):
        await self.session.start_route('phone', self.points, profile=self.profile)
        await self.arrive()
        before = self.session.traffic.remaining
        await self.session.control('pause')
        await self.session.tick(1)
        self.assertEqual(self.session.traffic.remaining, before)
        await self.session.control('resume')
        self.transport.fail_set = True
        await self.session.tick(1, now=10)
        after = self.session.traffic.remaining
        await self.session.tick(1, now=11)
        self.assertEqual(self.session.traffic.remaining, after)
        self.transport.fail_set = False
        await self.session.tick(1, now=15)
        self.assertTrue(self.session.paused)
        self.assertEqual(self.session.traffic.remaining, after)

    async def test_disabling_wait_and_restoring_cancels_all_stop_state(self):
        await self.session.start_route('phone', self.points, profile=self.profile)
        await self.arrive()
        await self.session.configure_traffic(False, .45)
        self.assertFalse(self.session.traffic.waiting)
        await self.session.tick(1)
        self.assertGreater(self.session.progress, self.session.route.lengths[1])
        await self.session.restore('phone')
        self.assertIsNone(self.session.snapshot()['traffic_stop'])
        count = len(self.transport.calls)
        await self.session.tick(1)
        self.assertEqual(count, len(self.transport.calls))

    async def test_stop_behavior_also_works_with_manual_car_speed(self):
        self.profile['driving'] = [False, False]
        await self.session.start_route('phone', self.points, profile=self.profile)
        await self.arrive()
        self.assertEqual(self.session.traffic.waiting['kind'], 'stop_sign')

    async def test_settings_changes_preserve_current_wait_and_loop_resamples(self):
        await self.session.start_route('phone', self.points, profile=self.profile)
        await self.arrive()
        remaining = self.session.traffic.remaining
        await self.session.configure_traffic(True, .9)
        self.assertEqual(self.session.traffic.remaining, remaining)
        await self.session.configure_traffic(True, .9)
        self.assertEqual(self.session.traffic.remaining, remaining)
        short = [(0, 0), (0, .00003), (0, .00009)]
        await self.session.start_route('phone', short, loop=True, profile=self.profile)
        await self.arrive()
        first_schedule = self.session.traffic
        first_wait = first_schedule.waiting['seconds']
        for _ in range(100):
            await self.session.tick(1)
            if self.session.traffic is not first_schedule and self.session.traffic.waiting:
                break
        else:
            self.fail('Loop did not encounter its stop again')
        self.assertNotEqual(self.session.traffic.waiting['seconds'], first_wait)


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_osm_reads_find_signs_preserve_manual_speed_and_cache(self):
        points = [(0, 0), (0, .001), (0, .002)]
        calls = []
        def request(url, params):
            calls.append(url)
            if url.endswith('ways.json'):
                return {'elements': [{'type': 'way', 'id': 10, 'nodes': [1, 2, 3]}]}
            if url.endswith('nodes.json'):
                return {'elements': [{'type': 'node', 'id': i + 1, 'lat': p[0], 'lon': p[1], 'tags': {'highway': 'stop', 'direction': 'forward'} if i == 1 else {}} for i, p in enumerate(points)]}
            if url.endswith('trace_attributes'):
                return {'shape': encode_shape(points), 'edges': [{'begin_shape_index': 0, 'end_shape_index': 2, 'speed': 30, 'way_id': 10, 'forward': True}]}
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}}
        service = Services(request)
        legs = [{'start': points[0], 'end': points[-1], 'spec': {'profile': 'car_roads', 'speed_mode': 'manual', 'speed_kph': 14.4}}]
        option = (await service.mixed_route(legs))[0][0]
        self.assertEqual(option['stops'], [{'index': 1, 'kind': 'stop_sign'}])
        self.assertEqual(option['speeds'], [4, 4])
        self.assertFalse(any(option['driving']))
        self.assertTrue(all(option['motorized']))
        before = len(calls)
        await service.mixed_route(legs)
        self.assertEqual(len(calls), before)

    async def test_provider_lights_survive_stop_lookup_failure(self):
        points = [(0, 0), (0, .001), (0, .002)]
        def request(url, params):
            if url.endswith('trace_attributes'):
                return {'shape': encode_shape(points), 'edges': [
                    {'begin_shape_index': 0, 'end_shape_index': 1, 'speed': 30, 'way_id': 10, 'forward': True, 'end_node': {'traffic_signal': True}},
                    {'begin_shape_index': 1, 'end_shape_index': 2, 'speed': 30, 'way_id': 10, 'forward': True}]}
            if 'data' in params:
                raise ConnectionError('provider unavailable')
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}}
        result = await Services(request).mixed_route([{'start': points[0], 'end': points[-1], 'spec': {'profile': 'car_roads'}}])
        self.assertEqual(result[0][0]['stops'], [{'index': 1, 'kind': 'traffic_light'}])
        self.assertIn('unavailable', result[0][0]['traffic_warning'])


if __name__ == '__main__':
    unittest.main()
