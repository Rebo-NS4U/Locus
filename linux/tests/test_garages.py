import unittest
from unittest.mock import AsyncMock
from locus_linux.garages import garage_access, GarageExitUnavailable
from locus_linux.services import Services


def map_data(oneway=None, forbidden=False):
    points = {1: (0, 0), 2: (0, .002), 3: (.002, .002), 4: (.002, 0),
              5: (.001, .002), 6: (.001, .0022), 7: (.001, .003), 8: (.002, .003)}
    nodes = [{'type': 'node', 'id': i, 'lat': p[0], 'lon': p[1]} for i, p in points.items()]
    tags = {'highway': 'service'}
    if oneway: tags['oneway'] = oneway
    if forbidden: tags['motor_vehicle'] = 'no'
    return nodes + [
        {'type': 'way', 'id': 10, 'nodes': [1, 2, 5, 3, 4, 1], 'tags': {'building': 'yes', 'parking': 'multi-storey'}},
        {'type': 'way', 'id': 11, 'nodes': [5, 6, 7], 'tags': tags},
        {'type': 'way', 'id': 12, 'nodes': [7, 8], 'tags': {'highway': 'residential'}}]


class GarageTests(unittest.TestCase):
    def test_garage_exit_uses_vehicle_access_and_junction(self):
        path = garage_access(map_data(), (.001, .001))
        self.assertEqual(path, [(.001, .001), (.001, .002), (.001, .0022), (.001, .003)])
        self.assertIsNone(garage_access(map_data(), (.003, .001)))

    def test_driving_between_pins_in_one_garage_does_not_exit_and_reenter(self):
        path = garage_access(map_data(), (.001, .001), True, (.0015, .0015))
        self.assertEqual(path, [(.001, .001), (.0015, .0015)])

    def test_oneway_exit_is_not_used_backwards_for_entry(self):
        self.assertIsNotNone(garage_access(map_data('yes'), (.001, .001), True))
        with self.assertRaises(GarageExitUnavailable):
            garage_access(map_data('yes'), (.001, .001), False)
        with self.assertRaises(GarageExitUnavailable):
            garage_access(map_data(forbidden=True), (.001, .001))

    def test_target_on_exit_road_does_not_require_detour_to_junction(self):
        path = garage_access(map_data(), (.001, .001), True, (.001, .0022))
        self.assertEqual(path[-1], (.001, .0022))
        self.assertNotIn((.001, .003), path)


class GarageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_garage_prefix_remains_driving_and_shifts_stop_indexes(self):
        service = Services()
        start, exit, end = (0, 0), (0, .002), (0, .01)
        prefix = [start, (0, .001), exit]
        service.garage_connection = AsyncMock(side_effect=[prefix, None])
        service.route = AsyncMock(return_value=[{'points': [exit, end]}])
        option = (await service.mixed_route([{'start': start, 'end': end,
            'spec': {'profile': 'car_roads', 'speed_mode': 'manual', 'speed_kph': 18}}], include_stops=False))[0][0]
        service.route.assert_awaited_once()
        self.assertEqual(service.route.call_args.args[0], [exit, end])
        self.assertEqual(option['points'], [*prefix, end])
        self.assertEqual(option['motorized'], [True, True, True])
        self.assertEqual(option['speeds'], [10 / 3.6, 10 / 3.6, 5])
        self.assertIn('garage_warning', option)

    async def test_exit_target_on_access_path_stays_driving_without_road_detour(self):
        service = Services()
        start, end = (0, 0), (0, .002)
        service.garage_connection = AsyncMock(return_value=[start, (0, .001), end])
        service.route = AsyncMock(side_effect=AssertionError('Already reached the exit'))
        option = (await service.mixed_route([{'start': start, 'end': end,
            'spec': {'profile': 'car_roads'}}], include_stops=False))[0][0]
        self.assertEqual(option['points'], [start, (0, .001), end])
        self.assertEqual(option['driving'], [True, True])
        self.assertEqual(option['motorized'], [True, True])
        service.route.assert_not_awaited()

    async def test_arriving_from_access_road_stays_driving(self):
        service = Services()
        start, end = (0, .002), (0, 0)
        service.garage_connection = AsyncMock(side_effect=[None, [start, (0, .001), end]])
        service.route = AsyncMock(side_effect=AssertionError('No road detour needed'))
        option = (await service.mixed_route([{'start': start, 'end': end,
            'spec': {'profile': 'car_roads'}}], include_stops=False))[0][0]
        self.assertEqual(option['motorized'], [True, True])
        self.assertEqual(option['points'][0], start)
        self.assertEqual(option['points'][-1], end)
