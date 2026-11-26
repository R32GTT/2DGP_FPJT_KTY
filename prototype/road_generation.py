"""지형 비용 기반 도로망. 연결 그래프와 표시·통행 경로를 같은 선분에서 만든다."""

from collections import defaultdict
import math

import numpy as np
from scipy.ndimage import distance_transform_edt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra, minimum_spanning_tree
from shapely import LineString, Point, Polygon, STRtree, box, union_all
from shapely.prepared import prep
from .road_junctions import build_junctions
from .road_paths import smooth_path


DIRT_WIDTH = 3.0
ROAD_WIDTHS = {"main": 6.0, "access": 4.5, "service": DIRT_WIDTH}
MAIN_ROAD = {"kind": "main", "width": ROAD_WIDTHS["main"], "material": "asphalt"}
ROAD_GAP = 6
VEHICLE_SIZE = (5.6, 2.4)
VEHICLE_MARGIN = 0.3


def facility_road(kind):
    role = "service" if kind in {"safehouse", "comms"} else "access"
    material = "asphalt" if kind in {"hq", "garrison"} else "dirt"
    return {"kind": role, "width": DIRT_WIDTH if material == "dirt" else ROAD_WIDTHS[role], "material": material}


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
        parts.append(Polygon(junction["vehicle_polygon" if vehicle else "polygon"]))
    return union_all(parts)


