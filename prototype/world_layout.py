"""월드에 지역 부지를 흩어 놓는다."""

import math
import random

from .site_planning import IMPORTANCE, REGION_PROFILES, LayoutUnavailable


REGION_GAP = 32


def plan_world(seed, version, count, sizes, importances, *, basic=False):
    if (len(sizes) != count or len(importances) != count
            or any(size not in REGION_PROFILES for size in sizes)
            or any(importance not in IMPORTANCE for importance in importances)):
        raise ValueError("지역 수와 지역별 크기·중요도 설정을 확인하세요")
    regions = [{"id": f"region/{index + 1}", "origin": [0, 0],
                "region_size": size, "importance": importance}
               for index, (size, importance) in enumerate(zip(sizes, importances))]
    rng = random.Random(f"world-layout:{version}:{seed}")
    extents = [REGION_PROFILES[size][0] for size in sizes]
    if basic:
        for index, region in enumerate(regions):
            if index:
                region["origin"][0] = (regions[index - 1]["origin"][0]
                                       + (extents[index - 1] + extents[index]) / 2 + REGION_GAP)
        return regions
    for index in range(1, count):
        for _ in range(256):
            anchor = rng.randrange(index)
            angle = rng.uniform(-.7, .7) if index == 1 else rng.uniform(-math.pi, math.pi)
            direction = math.cos(angle), math.sin(angle)
            distance = ((extents[index] + extents[anchor]) / 2 + REGION_GAP) / max(map(abs, direction)) * rng.uniform(1.05, 1.4)
            point = [round((regions[anchor]['origin'][axis] + direction[axis] * distance) / 2) * 2 for axis in (0, 1)]
            if all(math.hypot(*(max(abs(point[axis] - regions[other]['origin'][axis])
                                   - (extents[index] + extents[other]) / 2, 0) for axis in (0, 1))) >= REGION_GAP
                   for other in range(index)):
                regions[index]['origin'] = point
                break
        else:
            raise LayoutUnavailable("지역 간격을 확보할 수 없는 월드 배치")
    return regions
