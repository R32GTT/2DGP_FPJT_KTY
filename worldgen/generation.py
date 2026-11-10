"""Top-level world generation pipeline."""

from __future__ import annotations

import random
import time
from pathlib import Path

from .constants import DEFAULT_ROAD_WIDTH_M, DEFAULT_WORLD_METERS
from .facility import (
    FacilityPrefab,
    load_prefab,
    make_placeholder_prefab,
    place_facilities,
)
from .grid import WorldGrid
from .nav import validate_world
from .roads import build_road_network
from .terrain import generate_base
from .trees import generate_trees
from .world import WorldMap
from .zones import generate_zones


def generate_world(
    seed: int = 2026,
    width_m: float = DEFAULT_WORLD_METERS,
    height_m: float = DEFAULT_WORLD_METERS,
    *,
    prefab_sources: list[str | Path] | None = None,
    facility_count: int = 2,
    road_width_m: float = DEFAULT_ROAD_WIDTH_M,
) -> WorldMap:
    """Generate one finite region.

    This is intentionally one region, not a streamed world.  The same seed and
    generation version must produce the same result.
    """

    started = time.perf_counter()
    grid = WorldGrid(width_m, height_m)
    world = WorldMap(
        seed=int(seed),
        width_m=float(width_m),
        height_m=float(height_m),
        grid=grid,
        spawn=(width_m * 0.5, height_m * 0.5),
        road_width_m=road_width_m,
    )

    generate_zones(world, seed)
    generate_base(world, seed)

    if prefab_sources:
        prefabs: list[FacilityPrefab] = [
            load_prefab(source) for source in prefab_sources
        ]
    else:
        prefabs = [make_placeholder_prefab()]

    rng = random.Random(int(seed) ^ 0x5EEDBEEF)
    place_facilities(world, prefabs, rng, count=max(1, facility_count))
    build_road_network(world, rng, road_width_m=road_width_m)
    generate_trees(world, seed)

    world.validation = validate_world(world)
    world.generation_ms = (time.perf_counter() - started) * 1000.0
    return world
