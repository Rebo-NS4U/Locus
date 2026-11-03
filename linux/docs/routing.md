# Mixed routing and automatic driving speed

Open **Controls → Route**. Add two or more main pins using the preview pin or
**Add map clicks as route stops**. Stops and segments share one **Route stops & segments** list, with each segment
between its two stops. Adding a stop selects the newest segment automatically;
inserting a stop selects the new segment leading into it. Select a segment in
the list or click its colored line on the map to edit its shape and speed.
Selecting a stop edits its incoming segment (the first stop uses the first segment)
and makes that stop the target for Insert, Remove and reorder controls.

## Per-segment route types

Choose **Travel mode** (Walk, Run, Cycle or Drive), then **Path type** separately.
Walk and Run offer Roads / sidewalks or Trails / paths; Cycle offers Roads or
Trails / paths. Drive offers Roads, Dirt tracks or Free off-road. Walk also offers
Free off-road. Path choices are routing preferences, subject to mapped access.
Auto/manual speed remains independent. Changing travel mode selects its first
path type; switching between segments restores both saved choices.
Existing projects retain their original routing profiles.

| Profile | Geometry | Automatic target speed |
| --- | --- | --- |
| Walk · prefer sidewalks | Pedestrian routing favoring sidewalks and walkways | 5.04 km/h estimate |
| Walk · prefer trails | Pedestrian routing favoring paths/tracks, permitting hiking trails | 4.3 km/h estimate |
| Run · prefer sidewalks | Pedestrian routing favoring sidewalks | 11.88 km/h estimate |
| Run · paths | Pedestrian routing with a running speed estimate | 11.88 km/h estimate |
| Cycle · prefer roads | Bicycle routing favoring roads | 23.4 km/h estimate |
| Cycle · paths | Bicycle routing favoring paths | 23.4 km/h estimate |
| Car · roads | Driving routes avoiding intermediate unpaved roads | Mapped road speeds/limits; fallback estimate 30 km/h |
| Car · prefer dirt tracks | Driving routes favoring tracks and avoiding highways | Road/surface metadata; capped at 25 km/h, rough surfaces at 15 km/h |
| Walk · free off-road path | Exact pin/bend geometry; no server request | 4.3 km/h estimate |
| Car · free off-road path | Exact pin/bend geometry; no server request | 15 km/h estimate |

Mapped walking/running requests exclude ferry edges and reject any returned
ferry route, including ferries at an endpoint. River crossings must use connected
mapped pedestrian paths (such as a foot-accessible bridge). Disconnected leg
shapes are rejected rather than joined by an invented straight segment. If no
connected route exists, planning fails instead of switching to a free path.

Mapped modes are preferences, not guarantees of exclusively one terrain type.
OpenStreetMap coverage and the routing engine determine what paths are available.
Sidewalk tags sometimes describe a road rather than a separately drawn sidewalk;
the displayed geometry can then follow that road's centerline. Free paths do not
snap to roads, trails or barriers. Manual speeds override the automatic target
for that segment.

Use **Plan segments** after edits. Alternatives are selected independently for
the currently selected segment. Unchanged requests use the local five-minute
service cache. An edit made while planning is in progress invalidates the old
result; it cannot silently replace the updated route.

## Manually reshape the path between pins

- Enable **Map clicks add bends to this segment**, then click intermediate points.
  Alternatively preview a point and use **Add bend**.
- Drag the small square bend handles. Right-click one to remove it. **Undo**
  removes the last bend; **Clear** in the segment editor removes that segment's bends.
- Mapped modes route through the bends. Free modes connect the bends directly
  with great-circle interpolation during playback.
- Drag a numbered main pin to move it. Its neighboring segment settings remain;
  only their geometry becomes stale. Other ready segments stay available.
- Select a main pin and choose **Insert pin** to place the current preview point
  after it. Splitting a segment inherits its profile and speed settings and
  partitions its bends around the new pin. Reordering pins preserves settings on
  unchanged adjacent pairs; new pairs receive defaults.

Limits: twelve main pins, ten intermediate bends per mapped segment, a thousand
per free segment, and fifty thousand combined playback points. Walking stops
connect directly to the mapped path without an endpoint distance limit, including
when only one stop is off-path. For walking,
when every returned path snaps both stops more than two meters from their requested
positions, Locus assumes a direct off-road connection between the stops, following
any added bends. The segment details identify this direct connection. Automatic
speed uses the off-road walking estimate; manual speed is preserved. This does
not check terrain or barriers. Running retains its two-meter endpoint limit. Routing failures still report an error;
use Walk → Free off-road for an explicit direct path without a mapped-route lookup.
Cycling and driving retain a forty-meter endpoint limit. Pin-to-path
connectors are shown with light dashed lines and played at walking speed.
This follows the router's mapped network; it does not independently survey water,
closures or missing/incorrect map data. Explicit free paths, GPX and joystick
movement keep their exact geometry and do not check water or barriers.

## Automatic driving speed

