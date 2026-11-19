"""지형 비용 기반 도로망. 연결 그래프와 표시·통행 경로를 같은 선분에서 만든다."""

import math

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra, minimum_spanning_tree
from shapely import LineString, Point, box, union_all


ROAD_WIDTHS = {"main": 6.0, "access": 4.5, "service": 3.5}
ROAD_GAP = 6
VEHICLE_SIZE = (5.6, 2.4)
VEHICLE_MARGIN = 0.35
TURN_SPACE = math.hypot(*VEHICLE_SIZE) / 2 + VEHICLE_MARGIN


def facility_road(kind):
    role = "service" if kind in {"safehouse", "comms"} else "access"
    return {"kind": role, "width": ROAD_WIDTHS[role],
            "material": "asphalt" if kind in {"hq", "garrison"} else "dirt"}


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


def road_surface(network, *, vehicle=False):
    parts = []
    for segment in network["segments"]:
        radius = VEHICLE_SIZE[1] / 2 + VEHICLE_MARGIN if vehicle else segment["width"] / 2
        line = LineString([network["nodes"][key] for key in segment["nodes"]])
        parts.append(line.buffer(radius, cap_style="flat"))
    for junction in network["junctions"]:
        x, y = network["nodes"][junction["node"]]
        radius = junction["radius"]
        if vehicle:
            radius = TURN_SPACE if junction["turn"] else VEHICLE_SIZE[1] / 2 + VEHICLE_MARGIN
        parts.append(Point(x, y).buffer(radius))
    return union_all(parts)


