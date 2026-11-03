# Mixed routing and automatic driving speed

Open **Controls → Route**. Add two or more main pins using the preview pin or
**Add map clicks as route stops**. Each pair appears in **Segments between pins**.
Select a segment in that list or click its colored line on the map.

## Per-segment route types

| Profile | Geometry | Automatic target speed |
| --- | --- | --- |
| Walk · prefer sidewalks | Pedestrian routing favoring sidewalks and walkways | 5.04 km/h estimate |
| Walk · prefer trails | Pedestrian routing favoring paths/tracks, permitting hiking trails | 4.3 km/h estimate |
| Run · paths | Pedestrian routing with a running speed estimate | 11.88 km/h estimate |
| Cycle · paths | Bicycle routing favoring paths | 23.4 km/h estimate |
| Car · roads | Driving routes avoiding intermediate unpaved roads | Mapped road speeds/limits; fallback estimate 30 km/h |
| Car · prefer dirt tracks | Driving routes favoring tracks and avoiding highways | Road/surface metadata; capped at 25 km/h, rough surfaces at 15 km/h |
| Walk · free off-road path | Exact pin/bend geometry; no server request | 4.3 km/h estimate |
| Car · free off-road path | Exact pin/bend geometry; no server request | 15 km/h estimate |

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
per free segment, and fifty thousand combined playback points. A mapped pin must
be within forty meters of its routed endpoint. Short pin-to-path connectors are
shown with light dashed lines and played at walking speed. Larger gaps are
rejected; move the pin or explicitly choose a free off-road segment instead.

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
