# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit, rate-limited place search and multimodal road routing."""
import asyncio
import json
import time
from urllib.parse import urlsplit
from .engine import coordinates
from .routes import Route, MAX_POINTS

PHOTON = "https://photon.komoot.io/api/"
VALHALLA = "https://valhalla1.openstreetmap.de/route"


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
    with requests.get(url, params=params, headers={"User-Agent": "LocusLinux/0.2 (https://github.com/Rebo-NS4U/Locus)"}, timeout=(5, 15), stream=True) as response:
        response.raise_for_status()
        chunks, size = [], 0
        for chunk in response.iter_content(65536):
            size += len(chunk)
            if size > 4_000_000:
                raise ValueError("Service response is too large.")
            chunks.append(chunk)
        return json.loads(b"".join(chunks))


class Services:
    def __init__(self, requester=request_json):
        self.requester = requester
        self.cache = {}
        self.lock = asyncio.Lock()
        self.last_request = 0

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

    async def route(self, points, mode, url=VALHALLA):
        if not 2 <= len(points) <= 12:
            raise ValueError("Road routing needs 2–12 stops.")
        costing = {"Walk": "pedestrian", "Run": "pedestrian", "Cycle": "bicycle", "Drive": "auto"}[mode]
        payload = {"locations": [{"lat": coordinates(*p)[0], "lon": coordinates(*p)[1]} for p in points],
                   "costing": costing, "units": "kilometers", "alternates": 2, "shape_format": "polyline6"}
        # The public demo expects compact JSON; form-encoded '+' whitespace
        # inside the json query is not decoded by all Valhalla frontends.
        response = await self.get(url, {"json": json.dumps(payload, separators=(",", ":"))})
        trips = [response.get("trip", {})] + [item.get("trip", {}) for item in response.get("alternates", [])]
        results = []
        for trip in trips:
            if trip.get("status") != 0:
                continue
            shape = []
            for leg in trip.get("legs", []):
                decoded = decode_shape(leg["shape"])
                shape.extend(decoded if not shape else decoded[1:])
            route = Route(shape)
            results.append({"points": route.points, "distance": route.total, "seconds": trip["summary"]["time"]})
        if not results:
            raise ValueError(response.get("error", "No connected road route found. Try closer stops."))
        return results
