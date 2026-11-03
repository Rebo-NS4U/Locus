"""Mapped walking must not replace water crossings with ferry or gap geometry."""
import json
import unittest

from locus_linux.services import Services, encode_shape


class WalkingTests(unittest.IsolatedAsyncioTestCase):
    points = [(0, 0), (.001, 0), (.001, .002), (0, .002)]

    def trip(self):
        return {'status': 0, 'summary': {'time': 200},
                'legs': [{'shape': encode_shape(self.points)}]}

    async def test_short_walking_is_direct_offline_and_preserves_bends(self):
        def offline(*args):
            self.fail('Short walking must not request a road route')
        start, bend, end = (0, 0), (.0002, .001), (0, .002)
        option = (await Services(offline).mixed_route([{
            'start': start, 'end': end,
            'spec': {'profile': 'walk_auto', 'controls': [bend]}
        }]))[0][0]
        self.assertEqual(option['points'], [start, bend, end])
        self.assertEqual(option['speeds'], [1.4, 1.4])
        self.assertFalse(any(option['motorized']))
        self.assertIn('Direct short walking', option['speed_warning'])

    async def test_long_walk_and_other_modes_still_use_mapped_geometry(self):
        for profile, start, end in (
            ('walk_auto', (0, 0), (0, .004)),
            ('car_roads', (0, 0), (0, .002)),
            ('cycle_paths', (0, 0), (0, .002)),
        ):
            mapped = [start, (.001, .001), end]
            service = Services()
            calls = []
            async def route(points, mode, *args):
                calls.append(mode)
                return [{'points': mapped}]
            service.route = route
            option = (await service.mixed_route([{
                'start': start, 'end': end, 'spec': {
                    'profile': profile, 'speed_mode': 'manual', 'speed_kph': 18}
            }], include_stops=False))[0][0]
            self.assertEqual(option['points'], mapped)
            self.assertEqual(len(calls), 1)

    async def test_short_walk_then_drive_assembles_at_exact_shared_pin(self):
        from locus_linux.planning import RoutePlan
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .001), (0, .01)])
        plan.update(1, {'profile': 'car_roads', 'speed_mode': 'manual', 'speed_kph': 18})
        service = Services()
        async def route(points, mode, *args):
            self.assertEqual(mode, 'Drive')
            return [{'points': [points[0], (.001, .005), points[-1]]}]
        service.route = route
        plan.accept(await service.mixed_route(plan.requests(), include_stops=False), plan.revision)
        combined = plan.assemble()
        self.assertEqual(combined['points'], [(0, 0), (0, .001), (.001, .005), (0, .01)])
        self.assertEqual(combined['motorized'], [False, True, True])
        self.assertEqual(combined['speeds'], [1.4, 5, 5])

    async def test_walking_and_running_request_foot_network_without_ferries(self):
        for mode in ('Walk', 'Run'):
            calls = []
            def request(url, params):
                calls.append(json.loads(params['json']))
                return {'trip': self.trip()}
            result = await Services(request).route([self.points[0], self.points[-1]], mode)
            self.assertEqual(result[0]['points'], self.points)
            options = calls[0]['costing_options']['pedestrian']
            self.assertEqual(options['use_ferry'], 0)
            self.assertTrue(options['exclude_ferries'])
            self.assertTrue(all(p['search_filter']['exclude_ferry'] for p in calls[0]['locations']))

    async def test_ferry_is_rejected_even_if_router_ignores_exclusion(self):
        for flag in ('trip', 'leg', 'maneuver', 'type'):
            trip = self.trip()
            if flag == 'trip':
                trip['summary']['has_ferry'] = True
            elif flag == 'leg':
                trip['legs'][0]['summary'] = {'has_ferry': True}
            else:
                trip['legs'][0]['maneuvers'] = [{'ferry': True} if flag == 'maneuver' else {'type': 28}]
            with self.assertRaisesRegex(ValueError, 'ferry crossings'):
                await Services(lambda *_: {'trip': trip}).route([self.points[0], self.points[-1]], 'Walk')

    async def test_bad_alternative_does_not_discard_bridge_route(self):
        ferry = self.trip()
        ferry['summary']['has_ferry'] = True
        response = {'trip': ferry, 'alternates': [{'trip': self.trip()}]}
        result = await Services(lambda *_: response).route([self.points[0], self.points[-1]], 'Walk')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['points'], self.points)

    async def test_disconnected_legs_cannot_be_stitched_across_a_gap(self):
        trip = self.trip()
        trip['legs'] = [{'shape': encode_shape(self.points[:2])},
                        {'shape': encode_shape(self.points[2:])}]
        with self.assertRaisesRegex(ValueError, 'do not connect'):
            await Services(lambda *_: {'trip': trip}).route(self.points, 'Walk')

    async def test_walking_connects_single_off_path_stop_without_distance_limit(self):
        for profile in ('walk_sidewalks', 'walk_trails'):
            for offset in (.0001, .001, .1):
                for first in (True, False):
                    service = Services(lambda *_: {'trip': self.trip()})
                    start = (0, offset) if first else self.points[0]
                    end = (0, .002 + offset) if not first else self.points[-1]
                    result = await service.mixed_route([{'start': start, 'end': end,
                                                        'spec': {'profile': profile}}])
                    option = result[0][0]
                    expected = [start, *self.points] if first else [*self.points, end]
                    self.assertEqual(option['points'], expected)
                    self.assertEqual(len(option['speeds']), len(expected) - 1)
                    self.assertEqual(len(option['motorized']), len(expected) - 1)
                    self.assertEqual(len(option['connectors']), 1)
                    self.assertEqual(option['speeds'][0 if first else -1], 1.4)

    async def test_two_off_path_walking_stops_preserve_mapped_path(self):
        for profile in ('walk_sidewalks', 'walk_trails'):
            for speed_mode in ('auto', 'manual'):
                service = Services(lambda *_: {'trip': self.trip()})
                start, end, bend = (0, .0001), (0, .0021), (.0002, .001)
                result = await service.mixed_route([{
                    'start': start, 'end': end,
                    'spec': {'profile': profile, 'controls': [bend],
                             'speed_mode': speed_mode, 'speed_kph': 7.2}}])
                option = result[0][0]
                self.assertEqual(option['points'], [start, *self.points, end])
                self.assertEqual(len(option['connectors']), 2)
                self.assertEqual(option['speeds'], [1.4, *([2] * 3 if speed_mode == 'manual' else [5.04 / 3.6 if profile == 'walk_sidewalks' else 4.3 / 3.6] * 3), 1.4])
                self.assertNotIn('speed_warning', option)
                self.assertFalse(any(option['driving']))

    async def test_alternative_that_reaches_both_pins_keeps_mapped_geometry(self):
        start, end = (0, .0001), (0, .0021)
        mapped = [start, (.001, .001), end]
        async def route(*args):
            return [{'points': self.points}, {'points': mapped}]
        service = Services()
        service.route = route
        result = await service.mixed_route([{'start': start, 'end': end,
                                            'spec': {'profile': 'walk_trails'}}])
        self.assertEqual(len(result[0]), 2)
        self.assertEqual(result[0][1]['points'], mapped)
        self.assertNotIn('speed_warning', result[0][1])

    async def test_running_does_not_automatically_connect_off_path_stops(self):
        service = Services(lambda *_: {'trip': self.trip()})
        with self.assertRaisesRegex(ValueError, 'within 2 m'):
            await service.mixed_route([{'start': (0, .0001), 'end': (0, .0021),
                                        'spec': {'profile': 'run_paths'}}])

    async def test_no_path_does_not_fall_back_to_direct_line(self):
        service = Services(lambda *_: {'error': 'No path could be found'})
        with self.assertRaisesRegex(ValueError, 'No path'):
            await service.mixed_route([{'start': self.points[0], 'end': self.points[-1],
                                        'spec': {'profile': 'walk_trails'}}])