def build_roads(bounds, sites, points, gates, stubs, cost, rng):
    side = cost.shape[0]
    axis = bounds.x + np.arange(side) * 2
    free = np.ones_like(cost, dtype=bool)
    free[:2] = free[-2:] = False
    free[:, :2] = free[:, -2:] = False
    for site in sites:
        x, y, width, height = site["rect"]
        margin = max(max(ROAD_WIDTHS.values()) / 2, TURN_SPACE) + 1
        free[np.ix_((axis >= y - margin) & (axis <= y + height + margin),
                    (axis >= x - margin) & (axis <= x + width + margin))] = False
    keys = sorted(points)
    indices = [int(round((points[key][1] - bounds.y) / 2)) * side
               + int(round((points[key][0] - bounds.x) / 2)) for key in keys]
    if not free.ravel()[indices].all():
        raise ValueError("시설 진입로가 예약 영역에 막힘")
    graph = _grid_graph(cost, free)
    distances = dijkstra(graph, directed=False, indices=indices)
    terminal_cost = distances[:, indices]
    if not np.isfinite(terminal_cost).all():
        raise ValueError("도로망을 연결할 수 없는 시설 배치")
    tree = minimum_spanning_tree(terminal_cost).tocoo()
    pairs = {tuple(sorted((int(a), int(b)))) for a, b in zip(tree.row, tree.col)}
    order = [keys.index("spawn")]
    for parent in order:
        for a, b in sorted(pairs):
            child = b if a == parent else a if b == parent else None
            if child is not None and child not in order:
                order.append(child)
    specs = {site["id"]: site["road"] for site in sites}
    main = {"kind": "main", "width": ROAD_WIDTHS["main"], "material": "asphalt"}
    specs.update({key: main for key in keys if key not in specs})
    order = [order[0]] + sorted(order[1:], key=lambda number: keys[number] not in {"entry/0", "entry/1"})
    paths = []
    used = np.zeros(cost.size, dtype=bool)
    used[indices[order[0]]] = True
    used_width = np.zeros(cost.size)
    used_width[indices[order[0]]] = main["width"]
    row, col = np.indices(cost.shape)

    def spaced_path(start, end, joins, width):
        distance, nearest = distance_transform_edt(~used.reshape(cost.shape), return_indices=True)
        separation = (width + used_width.reshape(cost.shape)[tuple(nearest)]) / 2 + ROAD_GAP + 2
        allowed = distance * 2 >= separation
        # 합류점 주변에서만 간격 제한을 풀어 같은 길에 직접 접속한다.
        for cell in joins:
            radius = (width + used_width.max()) / 2 + ROAD_GAP + 4
            allowed |= (row - cell // side) ** 2 + (col - cell % side) ** 2 <= (radius / 2) ** 2
        routed = _grid_graph(cost, free & allowed)
        distance, parents = dijkstra(routed, directed=False, indices=start, return_predecessors=True)
        return _path(parents, start, end) if np.isfinite(distance[end]) else None

    for number in order[1:]:
        start = indices[number]
        if used[start]:
            continue
        distance = dijkstra(graph, directed=False, indices=start)
        existing = np.flatnonzero(used)
        join = int(existing[np.argmin(distance[existing])])
        spec = specs[keys[number]]
        path = spaced_path(start, join, (start, join), spec["width"])
        if path is None:
            raise ValueError("기존 도로와 간격을 확보할 수 없는 진입로")
        # 먼저 만난 기존 도로에 합류해 불필요한 평행 구간을 남기지 않는다.
        stop = next(i for i, cell in enumerate(path) if used[cell])
        path = path[:stop + 1]
        paths.append((path, keys[number], spec))
        used[path] = True
        used_width[path] = np.maximum(used_width[path], spec["width"])
    candidates = [(a, b) for a in range(len(keys)) for b in range(a + 1, len(keys)) if (a, b) not in pairs]
    rng.shuffle(candidates)
    loops = 0
    for a, b in candidates[:8]:
        spec = {"kind": "service", "width": ROAD_WIDTHS["service"], "material": "dirt"}
        if math.dist(points[keys[a]], points[keys[b]]) < (spec["width"] + ROAD_GAP) * 3:
            continue
        path = spaced_path(indices[a], indices[b], (indices[a], indices[b]), spec["width"])
        if path is None or used[path].all():
            continue
        paths.append((path, f"loop/{loops}", spec))
        used[path] = True
        used_width[path] = np.maximum(used_width[path], spec["width"])
        loops += 1
        if loops >= max(1, len(keys) // 4):
            break
    if not loops:
        raise ValueError("기존 도로와 떨어진 우회로를 확보하지 못함")
    routes = [(LineString([(bounds.x + (cell % side) * 2, bounds.y + (cell // side) * 2) for cell in path]),
               {"route": key, **spec}) for path, key, spec in paths]
    routes.extend((LineString(stub), {"route": key, **specs[key]}) for key, stub in zip(gates, stubs))
    merged = union_all([line for line, _ in routes])
    # 교차점을 먼저 확정한 뒤 짧은 격자 꺾임을 줄여 합류점은 그대로 보존한다.
    pieces = merged.geoms if merged.geom_type == "MultiLineString" else (merged,)
    rank = {kind: index for index, kind in enumerate(ROAD_WIDTHS)}

    def owner(line, sources):
        middle = line.interpolate(0.5, normalized=True)
        return min(sources, key=lambda item: (round(item[0].distance(middle), 6), rank[item[1]["kind"]]))[1]

    routes = [(line.simplify(1), owner(line, routes)) for line in pieces]
    merged = union_all([line for line, _ in routes])
    nodes, segments = {}, {}

    def node(point):
        x, y = (round(float(value), 6) or 0.0 for value in point)
        key = f"road/{x:g}/{y:g}"
        nodes[key] = [x, y]
        return key

    for line in merged.geoms if merged.geom_type == "MultiLineString" else (merged,):
        coords = list(line.simplify(0).coords)
        for a, b in zip(coords, coords[1:]):
            edge = tuple(sorted((node(a), node(b))))
            segments[edge] = {"nodes": list(edge), **owner(LineString([a, b]), routes)}
    terminals = {key: node(point) for key, point in sorted(gates.items())}
    spawn_node = node(points["spawn"])
    junctions = []
    incident = {}
    for edge, segment in sorted(segments.items()):
        for key in edge:
            incident.setdefault(key, []).append(segment)
    for key, adjacent in sorted(incident.items()):
        if len(adjacent) < 2:
            continue
        vectors = [np.subtract(nodes[next(other for other in segment["nodes"] if other != key)], nodes[key])
                   for segment in adjacent]
        a, b = vectors[:2]
        turn = len(adjacent) > 2 or abs(a[0] * b[1] - a[1] * b[0]) > 1e-6 or np.dot(a, b) >= 0
        spec = min(adjacent, key=lambda segment: (rank[segment["kind"]], segment["material"] != "asphalt"))
        radius = max(max(segment["width"] for segment in adjacent) / 2, TURN_SPACE if turn else 0)
        junctions.append({"node": key, "radius": radius, "turn": bool(turn), "material": spec["material"]})
    return {"nodes": dict(sorted(nodes.items())), "edges": sorted(segments),
            "segments": [segments[edge] for edge in sorted(segments)], "junctions": junctions,
            "terminals": terminals, "spawn_node": spawn_node}


def validate_roads(network, sites, bounds, colliders):
    if (sorted(tuple(segment["nodes"]) for segment in network["segments"]) != sorted(map(tuple, network["edges"]))
            or any(not math.isfinite(segment["width"]) or segment["width"] <= 0 for segment in network["segments"])):
        raise ValueError("도로 구간 속성과 연결 그래프 불일치")
    if any(segment["width"] < VEHICLE_SIZE[1] + VEHICLE_MARGIN * 2 for segment in network["segments"]):
        raise ValueError("차량 통행 폭이 부족한 도로")
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
                or footprint.boundary.distance(Point(gate)) > 1e-6
                or any(segment["width"] > connector["width_m"] for segment in network["segments"] if key in segment["nodes"])):
            raise ValueError(f"시설 도로 연결점 오류: {site['id']}")
        dx, dy = (network["nodes"][neighbors[0]][i] - gate[i] for i in (0, 1))
        nx, ny = connector["direction"]
        if abs(dx * ny - dy * nx) > 1e-6 or dx * nx + dy * ny <= 0:
            raise ValueError(f"시설 방향과 어긋난 진입로: {site['id']}")
        if surface.intersection(footprint).area > 1e-6:
            raise ValueError(f"시설 점유 영역을 통과하는 도로: {site['id']}")
    swept = road_surface(network, vehicle=True)
    blockers = [box(x, y, x + width, y + height) for x, y, width, height in
                (collider["rect"] for collider in colliders)]
    if not surface.buffer(1e-6).covers(swept) or swept.intersects(union_all(blockers)):
        raise ValueError("차량 통행 공간을 확보하지 못한 도로")
