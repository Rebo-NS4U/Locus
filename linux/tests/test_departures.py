import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from locus_linux.departures import departure, DepartureSchedule
from locus_linux.engine import Session
from locus_linux.planning import RoutePlan
from locus_linux.library import RouteLibrary
from test_engine import Transport


class DepartureTests(unittest.TestCase):
    def test_validation(self):
        for value in ({'mode': 'bad'}, {'mode': 'timer', 'seconds': 0},
                      {'mode': 'timer', 'seconds': float('inf')}, {'mode': 'clock', 'time': '25:00'}):
            with self.assertRaises(ValueError):
                departure(value)

    def test_clock_rolls_to_tomorrow_and_manual_cannot_expire(self):
        now = datetime(2026, 11, 3, 15, 0).timestamp()
        scheduler = DepartureSchedule([{'distance': 0, 'mode': 'clock', 'time': '14:00', 'stop': 1}], lambda: now)
        scheduler.arrive()
        self.assertEqual(datetime.fromtimestamp(scheduler.deadline), datetime(2026, 11, 4, 14, 0))
        now = scheduler.deadline
        scheduler.hold(0)
        self.assertIsNone(scheduler.waiting)
        scheduler = DepartureSchedule([{'distance': 0, 'mode': 'manual', 'stop': 1}])
        scheduler.arrive()
        scheduler.hold(999999)
        self.assertIsNotNone(scheduler.waiting)

    def test_arriving_during_selected_minute_is_due_today(self):
        for second in (0, 0.125, 30, 59.999):
            now = datetime(2026, 11, 3, 15, 0).timestamp() + second
            scheduler = DepartureSchedule(
                [{'distance': 0, 'mode': 'clock', 'time': '15:00'}], lambda: now)
            scheduler.arrive()
            self.assertEqual(scheduler.remaining, 0)
            scheduler.hold(0)
            self.assertIsNone(scheduler.waiting)

    def test_saved_rules_follow_pins_and_assemble_at_correct_shape_indexes(self):
        plan = RoutePlan()
        plan.reset([(0, 0), (0, .001), (0, .002)])
        plan.set_departure(1, {'mode': 'timer', 'seconds': 45})
        plan.move(1, (.0001, .001))
        restored = RoutePlan.deserialize(plan.serialize())
        self.assertEqual(restored.pins[1]['departure'], {'mode': 'timer', 'seconds': 45})
        from test_planning import candidate
        restored.accept([[candidate([restored.points[0], (.00005, .0005), restored.points[1]])],
                         [candidate(restored.points[1:])]], restored.revision)
        self.assertEqual(restored.assemble()['planned_stops'], [{'index': 2, 'stop': 2, 'mode': 'timer', 'seconds': 45}])
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'routes.json'
            library = RouteLibrary(path)
            identifier = library.save_route('Timed route', restored.serialize())
            self.assertEqual(RouteLibrary(path).get(identifier)['document']['pins'][1]['departure']['seconds'], 45)


class PlannedStopPlaybackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 11, 3, 15, 0).timestamp()
        self.transport = Transport()
        self.session = Session(self.transport, Path(self.directory.name) / 'session.json', wall_clock=lambda: self.now)
        await self.session.configure(100, False, False)
        self.points = [(0, 0), (0, .0001), (0, .0002)]

    async def asyncTearDown(self):
        if self.session.refresh:
            self.session.refresh.cancel()
            await asyncio.gather(self.session.refresh, return_exceptions=True)
        self.directory.cleanup()

    async def start(self, mode, index=1, loop=False, **values):
        await self.session.start_route('phone', self.points, loop, {'planned_stops': [{'index': index, 'stop': index + 1, 'mode': mode, **values}]})

    async def test_manual_wait_lands_exactly_and_refreshes_until_continue(self):
        await self.start('manual')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[1])
        count = len(self.transport.calls)
        for _ in range(3):
            await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[1])
        self.assertGreater(len(self.transport.calls), count)
        self.assertEqual(self.session.snapshot()['planned_stop']['mode'], 'manual')
        await self.session.control('depart')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_timer_freezes_during_pause_and_leaves_after_duration(self):
        await self.start('timer', seconds=2)
        await self.session.tick(1)
        await self.session.control('pause')
        await self.session.tick(1)
        self.assertEqual(self.session.snapshot()['planned_stop']['remaining'], 2)
        await self.session.control('resume')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[1])
        await self.session.tick(1)
        self.assertIsNone(self.session.snapshot()['planned_stop'])
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_time_of_day_waits_until_tomorrow(self):
        await self.start('clock', time='14:00')
        await self.session.tick(1)
        deadline = self.session.snapshot()['planned_stop']['deadline']
        self.assertEqual(datetime.fromtimestamp(deadline), datetime(2026, 11, 4, 14, 0))
        self.now = deadline - 1
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[1])
        self.now = deadline
        await self.session.tick(1)
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_clock_departs_when_system_time_reaches_selected_minute(self):
        await self.start('clock', index=0, time='15:01')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[0])
        self.now += 60.125
        await self.session.tick(1)
        self.assertIsNone(self.session.snapshot()['planned_stop'])
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_clock_starting_during_selected_minute_does_not_wait_a_day(self):
        self.now += .125
        await self.start('clock', index=0, time='15:00')
        await self.session.tick(1)
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_clock_wait_stays_armed_after_delay_until_515_pm(self):
        self.now = datetime(2026, 11, 3, 16, 0).timestamp()
        await self.start('clock', index=0, time='17:15')
        await self.session.tick(4)
        self.assertFalse(self.session.paused)
        self.now = datetime(2026, 11, 3, 17, 15).timestamp()
        await self.session.tick(4500)
        self.assertFalse(self.session.paused)
        self.assertIsNone(self.session.snapshot()['planned_stop'])
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_explicit_pause_still_blocks_clock_departure(self):
        await self.start('clock', index=0, time='15:01')
        await self.session.control('pause')
        self.now += 120
        await self.session.tick(120)
        self.assertTrue(self.session.paused)
        self.assertIsNotNone(self.session.snapshot()['planned_stop'])
        await self.session.control('resume')
        await self.session.tick(1)
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_start_and_final_pin_waits_and_loop_repeats(self):
        await self.start('manual', index=0)
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[0])
        await self.session.control('depart')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])
        await self.start('manual', index=2, loop=True)
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])
        self.assertIsNotNone(self.session.snapshot()['planned_stop'])
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])
        await self.session.control('depart')
        await self.session.tick(.01)
        self.assertLess(self.session.progress, self.session.route.total)

    async def test_traffic_stop_before_planned_stop_is_not_skipped(self):
        await self.session.start_route('phone', self.points, False, {
            'motorized': [True, True], 'stops': [{'index': 1, 'kind': 'stop_sign'}],
            'planned_stops': [{'index': 2, 'stop': 3, 'mode': 'manual'}]})
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[1])
        self.assertIsNone(self.session.snapshot()['planned_stop'])
        self.assertIsNotNone(self.session.snapshot()['traffic_stop'])
        for _ in range(10):
            await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])
        self.assertIsNotNone(self.session.snapshot()['planned_stop'])

    async def test_reconnect_keeps_manual_stop_until_resume_and_continue(self):
        await self.start('manual')
        await self.session.tick(1)
        self.transport.fail_set = True
        await self.session.tick(1)
        self.assertIsNone(self.session.target)
        self.transport.fail_set = False
        self.session.reconnect = True
        await self.session.tick(1, self.session.next_retry + 1)
        self.assertTrue(self.session.paused)
        self.assertEqual(self.session.target, self.points[1])
        self.assertIsNotNone(self.session.snapshot()['planned_stop'])
        await self.session.control('resume')
        await self.session.control('depart')
        await self.session.tick(1)
        self.assertEqual(self.session.target, self.points[-1])

    async def test_sleep_and_disconnect_do_not_automatically_depart(self):
        await self.start('manual', index=2)
        await self.session.tick(1)
        await self.session.tick(10)
        self.assertTrue(self.session.paused)
        with self.assertRaises(ValueError):
            await self.session.control('depart')
        await self.session.control('resume')
        await self.session.control('depart')
        await self.session.tick(1)
        self.assertTrue(self.session.paused)
