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
    def __init__(self, transport, journal: Path, notify=lambda message: None, changed=lambda state: None):
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
        self.reconnect = True
        self.last_target = None
        self.last_ack = None
        self.next_retry = 0
        self.last_tick = time.monotonic()

    def snapshot(self):
        return {"mode": self.mode, "paused": self.paused, "position": self.last_ack,
                "connected": self.target is not None, "progress": self.progress,
                "total": self.route.total if self.route and self.mode == "route" else 0}

    async def configure(self, speed, variation, reconnect):
        if not math.isfinite(speed) or not 0.1 <= speed <= 100:
            raise ValueError("Speed must be 0.1–100 m/s.")
        async with self.lock:
            self.speed, self.variation, self.reconnect = speed, bool(variation), bool(reconnect)
        return "Movement settings updated."

    async def start_route(self, device, points, loop=False):
        from .routes import Route
        route = Route(points)
        await self.apply(device, *route.points[0])
        async with self.lock:
            self.route, self.progress, self.loop_route = route, 0, bool(loop)
            self.mode, self.paused = "route", False
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
        return "Following route. Pause holds the current location."

    async def control(self, action):
        async with self.lock:
            if action == "hold":
                self.mode, self.vector = "fixed", (0, 0)
            elif action == "pause":
                self.paused = True
                if self.mode == "joystick":
                    self.mode, self.vector = "fixed", (0, 0)
            elif action == "resume":
                if self.target is None or not self.route or self.mode != "route":
                    raise ValueError("Reconnect the phone and start a route before resuming.")
                if self.progress >= self.route.total:
                    raise ValueError("Route has finished. Start it again to replay.")
                self.paused = False
            else:
                raise ValueError("Unknown movement action")
            self.last_tick = time.monotonic()
            self.changed(self.snapshot())
        return "Holding location." if action != "resume" else "Route resumed."

    async def joystick(self, east, north):
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and -1 <= v <= 1 for v in (east, north)):
            raise ValueError("Joystick vector must be within −1…1.")
        async with self.lock:
            if east == 0 and north == 0:
                self.vector = (0, 0)
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
            if self.journal.exists():
                previous = json.loads(self.journal.read_text())
                if previous["device"] != device:
                    raise RuntimeError("An unresolved session belongs to another iPhone. Restore it first.")
            # Record uncertainty before a command could reach the device.
            self.record(device, target)
            self.device, self.target = device, target
            self.last_target = target
            self.mode, self.vector = "fixed", (0, 0)
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
                elif self.mode == "route" and not self.paused:
                    if self.progress >= self.route.total and self.loop_route:
                        self.progress = 0
                    self.progress = min(self.route.total, self.progress + meters)
                    candidate = self.route.at(self.progress)
                    if self.progress >= self.route.total and not self.loop_route:
                        self.paused = True
                if candidate != self.last_target:
                    self.record(self.device, candidate)
                self.last_target = candidate
            try:
                await self.transport.set(self.device, *candidate)
            except Exception as error:
                first_failure = self.target is not None
                self.target, self.paused, self.vector = None, True, (0, 0)
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
            if self.journal.exists():
                owner = json.loads(self.journal.read_text())["device"]
            if owner and owner != device:
                raise RuntimeError("Select the iPhone that owns the unresolved session.")
            self.target = None
            self.paused, self.vector = True, (0, 0)
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
