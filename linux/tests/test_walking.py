"""Mapped walking must not replace water crossings with ferry or gap geometry."""
import json
import unittest

from locus_linux.services import Services, encode_shape


class WalkingTests(unittest.IsolatedAsyncioTestCase):
    points = [(0, 0), (.001, 0), (.001, .002), (0, .002)]

    def trip(self):
        return {'status': 0, 'summary': {'time': 200},
                'legs': [{'shape': encode_shape(self.points)}]}

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

    async def test_walking_rejects_shortcut_from_pin_to_other_bank(self):
        async def route(*args):
            return [{'points': self.points}]
        service = Services()
        service.route = route
        with self.assertRaisesRegex(ValueError, 'within 2 m'):
            await service.mixed_route([{'start': (0, .0001), 'end': self.points[-1],
                                        'spec': {'profile': 'walk_sidewalks'}}])

    async def test_two_off_path_walking_stops_connect_directly_with_bends(self):
        for profile in ('walk_sidewalks', 'walk_trails'):
            for speed_mode in ('auto', 'manual'):
                service = Services(lambda *_: {'trip': self.trip()})
                start, end, bend = (0, .0001), (0, .0021), (.0002, .001)
                result = await service.mixed_route([{
                    'start': start, 'end': end,
                    'spec': {'profile': profile, 'controls': [bend],
                             'speed_mode': speed_mode, 'speed_kph': 7.2}}])
                option = result[0][0]
                self.assertEqual(option['points'], [start, bend, end])
                self.assertEqual(option['connectors'], [])
                self.assertEqual(option['speeds'], [2, 2] if speed_mode == 'manual' else [4.3 / 3.6] * 2)
                self.assertIn('Direct off-road', option['speed_warning'])
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
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(result[0][0]['points'], mapped)
        self.assertNotIn('speed_warning', result[0][0])

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
