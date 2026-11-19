"""지형 비용 기반 도로망. 연결 그래프와 표시·통행 경로를 같은 선분에서 만든다."""

from collections import Counter
import math

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra, minimum_spanning_tree
from shapely import LineString, Point, box, union_all


ROAD_WIDTH = 8
VEHICLE_SIZE = (5.6, 2.4)


def _grid_graph(cost, free):
    ids = np.arange(cost.size).reshape(cost.shape)
    rows, columns, weights = [], [], []
    for dy, dx in ((0, 1), (1, 0), (1, 1), (1, -1)):
        a = (slice(0, -dy or None), slice(max(0, -dx), -max(0, dx) or None))
        b = (slice(dy, None), slice(max(0, dx), -max(0, -dx) or None))
        valid = free[a] & free[b]
        if dy and dx:
            valid &= free[a[0], b[1]] & free[b[0], a[1]]
        start, end = ids[a][valid], ids[b][valid]
        weight = (cost[a][valid] + cost[b][valid]) * math.hypot(dx, dy)
        rows.extend((start, end))
        columns.extend((end, start))
        weights.extend((weight, weight))
    return csr_matrix((np.concatenate(weights), (np.concatenate(rows), np.concatenate(columns))),
                      shape=(cost.size, cost.size))


def _path(previous, start, end):
    path = [end]
    while path[-1] != start:
        parent = int(previous[path[-1]])
        if parent < 0:
            raise ValueError("연결할 수 없는 도로 진입점")
        path.append(parent)
    return path[::-1]


def road_surface(network, radius=ROAD_WIDTH / 2):
    degree = Counter(key for edge in network["edges"] for key in edge)
    parts = [LineString([network["nodes"][a], network["nodes"][b]]).buffer(radius, cap_style="flat")
             for a, b in network["edges"]]
    parts.extend(box(x - radius, y - radius, x + radius, y + radius)
                 for key, count in degree.items() if count > 1 for x, y in [network["nodes"][key]])
    return union_all(parts)


def build_roads(bounds, sites, points, gates, stubs, cost, rng):
    side = cost.shape[0]
    axis = bounds.x + np.arange(side) * 2
    free = np.ones_like(cost, dtype=bool)
    free[:2] = free[-2:] = False
    free[:, :2] = free[:, -2:] = False
    for site in sites:
        x, y, width, height = site["rect"]
        margin = ROAD_WIDTH / 2 + 1
        free[np.ix_((axis >= y - margin) & (axis <= y + height + margin),
                    (axis >= x - margin) & (axis <= x + width + margin))] = False
    keys = sorted(points)
    indices = [int(round((points[key][1] - bounds.y) / 2)) * side
               + int(round((points[key][0] - bounds.x) / 2)) for key in keys]
    if not free.ravel()[indices].all():
        raise ValueError("시설 진입로가 예약 영역에 막힘")
    graph = _grid_graph(cost, free)
    distances, previous = dijkstra(graph, directed=False, indices=indices, return_predecessors=True)
    terminal_cost = distances[:, indices]
    if not np.isfinite(terminal_cost).all():
        raise ValueError("도로망을 연결할 수 없는 시설 배치")
    tree = minimum_spanning_tree(terminal_cost).tocoo()
    pairs = {tuple(sorted((int(a), int(b)))) for a, b in zip(tree.row, tree.col)}
    paths = [_path(previous[a], indices[a], indices[b]) for a, b in sorted(pairs)]
    candidates = [(a, b) for a in range(len(keys)) for b in range(a + 1, len(keys)) if (a, b) not in pairs]
    rng.shuffle(candidates)
    # 기존 길에 비용을 더해 실제 우회 경로가 생기도록 순환 연결을 찾는다.
    for a, b in candidates[:max(1, len(keys) // 4)]:
        used = np.zeros(cost.size, dtype=bool)
        used[np.concatenate(paths)] = True
        alternate = graph.copy()
        alternate.data *= np.where(used[alternate.indices] & np.repeat(used, np.diff(alternate.indptr)), 3, 1)
        _, parents = dijkstra(alternate, directed=False, indices=indices[a], return_predecessors=True)
        paths.append(_path(parents, indices[a], indices[b]))
    lines = [LineString([(bounds.x + (cell % side) * 2, bounds.y + (cell // side) * 2) for cell in path])
             for path in paths]
    merged = union_all(lines + [LineString(stub) for stub in stubs])
    nodes, edges = {}, set()

    def node(point):
        x, y = (round(float(value), 6) or 0.0 for value in point)
        key = f"road/{x:g}/{y:g}"
        nodes[key] = [x, y]
        return key

    for line in merged.geoms if merged.geom_type == "MultiLineString" else (merged,):
        coords = list(line.simplify(0).coords)
        for a, b in zip(coords, coords[1:]):
            edges.add(tuple(sorted((node(a), node(b)))))
    terminals = {key: node(point) for key, point in sorted(gates.items())}
    spawn_node = node(points["spawn"])
    return {"nodes": dict(sorted(nodes.items())), "edges": sorted(edges),
            "terminals": terminals, "spawn_node": spawn_node}


def validate_roads(network, sites, bounds, colliders):
    keys = sorted(network["nodes"])
    index = {key: number for number, key in enumerate(keys)}
    pairs = [(index[a], index[b]) for a, b in network["edges"]]
    graph = csr_matrix(([1] * len(pairs), tuple(zip(*pairs))), shape=(len(keys), len(keys)))
    if connected_components(graph, directed=False, return_labels=False) != 1:
        raise ValueError("끊어진 도로 연결")
    surface = road_surface(network)
    if not box(bounds.x, bounds.y, bounds.right, bounds.bottom).buffer(1e-6).covers(surface):
        raise ValueError("월드 경계를 벗어난 도로 폭")
    for site in sites:
        x, y, width, height = site["rect"]
        footprint = box(x, y, x + width, y + height)
        connector = site["connector"]
        gate = connector["position"]
        key = network["terminals"][site["id"]]
        neighbors = [b if a == key else a for a, b in network["edges"] if key in (a, b)]
        if (math.dist(gate, network["nodes"][key]) > 1e-6 or len(neighbors) != 1
                or footprint.boundary.distance(Point(gate)) > 1e-6 or connector["width_m"] < ROAD_WIDTH):
            raise ValueError(f"시설 도로 연결점 오류: {site['id']}")
        dx, dy = (network["nodes"][neighbors[0]][i] - gate[i] for i in (0, 1))
        nx, ny = connector["direction"]
        if abs(dx * ny - dy * nx) > 1e-6 or dx * nx + dy * ny <= 0:
            raise ValueError(f"시설 방향과 어긋난 진입로: {site['id']}")
        if surface.intersection(footprint).area > 1e-6:
            raise ValueError(f"시설 점유 영역을 통과하는 도로: {site['id']}")
    radius = math.hypot(*VEHICLE_SIZE) / 2 + 0.35
    swept = road_surface(network, radius)
    blockers = [box(x, y, x + width, y + height) for x, y, width, height in
                (collider["rect"] for collider in colliders)]
    if not surface.buffer(1e-6).covers(swept) or swept.intersects(union_all(blockers)):
        raise ValueError("차량 통행 공간을 확보하지 못한 도로")
