# Locus for Linux 0.4

A native PySide6/Qt desktop companion to the original Locus iPhone application.
It controls a connected iPhone; it does not change the Linux computer's location.
No sideloaded iPhone app or LocalDevVPN is needed for this desktop implementation.

## Features

- Glass desktop redesign with a translucent rounded shell, inset action bar,
  soft sidebar cards, pill controls, slim scrollbars and frosted map overlays.
  Native window transparency uses the compositor’s background blur when enabled
  (including Niri); map tiles and text stay crisp. Desktop colors and font come from
  Qt6ct, with a warm brown/peach fallback. Palette
  changes are picked up while the app stays open. Controls and map overlays share
  one font family, size and weight. Matching night and street maps.
  UI code changes take effect on the next launch; an active phone session is never
  restarted by installation. Without compositor blur, the tinted shell remains translucent.
- Map-first layout: one compact action bar, a slim status line and a collapsible
  Controls sidebar (`Ctrl+B`), hidden by default. Coordinates, map display and the
  legend live in Controls → Map; scanning, preparation and setup are in the `⋯`
  device menu. Tabs stay pinned while each page scrolls independently, and reopening
  Controls preserves its width. Status updates keep the action bar and footer stable.
  Set location, Hold and Restore remain accessible with Controls hidden.
- Explicit place search, map pins, coordinates, favorites, rename/delete and
  twenty recent applied locations. Selecting a place centers the map without applying it.
- Eight-direction joystick and optional WASD/arrow-key controls. Releasing the
  control or leaving the window holds position. Keyboard movement stays off in
  text fields and coordinate editors.
- Speeds display and accept mph; route distances display miles. Existing settings
  and route files retain metric values internally for compatibility.
- Walk/run/cycle/drive presets, custom speeds (0.23–223.69 mph) and optional ±10%
  variation. Movement and refresh continue while the window is minimized.
- Route planning keeps Plan, route files and playback pinned below the scrolling
  editor. Numbered stop/segment/shape sections group editing controls; the Route ⋯
  menu holds GPX, projects and clear actions.
- Choose Walking, Driving or Cycling between each pair of up to 99 ordered,
  draggable pins. Walking uses mapped sidewalks, trails and walking connections without favoring
  long sidewalk detours. The shortest returned walking alternative is selected. A direct connection
  of up to 300 metres (984 ft) is used only when no mapped walking route is found.
  Each segment keeps its own travel mode, speed policy and alternatives. Insert a pin to split a
  segment and inherit its settings. Existing route projects remain compatible.
- Mapped walking excludes ferries and requires connected path geometry. Off-path
  pins connect to that geometry without replacing the entire walking leg with a
  straight shortcut. Map coverage determines the available mapped paths. GPX supports exact geometry.
- Walking stops inside the same mapped building can connect indoors automatically
  without snapping out to sidewalks. Entry/exit connections also work when one
  stop is indoors and the other is within 40 metres (131 ft) of the footprint along
  the connection. Indoor connections cover up to 300 metres (984 ft). This approximates indoor movement;
  room walls, corridors and floor changes are not modeled. Missing footprints or
  connections crossing outside a concave outline keep normal outdoor routing.
- Automatic driving speed from mapped road speeds/limits and surface information,
  with acceleration and braking toward turns/lower limits. Missing data is labeled
  as an estimate. Manual speed overrides are available per segment. This estimates
  simulated movement; it does not sense the phone's actual vehicle speed or live traffic.
- Visible red STOP signs and traffic-light icons mark detected driving controls
  on planned routes. Hover for the control type and simulated behavior.
- Auto stops at mapped stop signs and lights, enabled by default. Signs get varied
  2–5 second pauses; each light has a configurable chance of simulated red (45%
  default), with 8–65 second waits. Driving brakes before the control and restarts
  smoothly while fixed-location refresh continues through the wait. Signal phases
  and durations are simulated, not live traffic data.
- A built-in route library with named saves, search, open, rename and delete.
  Save updates the current route; **Save a copy to library** keeps a separate copy.
  Walking/driving/cycling settings and imported GPX tracks are retained. The current
  working draft is saved automatically and restored on launch, without starting playback.
- A refreshed map-first route editor with a visible route name and Save action,
  a separate library view, quieter selection colors and always-reachable playback controls.
- Colored, clickable segments with automatic path selection. Editable
  `.locus.json` projects retain stops, travel modes and speeds. Loading an older
  project discards manual shape hints so routing is automatic.
- GPX import/export for up to 50,000 points follows exact geometry, including
  off-road sections. GPX import accepts
  one track segment or route at a time and rejects separated segments rather
  than drawing unexplained connecting lines.
- Route start, pause, resume, hold, looping, distance progress and a moving marker
  that shows the last acknowledged command alongside numbered route pins.
- USB pairing/preparation and rootless USB or trusted Wi-Fi iPhone transport.
- One-second refresh, optional reconnection to the same phone, background drop
  notifications where supported, persistent recovery state and a quit prompt.
- First-run setup guide and an application-menu installer. Launching again shows
  the existing window without opening a second phone session.
