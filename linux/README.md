# Locus for Linux 0.2

A native PySide6/Qt desktop companion to the original Locus iPhone application.
It controls a connected iPhone; it does not change the Linux computer's location.
No sideloaded iPhone app or LocalDevVPN is needed for this desktop implementation.

## Features

- Desktop colors and font from Qt6ct, with a warm brown/peach fallback. Palette
  changes are picked up while the app stays open. Matching night and street maps.
- Explicit place search, map pins, coordinates, favorites, rename/delete and
  twenty recent applied locations. Selecting a place only previews it.
- Eight-direction joystick and optional WASD/arrow-key controls. Releasing the
  control or leaving the window holds position. Keyboard movement stays off in
  text fields and coordinate editors.
- Walk/run/cycle/drive presets, custom speeds (0.36–360 km/h) and optional ±10%
  variation. Movement and refresh continue while the window is minimized.
- Up to twelve ordered/draggable route stops; pedestrian, bicycle or driving
  routes from Valhalla, with alternatives when available. Run uses pedestrian
  routing. The route planner's time estimate is distinct from the selected
  playback speed.
- Explicit drawn-path mode and GPX import/export for up to 50,000 points. They
  follow their exact geometry, including off-road sections. GPX import accepts
  one track segment or route at a time and rejects separated segments rather
  than drawing unexplained connecting lines.
- Route start, pause, resume, hold, looping, distance progress and a moving marker
  that shows the last acknowledged command separately from the preview pin.
- USB pairing/preparation and rootless USB or trusted Wi-Fi iPhone transport.
- One-second refresh, optional reconnection to the same phone, background drop
  notifications where supported, persistent recovery state and a quit prompt.
- First-run setup guide and an application-menu installer.
- `--demo` exercises the same desktop interface with fake phone hardware.

This is a development build. **USB and Wi-Fi have not yet been validated on a
physical iPhone.** Automated device tests use controlled doubles; passing them
does not prove a phone/OS combination works. No game compatibility is claimed.

## Install and run

Requires Linux, Python 3.12+ and a graphical desktop. Development verification
uses Arch Linux, Python 3.14 and Wayland/Niri. On Arch, install USB support if it
is missing:

```sh
sudo pacman -S --needed usbmuxd libimobiledevice
```

From this directory, run the per-user installer:

```sh
./install.sh
```

It creates `.venv`, installs the package and adds **Locus Linux** to the application
menu. It does not install system packages, change desktop configuration or start
phone simulation. Keep this checkout and its virtual environment at the same
path, or rerun the installer after moving them.

Manual installation and demo:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/locus-linux --demo
# Real hardware:
.venv/bin/locus-linux
# Optional menu entry:
.venv/bin/locus-linux --install-launcher
```

## iPhone setup

Use **iOS 17.4+**, a USB data cable, an unlocked iPhone and Developer Mode. Scan,
select the phone and choose **Prepare iPhone**. Accept the trust prompt. Enable
Developer Mode under Settings → Privacy & Security and restart if prompted.
First preparation requires internet to fetch Apple's developer disk image.

Search, select a saved place, click the map or enter coordinates. These preview a
pin; **Set location** sends it to the selected phone. Check the result in Maps.
Use Move for joystick/speed controls. On Route, add or drag stops and plan roads,
choose **Use drawn path**, or import GPX. Then explicitly choose **Start route**.

For Wi-Fi, first restore any active session, select USB and choose **Enable Wi-Fi
over USB**. It saves the trusted pairing and enables wireless connections. Unplug
the cable, select **Same Wi-Fi** and scan with the phone unlocked on the same LAN.
Prepare/select the phone as needed. Wi-Fi uses authenticated Bonjour discovery,
an iPhone heartbeat and a userspace developer tunnel. It never substitutes an
unrelated device when the selected phone is absent.

## Recovery and movement

**Hold / stop movement** stops the joystick or route and holds the current
simulated point. **Restore real location** sends the clear command and tears down
the connection. Clear acknowledgements are not independent GPS measurements;
check Maps. A lost connection always leaves location uncertain.

Automatic reconnection, when enabled, retries only the same phone at the last
requested location, at most once every five seconds after failure. A route stays
paused after reconnection. Sleep or a scheduling delay pauses movement rather
than jumping forward. Routes are not automatically resumed after app restart.
Looping intentionally restarts at the first point after reaching the last point;
use a closed route if you want a continuous circuit.

If Restore fails, the recovery file remains. Reconnect the recorded phone using
the recorded transport and try Restore again. Startup remembers that transport
but does not send location commands. An iPhone restart may be needed if developer
simulation is stuck. Force quit, laptop sleep and phone deep sleep cannot
guarantee immediate restoration. Normal quit offers Restore or an explicit quit
with unresolved state.

## Privacy and services

The map is local bundled Leaflet inside Qt WebEngine. There is no HTTP control
server. Map views request OpenStreetMap tiles; submitted searches go to Photon;
route planning sends selected stops to Valhalla. The services see your IP address
and the submitted text/area/stops, not device IDs. Playback itself sends no
routing requests. No autocomplete, bulk tile downloading or offline caching is
implemented. The night map styles the same tiles locally.

Photon and Valhalla public demos offer no availability guarantee. Requests are
explicit, cached and serialized at least 1.1 seconds apart. Configure your own
HTTPS endpoints in Settings for larger workloads. Coordinates, GPX and drawn
paths remain usable when online services are unavailable.

- [Photon API and demo policy](https://github.com/komoot/photon)
- [Valhalla route API](https://valhalla.github.io/valhalla/api/route/api-reference/)
- [Valhalla public demo](https://valhalla.openstreetmap.de/)

State stays under `$XDG_STATE_HOME/locus-linux`, default `~/.local/state/locus-linux`.
`places.json` stores favorites/recents/settings; `session.json` stores the selected
phone ID, last requested target and transport for recovery. Files are mode 0600.
Demo mode uses separate files. These personal files are never included in Git.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/pip check
.venv/bin/python tests/smoke_ui.py  # requires a graphical session
.venv/bin/pip wheel --no-deps -w /tmp/locus-wheels .
```

Tests cover movement geometry, date-line/polar behavior, speed/loop/pause/restore,
sleep and reconnection, recovery ownership, cancellation, GPX parsing/export,
place persistence, service adapters, Wi-Fi identity/heartbeat/cleanup, desktop
theme parsing and launcher creation. The native smoke test uses fake phone and
network responses, exercises the real Qt controls and map bridge, and closes its
window. It writes preview screenshots to `/tmp/locus-linux-*.png`.

Live Photon search and Valhalla walking/cycling/driving requests have also been
checked during development. Physical iPhone set/refresh/restore, unplug recovery
and Wi-Fi hardware validation remain pending.

## Implementation and licenses

The original Swift app remains under the root MIT license. The Linux code is
GPL-3.0-or-later (COPYING), consistent with its GPL pymobiledevice3 dependency.
Vendored Leaflet 1.9.4 retains its BSD license and recorded package integrity in
`locus_linux/web/`. No Ghost source was copied.

Phone operations run on a separate asyncio thread. USB uses pymobiledevice3's
UserspaceRsdTunnel. Wi-Fi composes its public CoreDeviceTunnelProxy,
UserspaceDialPlane and RSD primitives with a heartbeat and cleanup ownership,
without replacing upstream's USB-selection functions. One session and transport
are allowed at a time; changing transport requires resolving the current session.
