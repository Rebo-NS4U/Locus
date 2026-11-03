# SPDX-License-Identifier: GPL-3.0-or-later
"""Mapped control placement and sampled simulation waits, not live light phases."""
import math
import random
from .engine import coordinates
from .routes import distance, interpolate, EARTH, MAX_POINTS


def project(point, a, b):
    scale = math.cos(math.radians((point[0] + a[0] + b[0]) / 3))
    def xy(p):
        return (math.radians((p[1] - a[1] + 180) % 360 - 180) * EARTH * scale,
                math.radians(p[0] - a[0]) * EARTH)
    px, py = xy(point)
    bx, by = xy(b)
    length = bx * bx + by * by
    fraction = max(0, min(1, (px * bx + py * by) / length)) if length > 1e-10 else 0
    projected = interpolate(a, b, fraction)
    return fraction, projected, distance(point, projected)


def attach_controls(option, trace, elements):
    """Insert controls on their actual traversed way, respecting tagged direction."""
    points = option['points']
    edges = trace.get('edges', [])
    nodes = {int(e['id']): e for e in elements if e.get('type') == 'node' and e.get('tags', {}).get('highway') in ('stop', 'traffic_signals')}
    node_ways = {node: set() for node in nodes}
    for way in elements:
        if way.get('type') == 'way':
            for node in set(way.get('nodes', [])) & nodes.keys():
                node_ways[node].add(int(way['id']))
    placements = {}
    def add(edge, fraction, kind, point):
        placements.setdefault(edge, []).append((fraction, kind, point))
    for edge in edges:
        first, last = int(edge['begin_shape_index']), int(edge['end_shape_index'])
        if not 0 <= first <= last < len(points):
            continue
        provider_light = edge.get('end_node', {}).get('traffic_signal') or edge.get('traffic_signal')
        mapped_light_near_end = False
        way_id = int(edge.get('way_id', 0))
        for node_id, node in nodes.items():
            if way_id not in node_ways[node_id]:
                continue
            tags = node['tags']
            point = coordinates(node['lat'], node['lon'])
            # Prefer the explicit directional stop-line node over a provider's
            # generic intersection-center flag for the same traversed way.
            if tags['highway'] == 'traffic_signals' and distance(point, points[last]) <= 25:
                mapped_light_near_end = True
            direction = tags.get('direction', tags.get('traffic_signals:direction'))
            if direction in ('forward', 'backward') and tags.get('stop') != 'all':
                if 'forward' not in edge or bool(edge['forward']) != (direction == 'forward'):
                    continue
            matches = []
            for index in range(first, last):
                fraction, projected, offset = project(point, points[index], points[index + 1])
                matches.append((offset, index, fraction, projected))
            if matches:
                offset, index, fraction, projected = min(matches)
                if offset <= 12:
                    add(index, fraction, 'stop_sign' if tags['highway'] == 'stop' else 'traffic_light', projected)
        if provider_light and not mapped_light_near_end:
            add(max(0, last - 1), 1 if last else 0, 'traffic_light', points[last])
    output, controls = [points[0]], []
    names = ('speeds', 'sources', 'driving', 'motorized')
    metadata = {name: [] for name in names}
    for index, end in enumerate(points[1:]):
        for fraction, kind, point in sorted(placements.get(index, []), key=lambda item: item[0]):
            if distance(output[-1], point) > .01:
                output.append(point)
                for name in names:
                    metadata[name].append(option[name][index])
            controls.append({'index': len(output) - 1, 'kind': kind})
        if distance(output[-1], end) > .001:
            output.append(end)
            for name in names:
                metadata[name].append(option[name][index])
        if len(output) > MAX_POINTS:
            raise ValueError('Traffic geometry exceeds the route point limit')
    # Deduplicate a shared junction returned by neighboring road edges/providers.
    unique = {(entry['index'], entry['kind']): entry for entry in controls}
    option.update(points=output, stops=list(unique.values()), **metadata)


class TrafficSchedule:
    def __init__(self, controls, enabled=True, red_probability=.45, rng=None, after=-1):
        if not math.isfinite(red_probability) or not 0 <= red_probability <= 1:
            raise ValueError('Red-light probability must be between zero and one')
        self.events = []
        self.waiting = None
        self.remaining = 0.0
        rng = rng or random.Random()
        if not enabled:
            return
        grouped = []
        for control in sorted(controls, key=lambda value: value['distance']):
            if control['distance'] <= after:
                continue
            if grouped and control['distance'] - grouped[-1][0] <= 3:
                grouped[-1][1].add(control['kind'])
            else:
                grouped.append((control['distance'], {control['kind']}))
        for meters, kinds in grouped:
            if 'traffic_light' in kinds and rng.random() < red_probability:
                kind, seconds = 'traffic_light', rng.triangular(8, 65, 25)
            elif 'stop_sign' in kinds:
                kind, seconds = 'stop_sign', rng.triangular(2, 5, 3)
            else:
                continue
            self.events.append({'distance': meters, 'kind': kind, 'seconds': seconds})

    @property
    def upcoming(self):
        return self.events[0] if self.events else None

    def arrive(self):
        self.waiting = self.events.pop(0)
        self.remaining = self.waiting['seconds']

    def hold(self, elapsed):
        self.remaining = max(0, self.remaining - elapsed)
        if self.remaining == 0:
            self.waiting = None
