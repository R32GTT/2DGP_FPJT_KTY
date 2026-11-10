"""Broad zone field and smooth forest density.

Zones are intentionally large.  Their boundary is not a hard threshold for
trees: forest density ramps across a wide band so the map changes gradually.
"""

from __future__ import annotations

from .constants import TILE_METERS, Zone
from .rng import ValueNoise
from .world import WorldMap


def _smoothstep(edge0: float, edge1: float, value: float) -> float:
    if edge1 <= edge0:
        return 1.0 if value >= edge1 else 0.0
    t = max(0.0, min(1.0, (value - edge0) / (edge1 - edge0)))
    return t * t * (3.0 - 2.0 * t)


def generate_zones(world: WorldMap, seed: int) -> None:
    grid = world.grid
    zone_noise = ValueNoise(seed ^ 0x20E0A11)
    detail_noise = ValueNoise(seed ^ 0x20E0A12)

    for cy in range(grid.height):
        y = cy * TILE_METERS
        for cx in range(grid.width):
            x = cx * TILE_METERS
            broad = zone_noise.fbm(x * 0.010, y * 0.010, 3)
            detail = detail_noise.fbm(x * 0.060, y * 0.060, 3)

            if broad >= 0.67:
                zone = Zone.ROCKY
            elif broad >= 0.47:
                zone = Zone.FOREST
            else:
                zone = Zone.MEADOW

            forest = _smoothstep(0.44, 0.62, broad)
            forest *= 0.70 + 0.30 * detail
            # Trees fade out into the rocky zone instead of stopping on a line.
            forest *= 1.0 - 0.75 * _smoothstep(0.66, 0.76, broad)

            index = grid.index(cx, cy)
            grid.zone[index] = int(zone)
            grid.forest_density[index] = int(max(0.0, min(1.0, forest)) * 255.0)
