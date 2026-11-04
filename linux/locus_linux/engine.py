# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized location session, independent of Qt and tested with fake transport."""
import asyncio
import json
import math
import random
import time
from pathlib import Path


def coordinates(latitude, longitude):
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValueError("Coordinates must be numbers.")
    lat, lon = float(latitude), float(longitude)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude must be −90…90 and longitude −180…180.")
    return lat, lon


class Session:
    def __init__(self, transport, journal: Path, notify=lambda message: None, changed=lambda state: None, traffic_rng=None, wall_clock=time.time):
        self.transport, self.journal, self.notify = transport, journal, notify
        self.lock = asyncio.Lock()
        self.device = None
        self.target = None
        self.refresh = None
        self.changed = changed
        self.mode = "fixed"
        self.route = None
        self.progress = 0.0
        self.loop_route = False
        self.paused = False
        self.vector = (0.0, 0.0)
        self.speed = 1.4
        self.variation = False
        self.variation_factor = 1.0
        self.variation_target = 1.0
        self.variation_remaining = 0.0
        self.reconnect = True
        self.last_target = None
        self.last_ack = None
        self.next_retry = 0
        self.last_tick = time.monotonic()
        self.motion_speed = 0.0
        self.traffic_enabled = True
        self.red_probability = .45
        self.traffic_rng = traffic_rng or random.Random()
        self.traffic = None
        self.departures = None
        self.wall_clock = wall_clock

    def snapshot(self):
        return {"mode": self.mode, "paused": self.paused, "position": self.last_ack,
                "connected": self.target is not None, "progress": self.progress,
                "total": self.route.total if self.route and self.mode == "route" else 0,
                "speed_kph": self.motion_speed * 3.6,
                "speed_source": self.route.sources[self.route.edge_at(self.progress)] if self.route and self.route.sources and self.mode == "route" else "manual / global",
                "planned_stop": self.departures.snapshot() if self.mode == "route" and self.departures else None,
                "active_leg": self.route.leg_at(self.progress, bool(self.departures and self.departures.waiting)) if self.route and self.mode == "route" else None,
                "traffic_stop": {"kind": self.traffic.waiting['kind'], "remaining": self.traffic.remaining, "queue_distance": self.traffic.waiting.get("queue_distance", 0)} if self.mode == 'route' and self.traffic and self.traffic.waiting else None}

    async def configure_traffic(self, enabled, red_probability=.45):
        from .traffic import TrafficSchedule
        if not isinstance(enabled, bool) or not math.isfinite(red_probability) or not 0 <= red_probability <= 1:
            raise ValueError('Traffic stops need an on/off choice and red-light probability 0–100%.')
        async with self.lock:
            if enabled == self.traffic_enabled and red_probability == self.red_probability:
                return 'Driving stop settings unchanged.'
            previous = self.traffic
            self.traffic_enabled, self.red_probability = enabled, red_probability
            if self.route and self.mode == 'route':
                self.traffic = TrafficSchedule(self.route.stops, enabled, red_probability, self.traffic_rng, after=max(self.progress + .01, previous.served_through if previous else -1))
                if enabled and previous and previous.waiting:
                    self.traffic.waiting, self.traffic.remaining = previous.waiting, previous.remaining
            self.changed(self.snapshot())
        return 'Driving stop settings updated.'

    async def configure(self, speed, variation, reconnect):
        if not math.isfinite(speed) or not 0.1 <= speed <= 100:
            raise ValueError("Speed must be 0.1–100 m/s.")
        async with self.lock:
            self.speed, self.variation, self.reconnect = speed, bool(variation), bool(reconnect)
        return "Movement settings updated."

    async def start_route(self, device, points, loop=False, profile=None):
        from .routes import Route
        route = Route(points, **(profile or {}))
        await self.apply(device, *route.points[0])
        async with self.lock:
            self.route, self.progress, self.loop_route = route, 0, bool(loop)
            self.mode, self.paused = "route", False
            self.motion_speed = 0 if not route.speeds or route.motorized[0] or self.variation else route.speeds[0]
            self.variation_factor, self.variation_target, self.variation_remaining = 1., 1., 0.
            from .traffic import TrafficSchedule
            self.traffic = TrafficSchedule(route.stops, self.traffic_enabled, self.red_probability, self.traffic_rng)
            from .departures import DepartureSchedule
            self.departures = DepartureSchedule(route.planned_stops, self.wall_clock)
            if self.departures.upcoming and self.departures.upcoming['distance'] <= .01:
                self.departures.arrive()
                self.motion_speed = 0
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
        return "Following route. Pause holds the current location."

    async def control(self, action):
        async with self.lock:
            if action == 'depart':
                if self.mode != 'route' or not self.departures or not self.departures.waiting:
                    raise ValueError('The route is not waiting at a planned stop')
                if self.paused or self.target is None:
                    raise ValueError('Reconnect and resume the route before continuing')
                if self.departures.waiting['mode'] != 'manual':
                    raise ValueError('This stop leaves automatically when its timer or departure time is reached')
                self.departures.release()
            elif action == "hold":
                self.mode, self.vector = "fixed", (0, 0)
                self.motion_speed = 0
            elif action == "pause":
                self.paused = True
                self.motion_speed = 0
                if self.mode == "joystick":
                    self.mode, self.vector = "fixed", (0, 0)
            elif action == "resume":
                if self.target is None or not self.route or self.mode != "route":
                    raise ValueError("Reconnect the phone and start a route before resuming.")
                if self.progress >= self.route.total and not (self.departures and self.departures.waiting):
                    raise ValueError("Route has finished. Start it again to replay.")
                self.paused = False
            else:
                raise ValueError("Unknown movement action")
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
        return "Leaving planned stop." if action == "depart" else "Holding location." if action != "resume" else "Route resumed."

    async def joystick(self, east, north):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and -1 <= v <= 1 for v in (east, north)):
            raise ValueError("Joystick vector must be within −1…1.")
        async with self.lock:
            if east == 0 and north == 0:
                self.vector = (0, 0)
                self.motion_speed = 0
                if self.mode == "joystick":
                    self.mode = "fixed"
                self.changed(self.snapshot())
                return "Holding location."
            if self.target is None:
                raise ValueError("Set a location before using the joystick.")
            if self.mode == "route" and not self.paused:
                raise ValueError("Pause or stop the route before using the joystick.")
            magnitude = math.hypot(east, north)
            self.vector = (east / max(1, magnitude), north / max(1, magnitude))
            self.mode = "joystick" if magnitude else "fixed"
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
        return "Joystick moving." if magnitude else "Holding location."

    def read_record(self):
        try:
            record = json.loads(self.journal.read_text())
            if not isinstance(record, dict) or not isinstance(record.get('device'), str) or not record['device'].strip():
                raise ValueError('Missing iPhone ownership data')
            if record.get('transport', 'usb') not in ('usb', 'wifi'):
                raise ValueError('Unknown recovery transport')
            return record
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise RuntimeError(f'Recovery record at {self.journal} could not be read: {error}. '
                               'Original preserved; repair it before changing or restoring phone location.') from error

    def record(self, device, target):
        self.journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.journal.with_suffix(".tmp")
        temporary.write_text(json.dumps({"device": device, "target": target, "transport": getattr(self.transport, "mode", "usb")}))
        temporary.chmod(0o600)
        temporary.replace(self.journal)

    async def apply(self, device, latitude, longitude):
        target = coordinates(latitude, longitude)
        if not device:
            raise ValueError("Select an iPhone first.")
        async with self.lock:
            if self.device and self.device != device:
                raise RuntimeError("Restore the current iPhone before switching devices.")
            previous = self.read_record()
            if previous is not None:
                if previous["device"] != device:
                    raise RuntimeError("An unresolved session belongs to another iPhone. Restore it first.")
            # Record uncertainty before a command could reach the device.
            self.record(device, target)
            self.device, self.target = device, target
            self.last_target = target
            self.mode, self.vector = "fixed", (0, 0)
            self.motion_speed = 0
            self.traffic = None
            self.departures = None
            try:
                await self.transport.set(device, *target)
            except BaseException:
                self.target = None
                raise
            self.last_ack = target
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
            if self.refresh is None or self.refresh.done():
                self.refresh = asyncio.create_task(self.keep_alive())
            return "Location command acknowledged. Verify in Maps on your iPhone."

    async def keep_alive(self):
        while True:
            await asyncio.sleep(1)
            now = time.monotonic()
            elapsed, self.last_tick = now - self.last_tick, now
            await self.tick(elapsed, now)

    def smooth_variation(self, duration, limited):
        if not self.variation:
            return 1.
        self.variation_remaining -= duration
        if self.variation_remaining <= 0:
            self.variation_target = random.uniform(.9, 1. if limited else 1.1)
            self.variation_remaining = random.uniform(6, 16)
        self.variation_factor += (self.variation_target - self.variation_factor) * min(1., duration / 4)
        return min(1., self.variation_factor) if limited else self.variation_factor

    def advance_smoothly(self, duration, factor, stop_at):
        remaining = duration
        while remaining > 1e-8 and self.progress < stop_at - 1e-8:
            step = min(.1, remaining)
            edge = self.route.edge_at(self.progress)
            vehicle = self.route.motorized[edge]
            variation = min(factor, 1.) if vehicle else factor
            target = (self.route.desired_speed(self.progress) if self.route.speeds else self.speed) * variation
            acceleration, braking = (2., 3.) if vehicle else (.8, 1.2)
            target = min(target, math.sqrt(2 * braking * max(0, stop_at - self.progress)))
            next_speed = min(target, self.motion_speed + acceleration * step) if target >= self.motion_speed else max(target, self.motion_speed - braking * step)
            average = max(.001, (self.motion_speed + next_speed) / 2)
            if self.route.speeds:
                self.progress = min(stop_at, self.route.advance(self.progress, step, average, variation))
            else:
                self.progress = min(stop_at, self.progress + average * step)
            self.motion_speed = next_speed
            if self.route.speeds:
                edge = self.route.edge_at(self.progress)
                limit_factor = min(factor, 1.) if self.route.motorized[edge] else factor
                self.motion_speed = min(self.motion_speed, self.route.speeds[edge] * limit_factor)
            remaining -= step
        if 0 <= stop_at - self.progress <= 1e-8:
            self.progress = stop_at

    async def tick(self, elapsed, now=None):
        from .routes import offset
        now = time.monotonic() if now is None else now
        async with self.lock:
            if self.target is None:
                if not self.reconnect or self.last_target is None or now < self.next_retry:
                    return
                self.next_retry = now + 5
                candidate = self.last_target
            else:
                candidate = self.target
                if elapsed > 3 and self.mode in ("route", "joystick"):
                    self.paused, self.vector = True, (0, 0)
                    self.notify("Movement paused after a scheduling delay or sleep. Resume the route when ready.")
                speed = self.speed * (random.uniform(0.9, 1.1) if self.variation else 1)
                meters = speed * max(0, min(elapsed, 2))
                if self.mode == "joystick":
                    candidate = offset(candidate, self.vector[0] * meters, self.vector[1] * meters)
                    self.motion_speed = speed * math.hypot(*self.vector)
                elif self.mode == "route" and not self.paused:
                    if self.progress >= self.route.total and self.loop_route and not (self.departures and self.departures.waiting):
                        self.progress = 0
                        from .traffic import TrafficSchedule
                        self.traffic = TrafficSchedule(self.route.stops, self.traffic_enabled, self.red_probability, self.traffic_rng)
                        from .departures import DepartureSchedule
                        self.departures = DepartureSchedule(self.route.planned_stops, self.wall_clock)
                        if self.departures.upcoming and self.departures.upcoming['distance'] <= .01:
                            self.departures.arrive()
                    previous = self.progress
                    duration = max(0, min(elapsed, 2))
                    waiting = self.traffic and self.traffic.waiting
                    upcoming = self.traffic.upcoming if self.traffic else None
                    planned_waiting = self.departures and self.departures.waiting
                    planned_next = self.departures.upcoming if self.departures and not planned_waiting else None
                    if planned_waiting:
                        self.departures.hold(duration)
                        self.motion_speed = 0
                    elif waiting:
                        self.traffic.hold(duration)
                        self.motion_speed = 0
                    elif self.route.speeds or self.variation:
                        vehicle = self.route.motorized[self.route.edge_at(self.progress)]
                        factor = self.smooth_variation(duration, vehicle)
                        stop_at = min(self.route.total, planned_next['distance'] if planned_next else self.route.total,
                                      upcoming['distance'] if upcoming else self.route.total)
                        self.advance_smoothly(duration, factor, stop_at)
                    else:
                        self.progress = min(self.route.total, self.progress + meters)
                        self.motion_speed = speed
                    if not planned_waiting and planned_next and (not upcoming or planned_next['distance'] <= upcoming['distance'] + 1e-7) and self.progress >= planned_next['distance'] - 1e-7:
                        self.progress = planned_next['distance']
                        self.departures.arrive()
                        self.motion_speed = 0
                    if not planned_waiting and not waiting and upcoming and self.progress >= upcoming['distance'] - 1e-7:
                        self.progress = upcoming['distance']
                        self.traffic.arrive()
                        self.motion_speed = 0
                    candidate = self.route.at(self.progress)
                    if self.progress >= self.route.total and not self.loop_route and not (self.departures and self.departures.waiting):
                        self.paused = True
                        self.motion_speed = 0
                if candidate != self.last_target:
                    self.record(self.device, candidate)
                self.last_target = candidate
            try:
                await self.transport.set(self.device, *candidate)
            except Exception as error:
                first_failure = self.target is not None
                self.target, self.paused, self.vector = None, True, (0, 0)
                self.motion_speed = 0
                self.next_retry = now + 5
                if first_failure:
                    self.notify(f"Connection interrupted; location is unknown. Retrying the same iPhone: {error}")
                self.changed(self.snapshot())
                return
            reconnected = self.target is None
            self.target = self.last_ack = candidate
            if reconnected:
                self.notify("iPhone reconnected. Location held; resume the route explicitly.")
            self.changed(self.snapshot())

    async def restore(self, device):
        async with self.lock:
            owner = self.device
            previous = self.read_record()
            if previous is not None:
                owner = previous['device']
            if owner and owner != device:
                raise RuntimeError("Select the iPhone that owns the unresolved session.")
            self.target = None
            self.paused, self.vector = True, (0, 0)
            self.motion_speed = 0
            self.traffic = None
            self.departures = None
            if self.refresh:
                self.refresh.cancel()
            # Never remove the recovery record if clear/teardown fails.
            await self.transport.clear(device)
            await self.transport.close()
            self.journal.unlink(missing_ok=True)
            self.device = None
            self.last_target = self.last_ack = self.route = None
            self.mode, self.progress = "fixed", 0
            self.changed(self.snapshot())
            return "Clear command sent. Verify real location in Maps."


class DemoTransport:
    def __init__(self):
        self.mode = "usb"

    async def set_mode(self, mode):
        if mode not in ("usb", "wifi"):
            raise ValueError("Choose USB or Wi-Fi.")
        self.mode = mode
        return "Connection mode updated. Scan to find your phone."

    async def enable_wifi(self, device):
        return "Demo Wi-Fi enabled."
    async def discover(self):
        return [("demo", "Demo iPhone (no hardware)")]

    async def prepare(self, device):
        return "Demo ready. No phone commands are sent."

    async def set(self, device, latitude, longitude):
        pass

    async def clear(self, device):
        pass

    async def close(self):
        pass
