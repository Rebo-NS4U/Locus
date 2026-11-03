# Locus for Linux — native desktop preview

This fork adds a **native PySide6/Qt desktop application** alongside the original
Swift iPhone application. The desktop controls a connected iPhone; it does not
change the Linux computer's location. No LocalDevVPN or sideloaded iPhone app is
needed for this USB implementation.

## Implemented

- Native window, USB device selection, coordinate entry and interactive map.
- Click-to-preview pins; only **Set location** sends coordinates to the phone.
- Explicit **Prepare iPhone** action to pair and mount Apple's developer image.
- Persistent, rootless developer tunnel and one-second location refresh.
- Explicit restore, interrupted-session messages, recovery record and quit prompt.
- **Demo mode** exercises the interface without importing or contacting phone services.
- Vendored Leaflet 1.9.4 map code; map tiles come from OpenStreetMap.

This is an early port, not feature parity with the iPhone application. Joystick,
routes, GPX, favorites, place search and Wi-Fi transport are not implemented yet.
The map pin is a preview, not an independent reading from the phone.

## Run on Linux

Requires Python 3.12+ and a graphical desktop. The phone transport targets
**iOS 17.4+**, an unlocked, USB-connected iPhone and Developer Mode. Automated tests
use fake hardware; physical iPhone compatibility has **not** been verified.

On Arch, install system USB support if needed:

```sh
sudo pacman -S --needed usbmuxd libimobiledevice
```

Then, from this directory:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/locus-linux --demo
# Real iPhone mode:
.venv/bin/locus-linux
```

Select **Scan USB**, select your phone, and choose **Prepare iPhone**. Accept the
phone's trust prompt. Enable Developer Mode in Settings → Privacy & Security,
restart the phone if prompted, then prepare again. First preparation requires
internet to fetch Apple's developer disk image. Preview a location and explicitly
choose **Set location**. Verify the result in Apple Maps.

Use **Restore location** before disconnecting or quitting. The clear command does
not prove a fresh GPS fix; check Maps. If a clear command fails, the recovery file
is retained. Reconnect the same phone and try Restore again. A phone restart may
be needed if the developer simulation remains stuck. There is no automatic
session resumption after an application restart.

## Tests

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip check
```

Session tests cover coordinate rejection, refresh, restore, interruption and
recovery protection across devices. `tests/smoke_ui.py` checks the real Qt window,
map bridge and demo apply/restore workflow; it needs a graphical session.

## Privacy and implementation

Qt's WebEngine renders a local, bundled map inside the native application. There
is no HTTP control server. Only map clicks cross the Qt WebChannel bridge. Map
views fetch tiles from OpenStreetMap, exposing the viewed area and IP address to
that service; coordinates can still be entered when tiles are unavailable.
No bulk tile downloads or offline caching are implemented.

Recovery state (device ID and last requested coordinates) is saved with mode 0600
under `$XDG_STATE_HOME/locus-linux`, defaulting to `~/.local/state/locus-linux`.
Demo state uses a separate file. The application never changes passcodes, Find My,
or device location during discovery. It never restores an unrelated device.

The transport uses pymobiledevice3 11.12.4's public UserspaceRsdTunnel and DVT
LocationSimulation APIs. UI operations run off the Qt thread with bounded waits.
No source was copied from Ghost. The original Swift application remains under
the root MIT license. The new `linux/` implementation is GPL-3.0-or-later (see
COPYING), consistent with its GPL pymobiledevice3 dependency. Vendored Leaflet
retains its BSD license in `locus_linux/web/LEAFLET-LICENSE`.

## Next milestones

1. Validate USB preparation, set/refresh/restore, unplug and recovery on real iPhones.
2. Add joystick controls and Locus's walk/run/cycle/drive speed presets.
3. Add route editing, timed playback, GPX import/export and favorites.
4. Add Wi-Fi connection management, Linux packaging and an application-menu installer.