When an automatic car segment is planned, Locus requests Valhalla's mapped edge
speeds, posted limits, road classification and surface data. It caps the target by
an available posted limit and slows for rough surfaces. If road speed is absent
but classification exists, it uses a labeled road-class estimate. If metadata
lookup fails, the segment displays its fallback estimate instead of claiming a
detected limit.

During playback, auto-driving segments accelerate at up to 2 m/s² and brake
toward lower limits and sharp turns. The controller looks ahead up to 150 meters;
turn target caps are 25, 15 or 8 km/h according to turn sharpness. Each update
consumes time across speed boundaries, so a fast car segment cannot advance
through a following walking segment at the car's speed. Natural variation, when
enabled, only reduces an automatic driving target; it cannot increase that target
above the mapped cap.

The progress display shows simulated speed and its source. These are planned
movement estimates, not readings of the phone's GPS speed, current traffic,
traction or terrain conditions. The iPhone receives coordinates, and another
app may interpret speed differently. Missing posted limits remain estimates.
Speed metadata is fetched during planning, not continuously during playback.

Global speed under **Move** still controls joystick motion and imported GPX.
Planned mixed routes use their own per-segment speeds. Pause holds the current
point. Stop/hold before editing an active route, then plan and start the revised
route explicitly. Reconnection never automatically resumes a paused route.

## Automatic stop signs and traffic lights

**Controls → Move → Driving stops** is on by default for newly planned mapped car
segments, including manual-speed driving. Stop signs always receive a sampled
2–5 second wait, weighted toward about three seconds. Each traffic light is
independently sampled as red or green once per route lap; the default chance of
red is 45%, adjustable from 0–100%. A red light receives an 8–65 second wait,
weighted toward 25 seconds. These triangular distributions are simulation
heuristics, not measurements or predictions of local signal timing.

The controller slows on approach, clamps progress at the control, continues
sending the same coordinates throughout the wait, then accelerates again. The
sidebar and compact status badge show the reason and remaining time. The update
cadence is about one second, so observed waits are rounded up to the next update.
Pause, sleep and connection loss freeze the countdown. Resume continues the
remaining wait; changing speed settings does not randomly restart it. A new lap
samples new light decisions and durations. Restore cancels stop state entirely.

Walking, cycling and free off-road segments do not acquire driving controls.
The feature does not invent stops at ordinary intersections. Replan an existing
route to fetch control metadata. The moving-time estimates beside alternatives
do not include the additional random waits.

Light flags come from the routed Valhalla edges/nodes. For small segments, a
bounded OpenStreetMap read fetches at most twenty ways and 1,200 referenced nodes,
in batches of at most 300; larger segments use the configured Overpass endpoint.
Requests and individual map objects are cached for five minutes and rate limited.
No lookup occurs during playback. A custom Overpass endpoint is honored instead
of the small-segment read path. These services receive public road/node IDs, which
reveal the planned area, never the phone identifier.

Stop-sign nodes must belong to the actual traversed way and project within
twelve meters of it; forward/backward tags are respected when present. Explicit
directional light nodes take precedence over nearby generic intersection-center
flags on that road. Adjacent duplicate controls are grouped into one stop. Map
data can be incomplete or out of date; missing sign data is displayed as a warning,
while available routing-provider light flags can still be used. Overpass public
instances were timing out during development; small-segment reads were verified
live, with actual signs and signals found on a sample route.

These are simulated phases and approximate mapped stop locations, not live
traffic, real vehicle sensing, or a guarantee that every intersection is covered.
The phone still receives coordinates only; its apps may infer speed differently.

Detected driving controls appear as red STOP signs and traffic-light icons on
planned routes. Hover to see the control type and simulated behavior. The icons
show mapped controls, not live light phases. Replan a mapped car route with
Driving stops enabled to load controls; walking and free paths do not acquire them.

## Save, load and export

**Save route project** writes an editable `.locus.json` file containing pins,
profiles, bend points and automatic/manual speed settings. **Load project**
restores those definitions; plan again to regenerate current paths and speed data.
Alternative geometries are not frozen into the project. Projects are local files
and are sent nowhere unless their segments are subsequently planned online.

**Export GPX** writes the selected combined geometry. GPX does not retain Locus's
profiles or speed policies. Imported GPX plays at the global Move speed; use its
GPX export to preserve it rather than saving an endpoint-only route project.

Provider references:

- [Valhalla routing preferences](https://valhalla.github.io/valhalla/api/route/api-reference/)
- [Valhalla speed metadata](https://valhalla.github.io/valhalla/concepts/speeds/)
- [Valhalla trace attributes](https://valhalla.github.io/valhalla/api/map-matching/api-reference/)
- [OpenStreetMap stop-sign tagging](https://wiki.openstreetmap.org/wiki/Tag:highway%3Dstop)
- [OpenStreetMap bounded multi-fetch API](https://wiki.openstreetmap.org/wiki/API_v0.6#Multi_fetch:_GET_/api/0.6/%5Bnodes%7Cways%7Crelations%5D?#parameters)
