"""지역 사이의 간선도로를 가시 그래프·지형 비용으로 찾고 교차점을 공유한다."""

import heapq
import math

from shapely import LineString, Point, box, union_all
from shapely.prepared import prep

from .generation import _density, _sample
from .road_generation import ROAD_WIDTHS, ROAD_GAP
from .road_paths import smooth_path
from .site_planning import SIDES


def connect_regions(seed, regions, links, nodes, bounds):
    obstacles = [box(x, y, x + w, y + h) for x, y, w, h in (region['bounds'] for region in regions)]
    blocked = prep(union_all(obstacles).buffer(6 - 1e-6, join_style='mitre'))
    field = _density(seed, 'world-vegetation', math.ceil(max(bounds.w, bounds.h) / 2) * 2)
    cx, cy = bounds.x + bounds.w / 2, bounds.y + bounds.h / 2
    metadata = {region['generated_regions'][0]['id']: region['generated_regions'][0] for region in regions}
    routes = []
    for link in links:
        gates = [tuple(nodes[link['id'] + suffix]) for suffix in ('/a', '/b')]
        outside = []
        for key, entry_key, gate in zip(('source', 'destination'), ('source_entry', 'destination_entry'), gates):
            entry = metadata[link[key]]['entries'][link[entry_key]]
            axis, sign = SIDES[entry['side']]
            point = list(gate)
            point[axis] += sign * 12
            outside.append(tuple(point))
        existing = union_all([line for _, line in routes])
        reusable = prep(existing.buffer(1e-6))
        locked = {tuple(point) for _, line in routes for point in line.coords}
        points = outside + [point for polygon in obstacles for x, y, a, b in [polygon.buffer(10, join_style='mitre').bounds]
                            for point in ((x, y), (x, b), (a, y), (a, b))] + sorted(locked)
        points = list(dict.fromkeys(points))
        adjacency = [[] for _ in points]
        for a, start in enumerate(points):
            for b in range(a + 1, len(points)):
                end = points[b]
                line = LineString([start, end])
                if blocked.intersects(line):
                    continue
                if not existing.is_empty and line.distance(existing) < ROAD_WIDTHS['main'] + ROAD_GAP + 2 and not line.intersects(existing):
                    continue
                density = sum(_sample(field, start[0] * (1 - t) + end[0] * t - cx,
                                      start[1] * (1 - t) + end[1] * t - cy) for t in (.25, .5, .75)) / 3
                weight = line.length * (.6 if reusable.covers(line) else 1 + .6 * density)
                adjacency[a].append((b, weight))
                adjacency[b].append((a, weight))
        pending, costs, parents = [(0., 0)], {0: 0.}, {}
        while pending:
            cost, current = heapq.heappop(pending)
            if cost != costs[current]:
                continue
            if current == 1:
                break
            for other, distance in adjacency[current]:
                candidate = cost + distance
                if candidate < costs.get(other, math.inf):
                    costs[other], parents[other] = candidate, current
                    heapq.heappush(pending, (candidate, other))
        if 1 not in costs:
            raise ValueError('지역 사이 도로를 연결할 수 없음')
        path, current = [], 1
        while current != 0:
            path.append(points[current])
            current = parents[current]
        path = [gates[0], outside[0], *reversed(path), gates[1]]
        # 진입점 부근은 경계 바깥에서만 다듬고 이미 공유하는 길은 고정한다.
        path = smooth_path(path, lambda line: not blocked.intersects(line), locked)
        routes.append((link['id'], LineString(path)))
    coordinate_ids = {tuple(round(value, 6) for value in point): key for key, point in nodes.items()}

    def node(point, preferred=None):
        point = tuple(round(value, 6) for value in point)
        if point not in coordinate_ids:
            coordinate_ids[point] = preferred or f'world/road/{point[0]:g}/{point[1]:g}'
            nodes[coordinate_ids[point]] = list(point)
        return coordinate_ids[point]

    centered = []
    for link, (_, line) in zip(links, routes):
        middle = line.interpolate(.5, normalized=True)
        link['node'] = node((middle.x, middle.y), link['id'])
        coords = list(line.coords)
        position = next(index for index in range(len(coords) - 1)
                        if LineString(coords[index:index + 2]).distance(middle) < 1e-6)
        coords.insert(position + 1, (middle.x, middle.y))
        centered.append(LineString(coords))
    merged = union_all(centered)
    roads = {}
    for line in merged.geoms if merged.geom_type == 'MultiLineString' else (() if merged.is_empty else (merged,)):
        for a, b in zip(line.coords, list(line.coords)[1:]):
            if math.dist(a, b) < 1e-6:
                continue
            pair = tuple(sorted((node(a), node(b))))
            if pair in roads or pair[0] == pair[1]:
                continue
            owner = min(routes, key=lambda route: route[1].distance(Point((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)))[0]
            roads[pair] = {'id': f'world/segment/{len(roads)}', 'nodes': list(pair), 'start': nodes[pair[0]], 'end': nodes[pair[1]],
                           'route': owner, 'kind': 'main', 'width': ROAD_WIDTHS['main'], 'material': 'asphalt'}
    return list(roads.values())
