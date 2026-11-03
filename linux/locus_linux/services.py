# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, rate-limited place search and multimodal road routing."""
import asyncio
import json
import time
import copy
from urllib.parse import urlsplit
from .engine import coordinates
from .routes import Route, MAX_POINTS, distance

PHOTON = "https://photon.komoot.io/api/"
VALHALLA = "https://valhalla1.openstreetmap.de/route"
OVERPASS = "https://overpass.private.coffee/api/interpreter"
OSM_READ = 'https://api.openstreetmap.org/api/0.6'


def endpoint(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Service endpoints must be HTTPS URLs without credentials, query or fragment.")
    return value


def decode_shape(shape):
    if not isinstance(shape, str) or len(shape) > 2_000_000:
        raise ValueError("Invalid route geometry")
    result, index, lat, lon = [], 0, 0, 0
    while index < len(shape):
        deltas = []
        for _ in range(2):
            value = shift = 0
            while True:
                if index >= len(shape) or shift > 35:
                    raise ValueError("Truncated route geometry")
                char = ord(shape[index]) - 63
                index += 1
                if not 0 <= char <= 63:
                    raise ValueError("Invalid route geometry")
                value |= (char & 31) << shift
                shift += 5
                if char < 32:
                    break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        lat += deltas[0]
        lon += deltas[1]
        result.append(coordinates(lat / 1e6, lon / 1e6))
        if len(result) > MAX_POINTS:
            raise ValueError("Route is too large")
    return result


def request_json(url, params):
    import requests
    options = {"headers": {"User-Agent": "LocusLinux/0.4 (https://github.com/Rebo-NS4U/Locus)", "Accept": "application/json"}, "timeout": (5, 15), "stream": True}
    if 'data' in params:
        options['timeout'] = (5, 35)  # Allow the public Overpass queue plus its bounded query time.
    response = requests.post(url, json=params["_json_body"], **options) if "_json_body" in params else requests.get(url, params=params, **options)
    with response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 4_000_000:
                raise ValueError("Service response is too large.")
            chunks.append(chunk)
        return json.loads(b"".join(chunks))


def encode_shape(points):
    output, previous = [], [0, 0]
    for point in points:
        for index, coordinate in enumerate(point):
            value = round(coordinate * 1e6)
            delta, previous[index] = value - previous[index], value
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 32:
                output.append(chr((32 | (delta & 31)) + 63))
                delta >>= 5
            output.append(chr(delta + 63))
    return "".join(output)


class Services:
    def __init__(self, requester=request_json):
        self.requester = requester
        self.cache = {}
        self.lock = asyncio.Lock()
        self.last_request = 0
        self.map_objects = {}

    async def get(self, url, params):
        endpoint(url)
        key = (url, json.dumps(params, sort_keys=True))
        async with self.lock:
            cached = self.cache.get(key)
            if cached and time.monotonic() - cached[0] < 300:
                return cached[1]
            await asyncio.sleep(max(0, 1.1 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            result = await asyncio.to_thread(self.requester, url, params)
            if len(self.cache) >= 100:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = time.monotonic(), result
            return result

    async def search(self, query, url=PHOTON):
        query = query.strip()
        if not 3 <= len(query) <= 200:
            raise ValueError("Search needs 3–200 characters.")
        response = await self.get(url, {"q": query, "limit": 6})
        results = []
        for feature in response.get("features", [])[:6]:
            lon, lat = feature["geometry"]["coordinates"][:2]
            properties = feature.get("properties", {})
            name = ", ".join(dict.fromkeys(str(properties[k]) for k in ("name", "city", "state", "country") if properties.get(k)))
            results.append({"name": name or query, "point": coordinates(lat, lon)})
        return results

    async def route(self, points, mode, url=VALHALLA, costing_options=None):
        if not 2 <= len(points) <= 12:
            raise ValueError("Road routing needs 2–12 stops.")
        costing = {"Walk": "pedestrian", "Run": "pedestrian", "Cycle": "bicycle", "Drive": "auto"}[mode]
        payload = {"locations": [{"lat": coordinates(*p)[0], "lon": coordinates(*p)[1]} for p in points],
                   "costing": costing, "units": "kilometers", "alternates": 2, "shape_format": "polyline6"}
        options = dict(costing_options or {})
        if costing == "pedestrian":
            options.update(use_ferry=0, exclude_ferries=True)
            for location in payload["locations"]:
                location["search_filter"] = {"exclude_ferry": True}
        if options:
            payload["costing_options"] = {costing: options}
        # The public demo expects compact JSON; form-encoded '+' whitespace
        # inside the json query is not decoded by all Valhalla frontends.
        response = await self.get(url, {"json": json.dumps(payload, separators=(",", ":"))})
        trips = [response.get("trip", {})] + [item.get("trip", {}) for item in response.get("alternates", [])]
        results = []
        rejected = []
        for trip in trips:
            if trip.get("status") != 0:
                continue
            legs = trip.get("legs", [])
            if costing == "pedestrian" and (
                trip.get("summary", {}).get("has_ferry")
                or any(leg.get("summary", {}).get("has_ferry")
                       or any(m.get("ferry") or m.get("type") in (28, 29)
                              or m.get("travel_type") == "ferry"
                              for m in leg.get("maneuvers", [])) for leg in legs)
            ):
                rejected.append("Walking needs a connected footpath or bridge; ferry crossings are not walkable.")
                continue
            shape = []
            for leg in legs:
                decoded = decode_shape(leg["shape"])
                if not decoded or (shape and distance(shape[-1], decoded[0]) > .5):
                    rejected.append("Mapped route sections do not connect. Move the bend onto a connected path and plan again.")
                    break
                shape.extend(decoded if not shape else decoded[1:])
            else:
                route = Route(shape)
                results.append({"points": route.points, "distance": route.total, "seconds": trip["summary"]["time"]})
                continue
            # Never draw a straight shortcut between disconnected leg shapes.
            continue
        if not results:
            raise ValueError(rejected[0] if rejected else response.get("error", "No connected road route found. Try closer stops."))
        return results

    async def mixed_route(self, legs, url=VALHALLA, overpass_url=OVERPASS, include_stops=True):
        from .planning import specification, PROFILES
        if not 1 <= len(legs) <= 11:
            raise ValueError("A mixed route needs 2–12 main pins")
        validated = []
        for leg in legs:
            spec = specification(leg["spec"])
            start, end = coordinates(*leg["start"]), coordinates(*leg["end"])
            Route([start, *spec["controls"], end])
            validated.append((start, end, spec))
        result = []
        for number, (start, end, spec) in enumerate(validated, 1):
            profile = PROFILES[spec["profile"]]
            waypoints = [start, *spec["controls"], end]
            try:
                if profile["costing"]:
                    mode = {"auto": "Drive", "pedestrian": "Walk", "bicycle": "Cycle"}[profile["costing"]]
                    choices = await self.route(waypoints, mode, url, profile["options"])
                else:
                    choices = [{"points": Route(waypoints).points}]
                options = []
                rejected = []
                for choice in choices:
                    option = copy.deepcopy(choice)
                    await self.speed_metadata(option, spec, url, overpass_url, include_stops)
                    try:
                        self.anchor(option, start, end, max_gap=2 if profile["costing"] == "pedestrian" else 40)
                    except ValueError as error:
                        rejected.append(str(error))
                        continue
                    option["distance"] = Route(option["points"]).total
                    option["seconds"] = sum(distance(a, b) / speed for a, b, speed in zip(option["points"], option["points"][1:], option["speeds"]))
                    options.append(option)
                if not options:
                    raise ValueError(rejected[0] if rejected else "No usable segment alternatives")
                result.append(options)
            except Exception as error:
                raise ValueError(f"Segment {number}: {error}") from error
        return result

    async def speed_metadata(self, option, spec, url, overpass_url=OVERPASS, include_stops=True):
        from .planning import PROFILES
        profile = PROFILES[spec["profile"]]
        auto = spec["speed_mode"] == "auto"
        driving = auto and spec["profile"].startswith(("car_", "offroad_car"))
        speed = profile["speed"] if auto else spec["speed_kph"]
        source = "manual" if not auto else ("off-road estimate" if not profile["costing"] else "profile estimate")
        count = len(option["points"]) - 1
        vehicle = spec['profile'].startswith(('car_', 'offroad_car'))
        option.update(speeds=[speed / 3.6] * count, sources=[source] * count, driving=[driving] * count, motorized=[vehicle] * count, stops=[])
        if not (profile["costing"] == "auto" and (auto or include_stops)):
            return
        payload = {"encoded_polyline": encode_shape(option["points"]), "shape_match": "edge_walk", "costing": "auto", "units": "kilometers",
                   "filters": {"action": "include", "attributes": ["shape", "edge.begin_shape_index", "edge.end_shape_index", "edge.speed", "edge.speed_limit", "edge.road_class", "edge.surface", "edge.use", "edge.way_id", "edge.forward", "edge.traffic_signal", "node.traffic_signal"]}}
        try:
            trace = await self.get(url.rsplit("/", 1)[0] + "/trace_attributes", {"_json_body": payload})
            points = decode_shape(trace["shape"])
            Route(points)
            if distance(points[0], option["points"][0]) > 2 or distance(points[-1], option["points"][-1]) > 2:
                raise ValueError("Speed metadata did not match the planned geometry")
            speeds = [speed / 3.6] * (len(points) - 1)
            sources = ["road estimate" if auto else 'manual'] * len(speeds)
            for edge in trace.get("edges", []):
                first, last = int(edge["begin_shape_index"]), int(edge["end_shape_index"])
                if not 0 <= first <= last < len(points):
                    raise ValueError("Invalid road metadata edge indexes")
                value = float(edge.get("speed", 0))
                limit = float(edge.get("speed_limit", 0))
                label = "mapped road speed"
                if not math_is_positive(value):
                    road_class = edge.get("road_class", "unclassified")
                    value = {"motorway": 100, "trunk": 80, "primary": 60, "secondary": 50, "tertiary": 40, "residential": 25, "service_other": 15}.get(road_class, 30)
                    label = f"{road_class} speed estimate"
                if 0 < limit < 250:
                    value = min(value, limit)
                    label += f" · limit {limit:g} km/h"
                surface = edge.get("surface", "")
                if surface in ("dirt", "gravel", "path", "impassable") or edge.get("use") == "track":
                    value = min(value, 15)
                    label += " · rough surface"
                if spec["profile"] == "car_tracks":
                    value = min(value, 25)
                for index in range(first, last):
                    speeds[index] = max(.1, min(100, value / 3.6)) if auto else speed / 3.6
                    sources[index] = label if auto else 'manual'
            if not trace.get("edges"):
                raise ValueError("Road speed metadata unavailable")
            option.update(points=points, speeds=speeds, sources=sources, driving=[auto] * len(speeds), motorized=[True] * len(speeds))
            if include_stops:
                await self.traffic_metadata(option, trace, overpass_url)
        except Exception:
            # Explicit estimate, never a claim that a posted speed was detected.
            if auto:
                option["speed_warning"] = f"Road speed data unavailable; using a {speed:g} km/h estimate."
            if include_stops:
                option['traffic_warning'] = 'Mapped traffic control data unavailable for this segment.'

    async def traffic_metadata(self, option, trace, url):
        from .traffic import attach_controls
        elements = []
        try:
            ways = sorted({int(edge['way_id']) for edge in trace.get('edges', []) if int(edge.get('way_id', 0)) > 0})
            if not ways or len(ways) > 600:
                raise ValueError('Way metadata unavailable or segment too large')
            if url == OVERPASS and len(ways) <= 20:
                # Bounded, cached reads of only the roads/nodes in this small
                # human-requested segment; larger requests use Overpass.
                elements = await self.small_road_objects(ways)
            else:
                for start in range(0, len(ways), 60):
                    ids = ','.join(map(str, ways[start:start + 60]))
                    query = f'[out:json][timeout:12];way(id:{ids})->.roads;(.roads;node(w.roads)[highway=stop];node(w.roads)[highway=traffic_signals];);out body;'
                    response = await self.get(url, {'data': query})
                    if response.get('remark') or not isinstance(response.get('elements'), list):
                        raise ValueError('Incomplete traffic control response')
                    elements.extend(response['elements'])
        except Exception:
            option['traffic_warning'] = 'Stop-sign lookup unavailable; only available route-provider traffic lights can be used.'
        try:
            attach_controls(option, trace, elements)
        except Exception:
            option['traffic_warning'] = 'Traffic controls could not be aligned with this segment.'

    async def small_road_objects(self, ways):
        async def objects(kind, ids):
            now = time.monotonic()
            values = {identifier: self.map_objects[kind, identifier][1] for identifier in ids if (kind, identifier) in self.map_objects and now - self.map_objects[kind, identifier][0] < 300}
            missing = [identifier for identifier in ids if identifier not in values]
            for offset in range(0, len(missing), 300):
                response = await self.get(f'{OSM_READ}/{kind}s.json', {kind + 's': ','.join(map(str, missing[offset:offset + 300]))})
                for item in response.get('elements', []):
                    if item.get('type') == kind:
                        if len(self.map_objects) >= 5000:
                            self.map_objects.pop(next(iter(self.map_objects)))
                        # Discard contributor/user metadata; only routing data is needed.
                        public = {key: item[key] for key in ('type', 'id', 'nodes', 'lat', 'lon', 'tags') if key in item}
                        self.map_objects[kind, int(item['id'])] = time.monotonic(), public
                        values[int(item['id'])] = public
            return [values[identifier] for identifier in ids]
        roads = await objects('way', ways)
        nodes = sorted({int(node) for road in roads for node in road.get('nodes', [])})
        if not nodes or len(nodes) > 1200:
            raise ValueError('Segment too large for bounded OSM read lookup')
        return roads + await objects('node', nodes)

    @staticmethod
    def anchor(option, start, end, max_gap=40):
        option.setdefault('motorized', list(option.get('driving', [False] * (len(option['points']) - 1))))
        option["connectors"] = []
        for point, anchor in ((option["points"][0], start), (option["points"][-1], end)):
            if distance(point, anchor) > max_gap:
                if max_gap < 40:
                    raise ValueError("Walking pins must be within 2 m of their mapped path. Move the pin onto the path; no cross-country connector will be added.")
                raise ValueError("A pin is more than 40 m from the mapped path. Move it, or choose a free off-road segment.")
        for first, anchor in ((True, start), (False, end)):
            index = 0 if first else -1
            if distance(option["points"][index], anchor) < .05:
                option["points"][index] = anchor
                continue
            if first:
                for stop in option.get('stops', []):
                    stop['index'] += 1
                option["connectors"].append([anchor, option["points"][0]])
                option["points"].insert(0, anchor)
                option["speeds"].insert(0, 1.4)
                option["sources"].insert(0, "short pin connector")
                option["driving"].insert(0, False)
                option['motorized'].insert(0, False)
            else:
                option["connectors"].append([option["points"][-1], anchor])
                option["points"].append(anchor)
                option["speeds"].append(1.4)
                option["sources"].append("short pin connector")
                option["driving"].append(False)
                option['motorized'].append(False)


def math_is_positive(value):
    import math
    return math.isfinite(value) and value > 0
