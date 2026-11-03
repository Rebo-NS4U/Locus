# SPDX-License-Identifier: GPL-3.0-or-later
"""iOS 17.4+ USB and trusted Wi-Fi transports with rootless developer tunnels."""
import asyncio
from contextlib import AsyncExitStack, suppress, aclosing, asynccontextmanager


@asynccontextmanager
async def wifi_rsd(phone):
    """Rootless CoreDeviceProxy over an explicitly authenticated TCP lockdown.

    Uses public transport primitives rather than changing upstream's USB selector.
    This application permits only one session/tunnel at a time.
    """
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    stack = AsyncExitStack()
    tasks = []
    previous = tunnel_service.USE_USERSPACE_TUNNEL
    rsd = None
    try:
        service = await stack.enter_async_context(await phone.start_lockdown_service("com.apple.mobile.heartbeat"))

        async def heartbeat():
            try:
                while True:
                    response = await asyncio.wait_for(service.recv_plist(), 35)
                    if response.get("Command") == "SleepyTime":
                        raise ConnectionError("iPhone is sleeping")
                    await service.send_plist({"Command": "Polo"})
            except Exception:
                if rsd:
                    await rsd.close()
                raise

        tasks.append(asyncio.create_task(heartbeat()))
        provider = await tunnel_service.CoreDeviceTunnelProxy.create(phone)
        stack.push_async_callback(provider.close)
        tunnel_service.USE_USERSPACE_TUNNEL = True
        result = await stack.enter_async_context(provider.start_tcp_tunnel())
        result.client.tun.set_peer(result.address)
        dialer = await stack.enter_async_context(UserspaceDialPlane(result.client.tun, result.address))
        rsd = RemoteServiceDiscoveryService((result.address, result.port), open_connection=dialer.dial,
                                             auxiliary_metadata=result.auxiliary_metadata)
        stack.push_async_callback(rsd.close)
        await rsd.connect()

        async def disconnected():
            await result.client.wait_closed()
            await rsd.close()

        tasks.append(asyncio.create_task(disconnected()))
        if tasks[0].done():
            tasks[0].result()
        yield rsd
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await stack.aclose()
        finally:
            tunnel_service.USE_USERSPACE_TUNNEL = previous


class IPhoneTransport:
    def __init__(self):
        self.stack = None
        self.device = None
        self.location = None
        self.mode = "usb"

    async def set_mode(self, mode):
        if mode not in ("usb", "wifi"):
            raise ValueError("Choose USB or Wi-Fi.")
        if self.stack:
            raise RuntimeError("Restore location before changing transport.")
        self.mode = mode
        return "Connection mode changed. Scan to find your phone."

    async def lockdown(self, device, autopair=False):
        from pymobiledevice3.lockdown import create_using_usbmux, get_mobdev2_lockdowns
        if not device:
            raise ValueError("Select an iPhone first.")
        if self.mode == "usb":
            return await create_using_usbmux(serial=device, connection_type="USB", autopair=autopair, pair_timeout=20)
        async with asyncio.timeout(10), aclosing(get_mobdev2_lockdowns(udid=device, only_paired=True, timeout=2)) as phones:
            async for _, phone in phones:
                if phone.udid == device and phone.paired:
                    return phone
                await phone.close()
        raise ConnectionError("Trusted iPhone not found on Wi-Fi. Enable Wi-Fi over USB first, unlock it and use the same network.")

    async def enable_wifi(self, device):
        if self.mode != "usb" or self.stack:
            raise RuntimeError("Restore location and select USB before enabling Wi-Fi.")
        async with await self.lockdown(device, autopair=True) as phone:
            if not phone.paired:
                raise RuntimeError("Unlock the phone and accept Trust This Computer.")
            await phone.save_pair_record()
            await phone.set_enable_wifi_connections(True)
        return "Wi-Fi enabled. Unplug USB, select Wi-Fi and scan on the same network."

    async def discover(self):
        if self.mode == "wifi":
            from pymobiledevice3.lockdown import get_mobdev2_lockdowns
            found = {}
            async with asyncio.timeout(10), aclosing(get_mobdev2_lockdowns(only_paired=True, timeout=2)) as phones:
                async for _, phone in phones:
                    async with phone:
                        if phone.paired:
                            found[phone.udid] = phone.all_values.get("DeviceName", "iPhone") + " · Wi-Fi"
            return list(found.items())
        from pymobiledevice3 import usbmux
        return [(d.serial, f"iPhone · {d.serial}") for d in await usbmux.list_devices()
                if d.connection_type == "USB"]

    async def prepare(self, device):
        from packaging.version import Version
        from pymobiledevice3.exceptions import AlreadyMountedError
        from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter, fetch_personalized_ddi
        if not device:
            raise ValueError("Select an iPhone first.")
        async with await self.lockdown(device, autopair=True) as phone:
            if not phone.paired:
                raise RuntimeError("Unlock the phone and accept Trust This Computer.")
            if Version(phone.product_version) < Version("17.4"):
                raise RuntimeError("Locus Linux requires iOS 17.4 or newer.")
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
            if self.mode == "wifi":
                phone = await stack.enter_async_context(await self.lockdown(device))
                rsd = await stack.enter_async_context(wifi_rsd(phone))
            else:
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
