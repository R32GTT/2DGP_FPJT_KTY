"""지역 사이에도 연속된 지면과 숲을 생성하고 연결 도로를 비워 둔다."""

import math
import random

from shapely import box
from shapely.prepared import prep

from .generation import FOREST_DENSITY, FOREST_TREE_GAP, _density, _sample
from .geometry import Rect
from .road_generation import road_surface


def between_regions(seed, bounds, regions, network):
    layers, colliders, occupied = [], [], {}
    roads = prep(road_surface(network).buffer(1.8))
    areas = [Rect(*region['bounds']) for region in regions]
    field = _density(seed, 'world-vegetation', math.ceil(max(bounds.w, bounds.h) / 2) * 2)
    cx, cy = bounds.x + bounds.w / 2, bounds.y + bounds.h / 2
    for row in range(math.ceil(bounds.h / 16)):
        for col in range(math.ceil(bounds.w / 16)):
            x, y = bounds.x + col * 16, bounds.y + row * 16
            w, h = min(16, bounds.right - x), min(16, bounds.bottom - y)
            if any(area.x <= x and x + w <= area.right and area.y <= y and y + h <= area.bottom for area in areas):
                continue
            key = f"between/{row}/{col}"
            layers.append({"id": key, "rect": [x, y, w, h], "style": "grass", "image": "",
                           "color": [45, 65, 48], "variant": 0})
            if min(w, h) < 2:
                continue
            rng = random.Random(f"{seed}:{key}")
            for index in range(20):
                px, py = rng.uniform(x + .75, x + w - .75), rng.uniform(y + .75, y + h - .75)
                density = _sample(field, px - cx, py - cy)
                if rng.random() > .03 + .9 * density ** 2:
                    continue
                rect = Rect(px - .45, py - .45, .9, .9)
                spacing = rect.expanded(FOREST_TREE_GAP if density >= FOREST_DENSITY else 1.8)
                cell = math.floor(px / 4), math.floor(py / 4)
                nearby = (other for a in range(cell[0] - 1, cell[0] + 2) for b in range(cell[1] - 1, cell[1] + 2)
                          for other in occupied.get((a, b), ()))
                if (roads.intersects(box(rect.x, rect.y, rect.right, rect.bottom))
                        or any(spacing.overlaps(other) for other in nearby)
                        or any(spacing.overlaps(area) for area in areas)):
                    continue
                occupied.setdefault(cell, []).append(rect)
                colliders.append({"id": f"{key}/tree/{index}", "rect": [rect.x, rect.y, rect.w, rect.h],
                                  "kind": "tree", "material": "tree_trunk"})
    return layers, colliders
