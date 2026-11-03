# SPDX-License-Identifier: GPL-3.0-or-later
"""Serialized location session, independent of Qt and tested with fake transport."""
import asyncio
import json
import math
from pathlib import Path


def coordinates(latitude, longitude):
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        raise ValueError("Coordinates must be numbers.")
    lat, lon = float(latitude), float(longitude)
    if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Latitude must be −90…90 and longitude −180…180.")
    return lat, lon


class Session:
    def __init__(self, transport, journal: Path, notify=lambda message: None):
        self.transport, self.journal, self.notify = transport, journal, notify
        self.lock = asyncio.Lock()
        self.device = None
        self.target = None
        self.refresh = None

    def record(self, device, target):
        self.journal.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.journal.with_suffix(".tmp")
        temporary.write_text(json.dumps({"device": device, "target": target}))
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
            try:
                await self.transport.set(device, *target)
            except BaseException:
                self.target = None
                raise
            if self.refresh is None or self.refresh.done():
                self.refresh = asyncio.create_task(self.keep_alive())
            return "Location command acknowledged. Verify in Maps on your iPhone."

    async def keep_alive(self):
        while True:
            await asyncio.sleep(1)
            async with self.lock:
                if self.target is None:
                    return
                try:
                    await self.transport.set(self.device, *self.target)
                except Exception as error:
                    self.target = None
                    self.notify(f"Connection interrupted; location is unknown: {error}")
                    return

    async def restore(self, device):
        async with self.lock:
            owner = self.device
            if self.journal.exists():
                owner = json.loads(self.journal.read_text())["device"]
            if owner and owner != device:
                raise RuntimeError("Select the iPhone that owns the unresolved session.")
            self.target = None
            if self.refresh:
                self.refresh.cancel()
            # Never remove the recovery record if clear/teardown fails.
            await self.transport.clear(device)
            await self.transport.close()
            self.journal.unlink(missing_ok=True)
            self.device = None
            return "Clear command sent. Verify real location in Maps."


class DemoTransport:
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
