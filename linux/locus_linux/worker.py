# SPDX-License-Identifier: GPL-3.0-or-later
"""Background phone and routing operations."""
import asyncio
import json
import os
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from .engine import Session, DemoTransport
from .device import IPhoneTransport
from .services import Services


def state_directory():
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "locus-linux"


class Worker(QThread):
    finished_operation = Signal(str, object)
    failed = Signal(str, str)
    interrupted = Signal(str)
    state_changed = Signal(object)
    ready = Signal()

    def __init__(self, demo):
        super().__init__()
        self.demo = demo

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.transport = DemoTransport() if self.demo else IPhoneTransport()
        self.session = Session(self.transport, state_directory() / ("demo-session.json" if self.demo else "session.json"),
                               self.interrupted.emit, self.state_changed.emit)
        if self.session.journal.exists():
            saved_mode = json.loads(self.session.journal.read_text()).get("transport", "usb")
            if saved_mode in ("usb", "wifi"):
                self.transport.mode = saved_mode
        self.services = Services()
        self.operation_lock = asyncio.Lock()
        self.ready.emit()
        self.loop.run_forever()
        self.loop.close()

    def submit(self, operation, *args):
        async def execute():
            try:
                async with self.operation_lock, asyncio.timeout(180):
                    if operation == "discover":
                        result = await self.transport.discover()
                    elif operation in ("prepare", "enable_wifi", "set_mode"):
                        if self.session.journal.exists():
                            raise ValueError("Restore the current session before changing device setup or transport.")
                        result = await getattr(self.transport, operation)(*args)
                    elif operation in ("apply", "restore", "configure", "configure_traffic", "start_route", "control", "joystick"):
                        result = await getattr(self.session, operation)(*args)
                    elif operation in ("search", "mixed_route"):
                        result = await getattr(self.services, operation)(*args)
                    else:
                        raise ValueError("Unknown operation")
                self.finished_operation.emit(operation, result)
            except Exception as error:
                self.failed.emit(operation, str(error) or type(error).__name__)
        asyncio.run_coroutine_threadsafe(execute(), self.loop)

    def stop(self):
        async def cleanup():
            if self.session.refresh:
                self.session.refresh.cancel()
                await asyncio.gather(self.session.refresh, return_exceptions=True)
            try:
                await asyncio.wait_for(self.transport.close(), 10)
            finally:
                self.loop.stop()
        asyncio.run_coroutine_threadsafe(cleanup(), self.loop)
