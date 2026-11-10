"""Navigation helpers for the generated semantic grid.

Outdoor navigation is grid-based and generated.  Authored facility nav nodes
are kept on the placement data and will be joined to this grid at entrances.
"""

from __future__ import annotations

from collections import deque
import heapq
import math
from typing import Callable, Iterable

from .constants import Flag
from .grid import WorldGrid
from .world import Tree, ValidationReport, WorldMap

Passable = Callable[[int, int], bool]

_DIAGONALS = {(1, 1), (1, -1), (-1, 1), (-1, -1)}
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


def _default_passable(grid: WorldGrid) -> Passable:
    return grid.is_walkable


def _can_step(grid: WorldGrid, cx: int, cy: int, dx: int, dy: int, passable: Passable) -> bool:
    nx, ny = cx + dx, cy + dy
    if not passable(nx, ny):
        return False
    if (dx, dy) in _DIAGONALS:
        if not passable(cx + dx, cy) or not passable(cx, cy + dy):
            return False
    return True


def bfs_reachable(
    grid: WorldGrid,
    start: tuple[int, int],
    passable: Passable | None = None,
) -> bytearray:
    predicate = passable or _default_passable(grid)
    visited = bytearray(grid.size)
    if not predicate(*start):
        return visited

    queue: deque[tuple[int, int]] = deque([start])
    visited[grid.index(*start)] = 1
    while queue:
        cx, cy = queue.popleft()
        for dx, dy in _STEPS:
            if not _can_step(grid, cx, cy, dx, dy, predicate):
                continue
            nx, ny = cx + dx, cy + dy
            index = grid.index(nx, ny)
            if visited[index]:
                continue
            visited[index] = 1
            queue.append((nx, ny))
    return visited


def nearest_passable(
    grid: WorldGrid,
    cell: tuple[int, int],
    passable: Passable | None = None,
    max_radius: int = 24,
) -> tuple[int, int] | None:
    predicate = passable or _default_passable(grid)
    cx, cy = cell
    if predicate(cx, cy):
        return cx, cy
    for radius in range(1, max_radius + 1):
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                nx, ny = cx + dx, cy + dy
                if grid.in_bounds(nx, ny) and predicate(nx, ny):
                    return nx, ny
    return None


