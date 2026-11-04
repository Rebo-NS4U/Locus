# SPDX-License-Identifier: GPL-3.0-or-later
"""Geodesic movement, distance-based route playback and bounded GPX interchange."""
import bisect
import math
import xml.etree.ElementTree as ET
from .engine import coordinates

EARTH = 6371008.8
SPEEDS = {"Walk": 1.4, "Run": 3.3, "Cycle": 6.5, "Drive": 13.4}
MAX_POINTS = 50000


def distance(a, b):
    lat1, lat2 = map(math.radians, (a[0], b[0]))
    dlat = lat2 - lat1
    dlon = math.radians(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH * math.asin(min(1, math.sqrt(max(0, h))))


def offset(point, east, north):
    angular = math.hypot(east, north) / EARTH
    if angular == 0:
        return tuple(point)
    bearing = math.atan2(east, north)
    lat, lon = map(math.radians, point)
    newlat = math.asin(math.sin(lat) * math.cos(angular) + math.cos(lat) * math.sin(angular) * math.cos(bearing))
    newlon = lon + math.atan2(math.sin(bearing) * math.sin(angular) * math.cos(lat), math.cos(angular) - math.sin(lat) * math.sin(newlat))
    return math.degrees(newlat), (math.degrees(newlon) + 180) % 360 - 180


def interpolate(a, b, fraction):
    # Great-circle interpolation avoids crossing the entire globe at the date line.
    angular = distance(a, b) / EARTH
    if angular < 1e-12:
        return tuple(a)
    if abs(math.pi - angular) < 1e-8:
        raise ValueError("Antipodal route points need an intermediate waypoint.")
    left, right = math.sin((1 - fraction) * angular) / math.sin(angular), math.sin(fraction * angular) / math.sin(angular)
    vectors = [(math.cos(math.radians(p[0])) * math.cos(math.radians(p[1])),
                math.cos(math.radians(p[0])) * math.sin(math.radians(p[1])), math.sin(math.radians(p[0]))) for p in (a, b)]
    x, y, z = [left * vectors[0][i] + right * vectors[1][i] for i in range(3)]
    return math.degrees(math.atan2(z, math.hypot(x, y))), math.degrees(math.atan2(y, x))


class Route:
    def __init__(self, points, speeds=None, sources=None, driving=None, motorized=None, stops=None, planned_stops=None, legs=None):
        if not 2 <= len(points) <= MAX_POINTS:
            raise ValueError(f"A route needs 2–{MAX_POINTS} points.")
        self.points = [coordinates(*p) for p in points]
        self.lengths = [0.0]
        for a, b in zip(self.points, self.points[1:]):
            if abs(math.pi - distance(a, b) / EARTH) < 1e-8:
                raise ValueError("Add a waypoint between antipodal points.")
            self.lengths.append(self.lengths[-1] + distance(a, b))
        self.total = self.lengths[-1]
        if self.total < 0.01:
            raise ValueError("Route points must have different locations.")
        self.speeds = list(speeds) if speeds is not None else None
        self.sources = list(sources) if sources is not None else None
        self.driving = list(driving) if driving is not None else None
        self.motorized = list(motorized) if motorized is not None else list(self.driving or [False] * (len(self.points) - 1))
        for values in (self.speeds, self.sources, self.driving, self.motorized):
            if values is not None and len(values) != len(self.points) - 1:
                raise ValueError("Speed metadata must match every route edge")
        if self.speeds is not None and any(not isinstance(s, (int, float)) or not math.isfinite(s) or not .1 <= s <= 100 for s in self.speeds):
            raise ValueError("Route speeds must be 0.1–100 m/s")
        self.motor_starts = []
        first_motor = 0
        for i, vehicle in enumerate(self.motorized):
            if not vehicle:
                first_motor = i + 1
            self.motor_starts.append(first_motor)
        self.legs = []
        for i, leg in enumerate(legs or []):
            first, last = leg.get('first'), leg.get('last')
            if type(first) is not int or type(last) is not int or not 0 <= first < last < len(self.points):
                raise ValueError('Invalid route leg boundaries')
            self.legs.append({**leg, 'index': i, 'start': self.lengths[first], 'end': self.lengths[last]})
        self.leg_ends = [leg['end'] for leg in self.legs]
        self.curve_caps = list(self.speeds or [])
        if self.speeds:
            for i in range(1, len(self.points) - 1):
                a, b, c = self.points[i - 1:i + 2]
                ab, bc, ac = self.lengths[i] - self.lengths[i - 1], self.lengths[i + 1] - self.lengths[i], distance(a, c)
                if ab > .1 and bc > .1:
                    cosine = max(-1, min(1, (ab * ab + bc * bc - ac * ac) / (2 * ab * bc)))
                    turn = 180 - math.degrees(math.acos(cosine))
                    cap = 8 if turn > 120 else 15 if turn > 60 else 25 if turn > 30 else float('inf')
                    self.curve_caps[i] = min(self.speeds[i], cap / 3.6)
        # Range minima make the 150 m braking lookahead logarithmic even on
        # dense geometry. cap² + 6*distance preserves the same braking formula.
        self.curve_tree_size = 1 << max(0, len(self.curve_caps) - 1).bit_length()
        self.curve_tree = [float('inf')] * (2 * self.curve_tree_size)
        for i, cap in enumerate(self.curve_caps):
            self.curve_tree[self.curve_tree_size + i] = cap * cap + 6 * self.lengths[i]
        for i in range(self.curve_tree_size - 1, 0, -1):
            self.curve_tree[i] = min(self.curve_tree[2 * i], self.curve_tree[2 * i + 1])
        self.stops = []
        for entry in stops or []:
            index, kind = entry.get('index'), entry.get('kind')
            if type(index) is not int or not 0 <= index < len(self.points) or kind not in ('stop_sign', 'traffic_light', 'crosswalk'):
                raise ValueError('Invalid mapped traffic control')
            if self.lengths[index] < self.total - .1 and (self.motorized[min(index, len(self.motorized) - 1)] or self.motorized[max(0, index - 1)]):
                edge = min(index, len(self.motorized) - 1)
                if not self.motorized[edge]:
                    edge = max(0, index - 1)
                leg = self.leg_at(self.lengths[index], arriving=True)
                minimum = max(self.lengths[self.motor_starts[edge]], leg['start'] if leg else 0)
                self.stops.append({'distance': self.lengths[index], 'kind': kind, 'min_distance': minimum})

        from .departures import departure
        self.planned_stops = []
        for entry in planned_stops or []:
            index = entry.get('index')
            if type(index) is not int or not 0 <= index < len(self.points):
                raise ValueError('Invalid planned stop location')
            rule = departure(entry)
            if rule['mode'] != 'none':
                self.planned_stops.append({'distance': self.lengths[index], 'stop': entry.get('stop', index + 1), **rule})

    def leg_at(self, meters, arriving=False):
        if not self.legs:
            return None
        bisect_fn = bisect.bisect_left if arriving else bisect.bisect_right
        index = min(len(self.legs) - 1, bisect_fn(self.leg_ends, meters))
        return dict(self.legs[index])

    def edge_at(self, meters):
        return min(len(self.points) - 2, max(0, bisect.bisect_right(self.lengths, meters) - 1))

    def speed_at(self, meters):
        return self.speeds[self.edge_at(meters)] if self.speeds is not None else None

    def desired_speed(self, meters):
        index = self.edge_at(meters)
        speed = self.speeds[index]
        if not self.motorized[index]:
            return speed
        first = self.curve_tree_size + index + 1
        last = self.curve_tree_size + min(len(self.curve_caps), bisect.bisect_right(self.lengths, meters + 150))
        minimum = float('inf')
        while first < last:
            if first & 1:
                minimum = min(minimum, self.curve_tree[first])
                first += 1
            if last & 1:
                last -= 1
                minimum = min(minimum, self.curve_tree[last])
            first //= 2
            last //= 2
        return min(speed, math.sqrt(max(0, minimum - 6 * meters)))

    def advance(self, meters, seconds, cap, factor=1):
        # Consume time separately on either side of a mode/speed boundary.
        while seconds > 1e-9 and meters < self.total:
            index = self.edge_at(meters)
            speed = min(cap, self.speeds[index] * factor)
            remaining = self.lengths[index + 1] - meters
            if remaining <= 1e-9:
                meters = self.lengths[index + 1]
                continue
            elapsed = min(seconds, remaining / speed)
            meters = min(self.lengths[index + 1], meters + elapsed * speed)
            seconds -= elapsed
        return meters

    def at(self, meters):
        if meters <= 0:
            return self.points[0]
        if meters >= self.total:
            return self.points[-1]
        i = bisect.bisect_right(self.lengths, meters) - 1
        if meters == self.lengths[i]:
            return self.points[i]
        fraction = (meters - self.lengths[i]) / (self.lengths[i + 1] - self.lengths[i])
        return interpolate(self.points[i], self.points[i + 1], fraction)


def parse_gpx(data):
    if len(data) > 8_000_000:
        raise ValueError("GPX is too large (8 MB limit).")
    upper = data.upper() if isinstance(data, bytes) else data.upper().encode()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("GPX must not contain document type or entity declarations.")
    from defusedxml.ElementTree import fromstring
    root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    # Separate segments must not silently create a straight line across gaps.
    segments = [item for item in root.iter() if item.tag.split('}')[-1] in ("trkseg", "rte")]
    if len(segments) > 1:
        raise ValueError("Import one GPX track segment or route at a time.")
    source = segments[0] if segments else root
    points = [coordinates(node.attrib["lat"], node.attrib["lon"]) for node in source.iter()
              if node.tag.split('}')[-1] in ("trkpt", "rtept", "wpt")]
    return Route(points).points


def export_gpx(points, name="Locus route"):
    route = Route(points)
    root = ET.Element("gpx", version="1.1", creator="Locus Linux", xmlns="http://www.topografix.com/GPX/1/1")
    track = ET.SubElement(root, "trk")
    ET.SubElement(track, "name").text = name
    segment = ET.SubElement(track, "trkseg")
    for lat, lon in route.points:
        ET.SubElement(segment, "trkpt", lat=f"{lat:.8f}", lon=f"{lon:.8f}")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
