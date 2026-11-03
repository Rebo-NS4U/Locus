import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from locus_linux.engine import Session
from locus_linux.planning import RoutePlan, specification
from locus_linux.routes import Route, distance
from locus_linux.services import Services, encode_shape
from test_engine import Transport


def candidate(points, speed=5, driving=False):
    route = Route(points)
    return {"points": route.points, "speeds": [speed] * (len(points) - 1), "sources": ["test road"] * (len(points) - 1),
            "driving": [driving] * (len(points) - 1), "distance": route.total, "seconds": route.total / speed}


class DocumentTests(unittest.TestCase):
    def test_mixed_settings_survive_pin_drag_and_unaffected_legs_stay_ready(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .01), (0, .02), (0, .03)])
        plan.update(0, {"profile": "car_roads"})
        plan.update(2, {"profile": "walk_trails"})
        results = [[candidate(plan.points[i:i + 2])] for i in range(3)]
        plan.accept(results, plan.revision)
        last_key = plan.key(2)
        plan.move(0, (.001, 0))
        self.assertEqual(plan.spec(0)['profile'], 'car_roads')
        self.assertNotIn(plan.key(0), plan.results)
        self.assertIn(last_key, plan.results)
        self.assertEqual(plan.spec(2)['profile'], 'walk_trails')

    def test_insert_splits_controls_and_inherits_speed_and_profile(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .03)])
        plan.update(0, {'profile': 'offroad_car', 'speed_mode': 'manual', 'speed_kph': 12, 'controls': [(0, .01), (0, .02)]})
        plan.insert(1, (0, .015))
        self.assertEqual(plan.spec(0)['controls'], [(0, .01)])
        self.assertEqual(plan.spec(1)['controls'], [(0, .02)])
        for i in (0, 1):
            self.assertEqual(plan.spec(i)['profile'], 'offroad_car')
            self.assertEqual(plan.spec(i)['speed_kph'], 12)

    def test_project_roundtrip_and_stale_planning_result(self):
        plan = RoutePlan()
        plan.reset([(51, 1), (51.01, 1.01)])
        plan.update(0, {'profile': 'walk_trails', 'controls': [(51.005, 1.007)], 'speed_mode': 'manual', 'speed_kph': 4})
        restored = RoutePlan.deserialize(plan.serialize())
        self.assertEqual(plan.requests(), restored.requests())
        old_revision = plan.revision
        plan.move(1, (51.02, 1.02))
        with self.assertRaisesRegex(ValueError, 'changed while planning'):
            plan.accept([[candidate([(51, 1), (51.01, 1.01)])]], old_revision)

    def test_continuous_assembly_and_alternative_choice(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .01), (0, .02)])
        first = candidate([(0, 0), (0, .01)], 10)
        alternative = candidate([(0, 0), (.001, .005), (0, .01)], 5)
        second = candidate([(0, .01), (0, .02)], 1.4)
        plan.accept([[first, alternative], [second]], plan.revision)
        plan.select(0, 1)
        combined = plan.assemble()
        self.assertEqual(len(combined['speeds']), len(combined['points']) - 1)
        self.assertEqual(combined['speeds'], [5, 5, 1.4])
        plan.results[plan.key(1)][0]['points'][0] = (.1, .01)
        with self.assertRaisesRegex(ValueError, 'do not join'):
            plan.assemble()

    def test_bend_limits_and_invalid_project(self):
        with self.assertRaises(ValueError):
            specification({'profile': 'car_roads', 'controls': [(0, i / 100) for i in range(11)]})
        self.assertEqual(len(specification({'profile': 'offroad_car', 'controls': [(0, i / 100) for i in range(11)]})['controls']), 11)
        with self.assertRaises(ValueError):
            RoutePlan.deserialize({'format': 'other', 'version': 1})


class MixedServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_path_is_offline_and_preserves_user_bends(self):
        def no_network(*args):
            raise AssertionError('Free routing must not request road data')
        legs = [{'start': (0, 0), 'end': (0, .02), 'spec': {'profile': 'offroad_car', 'controls': [(0.001, .01)]}}]
        result = await Services(no_network).mixed_route(legs)
        self.assertEqual(result[0][0]['points'], [(0, 0), (.001, .01), (0, .02)])
        self.assertEqual(result[0][0]['sources'], ['off-road estimate'] * 2)

    async def test_preference_payload_manual_speed_and_exact_pin_connectors(self):
        import json
        calls = []
        def request(url, params):
            calls.append(json.loads(params['json']))
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape([(0, .00001), (0, .001)])}], 'summary': {'time': 20}}}
        service = Services(request)
        result = await service.mixed_route([{'start': (0, 0), 'end': (0, .001), 'spec': {'profile': 'walk_trails', 'speed_mode': 'manual', 'speed_kph': 7.2}}])
        self.assertEqual(calls[0]['costing_options']['pedestrian']['use_tracks'], 1)
        option = result[0][0]
        self.assertEqual(option['points'][0], (0, 0))
        self.assertEqual(option['sources'][0], 'short pin connector')
        self.assertEqual(option['speeds'][1], 2)
        self.assertEqual(len(calls), 1)

    async def test_auto_road_metadata_obeys_posted_limits_and_surface_cap(self):
        points = [(0, 0), (0, .001), (0, .002)]
        def request(url, params):
            if url.endswith('trace_attributes'):
                return {'shape': encode_shape(points), 'edges': [
                    {'begin_shape_index': 0, 'end_shape_index': 1, 'speed': 90, 'speed_limit': 30, 'surface': 'paved_smooth'},
                    {'begin_shape_index': 1, 'end_shape_index': 2, 'speed': 50, 'speed_limit': 60, 'surface': 'gravel'}]}
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}}
        result = await Services(request).mixed_route([{'start': points[0], 'end': points[-1], 'spec': {'profile': 'car_roads'}}])
        option = result[0][0]
        self.assertEqual(option['speeds'], [30 / 3.6, 15 / 3.6])
        self.assertIn('limit 30', option['sources'][0])
        self.assertIn('rough surface', option['sources'][1])
        self.assertTrue(all(option['driving']))

    async def test_unavailable_metadata_is_labelled_estimate_not_detected_limit(self):
        points = [(0, 0), (0, .001)]
        def request(url, params):
            if url.endswith('trace_attributes'):
                raise ConnectionError('offline')
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}}
        option = (await Services(request).mixed_route([{'start': points[0], 'end': points[1], 'spec': {'profile': 'car_tracks'}}]))[0][0]
        self.assertIn('estimate', option['speed_warning'])
        self.assertEqual(option['sources'], ['profile estimate'])

    async def test_road_class_fallback_is_labelled_and_respects_limit(self):
        points = [(0, 0), (0, .001)]
        def request(url, params):
            if url.endswith('trace_attributes'):
                return {'shape': encode_shape(points), 'edges': [{'begin_shape_index': 0, 'end_shape_index': 1, 'road_class': 'residential', 'speed_limit': 20}]}
            return {'trip': {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}}
        option = (await Services(request).mixed_route([{'start': points[0], 'end': points[1], 'spec': {'profile': 'car_roads'}}]))[0][0]
        self.assertEqual(option['speeds'], [20 / 3.6])
        self.assertIn('estimate', option['sources'][0])

    async def test_far_snapped_alternative_does_not_discard_good_path(self):
        good = [(0, 0), (0, .001)]
        def request(url, params):
            trip = lambda points: {'status': 0, 'legs': [{'shape': encode_shape(points)}], 'summary': {'time': 20}}
            return {'trip': trip(good), 'alternates': [{'trip': trip([(.1, 0), (.1, .001)])}]}
        options = (await Services(request).mixed_route([{'start': good[0], 'end': good[1], 'spec': {'profile': 'walk_sidewalks'}}]))[0]
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]['points'], good)

    async def test_remote_snapping_cannot_silently_bridge_a_large_gap(self):
        option = candidate([(0, 0), (0, .01)])
        with self.assertRaisesRegex(ValueError, '40 m'):
            Services.anchor(option, (.01, 0), (0, .01))


class AutomaticSpeedTests(unittest.IsolatedAsyncioTestCase):
    async def test_variation_never_exceeds_auto_driving_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Transport(), Path(directory) / 'session.json')
            try:
                await session.configure(20, True, False)
                await session.start_route('phone', [(0, 0), (0, .01)], profile={'speeds': [5], 'sources': ['mapped limit'], 'driving': [True]})
                for _ in range(20):
                    await session.tick(1)
                    self.assertLessEqual(session.motion_speed, 5.000001)
            finally:
                await session.restore('phone')
                await asyncio.gather(session.refresh, return_exceptions=True)

    async def test_vehicle_accelerates_then_stops_moving_when_paused(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Session(Transport(), Path(directory) / 'session.json')
            profile = {'speeds': [20.0], 'sources': ['road speed · limit 72 km/h'], 'driving': [True]}
            try:
                await session.start_route('phone', [(0, 0), (0, .01)], profile=profile)
                await session.tick(1)
                self.assertAlmostEqual(session.motion_speed, 2)
                await session.tick(1)
                self.assertAlmostEqual(session.motion_speed, 4)
                await session.control('pause')
                before = session.progress
                await session.tick(1)
                self.assertEqual(session.progress, before)
                self.assertEqual(session.snapshot()['speed_kph'], 0)
            finally:
                await session.restore('phone')
                await asyncio.gather(session.refresh, return_exceptions=True)

    async def test_time_integration_slows_at_driving_to_walking_boundary(self):
        points = [(0, 0), (0, .001), (0, .002)]
        route = Route(points, speeds=[20, 1], sources=['road', 'walk'], driving=[True, False])
        boundary = route.lengths[1]
        end = route.advance(boundary - 2, 1, 20)
        self.assertAlmostEqual(end - boundary, .9, places=5)

    async def test_curve_and_upcoming_limit_reduce_target_speed(self):
        route = Route([(0, 0), (0, .001), (.001, .001)], speeds=[30, 30], sources=['road'] * 2, driving=[True] * 2)
        self.assertLess(route.desired_speed(route.lengths[1] - 1), 6)
        straight = Route([(0, 0), (0, .001), (0, .002)], speeds=[30, 5], sources=['road'] * 2, driving=[True] * 2)
        self.assertLess(straight.desired_speed(straight.lengths[1] - 1), 6)


if __name__ == '__main__':
    unittest.main()
