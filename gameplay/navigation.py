"""Runtime navigation across generated outdoors and authored facilities.

Outdoors use the generated 0.5m walk grid.  Inside a facility, authored
nav_node/nav_edge data is preferred.  The two domains meet at the facility
entrance, so AI does not need to know which authoring source produced a room.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math

from worldgen.constants import TILE_METERS
from worldgen.nav import a_star, nearest_passable
from worldgen.world import FacilityPlacement, WorldMap


@dataclass
class PathSegment:
    domain: str
    points: list[tuple[float, float]] = field(default_factory=list)


@dataclass
class NavPath:
    segments: list[PathSegment] = field(default_factory=list)

    @property
    def points(self) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for segment in self.segments:
            for point in segment.points:
                if not result or math.hypot(
                    point[0] - result[-1][0], point[1] - result[-1][1]
                ) > 1e-6:
                    result.append(point)
        return result

    def __bool__(self) -> bool:
        return bool(self.points)


def can_stand(world: WorldMap, x: float, y: float, radius: float = 0.30) -> bool:
    grid = world.grid
    cx0 = max(0, int(math.floor((x - radius) / TILE_METERS)))
    cy0 = max(0, int(math.floor((y - radius) / TILE_METERS)))
    cx1 = min(grid.width - 1, int(math.floor((x + radius) / TILE_METERS)))
    cy1 = min(grid.height - 1, int(math.floor((y + radius) / TILE_METERS)))
    for cy in range(cy0, cy1 + 1):
        for cx in range(cx0, cx1 + 1):
            if grid.is_blocked(cx, cy):
                return False
    return True


def has_line_of_sight(
    world: WorldMap,
    start: tuple[float, float],
    goal: tuple[float, float],
) -> bool:
    grid = world.grid
    distance = math.hypot(goal[0] - start[0], goal[1] - start[1])
    steps = max(1, int(math.ceil(distance / 0.25)))
    for step in range(steps + 1):
        t = step / steps
        x = start[0] + (goal[0] - start[0]) * t
        y = start[1] + (goal[1] - start[1]) * t
        cx, cy = grid.world_to_cell(x, y)
        if grid.occlusion[grid.index(cx, cy)]:
            return False
    return True


def _line_walkable(world: WorldMap, a: tuple[float, float], b: tuple[float, float]) -> bool:
    grid = world.grid
    distance = math.hypot(b[0] - a[0], b[1] - a[1])
    steps = max(1, int(math.ceil(distance / (TILE_METERS * 0.5))))
    for step in range(steps + 1):
        t = step / steps
        x = a[0] + (b[0] - a[0]) * t
        y = a[1] + (b[1] - a[1]) * t
        cx, cy = grid.world_to_cell(x, y)
        if not grid.is_walkable(cx, cy):
            return False
    return True


def _smooth(world: WorldMap, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    result = [points[0]]
    current = 0
    while current < len(points) - 1:
        farthest = current + 1
        for candidate in range(len(points) - 1, current, -1):
            if _line_walkable(world, points[current], points[candidate]):
                farthest = candidate
                break
        result.append(points[farthest])
        current = farthest
    return result


def _nearest_node(
    nodes: list[dict], point: tuple[float, float]
) -> dict:
    return min(
        nodes,
        key=lambda node: math.hypot(node["x"] - point[0], node["y"] - point[1]),
    )


def _facility_path(
    placement: FacilityPlacement,
    start: tuple[float, float],
    goal: tuple[float, float],
) -> list[tuple[float, float]] | None:
    nodes = placement.nav_nodes
    if len(nodes) < 2:
        return None
    adjacency: dict[str, list[tuple[str, float]]] = {node["id"]: [] for node in nodes}
    for edge in placement.nav_edges:
        a = str(edge.get("from", ""))
        b = str(edge.get("to", ""))
        if a not in adjacency or b not in adjacency:
            continue
        cost = float(edge.get("cost", 1.0))
        adjacency[a].append((b, cost))
        if not edge.get("one_way", False):
            adjacency[b].append((a, cost))
    if not any(adjacency.values()):
        return None

    start_node = _nearest_node(nodes, start)
    goal_node = _nearest_node(nodes, goal)
    positions = {node["id"]: (node["x"], node["y"]) for node in nodes}

    heap: list[tuple[float, str]] = [(0.0, start_node["id"])]
    cost_so_far: dict[str, float] = {start_node["id"]: 0.0}
    came_from: dict[str, str] = {}
    found = False
    while heap:
        cost, current = heapq.heappop(heap)
        if current == goal_node["id"]:
            found = True
            break
        if cost > cost_so_far.get(current, float("inf")):
            continue
        for neighbor, step in adjacency.get(current, []):
            new_cost = cost + step
            if new_cost >= cost_so_far.get(neighbor, float("inf")):
                continue
            cost_so_far[neighbor] = new_cost
            came_from[neighbor] = current
            heapq.heappush(heap, (new_cost, neighbor))
    if not found:
        return None

    ids = [goal_node["id"]]
    while ids[-1] != start_node["id"]:
        ids.append(came_from[ids[-1]])
    ids.reverse()
    return [
        start,
        *(positions[node_id] for node_id in ids),
        goal,
    ]


class NavigationWorld:
    def __init__(self, world: WorldMap) -> None:
        self.world = world
        self.grid = world.grid

    def facility_at(self, point: tuple[float, float]) -> FacilityPlacement | None:
        x, y = point
        for placement in self.world.facilities:
            if (
                placement.x <= x <= placement.x + placement.width_m
                and placement.y <= y <= placement.y + placement.height_m
            ):
                return placement
        return None

    def _grid_path(
        self, start: tuple[float, float], goal: tuple[float, float]
    ) -> list[tuple[float, float]] | None:
        grid = self.grid
        start_cell = nearest_passable(grid, grid.world_to_cell(*start))
        goal_cell = nearest_passable(grid, grid.world_to_cell(*goal))
        if start_cell is None or goal_cell is None:
            return None
        cells = a_star(grid, start_cell, goal_cell)
        if cells is None:
            return None
        points = [start]
        points.extend(grid.cell_center(cx, cy) for cx, cy in cells)
        points.append(goal)
        deduped: list[tuple[float, float]] = []
        for point in points:
            if not deduped or math.hypot(
                point[0] - deduped[-1][0], point[1] - deduped[-1][1]
            ) > 1e-6:
                deduped.append(point)
        return _smooth(self.world, deduped)

    def find_path(
        self, start: tuple[float, float], goal: tuple[float, float]
    ) -> NavPath | None:
        start_facility = self.facility_at(start)
        goal_facility = self.facility_at(goal)

        if start_facility is goal_facility and start_facility is not None:
            indoor = _facility_path(start_facility, start, goal)
            if indoor is not None:
                return NavPath([PathSegment(f"facility:{start_facility.id}", indoor)])

        if start_facility is not None and goal_facility is None:
            indoor = _facility_path(start_facility, start, start_facility.entrance)
            outdoor = self._grid_path(start_facility.entrance, goal)
            if indoor is not None and outdoor is not None:
                return NavPath(
                    [
                        PathSegment(f"facility:{start_facility.id}", indoor),
                        PathSegment("outdoor", outdoor),
                    ]
                )

        if start_facility is None and goal_facility is not None:
            outdoor = self._grid_path(start, goal_facility.entrance)
            indoor = _facility_path(goal_facility, goal_facility.entrance, goal)
            if outdoor is not None and indoor is not None:
                return NavPath(
                    [
                        PathSegment("outdoor", outdoor),
                        PathSegment(f"facility:{goal_facility.id}", indoor),
                    ]
                )

        if (
            start_facility is not None
            and goal_facility is not None
            and start_facility is not goal_facility
        ):
            indoor_a = _facility_path(start_facility, start, start_facility.entrance)
            outdoor = self._grid_path(start_facility.entrance, goal_facility.entrance)
            indoor_b = _facility_path(goal_facility, goal_facility.entrance, goal)
            if indoor_a is not None and outdoor is not None and indoor_b is not None:
                return NavPath(
                    [
                        PathSegment(f"facility:{start_facility.id}", indoor_a),
                        PathSegment("outdoor", outdoor),
                        PathSegment(f"facility:{goal_facility.id}", indoor_b),
                    ]
                )

        grid_path = self._grid_path(start, goal)
        if grid_path is None:
            return None
        return NavPath([PathSegment("outdoor", grid_path)])
