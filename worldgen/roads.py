"""Road graph generation and rasterisation.

The graph is built from required nodes (spawn, world gate, facility entrances)
with a minimum spanning tree plus one extra loop edge.  Paths are found on a
coarse 2m grid, then rasterised as 0.5m-wide semantic cells.
"""

from __future__ import annotations

from collections import deque
import heapq
import math
import random

from .constants import DEFAULT_ROAD_WIDTH_M, Flag, Terrain, TILE_METERS
from .rng import ValueNoise
from .world import RoadEdge, RoadNetwork, RoadNode, WorldMap

_COARSE_M = 2.0
_STEPS = (
    (1, 0),
    (-1, 0),
    (0, 1),
    (0, -1),
    (1, 1),
    (1, -1),
    (-1, 1),
    (-1, -1),
)


class _CoarseGrid:
    def __init__(self, world: WorldMap) -> None:
        self.world = world
        self.width = max(1, math.ceil(world.width_m / _COARSE_M))
        self.height = max(1, math.ceil(world.height_m / _COARSE_M))
        self.blocked = bytearray(self.width * self.height)
        grid = world.grid
        for cy in range(self.height):
            for cx in range(self.width):
                x0 = cx * _COARSE_M
                y0 = cy * _COARSE_M
                blocked = False
                for fx, fy in grid.iter_cells_in_rect_m(
                    x0, y0, x0 + _COARSE_M, y0 + _COARSE_M
                ):
                    index = grid.index(fx, fy)
                    if grid.owner[index] != 0 or (
                        grid.flags[index] & int(Flag.BORDER)
                    ):
                        blocked = True
                        break
                self.blocked[cy * self.width + cx] = 1 if blocked else 0

    def in_bounds(self, cx: int, cy: int) -> bool:
        return 0 <= cx < self.width and 0 <= cy < self.height

    def is_free(self, cx: int, cy: int) -> bool:
        return self.in_bounds(cx, cy) and not self.blocked[cy * self.width + cx]

    def world_to_cell(self, x: float, y: float) -> tuple[int, int]:
        return (
            max(0, min(self.width - 1, int(x / _COARSE_M))),
            max(0, min(self.height - 1, int(y / _COARSE_M))),
        )

    def cell_center(self, cx: int, cy: int) -> tuple[float, float]:
        return ((cx + 0.5) * _COARSE_M, (cy + 0.5) * _COARSE_M)

    def nearest_free(self, cell: tuple[int, int], radius: int = 12) -> tuple[int, int] | None:
        cx, cy = cell
        if self.is_free(cx, cy):
            return cx, cy
        for r in range(1, radius + 1):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    nx, ny = cx + dx, cy + dy
                    if self.is_free(nx, ny):
                        return nx, ny
        return None


