"""Forest scattering.

Trees are small movement blockers and large vision blockers.  The trunk blocks
the nav grid, the canopy writes to the occlusion grid.  Dense forest is allowed
as long as the generated world remains connected.
"""

from __future__ import annotations

import math
import random

from .constants import Flag, TILE_METERS
from .nav import compute_clearance, distance_field, repair_reachability
from .rng import ValueNoise
from .world import Tree, WorldMap

_BUCKET_M = 2.4


def _near_existing(
    buckets: dict[tuple[int, int], list[Tree]],
    x: float,
    y: float,
    min_distance_m: float,
) -> bool:
    bx = int(x // _BUCKET_M)
    by = int(y // _BUCKET_M)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            for tree in buckets.get((bx + ox, by + oy), ()):
                if math.hypot(tree.x - x, tree.y - y) < min_distance_m:
                    return True
    return False


def _mark_trunk(
    world: WorldMap, x: float, y: float, radius_m: float
) -> list[tuple[int, int]]:
    grid = world.grid
    cx0 = max(0, int(math.floor((x - radius_m) / TILE_METERS)))
    cy0 = max(0, int(math.floor((y - radius_m) / TILE_METERS)))
    cx1 = min(grid.width - 1, int(math.ceil((x + radius_m) / TILE_METERS)))
    cy1 = min(grid.height - 1, int(math.ceil((y + radius_m) / TILE_METERS)))
    cells: list[tuple[int, int]] = []
    r2 = radius_m * radius_m
    for cy in range(cy0, cy1 + 1):
        py = (cy + 0.5) * TILE_METERS
        for cx in range(cx0, cx1 + 1):
            px = (cx + 0.5) * TILE_METERS
            if (px - x) ** 2 + (py - y) ** 2 > r2:
                continue
            index = grid.index(cx, cy)
            if grid.owner[index] != 0:
                continue
            if grid.flags[index] & int(Flag.ROAD | Flag.BORDER):
                continue
            grid.flags[index] = (
                (grid.flags[index] | int(Flag.BLOCKED | Flag.TREE_TRUNK))
                & ~int(Flag.WALKABLE)
            ) & 0xFF
            cells.append((cx, cy))
    return cells


def generate_trees(world: WorldMap, seed: int) -> None:
    grid = world.grid
    road_cells = (
        (index % grid.width, index // grid.width)
        for index, flag in enumerate(grid.flags)
        if flag & int(Flag.ROAD)
    )
    road_distance = distance_field(grid, road_cells, max_distance=24)

    facility_cells = (
        (index % grid.width, index // grid.width)
        for index, (owner, flag) in enumerate(zip(grid.owner, grid.flags))
        if owner != 0 or (flag & int(Flag.APRON))
    )
    facility_distance = distance_field(grid, facility_cells, max_distance=20)

    detail_noise = ValueNoise(seed ^ 0x51F0E58)
    rng = random.Random(seed ^ 0x7A11E5)
    buckets: dict[tuple[int, int], list[Tree]] = {}
    trees: list[Tree] = []
    tree_id = 0

    y = 4.0
    while y < world.height_m - 4.0:
        x = 4.0
        while x < world.width_m - 4.0:
            tx = x + rng.uniform(-0.38, 0.38)
            ty = y + rng.uniform(-0.38, 0.38)
            cx, cy = grid.world_to_cell(tx, ty)
            index = grid.index(cx, cy)

            density = grid.forest_density[index] / 255.0
            if density < 0.035:
                x += 0.9
                continue
            local = detail_noise.fbm(tx * 0.10, ty * 0.10, 3)
            if rng.random() > min(0.95, density * (0.55 + 0.90 * local)):
                x += 0.9
                continue

            canopy_radius = 1.2 + rng.random() * 1.6
            road_clearance = world.road_width_m * 0.5 + canopy_radius + 0.4
            if road_distance[index] * TILE_METERS < road_clearance:
                x += 0.9
                continue
            if facility_distance[index] < 16:
                x += 0.9
                continue
            if _near_existing(buckets, tx, ty, 2.2):
                x += 0.9
                continue

            trunk_radius = 0.26
            trunk_cells = _mark_trunk(world, tx, ty, trunk_radius)
            if not trunk_cells:
                x += 0.9
                continue
            tree = Tree(
                id=tree_id,
                x=tx,
                y=ty,
                trunk_radius_m=trunk_radius,
                canopy_radius_m=canopy_radius,
                trunk_cells=trunk_cells,
            )
            tree_id += 1
            trees.append(tree)
            buckets.setdefault((int(tx // _BUCKET_M), int(ty // _BUCKET_M)), []).append(tree)
            if len(trees) >= 4000:
                y = world.height_m
                break
            x += 0.9
        y += 0.9

    world.trees = trees
    repair_reachability(world)
    rebuild_occlusion(world)
    compute_clearance(world)


def rebuild_occlusion(world: WorldMap) -> None:
    grid = world.grid
    grid.occlusion = bytearray(grid.size)
    for placement in world.facilities:
        for cx, cy in placement.occluder_cells:
            if grid.in_bounds(cx, cy):
                grid.occlusion[grid.index(cx, cy)] = 1
    for tree in world.trees:
        grid.paint_circle_m(
            tree.x,
            tree.y,
            tree.canopy_radius_m,
            occlusion=1,
        )