- `--demo` exercises the same desktop interface with fake phone hardware.

This is a development build. USB discovery, preparation, fixed-location refresh,
joystick, route pause/resume/loop and the clear/cleanup sequence have passed
command-level tests on a physical iPhone running iOS 26.6.2. **The location shown
inside phone apps has not been independently measured. Wi-Fi location testing
remains pending because the phone did not advertise its wireless service.** See
[the hardware test record](docs/hardware-validation.md) for scope and limits.
Automated device tests use controlled doubles. No game compatibility is claimed.

## Install and run

Requires Linux, Python 3.12+ and a graphical desktop. Development verification
uses Arch Linux, Python 3.14 and Wayland/Niri. On Arch, install USB support if it
is missing:

```sh
sudo pacman -S --needed usbmuxd libimobiledevice
```

If the phone was already connected when usbmuxd was installed, reconnect it so
the new udev permissions take effect. Verify the service is running before
scanning. Installing the package alone does not always start it for a device
that was plugged in earlier.

From this directory, run the per-user installer:

```sh
./install.sh
```

It creates `.venv`, installs the package and adds **Locus Linux** to the application
menu. The desktop entry clears inherited `LD_PRELOAD` libraries so shell helpers
linked to system Qt cannot conflict with PySide’s bundled Qt. It does not install
system packages, change desktop configuration or start phone simulation. Keep this checkout and its virtual environment at the same
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

Single-click the map to add a numbered route pin; no drawing toggle is needed,
even with Controls hidden. Paths between stops are chosen automatically.
Search, saved places and coordinate fields select coordinates without adding route
pins. Map clicks do not send phone commands. **Set location** sends it to the selected phone. Check the result in Maps.
Use Move for joystick/imported-GPX speed controls. On Route, add or drag pins,
choose **Walking**, **Driving** or **Cycling** for each segment, then **Plan route**.
Name the route and press **Save route** (Ctrl+S) to keep it inside Locus.
**Saved routes** opens your searchable library; double-click a route or press
**Open** to restore it. Rename and delete are available there. Opening editable
routes restores their stops and settings; Plan route rebuilds paths with current
map data. GPX tracks reopen with their exact geometry. The last working draft is
restored automatically on startup. File import/export remains in **Route ⋯**.
The library is stored privately in `$XDG_STATE_HOME/locus-linux/routes.json`
(default `~/.local/state/locus-linux/routes.json`); demo mode uses a separate library.
Walking considers mapped sidewalks, trails, driveways and alleys with pedestrian
access, including on short legs, and selects the shortest returned alternative. When the
router finds no walking connection, legs up to 300 metres (984 ft) can connect
directly; the segment labels this fallback. Network errors, ferries and disconnected
route geometry do not trigger shortcuts. Driving and Cycling use their mapped networks.
You can import GPX for exact geometry. Older projects retain their saved profiles. Explicitly choose **Start route** to apply movement to the phone.

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
route planning sends selected stops to Valhalla and looks up nearby building
footprints through bounded OpenStreetMap read requests (or a custom Overpass endpoint), and driving speed lookup
sends planned route geometry to its trace-attributes endpoint. Driving-stop lookup
sends road/node IDs to the OpenStreetMap read API for small segments, or to the
configured Overpass server for larger/custom lookups. The services see
your IP address and submitted text/area/paths, not device IDs. Playback itself
sends no routing requests. Viewed tiles use a browser HTTP disk cache (up to 256 MiB) under
`$XDG_CACHE_HOME/locus-linux/map` (default `~/.cache/locus-linux/map`),
respecting server expiry and revalidation headers. Revisiting cached areas can
avoid downloading them again, including after relaunch. Demo mode uses a separate
cache. Delete that directory while Locus is closed to clear it. No autocomplete,
bulk tile downloading, prefetch or guaranteed offline maps are implemented. The night map styles the same tiles locally.

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
.venv/bin/python tests/smoke_library.py  # GUI route-library recovery and GPX saves
.venv/bin/python tests/smoke_map_cache.py  # local-server, cross-launch cache check
.venv/bin/pip wheel --no-deps -w /tmp/locus-wheels .
```

Tests cover movement geometry, date-line/polar behavior, speed/loop/pause/restore,
sleep and reconnection, recovery ownership, cancellation, GPX parsing/export,
mixed profiles, project round trips, stale-result protection, pin insertion,
mapped speed limits/surface caps, turn braking, acceleration and speed boundaries,
place persistence, service adapters, Wi-Fi identity/heartbeat/cleanup, desktop
theme parsing and launcher creation. The native smoke test uses fake phone and
network responses, exercises the real Qt controls and map bridge, and closes its
window. It writes preview screenshots to `/tmp/locus-linux-*.png`.

Live Photon search and Valhalla walking/cycling/driving requests have also been
checked during development. On iOS 26.6.2, physical USB detection, preparation,
rootless DVT connection, repeated set acknowledgements, joystick/route movement
commands, pause/resume/loop and clear/cleanup passed. Independent phone-app GPS
readback, physical unplug recovery and Wi-Fi set/refresh/clear remain pending.

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
