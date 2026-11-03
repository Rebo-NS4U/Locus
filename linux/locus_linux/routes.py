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
    def __init__(self, points):
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

    def at(self, meters):
        if meters <= 0:
            return self.points[0]
        if meters >= self.total:
            return self.points[-1]
        i = bisect.bisect_right(self.lengths, meters) - 1
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
