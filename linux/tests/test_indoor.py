import unittest
from unittest.mock import AsyncMock

from locus_linux.indoor import inside_connection, building_access, walking_path
from locus_linux.services import Services


class FootprintTests(unittest.TestCase):
    def test_same_building_interior_and_boundary(self):
        ring = [(0, 0), (0, 4), (4, 4), (4, 0), (0, 0)]
        self.assertTrue(inside_connection((1, 1), (3, 3), ring))
        self.assertTrue(inside_connection((0, 1), (0, 3), ring))
        self.assertFalse(inside_connection((1, 1), (5, 3), ring))
        self.assertFalse(inside_connection((1, 1), (3, 3), ring[:-1]))

    def test_concave_outline_cannot_shortcut_outside_building(self):
        ring = [(0, 0), (0, 4), (1, 4), (1, 1), (4, 1), (4, 0), (0, 0)]
        self.assertFalse(inside_connection((.5, 3), (3, .5), ring))
        self.assertTrue(inside_connection((.5, 3), (.5, .5), ring))
    def test_building_access_requires_indoor_end_and_short_outdoor_approach(self):
        ring = [(0, 0), (0, .002), (.002, .002), (.002, 0), (0, 0)]
        self.assertTrue(building_access((.001, .001), (.0021, .001), ring))
        self.assertFalse(building_access((.001, .001), (.003, .001), ring))
        self.assertFalse(building_access((-.0001, .001), (.0021, .001), ring))

    def test_building_access_cannot_jump_across_concave_exterior_gap(self):
        ring = [(0, 0), (0, .004), (.001, .004), (.001, .001), (.004, .001), (.004, 0), (0, 0)]
        self.assertFalse(building_access((.0005, .003), (.003, .0005), ring))

    def test_concave_building_routes_around_corner_instead_of_outdoors(self):
        ring = [(0, 0), (0, .004), (.001, .004), (.001, .001), (.004, .001), (.004, 0), (0, 0)]
        start, end = (.0005, .003), (.003, .0005)
        path = walking_path(start, end, ring)
        self.assertIsNotNone(path)
        self.assertGreater(len(path), 2)
        self.assertEqual(path[0], start)
        self.assertEqual(path[-1], end)
        self.assertTrue(all(inside_connection(a, b, ring) for a, b in zip(path, path[1:])))



class IndoorServiceTests(unittest.IsolatedAsyncioTestCase):
    ring = [(0, 0), (0, .002), (.002, .002), (.002, 0), (0, 0)]

    def service(self):
        service = Services()
        service.get = AsyncMock(return_value={'elements': [{
            'type': 'way', 'tags': {'building': 'apartments'},
            'geometry': [{'lat': lat, 'lon': lon} for lat, lon in self.ring]
        }]})
        return service

    async def test_indoor_stops_do_not_snap_outside_to_sidewalk(self):
        service = self.service()
        service.route = AsyncMock(side_effect=AssertionError('Indoor movement must stay inside'))
        start, end = (.0002, .0002), (.0015, .0015)
        option = (await service.mixed_route([{'start': start, 'end': end,
                                            'spec': {'profile': 'walk_auto'}}]))[0][0]
        self.assertEqual(option['points'], [start, end])
        self.assertEqual(option['connectors'], [])
        self.assertEqual(option['speeds'], [1.4])
        self.assertFalse(any(option['motorized']))
        self.assertIn('Approximate indoor', option['speed_warning'])
        service.route.assert_not_awaited()

    async def test_walking_can_enter_and_leave_a_building_without_sidewalk_detour(self):
        for start, end in (((.001, .001), (.0021, .001)),
                           ((.0021, .001), (.001, .001))):
            service = self.service()
            service.route = AsyncMock(side_effect=AssertionError('Must use building access'))
            option = (await service.mixed_route([{'start': start, 'end': end,
                        'spec': {'profile': 'walk_auto'}}]))[0][0]
            self.assertEqual(option['points'], [start, end])
            self.assertEqual(option['connectors'], [])
            self.assertIn('building access', option['speed_warning'])
            service.route.assert_not_awaited()

    async def test_default_osm_read_resolves_way_nodes(self):
        service = self.service()
        elements = [{'type': 'node', 'id': i, 'lat': p[0], 'lon': p[1]}
                    for i, p in enumerate(self.ring)]
        elements.append({'type': 'way', 'tags': {'building': 'yes'}, 'nodes': list(range(len(self.ring)))})
        service.get.return_value = {'elements': elements}
        result = await service.indoor_connection((.0002, .0002), (.0015, .0015))
        self.assertIsNotNone(result)
        url, params = service.get.call_args.args
        self.assertTrue(url.endswith('/map.json'))
        self.assertIn('bbox', params)

    async def test_outside_stop_or_missing_footprint_keeps_outdoor_routing(self):
        service = self.service()
        self.assertIsNone(await service.indoor_connection((.001, .001), (.0025, .001)))
        service.get.return_value = {'elements': []}
        self.assertIsNone(await service.indoor_connection((.001, .001), (.0015, .001)))
        service.get.side_effect = ConnectionError('offline')
        self.assertIsNone(await service.indoor_connection((.001, .001), (.0015, .001)))

    async def test_drive_cycle_and_free_paths_do_not_use_indoor_lookup(self):
        service = self.service()
        service.indoor_connection = AsyncMock()
        service.route = AsyncMock(return_value=[{'points': [(0, 0), (0, .001)]}])
        for profile in ('car_roads', 'cycle_paths', 'offroad_walk'):
            await service.mixed_route([{'start': (0, 0), 'end': (0, .001),
                'spec': {'profile': profile, 'speed_mode': 'manual', 'speed_kph': 5}}], include_stops=False)
        service.indoor_connection.assert_not_awaited()
