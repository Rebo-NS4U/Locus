import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from locus_linux.device import IPhoneTransport, wifi_rsd


class Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        if hasattr(self.value, 'close'):
            await self.value.close()


class DeviceTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_usbmuxd_reports_actionable_setup(self):
        from pymobiledevice3.exceptions import ConnectionFailedToUsbmuxdError
        with patch('pymobiledevice3.usbmux.list_devices', AsyncMock(side_effect=ConnectionFailedToUsbmuxdError)):
            with self.assertRaisesRegex(RuntimeError, 'Install and start usbmuxd'):
                await IPhoneTransport().discover()

    async def test_wifi_lookup_authenticates_and_matches_exact_device(self):
        wrong = SimpleNamespace(udid='wrong', paired=True, close=AsyncMock())
        wanted = SimpleNamespace(udid='wanted', paired=True, close=AsyncMock())

        async def devices(**kwargs):
            self.assertTrue(kwargs['only_paired'])
            self.assertEqual(kwargs['udid'], 'wanted')
            yield '192.0.2.1', wrong
            yield '192.0.2.2', wanted

        transport = IPhoneTransport()
        await transport.set_mode('wifi')
        with patch('pymobiledevice3.lockdown.get_mobdev2_lockdowns', devices):
            self.assertIs(await transport.lockdown('wanted'), wanted)
        wrong.close.assert_awaited_once()
        wanted.close.assert_not_awaited()

    async def test_wifi_enable_requires_idle_usb_and_saves_pairing(self):
        transport = IPhoneTransport()
        phone = SimpleNamespace(paired=True, save_pair_record=AsyncMock(), set_enable_wifi_connections=AsyncMock(), close=AsyncMock())
        transport.lockdown = AsyncMock(return_value=Context(phone))
        await transport.enable_wifi('phone')
        transport.lockdown.assert_awaited_once_with('phone', autopair=True)
        phone.save_pair_record.assert_awaited_once()
        phone.set_enable_wifi_connections.assert_awaited_once_with(True)
        transport.stack = object()
        with self.assertRaises(RuntimeError):
            await transport.set_mode('wifi')
        with self.assertRaises(RuntimeError):
            await transport.enable_wifi('phone')

    async def test_rootless_wifi_owns_heartbeat_and_restores_global_flag(self):
        from pymobiledevice3.remote import tunnel_service
        service = SimpleNamespace(recv_plist=AsyncMock(side_effect=lambda: asyncio.sleep(100)), send_plist=AsyncMock(), close=AsyncMock())

        async def receive():
            await asyncio.sleep(100)

        service.recv_plist = receive
        phone = SimpleNamespace(start_lockdown_service=AsyncMock(return_value=Context(service)))
        tunnel = SimpleNamespace(address='fd00::1', port=1234, auxiliary_metadata={},
            client=SimpleNamespace(tun=SimpleNamespace(set_peer=lambda address: None), wait_closed=receive))
        provider = SimpleNamespace(close=AsyncMock(), start_tcp_tunnel=lambda: Context(tunnel))
        rsd = SimpleNamespace(connect=AsyncMock(), close=AsyncMock(), udid='phone')
        dialer = SimpleNamespace(dial=AsyncMock())
        previous = tunnel_service.USE_USERSPACE_TUNNEL
        with patch.object(tunnel_service.CoreDeviceTunnelProxy, 'create', AsyncMock(return_value=provider)), \
             patch('pymobiledevice3.remote.userspace_tunnel.UserspaceDialPlane', return_value=Context(dialer)), \
             patch('pymobiledevice3.remote.remote_service_discovery.RemoteServiceDiscoveryService', return_value=rsd):
            async with wifi_rsd(phone) as connected:
                self.assertIs(connected, rsd)
                self.assertTrue(tunnel_service.USE_USERSPACE_TUNNEL)
        self.assertEqual(tunnel_service.USE_USERSPACE_TUNNEL, previous)
        rsd.connect.assert_awaited_once()
        rsd.close.assert_awaited_once()
        service.close.assert_awaited_once()
        provider.close.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
