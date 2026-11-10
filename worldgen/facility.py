"""Facility prefab loading and placement.

A facility is a finished stamp, not a set of outdoor modules.  The loader
accepts either a Tiled .tmj (compiled through maptool) or an already compiled
runtime JSON.  If authored collision is missing, the Architecture tile layer
is used as a conservative wall fallback so the facility is still solid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import random
import sys
from typing import Iterable

from .constants import (
    DEFAULT_FACILITY_MARGIN_M,
    Flag,
    TILE_METERS,
    Terrain,
)
from .world import FacilityPlacement, WorldMap


@dataclass
class FacilityPrefab:
    id: str
    facility_type: str
    width_m: float
    height_m: float
    width_cells: int
    height_cells: int
    blocked: set[tuple[int, int]] = field(default_factory=set)
    occluder: set[tuple[int, int]] = field(default_factory=set)
    doors: list[dict] = field(default_factory=list)
    connectors: list[dict] = field(default_factory=list)
    nav_nodes: list[dict] = field(default_factory=list)
    nav_edges: list[dict] = field(default_factory=list)
    entrance_local: tuple[float, float] = (0.0, 0.0)
    outward_local: tuple[float, float] = (0.0, 1.0)
    source: str = ""


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _rect_cells(rect: dict, width_cells: int, height_cells: int) -> set[tuple[int, int]]:
    x = float(rect.get("x", 0.0))
    y = float(rect.get("y", 0.0))
    width = float(rect.get("width", 0.0))
    height = float(rect.get("height", 0.0))
    if width <= 0.0 or height <= 0.0:
        return set()
    cx0 = max(0, int(math.floor(x / TILE_METERS)))
    cy0 = max(0, int(math.floor(y / TILE_METERS)))
    cx1 = min(
        width_cells - 1,
        int(math.floor(max(x, x + width - 1e-9) / TILE_METERS)),
    )
    cy1 = min(
        height_cells - 1,
        int(math.floor(max(y, y + height - 1e-9) / TILE_METERS)),
    )
    return {
        (cx, cy)
        for cy in range(cy0, cy1 + 1)
        for cx in range(cx0, cx1 + 1)
    }


def _architecture_cells(runtime: dict, width_cells: int, height_cells: int) -> set[tuple[int, int]]:
    layer = runtime.get("visual_layers", {}).get("architecture")
    if not isinstance(layer, dict):
        return set()
    tiles = layer.get("tiles") or []
    layer_width = int(layer.get("width", width_cells))
    layer_height = int(layer.get("height", height_cells))
    cells: set[tuple[int, int]] = set()
    for index, value in enumerate(tiles):
        if not value:
            continue
        cx = index % layer_width
        cy = index // layer_width
        if cx < width_cells and cy < height_cells:
            cells.add((cx, cy))
    return cells


def _direction_vector(direction: str | None) -> tuple[float, float] | None:
    if direction is None:
        return None
    key = str(direction).strip().lower()
    return {
        "east": (1.0, 0.0),
        "west": (-1.0, 0.0),
        "north": (0.0, -1.0),
        "south": (0.0, 1.0),
        "right": (1.0, 0.0),
        "left": (-1.0, 0.0),
        "up": (0.0, -1.0),
        "down": (0.0, 1.0),
    }.get(key)


def _nearest_edge_outward(
    x: float, y: float, width_m: float, height_m: float
) -> tuple[float, float]:
    distances = [
        (x, (-1.0, 0.0)),
        (width_m - x, (1.0, 0.0)),
        (y, (0.0, -1.0)),
        (height_m - y, (0.0, 1.0)),
    ]
    return min(distances, key=lambda item: item[0])[1]


def load_prefab(source: str | Path) -> FacilityPrefab:
    path = Path(source)
    if path.suffix.lower() == ".tmj":
        root = _repo_root()
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from maptool.compiler import compile_module

        runtime = compile_module(path)
    elif path.suffix.lower() == ".json":
        runtime = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"unsupported facility prefab: {path}")

    if runtime.get("format") != "revenant_lost.module":
        raise ValueError(f"not a Revenant module: {path}")

    module = runtime.get("module", {})
    facility = runtime.get("facility") or {}
    bounds = runtime.get("bounds", {})
    width_m = float(bounds.get("width_m", 0.0))
    height_m = float(bounds.get("height_m", 0.0))
    if width_m <= 0.0 or height_m <= 0.0:
        raise ValueError(f"facility has invalid bounds: {path}")
    width_cells = max(1, int(round(width_m / TILE_METERS)))
    height_cells = max(1, int(round(height_m / TILE_METERS)))

    navigation = runtime.get("navigation", {})
    geometry = runtime.get("geometry", {})
    grid = navigation.get("grid", {})

    blocked: set[tuple[int, int]] = set()
    for cell in grid.get("blocked_cells", []):
        if isinstance(cell, (list, tuple)) and len(cell) >= 2:
            cx, cy = int(cell[0]), int(cell[1])
            if 0 <= cx < width_cells and 0 <= cy < height_cells:
                blocked.add((cx, cy))
    for rect in geometry.get("collision_rects", []):
        blocked.update(_rect_cells(rect, width_cells, height_cells))
    for rect in geometry.get("wall_edges", []):
        blocked.update(_rect_cells(rect, width_cells, height_cells))

    architecture = _architecture_cells(runtime, width_cells, height_cells)
    if not blocked and architecture:
        # The author may not have painted collision yet.  A wall-looking tile is
        # safer as solid than as a walk-through wall.
        blocked.update(architecture)

    occluder: set[tuple[int, int]] = set(blocked)
    for rect in geometry.get("occlusion_rects", []):
        occluder.update(_rect_cells(rect, width_cells, height_cells))
    if architecture:
        occluder.update(architecture)

    doors: list[dict] = []
    for item in navigation.get("dynamic_portals", []):
        if item.get("kind") != "door":
            continue
        rect = item.get("geometry", {})
        x = float(rect.get("x", 0.0))
        y = float(rect.get("y", 0.0))
        width = float(rect.get("width", 0.0))
        height = float(rect.get("height", 0.0))
        center = (x + width * 0.5, y + height * 0.5)
        outward = _nearest_edge_outward(center[0], center[1], width_m, height_m)
        doors.append(
            {
                "id": str(item.get("id", len(doors))),
                "x": x,
                "y": y,
                "width": width,
                "height": height,
                "center": center,
                "outward": outward,
            }
        )

    connectors: list[dict] = []
    for item in navigation.get("connectors", []):
        geometry_item = item.get("geometry", {})
        x = float(geometry_item.get("x", 0.0))
        y = float(geometry_item.get("y", 0.0))
        properties = item.get("properties", {})
        outward = _direction_vector(properties.get("direction"))
        if outward is None:
            outward = _nearest_edge_outward(x, y, width_m, height_m)
        connectors.append(
            {
                "id": str(item.get("id", len(connectors))),
                "x": x,
                "y": y,
                "outward": outward,
                "connector_type": str(properties.get("connector_type", "pedestrian")),
            }
        )

    nav_nodes: list[dict] = []
    nav_edges: list[dict] = []
    graph = navigation.get("graph", {})
    for node in graph.get("nodes", []):
        position = node.get("position", {})
        nav_nodes.append(
            {
                "id": str(node.get("id", len(nav_nodes))),
                "kind": str(node.get("kind", "corridor")),
                "x": float(position.get("x", 0.0)),
                "y": float(position.get("y", 0.0)),
            }
        )
    for edge in graph.get("edges", []):
        nav_edges.append(
            {
                "from": str(edge.get("from", "")),
                "to": str(edge.get("to", "")),
                "mode": str(edge.get("mode", "normal")),
                "cost": float(edge.get("cost", 1.0)),
                "width_m": float(edge.get("width_m", 1.0)),
                "one_way": bool(edge.get("one_way", False)),
            }
        )

    if connectors:
        connector = connectors[0]
        entrance_local = (connector["x"], connector["y"])
        outward_local = connector["outward"]
    elif doors:
        door = doors[0]
        entrance_local = door["center"]
        outward_local = door["outward"]
    else:
        entrance_local = (width_m * 0.5, height_m)
        outward_local = (0.0, 1.0)

    return FacilityPrefab(
        id=str(module.get("id", path.stem)),
        facility_type=str(facility.get("facility_type", "generic")),
        width_m=width_m,
        height_m=height_m,
        width_cells=width_cells,
        height_cells=height_cells,
        blocked=blocked,
        occluder=occluder,
        doors=doors,
        connectors=connectors,
        nav_nodes=nav_nodes,
        nav_edges=nav_edges,
        entrance_local=entrance_local,
        outward_local=outward_local,
        source=str(path),
    )


def make_placeholder_prefab() -> FacilityPrefab:
    width_m, height_m = 10.0, 7.0
    width_cells = int(width_m / TILE_METERS)
    height_cells = int(height_m / TILE_METERS)
    blocked: set[tuple[int, int]] = set()
    for x in range(width_cells):
        blocked.add((x, 0))
        blocked.add((x, height_cells - 1))
    for y in range(height_cells):
        blocked.add((0, y))
        blocked.add((width_cells - 1, y))
    door_x = width_cells // 2
    for x in (door_x - 1, door_x, door_x + 1):
        blocked.discard((x, height_cells - 1))
    return FacilityPrefab(
        id="placeholder_facility",
        facility_type="garrison",
        width_m=width_m,
        height_m=height_m,
        width_cells=width_cells,
        height_cells=height_cells,
        blocked=blocked,
        occluder=set(blocked),
        doors=[
            {
                "id": "main",
                "x": door_x * TILE_METERS - 0.75,
                "y": height_m - 0.25,
                "width": 1.5,
                "height": 0.5,
                "center": (door_x * TILE_METERS, height_m),
                "outward": (0.0, 1.0),
            }
        ],
        nav_nodes=[{"id": "center", "kind": "room", "x": width_m * 0.5, "y": height_m * 0.5}],
        entrance_local=(door_x * TILE_METERS, height_m),
        outward_local=(0.0, 1.0),
        source="<procedural>",
    )


def _rotated_dims(
    prefab: FacilityPrefab, rotation: int
) -> tuple[int, int]:
    if rotation in (90, 270):
        return prefab.height_cells, prefab.width_cells
    return prefab.width_cells, prefab.height_cells


def _rotate_cell(
    lx: int, ly: int, width_cells: int, height_cells: int, rotation: int
) -> tuple[int, int]:
    rotation %= 360
    if rotation == 90:
        return height_cells - 1 - ly, lx
    if rotation == 180:
        return width_cells - 1 - lx, height_cells - 1 - ly
    if rotation == 270:
        return ly, width_cells - 1 - lx
    return lx, ly


def _rotate_point(
    x: float, y: float, width_m: float, height_m: float, rotation: int
) -> tuple[float, float]:
    rotation %= 360
    if rotation == 90:
        return height_m - y, x
    if rotation == 180:
        return width_m - x, height_m - y
    if rotation == 270:
        return y, width_m - x
    return x, y


def _rotate_vector(x: float, y: float, rotation: int) -> tuple[float, float]:
    rotation %= 360
    if rotation == 90:
        return -y, x
    if rotation == 180:
        return -x, -y
    if rotation == 270:
        return y, -x
    return x, y


def _can_place(
    world: WorldMap,
    prefab: FacilityPrefab,
    origin_m: tuple[float, float],
    rotation: int,
    existing: Iterable[FacilityPlacement],
    margin_m: float = DEFAULT_FACILITY_MARGIN_M,
) -> bool:
    width_cells, height_cells = _rotated_dims(prefab, rotation)
    width_m = width_cells * TILE_METERS
    height_m = height_cells * TILE_METERS
    x, y = origin_m
    if x < 2.0 or y < 2.0:
        return False
    if x + width_m > world.width_m - 2.0 or y + height_m > world.height_m - 2.0:
        return False

    for other in existing:
        if (
            x < other.x + other.width_m + margin_m
            and other.x < x + width_m + margin_m
            and y < other.y + other.height_m + margin_m
            and other.y < y + height_m + margin_m
        ):
            return False

    spawn_x, spawn_y = world.spawn
    closest_x = max(x, min(spawn_x, x + width_m))
    closest_y = max(y, min(spawn_y, y + height_m))
    if math.hypot(spawn_x - closest_x, spawn_y - closest_y) < 12.0:
        return False
    if world.gate is not None:
        gate_x, gate_y = world.gate
        closest_x = max(x, min(gate_x, x + width_m))
        closest_y = max(y, min(gate_y, y + height_m))
        if math.hypot(gate_x - closest_x, gate_y - closest_y) < 8.0:
            return False

    origin_cell = (
        int(round(x / TILE_METERS)),
        int(round(y / TILE_METERS)),
    )
    for ly in range(height_cells):
        for lx in range(width_cells):
            rx, ry = _rotate_cell(lx, ly, prefab.width_cells, prefab.height_cells, rotation)
            cx, cy = origin_cell[0] + rx, origin_cell[1] + ry
            if not world.grid.in_bounds(cx, cy):
                return False
            index = world.grid.index(cx, cy)
            if world.grid.owner[index] != 0:
                return False
            if world.grid.flags[index] & int(Flag.BORDER):
                return False
    return True


def place_prefab(
    world: WorldMap,
    prefab: FacilityPrefab,
    origin_m: tuple[float, float],
    rotation: int,
    owner_id: int,
    facility_id: str,
) -> FacilityPlacement:
    grid = world.grid
    origin_cell = (
        int(round(origin_m[0] / TILE_METERS)),
        int(round(origin_m[1] / TILE_METERS)),
    )
    width_cells, height_cells = _rotated_dims(prefab, rotation)
    occluder_cells: list[tuple[int, int]] = []
    blocked_cells: list[tuple[int, int]] = []

    for ly in range(prefab.height_cells):
        for lx in range(prefab.width_cells):
            rx, ry = _rotate_cell(lx, ly, prefab.width_cells, prefab.height_cells, rotation)
            cx, cy = origin_cell[0] + rx, origin_cell[1] + ry
            if not grid.in_bounds(cx, cy):
                continue
            index = grid.index(cx, cy)
            grid.owner[index] = owner_id
            grid.terrain[index] = int(Terrain.FACILITY_FLOOR)
            grid.flags[index] = (
                (grid.flags[index] | int(Flag.FACILITY | Flag.WALKABLE))
                & ~int(Flag.BLOCKED)
            ) & 0xFF
            grid.occlusion[index] = 0
            if (lx, ly) in prefab.blocked:
                grid.terrain[index] = int(Terrain.FACILITY_WALL)
                grid.flags[index] = (
                    (grid.flags[index] | int(Flag.FACILITY | Flag.BLOCKED))
                    & ~int(Flag.WALKABLE)
                ) & 0xFF
                grid.occlusion[index] = 1
                blocked_cells.append((cx, cy))
                if (lx, ly) in prefab.occluder:
                    occluder_cells.append((cx, cy))

    # Doors are openings.  Clear a small circle around the authored door center
    # so the hand-authored interior connects to the exterior apron.
    for door in prefab.doors:
        center_x, center_y = door["center"]
        rotated = _rotate_point(
            center_x, center_y, prefab.width_m, prefab.height_m, rotation
        )
        world_x = origin_m[0] + rotated[0]
        world_y = origin_m[1] + rotated[1]
        cx0 = max(0, int(math.floor((world_x - 0.75) / TILE_METERS)))
        cy0 = max(0, int(math.floor((world_y - 0.75) / TILE_METERS)))
        cx1 = min(grid.width - 1, int(math.ceil((world_x + 0.75) / TILE_METERS)))
        cy1 = min(grid.height - 1, int(math.ceil((world_y + 0.75) / TILE_METERS)))
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                if math.hypot(
                    (cx + 0.5) * TILE_METERS - world_x,
                    (cy + 0.5) * TILE_METERS - world_y,
                ) > 0.75:
                    continue
                index = grid.index(cx, cy)
                if grid.owner[index] != owner_id:
                    continue
                grid.terrain[index] = int(Terrain.FACILITY_FLOOR)
                grid.flags[index] = (
                    (grid.flags[index] | int(Flag.FACILITY | Flag.WALKABLE))
                    & ~int(Flag.BLOCKED)
                ) & 0xFF
                grid.occlusion[index] = 0

    entrance_local = prefab.entrance_local
    rotated_entrance = _rotate_point(
        entrance_local[0],
        entrance_local[1],
        prefab.width_m,
        prefab.height_m,
        rotation,
    )
    entrance = (origin_m[0] + rotated_entrance[0], origin_m[1] + rotated_entrance[1])
    outward = _rotate_vector(prefab.outward_local[0], prefab.outward_local[1], rotation)

    # Exterior apron: a 4m deep, 4m wide walkable landing outside the entrance.
    apron_x = entrance[0] + outward[0] * (2.0 - 0.5)
    apron_y = entrance[1] + outward[1] * (2.0 - 0.5)
    perpendicular = (-outward[1], outward[0])
    half_width = 2.0
    x0 = min(apron_x - perpendicular[0] * half_width, apron_x + perpendicular[0] * half_width)
    x1 = max(apron_x - perpendicular[0] * half_width, apron_x + perpendicular[0] * half_width)
    y0 = min(apron_y - perpendicular[1] * half_width, apron_y + perpendicular[1] * half_width)
    y1 = max(apron_y - perpendicular[1] * half_width, apron_y + perpendicular[1] * half_width)
    for cx, cy in grid.iter_cells_in_rect_m(x0 - 2.0, y0 - 2.0, x1 + 2.0, y1 + 2.0):
        index = grid.index(cx, cy)
        if grid.owner[index] != 0:
            continue
        grid.flags[index] = (
            (grid.flags[index] | int(Flag.WALKABLE | Flag.APRON))
            & ~int(Flag.BLOCKED | Flag.BORDER)
        ) & 0xFF
        grid.terrain[index] = int(Terrain.DIRT)
        grid.occlusion[index] = 0

    nav_nodes: list[dict] = []
    for node in prefab.nav_nodes:
        rotated = _rotate_point(
            node["x"], node["y"], prefab.width_m, prefab.height_m, rotation
        )
        nav_nodes.append(
            {
                "id": node["id"],
                "kind": node["kind"],
                "x": origin_m[0] + rotated[0],
                "y": origin_m[1] + rotated[1],
            }
        )

    occluder_cells = [
        (cx, cy)
        for cx, cy in occluder_cells
        if grid.occlusion[grid.index(cx, cy)] == 1
    ]

    return FacilityPlacement(
        id=facility_id,
        prefab_id=prefab.id,
        facility_type=prefab.facility_type,
        x=origin_m[0],
        y=origin_m[1],
        width_m=width_cells * TILE_METERS,
        height_m=height_cells * TILE_METERS,
        rotation=rotation,
        entrance=entrance,
        outward=outward,
        connector_type=prefab.connectors[0]["connector_type"]
        if prefab.connectors
        else "pedestrian",
        doors=prefab.doors,
        nav_nodes=nav_nodes,
        nav_edges=list(prefab.nav_edges),
        occluder_cells=occluder_cells,
        blocked_cells=blocked_cells,
    )


def place_facilities(
    world: WorldMap,
    prefabs: list[FacilityPrefab],
    rng: random.Random,
    count: int = 2,
) -> list[FacilityPlacement]:
    if not prefabs:
        prefabs = [make_placeholder_prefab()]
    placements: list[FacilityPlacement] = []
    margin = 10.0
    for index in range(max(1, count)):
        prefab = prefabs[index % len(prefabs)]
        width_cells, height_cells = _rotated_dims(prefab, 0)
        placed: FacilityPlacement | None = None
        for _ in range(300):
            rotation = rng.choice((0, 90, 180, 270))
            width_cells, height_cells = _rotated_dims(prefab, rotation)
            width_m = width_cells * TILE_METERS
            height_m = height_cells * TILE_METERS
            max_x = world.width_m - width_m - margin
            max_y = world.height_m - height_m - margin
            if max_x <= margin or max_y <= margin:
                continue
            x = rng.uniform(margin, max_x)
            y = rng.uniform(margin, max_y)
            if _can_place(world, prefab, (x, y), rotation, placements):
                placed = place_prefab(
                    world,
                    prefab,
                    (x, y),
                    rotation,
                    owner_id=index + 1,
                    facility_id=f"{prefab.id}_{index + 1}",
                )
                break
        if placed is not None:
            placements.append(placed)
    world.facilities = placements

    if not placements:
        fallback = make_placeholder_prefab()
        placed = place_prefab(
            world,
            fallback,
            (
                max(4.0, world.width_m * 0.5 - fallback.width_m * 0.5),
                max(4.0, world.height_m * 0.5 - fallback.height_m * 0.5),
            ),
            0,
            owner_id=99,
            facility_id="fallback_facility",
        )
        world.facilities = [placed]
        placements = [placed]
    return placements
