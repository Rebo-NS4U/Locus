# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect parking-building pins to mapped vehicle exits and public road junctions."""
import heapq
from .indoor import contains, walking_path
from .routes import distance

ROAD_TYPES = {'service', 'residential', 'unclassified', 'living_street', 'tertiary',
              'secondary', 'primary', 'trunk', 'motorway', 'road',
              'tertiary_link', 'secondary_link', 'primary_link', 'trunk_link', 'motorway_link'}


class GarageExitUnavailable(ValueError):
    """A garage was identified, but no usable vehicle access was mapped."""


def garage_access(elements, point, departing=True, target=None):
    nodes = {item['id']: item for item in elements if item.get('type') == 'node'}
    graph, road_nodes, garages = {}, set(), []
    for way in elements:
        if way.get('type') != 'way':
            continue
        tags, ids = way.get('tags', {}), way.get('nodes', [])
        if (tags.get('building') == 'parking' or tags.get('parking') == 'multi-storey') and tags.get('building') not in (None, 'no'):
            if len(ids) > 180 or any(node not in nodes for node in ids):
                continue
            ring = [(nodes[node]['lat'], nodes[node]['lon']) for node in ids]
            if len(ring) >= 4 and ring[0] == ring[-1] and contains(point, ring):
                garages.append((ids, ring))
        if tags.get('highway') not in ROAD_TYPES or tags.get('area') == 'yes':
            continue
        permission = tags.get('motorcar', tags.get('motor_vehicle', tags.get('vehicle', tags.get('access'))))
        if permission in ('no', 'agricultural', 'forestry'):
            continue
        if tags.get('highway') != 'service':
            road_nodes.update(ids)
        forward = tags.get('oneway') not in ('-1',)
        backward = tags.get('oneway') not in ('yes', '1', 'true')
        if not departing:
            forward, backward = backward, forward
        for a, b in zip(ids, ids[1:]):
            if a not in nodes or b not in nodes:
                continue
            pa, pb = nodes[a], nodes[b]
            cost = distance((pa['lat'], pa['lon']), (pb['lat'], pb['lon']))
            if forward:
                graph.setdefault(a, []).append((b, cost))
            if backward:
                graph.setdefault(b, []).append((a, cost))
    best_path, best_cost = None, float('inf')
    for ids, ring in garages:
        if target is not None and contains(target, ring):
            path = walking_path(point, target, ring)
            if path:
                return path if departing else path[::-1]
        queue, best = [], {}
        # Shared nodes between the garage outline and a vehicle road are mapped
        # access points. A pedestrian entrance alone is never a vehicle exit.
        seeds = {}
        for node in ids[:-1]:
            if node not in graph:
                continue
            entrance = (nodes[node]['lat'], nodes[node]['lon'])
            path = walking_path(point, entrance, ring)
            if not path:
                continue
            cost = sum(distance(a, b) for a, b in zip(path, path[1:]))
            seeds[node] = path
            best[node] = (cost, None)
            heapq.heappush(queue, (cost, node))
        while queue:
            cost, node = heapq.heappop(queue)
            if cost > best[node][0] or cost >= best_cost:
                continue
            position = (nodes[node]['lat'], nodes[node]['lon'])
            reached_target = target is not None and distance(position, target) <= 5
            if (node in road_nodes or reached_target) and not contains(position, ring):
                chain, cursor = [], node
                while best[cursor][1] is not None:
                    chain.append((nodes[cursor]['lat'], nodes[cursor]['lon']))
                    cursor = best[cursor][1]
                best_path = [*seeds[cursor], *reversed(chain)]
                if reached_target and distance(best_path[-1], target) > .05:
                    best_path.append(tuple(target))
                best_cost = cost
                break
            for neighbor, step in graph.get(node, []):
                item = nodes[neighbor]
                permission = item.get('tags', {}).get('motor_vehicle', item.get('tags', {}).get('access'))
                if permission == 'no' or item.get('tags', {}).get('barrier') in ('bollard', 'block', 'cycle_barrier', 'wall', 'fence'):
                    continue
                total = cost + step
                if total < best.get(neighbor, (float('inf'), None))[0]:
                    best[neighbor] = total, node
                    heapq.heappush(queue, (total, neighbor))
    if best_path and len(best_path) >= 2:
        return best_path if departing else best_path[::-1]
    if garages:
        raise GarageExitUnavailable('No connected mapped vehicle exit was found for this garage.' if departing else 'No connected mapped vehicle entrance was found for this garage.')
    return None
