# SPDX-License-Identifier: GPL-3.0-or-later
"""iOS 17.4+ USB transport using pymobiledevice3's rootless tunnel."""
import asyncio
from contextlib import AsyncExitStack, suppress


class IPhoneTransport:
    def __init__(self):
        self.stack = None
        self.device = None
        self.location = None

    async def discover(self):
        from pymobiledevice3 import usbmux
        return [(d.serial, f"iPhone · {d.serial}") for d in await usbmux.list_devices()
                if d.connection_type == "USB"]

    async def prepare(self, device):
        from packaging.version import Version
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter, fetch_personalized_ddi
        if not device:
            raise ValueError("Select an iPhone first.")
        async with await create_using_usbmux(serial=device, connection_type="USB", autopair=True, pair_timeout=20) as phone:
            if not phone.paired:
                raise RuntimeError("Unlock the phone and accept Trust This Computer.")
            if Version(phone.product_version) < Version("17.4"):
                raise RuntimeError("This prototype requires iOS 17.4 or newer.")
            if not await phone.get_developer_mode_status():
                raise RuntimeError("Enable Developer Mode in Settings → Privacy & Security.")
            async with PersonalizedImageMounter(phone) as mounter:
                if not await mounter.is_image_mounted("Personalized"):
                    image, manifest, trustcache = await asyncio.to_thread(fetch_personalized_ddi)
                    with suppress(AlreadyMountedError):
                        await mounter.mount(image, manifest, trustcache)
        return "Developer image ready. You can now set a location."

    async def connect(self, device):
        if not device:
            raise ValueError("Select an iPhone first.")
        if self.location:
            if self.device != device:
                raise RuntimeError("Restore before switching iPhones.")
            return
        from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
        stack = AsyncExitStack()
        try:
            rsd = await stack.enter_async_context(UserspaceRsdTunnel(
                serial=device, autopair=False, remotepairing_fallback=False))
            if rsd.udid != device:
                raise RuntimeError("Connected device does not match your selection.")
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            location = await stack.enter_async_context(LocationSimulation(dvt))
        except BaseException:
            await stack.aclose()
            raise
        self.stack, self.device, self.location = stack, device, location

    async def set(self, device, latitude, longitude):
        try:
            await self.connect(device)
            await asyncio.wait_for(self.location.set(latitude, longitude), 10)
        except BaseException:
            await self.close()
            raise

    async def clear(self, device):
        try:
            await self.connect(device)
            await asyncio.wait_for(self.location.clear(), 10)
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self.stack:
            await self.stack.aclose()
        self.stack = self.device = self.location = None