def a_star(
    grid: WorldGrid,
    start: tuple[int, int],
    goal: tuple[int, int],
    passable: Passable | None = None,
    max_visited: int = 250_000,
) -> list[tuple[int, int]] | None:
    predicate = passable or _default_passable(grid)
    if not predicate(*start) or not predicate(*goal):
        return None
    if start == goal:
        return [start]

    start_index = grid.index(*start)
    goal_index = grid.index(*goal)
    open_heap: list[tuple[float, int, int, int]] = [
        (math.hypot(goal[0] - start[0], goal[1] - start[1]), 0, start[0], start[1])
    ]
    came_from: dict[int, int] = {}
    g_score: dict[int, float] = {start_index: 0.0}
    closed = bytearray(grid.size)
    visited = 0

    while open_heap:
        _, g, cx, cy = heapq.heappop(open_heap)
        current_index = grid.index(cx, cy)
        if closed[current_index]:
            continue
        if current_index == goal_index:
            path = [(cx, cy)]
            while current_index != start_index:
                current_index = came_from[current_index]
                path.append((current_index % grid.width, current_index // grid.width))
            path.reverse()
            return path

        closed[current_index] = 1
        visited += 1
        if visited > max_visited:
            return None

        for dx, dy in _STEPS:
            if not _can_step(grid, cx, cy, dx, dy, predicate):
                continue
            nx, ny = cx + dx, cy + dy
            neighbor_index = grid.index(nx, ny)
            if closed[neighbor_index]:
                continue
            step_cost = 1.41421356237 if (dx, dy) in _DIAGONALS else 1.0
            tentative = g + step_cost
            if tentative >= g_score.get(neighbor_index, float("inf")):
                continue
            g_score[neighbor_index] = tentative
            came_from[neighbor_index] = current_index
            heuristic = math.hypot(goal[0] - nx, goal[1] - ny)
            heapq.heappush(open_heap, (tentative + heuristic, tentative, nx, ny))
    return None


def distance_field(
    grid: WorldGrid,
    sources: Iterable[tuple[int, int]],
    max_distance: int = 255,
) -> bytearray:
    """BFS distance in cells from the nearest source (8-way)."""

    distances = bytearray([255]) * grid.size
    queue: deque[tuple[int, int, int]] = deque()
    for cx, cy in sources:
        if not grid.in_bounds(cx, cy):
            continue
        index = grid.index(cx, cy)
        if distances[index] == 0:
            continue
        distances[index] = 0
        queue.append((cx, cy, 0))
    while queue:
        cx, cy, distance = queue.popleft()
        if distance >= max_distance:
            continue
        next_distance = distance + 1
        for dx, dy in _STEPS:
            nx, ny = cx + dx, cy + dy
            if not grid.in_bounds(nx, ny):
                continue
            index = grid.index(nx, ny)
            if next_distance < distances[index]:
                distances[index] = next_distance
                queue.append((nx, ny, next_distance))
    return distances


def _target_cells(world: WorldMap) -> dict[str, tuple[int, int]]:
    grid = world.grid
    targets: dict[str, tuple[int, int]] = {
        "spawn": grid.world_to_cell(*world.spawn),
    }
    if world.gate is not None:
        targets["gate"] = grid.world_to_cell(*world.gate)
    for placement in world.facilities:
        targets[f"facility:{placement.id}:entrance"] = grid.world_to_cell(
            *placement.entrance
        )
        if placement.nav_nodes:
            node = placement.nav_nodes[0]
            targets[f"facility:{placement.id}:nav"] = grid.world_to_cell(
                node["x"], node["y"]
            )
        else:
            targets[f"facility:{placement.id}:center"] = grid.world_to_cell(
                placement.x + placement.width_m * 0.5,
                placement.y + placement.height_m * 0.5,
            )
    if world.roads is not None:
        for node in world.roads.nodes.values():
            targets[f"road:{node.id}"] = grid.world_to_cell(node.x, node.y)
    return targets


def validate_world(world: WorldMap) -> ValidationReport:
    grid = world.grid
    report = ValidationReport()
    spawn_cell = grid.world_to_cell(*world.spawn)
    spawn_cell = nearest_passable(grid, spawn_cell) or spawn_cell
    visited = bfs_reachable(grid, spawn_cell)
    report.reachable_cells = sum(visited)
    report.walkable_cells = sum(
        1
        for flag in grid.flags
        if (flag & int(Flag.WALKABLE)) and not (flag & int(Flag.BLOCKED))
    )

    for name, cell in _target_cells(world).items():
        target = nearest_passable(grid, cell)
        ok = target is not None and bool(visited[grid.index(*target)])
        report.targets[name] = ok
        if not ok:
            report.errors.append(f"unreachable target: {name} at {cell}")

    ratio = report.reachable_cells / max(1, report.walkable_cells)
    if ratio < 0.97:
        report.errors.append(f"reachable ratio too low: {ratio:.3f}")

    if world.roads is not None and not world.roads.connected():
        report.errors.append("road graph is disconnected")

    for tree in world.trees:
        for cx, cy in tree.trunk_cells:
            if grid.get_flag(cx, cy, Flag.ROAD):
                report.warnings.append(f"tree {tree.id} trunk overlaps a road")
                break

    if not report.targets.get("spawn", False):
        report.errors.append("spawn is not walkable")

    report.ok = not report.errors
    return report


def _remove_tree(world: WorldMap, tree: Tree) -> None:
    grid = world.grid
    for cx, cy in tree.trunk_cells:
        if not grid.in_bounds(cx, cy):
            continue
        index = grid.index(cx, cy)
        if grid.owner[index] == 0:
            grid.flags[index] = (
                (grid.flags[index] & ~int(Flag.BLOCKED | Flag.TREE_TRUNK))
                | int(Flag.WALKABLE)
            ) & 0xFF


def repair_reachability(world: WorldMap, max_repairs: int = 12) -> None:
    """Remove trees that seal a target off from the spawn.

    Walls and authored facility data are never removed.  Only generated trees
    are sacrificed, which keeps the outdoor world traversable without touching
    hand-authored interiors.
    """

    grid = world.grid

    def wall_passable(cx: int, cy: int) -> bool:
        if not grid.in_bounds(cx, cy):
            return False
        flags = grid.flags[grid.index(cx, cy)]
        if not (flags & int(Flag.BLOCKED)):
            return True
        return bool(flags & int(Flag.TREE_TRUNK)) and not bool(
            flags & int(Flag.FACILITY)
        )

    spawn_cell = nearest_passable(grid, grid.world_to_cell(*world.spawn))
    if spawn_cell is None:
        return

    tree_by_cell: dict[tuple[int, int], Tree] = {}
    for tree in world.trees:
        for cell in tree.trunk_cells:
            tree_by_cell[cell] = tree

    for _ in range(max_repairs):
        visited = bfs_reachable(grid, spawn_cell)
        missing: list[tuple[int, int]] = []
        for cell in _target_cells(world).values():
            target = nearest_passable(grid, cell)
            if target is not None and not visited[grid.index(*target)]:
                missing.append(target)
        if not missing:
            return

        changed = False
        for target in missing:
            path = a_star(grid, spawn_cell, target, passable=wall_passable)
            if path is None:
                continue
            removed: set[int] = set()
            for cx, cy in path:
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        tree = tree_by_cell.get((cx + dx, cy + dy))
                        if tree is None or tree.id in removed:
                            continue
                        _remove_tree(world, tree)
                        removed.add(tree.id)
                        changed = True
            if removed:
                world.trees = [tree for tree in world.trees if tree.id not in removed]
        if not changed:
            return


def compute_clearance(world: WorldMap, max_distance: int = 32) -> None:
    grid = world.grid
    blocked_cells = (
        (index % grid.width, index // grid.width)
        for index, flag in enumerate(grid.flags)
        if flag & int(Flag.BLOCKED)
    )
    grid.clearance = distance_field(grid, blocked_cells, max_distance=max_distance)


def find_world_path(
    world: WorldMap,
    start_m: tuple[float, float],
    goal_m: tuple[float, float],
) -> list[tuple[float, float]] | None:
    grid = world.grid
    start = nearest_passable(grid, grid.world_to_cell(*start_m))
    goal = nearest_passable(grid, grid.world_to_cell(*goal_m))
    if start is None or goal is None:
        return None
    cells = a_star(grid, start, goal)
    if cells is None:
        return None
    return [grid.cell_center(cx, cy) for cx, cy in cells]
