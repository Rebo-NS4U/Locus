# SPDX-License-Identifier: GPL-3.0-or-later
"""Footprint checks for approximate same-building walking connections."""


def contains(point, ring):
    x, y = point
    inside = False
    for a, b in zip(ring, ring[1:]):
        cross = (x - a[0]) * (b[1] - a[1]) - (y - a[1]) * (b[0] - a[0])
        if abs(cross) < 1e-12 and min(a[0], b[0]) <= x <= max(a[0], b[0]) and min(a[1], b[1]) <= y <= max(a[1], b[1]):
            return True
        if (a[1] > y) != (b[1] > y):
            if x < a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]):
                inside = not inside
    return inside


def intervals(start, end, ring):
    """Partition a straight connection at every building boundary crossing."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    cuts = [0., 1.]
    for a, b in zip(ring, ring[1:]):
        ex, ey = b[0] - a[0], b[1] - a[1]
        determinant = dx * ey - dy * ex
        if abs(determinant) < 1e-18:
            continue
        ax, ay = a[0] - start[0], a[1] - start[1]
        t = (ax * ey - ay * ex) / determinant
        u = (ax * dy - ay * dx) / determinant
        if 0 <= t <= 1 and 0 <= u <= 1:
            cuts.append(t)
    cuts = sorted(set(cuts))
    return [(a, b, contains((start[0] + dx * ((a + b) / 2), start[1] + dy * ((a + b) / 2)), ring))
            for a, b in zip(cuts, cuts[1:]) if b - a > 1e-10]


def inside_connection(start, end, ring):
    """Check every interval, including concave outlines."""
    if len(ring) < 4 or ring[0] != ring[-1] or not all(contains(p, ring) for p in (start, end)):
        return False
    return all(inside for _, _, inside in intervals(start, end, ring))


def building_access(start, end, ring, max_access_metres=40):
    """Allow a short approach at one end of an otherwise indoor connection."""
    from .routes import distance
    if len(ring) < 4 or ring[0] != ring[-1]:
        return False
    # Outdoor-to-outdoor legs keep normal path routing.
    if not any(contains(p, ring) for p in (start, end)):
        return False
    pieces = intervals(start, end, ring)
    indoor = [i for i, (_, _, inside) in enumerate(pieces) if inside]
    if not indoor:
        return False
    first, last = min(indoor), max(indoor)
    if any(not inside for _, _, inside in pieces[first:last + 1]):
        return False  # Do not cross an exterior gap in a concave outline.
    length = distance(start, end)
    return pieces[first][0] * length <= max_access_metres and (1 - pieces[last][1]) * length <= max_access_metres


def walking_path(start, end, ring):
    """Shortest approximate connection around a building's exterior corners."""
    import heapq
    from .routes import distance
    if len(ring) < 4 or len(ring) > 180 or ring[0] != ring[-1]:
        return None
    if not any(contains(p, ring) for p in (start, end)):
        return None
    if inside_connection(start, end, ring) or building_access(start, end, ring):
        return [start, end]
    # Reject far outdoor endpoints before constructing an indoor graph.
    import math
    scale = math.cos(math.radians(start[0]))
    for point in (start, end):
        if contains(point, ring):
            continue
        gaps = []
        for a, b in zip(ring, ring[1:]):
            dx, dy = b[0] - a[0], (b[1] - a[1]) * scale
            length = dx * dx + dy * dy
            t = max(0, min(1, ((point[0] - a[0]) * dx + (point[1] - a[1]) * scale * dy) / length)) if length else 0
            gaps.append(distance(point, (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))))
        if min(gaps) > 40:
            return None
    points = [start, end, *dict.fromkeys(ring[:-1])]
    visibility = {}
    queue, best = [(0., 0)], {0: (0., None)}
    while queue:
        cost, index = heapq.heappop(queue)
        if cost > best[index][0]:
            continue
        if index == 1:
            path = []
            while index is not None:
                path.append(points[index])
                index = best[index][1]
            return path[::-1]
        for target, point in enumerate(points):
            if target == index:
                continue
            a = points[index]
            # Only an actual endpoint may have an outdoor approach. Interior
            # graph edges stay in the footprint, including concave buildings.
            access = index in (0, 1) or target in (0, 1)
            key = tuple(sorted((index, target)))
            if key not in visibility:
                visibility[key] = inside_connection(a, point, ring) or (access and building_access(a, point, ring))
            if not visibility[key]:
                continue
            candidate = cost + distance(a, point)
            if candidate < best.get(target, (float('inf'), None))[0]:
                best[target] = candidate, index
                heapq.heappush(queue, (candidate, target))
    return None