def build_roads(bounds, sites, points, gates, stubs, cost, rng, *, trunks=None, step=2):
    rows, columns = cost.shape
    xs, ys = bounds.x + np.arange(columns) * step, bounds.y + np.arange(rows) * step
    free = np.ones_like(cost, dtype=bool)
    free[:2] = free[-2:] = False
    free[:, :2] = free[:, -2:] = False
    for site in sites:
        x, y, width, height = site["rect"]
        margin = math.hypot(VEHICLE_SIZE[0] / 2 + VEHICLE_MARGIN, max(ROAD_WIDTHS.values()) / 2) + 1
        free[np.ix_((ys >= y - margin) & (ys <= y + height + margin),
                    (xs >= x - margin) & (xs <= x + width + margin))] = False
    keys = sorted(points)
    indices = [int(round((points[key][1] - bounds.y) / step)) * columns
               + int(round((points[key][0] - bounds.x) / step)) for key in keys]
    if step > 4:
        used_anchors = set()
        for number, key in enumerate(keys):
            px, py = points[key]
            gx, gy = gates[key]
            row, column = divmod(indices[number], columns)
            candidates = []
            for iy in range(max(0, row - 2), min(rows, row + 3)):
                for ix in range(max(0, column - 2), min(columns, column + 3)):
                    cell = iy * columns + ix
                    ahead = (xs[ix] - gx) * (px - gx) + (ys[iy] - gy) * (py - gy)
                    if free[iy, ix] and cell not in used_anchors and ahead > 0:
                        candidates.append((math.hypot(xs[ix] - px, ys[iy] - py), cell))
            if not candidates:
                raise ValueError("시설 진입로 주변에 비어 있는 도로 격자가 없음")
            indices[number] = min(candidates)[1]
            used_anchors.add(indices[number])
    anchors = {index: tuple(points[key]) for key, index in zip(keys, indices)}
    if len(anchors) != len(set(map(tuple, points.values()))):
        raise ValueError("같은 도로 격자에 놓인 진입점")
    if not free.ravel()[indices].all():
        raise ValueError("시설 진입로가 예약 영역에 막힘")
    graph = _grid_graph(cost, free)
    if trunks:
        order = [keys.index(key) for key in trunks]
        order.extend(index for index, key in enumerate(keys) if key not in trunks)
    else:
        order, pairs = _local_road_order(graph, keys, indices)
    specs = {site["id"]: site["road"] for site in sites}
    specs.update({key: MAIN_ROAD for key in keys if key not in specs})
    paths = []
    used = np.zeros(cost.size, dtype=bool)
    used[indices[order[0]]] = True
    used_width = np.zeros(cost.size)
    used_width[indices[order[0]]] = MAIN_ROAD["width"]
    row, col = np.indices(cost.shape)

    def spaced_path(start, end, joins, width):
        distance, nearest = distance_transform_edt(~used.reshape(cost.shape), return_indices=True)
        separation = (width + used_width.reshape(cost.shape)[tuple(nearest)]) / 2 + ROAD_GAP + 2
        allowed = distance * step >= separation
        # 합류점 주변에서만 간격 제한을 풀어 같은 길에 직접 접속한다.
        for cell in joins:
            radius = (width + used_width.max()) / 2 + ROAD_GAP + 4
            allowed |= (row - cell // columns) ** 2 + (col - cell % columns) ** 2 <= (radius / step) ** 2
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
        spec = MAIN_ROAD if trunks and keys[number] in trunks else specs[keys[number]]
        path = spaced_path(start, join, (start, join), spec["width"])
        if path is None:
            raise ValueError("기존 도로와 간격을 확보할 수 없는 진입로")
        # 먼저 만난 기존 도로에 합류해 불필요한 평행 구간을 남기지 않는다.
        stop = next(i for i, cell in enumerate(path) if used[cell])
        path = path[:stop + 1]
        paths.append((path, keys[number], spec))
        used[path] = True
        used_width[path] = np.maximum(used_width[path], spec["width"])
    candidates = [] if trunks else [(a, b) for a in range(len(keys)) for b in range(a + 1, len(keys))
                                    if (a, b) not in pairs]
    rng.shuffle(candidates)
    loops = 0
    for a, b in candidates[:8]:
        spec = {"kind": "service", "width": DIRT_WIDTH, "material": "dirt"}
        if math.dist(points[keys[a]], points[keys[b]]) < (spec["width"] + ROAD_GAP) * 3:
            continue
        path = spaced_path(indices[a], indices[b], (indices[a], indices[b]), spec["width"])
        if path is None or used[path].all():
            continue
        old_distance = dijkstra(_grid_graph(np.ones_like(cost), used.reshape(cost.shape)),
                                directed=False, indices=indices[a])[indices[b]]
        length = sum(math.hypot((a % columns) - (b % columns), (a // columns) - (b // columns)) * step
                     for a, b in zip(path, path[1:]))
        if length >= old_distance * (step / 2) * .75:
            continue
        paths.append((path, f"loop/{loops}", spec))
        used[path] = True
        used_width[path] = np.maximum(used_width[path], spec["width"])
        loops += 1
        if loops >= 1:
            break
    # 격자로 경로를 찾되 실제 시설 진입점 좌표에서 연결한다.
    routes = [(LineString([anchors.get(cell, (xs[cell % columns], ys[cell // columns])) for cell in path]),
               {"route": key, **spec}) for path, key, spec in paths]
    routes.extend((LineString(stub), {"route": key, **specs[key]}) for key, stub in zip(gates, stubs))
    merged = union_all([line for line, _ in routes])
    # 교차점을 먼저 확정한 뒤 짧은 격자 꺾임을 줄여 합류점은 그대로 보존한다.
    pieces = merged.geoms if merged.geom_type == "MultiLineString" else (merged,)
    rank = {kind: index for index, kind in enumerate(ROAD_WIDTHS)}

    def owner(line, sources, source_index):
        middle = line.interpolate(0.5, normalized=True)
        nearest = source_index.nearest(middle)
        distance = sources[nearest][0].distance(middle)
        candidates = source_index.query(middle, predicate="dwithin", distance=distance + 1e-6)
        return min((sources[index] for index in candidates),
                   key=lambda item: (round(item[0].distance(middle), 6), rank[item[1]["kind"]]))[1]

    forbidden = prep(union_all([box(x, y, x + w, y + h) for x, y, w, h in (site['rect'] for site in sites)]).buffer(-1e-6))
    envelope = box(bounds.x, bounds.y, bounds.right, bounds.bottom).buffer(1e-6)
    refined = []
    source_index = STRtree([line for line, _ in routes])
    for line in pieces:
        spec = owner(line, routes, source_index)
        clearance = max(spec['width'] / 2, math.hypot(VEHICLE_SIZE[0] / 2, VEHICLE_SIZE[1] / 2) + VEHICLE_MARGIN)
        def valid(candidate):
            bed = candidate.buffer(clearance, cap_style='flat', join_style='bevel')
            return not forbidden.intersects(bed) and envelope.covers(bed)
        candidate = line.simplify(2)
        candidate = candidate if valid(candidate) else line
        refined.append((LineString(smooth_path(candidate.coords, valid)), spec))
    routes = refined
    source_index = STRtree([line for line, _ in routes])
    merged = union_all([line for line, _ in routes])
    nodes, segments = {}, {}

    def node(point):
        x, y = (round(float(value), 6) or 0.0 for value in point)
        key = f"road/{x:.15g}/{y:.15g}" if trunks else f"road/{x:g}/{y:g}"
        nodes[key] = [x, y]
        return key

    for line in merged.geoms if merged.geom_type == "MultiLineString" else (merged,):
        coords = list(line.simplify(0).coords)
        for a, b in zip(coords, coords[1:]):
            edge = tuple(sorted((node(a), node(b))))
            if edge[0] == edge[1]:
                continue
            segments[edge] = {"nodes": list(edge), **owner(LineString([a, b]), routes, source_index)}
    terminals = {key: node(point) for key, point in sorted(gates.items())}
    spawn_node = terminals[next(site["id"] for site in sites if site["kind"] == "safehouse")] if trunks else node(points["spawn"])
    junctions = build_junctions(nodes, [segments[edge] for edge in sorted(segments)], bounds)
    return {"nodes": dict(sorted(nodes.items())), "edges": sorted(segments),
            "segments": [segments[edge] for edge in sorted(segments)], "junctions": junctions,
            "terminals": terminals, "spawn_node": spawn_node}


def _local_road_order(graph, keys, indices):
    distances = dijkstra(graph, directed=False, indices=indices)[:, indices]
    if not np.isfinite(distances).all():
        raise ValueError("도로망을 연결할 수 없는 시설 배치")
    tree = minimum_spanning_tree(distances).tocoo()
    pairs = {tuple(sorted((int(a), int(b)))) for a, b in zip(tree.row, tree.col)}
    order = [keys.index("spawn")]
    for parent in order:
        for a, b in sorted(pairs):
            child = b if a == parent else a if b == parent else None
            if child is not None and child not in order:
                order.append(child)
    return [order[0]] + sorted(order[1:], key=lambda number: not keys[number].startswith('entry/')), pairs


def build_basic_roads(bounds, sites, gates):
    # 양쪽 포털을 먼저 연결하고, 부지는 겹치지 않는 직각 진입로로 붙인다.
    nodes, terminals, segments = {}, {}, []

    def node(x, y):
        key = f"road/{x:g}/{y:g}"
        nodes[key] = [x, y]
        return key

    def connect(a, b, route, spec):
        segments.append({"nodes": sorted((a, b)), "route": route, **spec})

    for key, point in gates.items():
        terminals[key] = node(*point)
    axis = sorted({bounds.x, 0, bounds.right, *(site["connector"]["position"][0] for site in sites)})
    spine = [node(x, 0) for x in axis]
    for a, b in zip(spine, spine[1:]):
        connect(a, b, "spine", MAIN_ROAD)
    for site in sites:
        x, _ = site["connector"]["position"]
        key = site["id"]
        connect(node(x, 0), terminals[key], key, site["road"])
    segments.sort(key=lambda segment: segment["nodes"])
    return {"nodes": dict(sorted(nodes.items())), "edges": [tuple(row["nodes"]) for row in segments],
            "segments": segments, "junctions": build_junctions(nodes, segments, bounds),
            "terminals": terminals, "spawn_node": node(0, 0)}


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
    lines = [(segment, LineString([network["nodes"][key] for key in segment["nodes"]]))
             for segment in network["segments"]]
    shapes = STRtree([line for _, line in lines])
    search_distance = max(segment["width"] for segment, _ in lines) + ROAD_GAP
    for index, (segment, line) in enumerate(lines):
        a, b = line.coords
        direction = np.subtract(b, a) / line.length
        for other_index in shapes.query(line, predicate="dwithin", distance=search_distance):
            if other_index <= index:
                continue
            other_segment, other = lines[other_index]
            gap = (segment["width"] + other_segment["width"]) / 2 + ROAD_GAP
            if (set(segment["nodes"]) & set(other_segment["nodes"])
                    or line.distance(other) >= gap or line.intersects(other)):
                continue
            p, q = other.coords
            other_direction = np.subtract(q, p) / other.length
            if abs(direction[0] * other_direction[1] - direction[1] * other_direction[0]) >= 0.2:
                continue
            projections = [np.dot(np.subtract(point, a), direction) for point in other.coords]
            overlap = min(line.length, max(projections)) - max(0, min(projections))
            if overlap >= min(segment["width"], other_segment["width"]):
                raise ValueError("평행 도로 간격 부족")
    surface = road_surface(network)
    if not box(bounds.x, bounds.y, bounds.right, bounds.bottom).buffer(1e-6).covers(surface):
        raise ValueError("월드 경계를 벗어난 도로 폭")
    incident = defaultdict(list)
    for segment in network["segments"]:
        a, b = segment["nodes"]
        incident[a].append((b, segment["width"]))
        incident[b].append((a, segment["width"]))
    for site in sites:
        x, y, width, height = site["rect"]
        footprint = box(x, y, x + width, y + height)
        connector = site["connector"]
        gate = connector["position"]
        key = network["terminals"][site["id"]]
        neighbors = incident[key]
        if (math.dist(gate, network["nodes"][key]) > 1e-6 or len(neighbors) != 1
                or footprint.boundary.distance(Point(gate)) > 1e-6
                or neighbors[0][1] > connector["width_m"]):
            raise ValueError(f"시설 도로 연결점 오류: {site['id']}")
        dx, dy = (network["nodes"][neighbors[0][0]][i] - gate[i] for i in (0, 1))
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
