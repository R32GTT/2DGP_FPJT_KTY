"""시설별 진입점을 월드 전역의 도로망으로 연결한다."""

import math
import random

import numpy as np
from scipy.ndimage import map_coordinates

from .generation import _density
from .road_generation import build_basic_roads, build_roads


ROAD_GRID_TARGET_CELLS = 48_000
ROAD_DENSITY_MAX_EXTENT = 512


def build_world_roads(seed, bounds, sites, *, basic=False, large_world=False):
    hqs = [site for site in sites if site["kind"] == "hq"]
    gates = {"entry/0": (bounds.x, 0 if basic else hqs[0]["position"][1]),
             "entry/1": (bounds.right, 0 if basic else hqs[-1]["position"][1])}
    gates.update((site["id"], site["connector"]["position"]) for site in sites)
    if basic:
        return build_basic_roads(bounds, sites, gates)
    extent = int(max(bounds.w, bounds.h))
    step = 4 if extent > 768 else 2
    if large_world:
        step = max(step, math.ceil(math.sqrt(bounds.w * bounds.h / ROAD_GRID_TARGET_CELLS)))
    inset = max(12, 3 * step)
    points = {"entry/0": (bounds.x + inset, gates["entry/0"][1]),
              "entry/1": (bounds.right - inset, gates["entry/1"][1])}
    points.update((site["id"], site["connector"]["approach"]) for site in sites)
    stubs = [(gate, points[key]) for key, gate in gates.items()]
    rows, columns = np.indices((int(bounds.h) // step + 1, int(bounds.w) // step + 1), dtype=float)
    coords = [rows * (step / 2) + (extent - bounds.h) / 4, columns * (step / 2) + (extent - bounds.w) / 4]
    density_extent = min(extent, ROAD_DENSITY_MAX_EXTENT) if large_world else extent
    if density_extent != extent:
        coords = [coord * (density_extent / extent) for coord in coords]
    woods = map_coordinates(_density(seed, "world-vegetation", density_extent), coords, order=1, prefilter=False)
    soil = map_coordinates(_density(seed, "world-soil", density_extent), coords, order=1, prefilter=False)
    trunks = ["entry/0", *(site["id"] for site in hqs), "entry/1"]
    return build_roads(bounds, sites, points, gates, stubs, 1 + 3 * woods + .5 * (1 - soil),
                       random.Random(seed), trunks=trunks, step=step)
