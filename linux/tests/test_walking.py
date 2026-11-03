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

    async def test_no_path_does_not_fall_back_to_direct_line(self):
        service = Services(lambda *_: {'error': 'No path could be found'})
        with self.assertRaisesRegex(ValueError, 'No path'):
            await service.mixed_route([{'start': self.points[0], 'end': self.points[-1],
                                        'spec': {'profile': 'walk_trails'}}])
