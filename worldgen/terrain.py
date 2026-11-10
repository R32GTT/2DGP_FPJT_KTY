"""Base terrain painting for a finite world chunk."""

from __future__ import annotations

from .constants import Flag, Terrain
from .rng import ValueNoise
from .world import WorldMap


def generate_base(world: WorldMap, seed: int) -> None:
    grid = world.grid
    width = world.width_m
    height = world.height_m

    # Natural boundary.  A real world will use cliffs/water; this is the MVP
    # equivalent and keeps generated nav data inside the playable rectangle.
    border = 1.0
    grid.paint_rect_m(
        0.0,
        0.0,
        width,
        border,
        add_flags=int(Flag.BLOCKED | Flag.BORDER),
        remove_flags=int(Flag.WALKABLE),
    )
    grid.paint_rect_m(
        0.0,
        height - border,
        width,
        height,
        add_flags=int(Flag.BLOCKED | Flag.BORDER),
        remove_flags=int(Flag.WALKABLE),
    )
    grid.paint_rect_m(
        0.0,
        0.0,
        border,
        height,
        add_flags=int(Flag.BLOCKED | Flag.BORDER),
        remove_flags=int(Flag.WALKABLE),
    )
    grid.paint_rect_m(
        width - border,
        0.0,
        width,
        height,
        add_flags=int(Flag.BLOCKED | Flag.BORDER),
        remove_flags=int(Flag.WALKABLE),
    )

    noise = ValueNoise(seed ^ 0x1A2B3C4D)
    for cy in range(grid.height):
        for cx in range(grid.width):
            index = grid.index(cx, cy)
            n = noise.fbm(cx * 0.035, cy * 0.035, 4)
            forest = grid.forest_density[index] / 255.0
            if n > 0.73 and forest < 0.55:
                grid.terrain[index] = int(Terrain.ROCK)
            elif n > 0.58 or (forest > 0.35 and n > 0.50):
                grid.terrain[index] = int(Terrain.DIRT)

    world.spawn = (width * 0.5, height * 0.5)
    grid.paint_circle_m(
        world.spawn[0],
        world.spawn[1],
        5.0,
        add_flags=int(Flag.WALKABLE),
        remove_flags=int(Flag.BLOCKED | Flag.BORDER),
        terrain=Terrain.DIRT,
    )
    grid.add_flag(*grid.world_to_cell(*world.spawn), Flag.SPAWN)

    # A road gate on the east edge.  It is also the future region connector.
    world.gate = (width - 4.0, height * 0.5)
    grid.paint_rect_m(
        width - 5.0,
        height * 0.5 - 3.0,
        width,
        height * 0.5 + 3.0,
        add_flags=int(Flag.WALKABLE),
        remove_flags=int(Flag.BLOCKED | Flag.BORDER),
        terrain=Terrain.DIRT,
    )