def _coarse_path(
    coarse: _CoarseGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    noise: ValueNoise,
) -> list[tuple[int, int]] | None:
    if not coarse.is_free(*start) or not coarse.is_free(*goal):
        return None
    if start == goal:
        return [start]

    start_index = start[1] * coarse.width + start[0]
    goal_index = goal[1] * coarse.width + goal[0]
    heap: list[tuple[float, float, int, int]] = [
        (math.hypot(goal[0] - start[0], goal[1] - start[1]), 0.0, start[0], start[1])
    ]
    g_score: dict[int, float] = {start_index: 0.0}
    came_from: dict[int, int] = {}
    closed = bytearray(coarse.width * coarse.height)

    while heap:
        _, g, cx, cy = heapq.heappop(heap)
        index = cy * coarse.width + cx
        if closed[index]:
            continue
        if index == goal_index:
            path = [(cx, cy)]
            while index != start_index:
                index = came_from[index]
                path.append((index % coarse.width, index // coarse.width))
            path.reverse()
            return path
        closed[index] = 1

        for dx, dy in _STEPS:
            nx, ny = cx + dx, cy + dy
            if not coarse.is_free(nx, ny):
                continue
            if dx and dy:
                if not coarse.is_free(cx + dx, cy) or not coarse.is_free(cx, cy + dy):
                    continue
            neighbor = ny * coarse.width + nx
            if closed[neighbor]:
                continue
            cost = 1.41421356237 if (dx and dy) else 1.0
            # A little deterministic roughness keeps roads from looking like
            # pure graph edges without making pathfinding unstable.
            cost += noise.value(nx * 0.31, ny * 0.31) * 0.45
            tentative = g + cost
            if tentative >= g_score.get(neighbor, float("inf")):
                continue
            g_score[neighbor] = tentative
            came_from[neighbor] = index
            heuristic = math.hypot(goal[0] - nx, goal[1] - ny)
            heapq.heappush(heap, (tentative + heuristic, tentative, nx, ny))
    return None


def _line_clear(world: WorldMap, a: tuple[float, float], b: tuple[float, float]) -> bool:
    grid = world.grid
    distance = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(1, int(math.ceil(distance / (TILE_METERS * 0.5))))
    for step in range(steps + 1):
        t = step / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        cx, cy = grid.world_to_cell(x, y)
        index = grid.index(cx, cy)
        if grid.owner[index] != 0:
            return False
        if grid.flags[index] & int(Flag.BORDER):
            return False
        if grid.flags[index] & int(Flag.BLOCKED):
            return False
    return True


def _simplify(world: WorldMap, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    result = [points[0]]
    current = 0
    while current < len(points) - 1:
        farthest = current + 1
        for candidate in range(len(points) - 1, current, -1):
            if _line_clear(world, points[current], points[candidate]):
                farthest = candidate
                break
        result.append(points[farthest])
        current = farthest
    return result


def _edge_path(
    world: WorldMap,
    coarse: _CoarseGrid,
    start: tuple[float, float],
    goal: tuple[float, float],
    noise: ValueNoise,
) -> list[tuple[float, float]]:
    start_cell = coarse.nearest_free(coarse.world_to_cell(*start))
    goal_cell = coarse.nearest_free(coarse.world_to_cell(*goal))
    if start_cell is None or goal_cell is None:
        raise RuntimeError("road endpoint has no free coarse cell")

    cells = _coarse_path(coarse, start_cell, goal_cell, noise)
    if cells is None:
        if _line_clear(world, start, goal):
            return [start, goal]
        raise RuntimeError("road pathfinding failed")

    points = [start]
    points.extend(coarse.cell_center(cx, cy) for cx, cy in cells)
    points.append(goal)
    # Remove duplicates while preserving order.
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped or math.hypot(point[0] - deduped[-1][0], point[1] - deduped[-1][1]) > 1e-6:
            deduped.append(point)
    return _simplify(world, deduped)


def _rasterise(
    world: WorldMap,
    points: list[tuple[float, float]],
    width_m: float,
) -> None:
    grid = world.grid
    radius = width_m * 0.5
    for a, b in zip(points, points[1:]):
        distance = math.hypot(b[0] - a[0], b[1] - a[1])
        steps = max(1, int(math.ceil(distance / 0.25)))
        for step in range(steps + 1):
            t = step / steps
            x = a[0] + (b[0] - a[0]) * t
            y = a[1] + (b[1] - a[1]) * t
            cx0 = max(0, int(math.floor((x - radius) / TILE_METERS)))
            cy0 = max(0, int(math.floor((y - radius) / TILE_METERS)))
            cx1 = min(grid.width - 1, int(math.ceil((x + radius) / TILE_METERS)))
            cy1 = min(grid.height - 1, int(math.ceil((y + radius) / TILE_METERS)))
            r2 = radius * radius
            for cy in range(cy0, cy1 + 1):
                py = (cy + 0.5) * TILE_METERS
                for cx in range(cx0, cx1 + 1):
                    px = (cx + 0.5) * TILE_METERS
                    dx = px - x
                    dy = py - y
                    if dx * dx + dy * dy > r2:
                        continue
                    index = grid.index(cx, cy)
                    if grid.owner[index] != 0:
                        continue
                    grid.flags[index] = (
                        (grid.flags[index] | int(Flag.ROAD | Flag.WALKABLE))
                        & ~int(Flag.BLOCKED | Flag.BORDER)
                    ) & 0xFF
                    grid.terrain[index] = int(Terrain.ROAD)


def build_road_network(
    world: WorldMap,
    rng: random.Random,
    road_width_m: float = DEFAULT_ROAD_WIDTH_M,
) -> RoadNetwork:
    nodes: dict[str, RoadNode] = {
        "spawn": RoadNode("spawn", "spawn", world.spawn[0], world.spawn[1]),
    }
    if world.gate is not None:
        nodes["gate"] = RoadNode("gate", "gate", world.gate[0], world.gate[1])
    for placement in world.facilities:
        node_id = f"facility:{placement.id}"
        nodes[node_id] = RoadNode(
            node_id,
            "facility_entrance",
            placement.entrance[0],
            placement.entrance[1],
            facility_id=placement.id,
        )

    if len(nodes) == 1:
        return RoadNetwork(nodes=nodes)

    ids = list(nodes)
    edges: list[RoadEdge] = []
    edge_keys: set[tuple[str, str]] = set()
    visited = {ids[0]}
    while len(visited) < len(ids):
        best: tuple[float, str, str] | None = None
        for a in visited:
            for b in ids:
                if b in visited:
                    continue
                distance = math.hypot(
                    nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y
                )
                if best is None or distance < best[0]:
                    best = (distance, a, b)
        if best is None:
            break
        _, a, b = best
        edges.append(RoadEdge(a, b))
        edge_keys.add(tuple(sorted((a, b))))
        visited.add(b)

    # Add one loop so the road network is not a pure tree.
    candidates: list[tuple[float, str, str]] = []
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            key = tuple(sorted((a, b)))
            if key in edge_keys:
                continue
            distance = math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y)
            candidates.append((distance, a, b))
    if candidates:
        _, a, b = min(candidates, key=lambda item: item[0])
        edges.append(RoadEdge(a, b))
        edge_keys.add(tuple(sorted((a, b))))

    coarse = _CoarseGrid(world)
    noise = ValueNoise(world.seed ^ 0x0BADF00D)
    for edge in edges:
        start = (nodes[edge.a].x, nodes[edge.a].y)
        goal = (nodes[edge.b].x, nodes[edge.b].y)
        edge.points = _edge_path(world, coarse, start, goal, noise)
        edge.length_m = sum(
            math.hypot(b[0] - a[0], b[1] - a[1])
            for a, b in zip(edge.points, edge.points[1:])
        )
        _rasterise(world, edge.points, road_width_m)

    network = RoadNetwork(nodes=nodes, edges=edges)
    world.roads = network
    return network
