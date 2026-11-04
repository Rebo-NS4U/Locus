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


def inside_connection(start, end, ring):
    """Check every interval between boundary crossings, including concave outlines."""
    if len(ring) < 4 or ring[0] != ring[-1] or not all(contains(p, ring) for p in (start, end)):
        return False
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
    return all(contains((start[0] + dx * ((a + b) / 2), start[1] + dy * ((a + b) / 2)), ring)
               for a, b in zip(cuts, cuts[1:]))
