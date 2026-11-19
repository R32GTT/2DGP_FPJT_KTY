"""지역별 설정과 방향별 연결점을 먼저 정한다. 시설 배치는 지역 생성기에 맡긴다."""

import random

from .site_planning import IMPORTANCE, REGION_PROFILES, SIDES


REGION_GAP = 32


def plan_world(seed, version, count, sizes, importances):
    if (len(sizes) != count or len(importances) != count
            or any(size not in REGION_PROFILES for size in sizes)
            or any(importance not in IMPORTANCE for importance in importances)):
        raise ValueError("지역 수와 지역별 크기·중요도 설정을 확인하세요")
    regions = [{"id": f"region/{index + 1}", "origin": [0, 0], "entries": [],
                "region_size": size, "importance": importance}
               for index, (size, importance) in enumerate(zip(sizes, importances))]
    pitch = max(REGION_PROFILES[size][0] for size in sizes) + REGION_GAP
    rng = random.Random(f"world-layout:{version}:{seed}")
    directions = ["east", "north", "south"]
    rotation = 0
    compass = ["east", "south", "west", "north"]
    if count > 2:
        rng.shuffle(directions)
        rotation = rng.randrange(4)

    def rotate(side):
        return compass[(compass.index(side) + rotation) % 4]

    def add_entry(region, side, offset=None):
        if offset is None:
            half = REGION_PROFILES[region['region_size']][0] // 4
            offset = random.Random(f"world-port:{version}:{seed}:{region['id']}:{side}").randrange(-half, half, 2)
        region['entries'].append({"side": side, "offset": offset})
        return len(region['entries']) - 1

    add_entry(regions[0], rotate("west"))
    links = []
    for index, region in enumerate(regions[1:]):
        side = rotate(directions[index])
        axis, sign = SIDES[side]
        region['origin'][axis] = sign * pitch
        limit = min(REGION_PROFILES[sizes[0]][0], REGION_PROFILES[sizes[index + 1]][0]) // 4
        offset = random.Random(f"world-port:{version}:{seed}:region/1:{side}").randrange(-limit, limit, 2)
        source_entry = add_entry(regions[0], side, offset)
        add_entry(region, compass[(compass.index(side) + 2) % 4], offset)
        links.append({"id": f"portal/{index + 1}", "source": regions[0]['id'], "destination": region['id'],
                      "source_entry": source_entry, "destination_entry": 0})
    last_side = rotate(directions[count - 2]) if count > 1 else rotate("east")
    add_entry(regions[-1], last_side)
    return regions, links
