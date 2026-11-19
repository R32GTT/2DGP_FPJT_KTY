"""지역별 설정과 방향별 연결점을 먼저 정한다. 시설 배치는 지역 생성기에 맡긴다."""

import math
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
    rng = random.Random(f"world-layout:{version}:{seed}")
    extents = [REGION_PROFILES[size][0] for size in sizes]
    for index in range(1, count):
        for _ in range(256):
            anchor = rng.randrange(index)
            angle = rng.uniform(-.7, .7) if index == 1 else rng.uniform(-math.pi, math.pi)
            direction = math.cos(angle), math.sin(angle)
            distance = ((extents[index] + extents[anchor]) / 2 + REGION_GAP) / max(map(abs, direction)) * rng.uniform(1.05, 1.4)
            point = [round(regions[anchor]['origin'][axis] + direction[axis] * distance, 3) for axis in (0, 1)]
            if all(math.hypot(*(max(abs(point[axis] - regions[other]['origin'][axis])
                                   - (extents[index] + extents[other]) / 2, 0) for axis in (0, 1))) >= REGION_GAP
                   for other in range(index)):
                regions[index]['origin'] = point
                break
        else:
            raise ValueError("지역 간격을 확보할 수 없는 월드 배치")
    edges = sorted((math.dist(regions[a]['origin'], regions[b]['origin']), a, b)
                   for a in range(count) for b in range(a + 1, count))
    groups, selected = list(range(count)), []
    for distance, a, b in edges:
        if groups[a] != groups[b]:
            previous = groups[b]
            groups = [groups[a] if group == previous else group for group in groups]
            selected.append((a, b))

    def detour(a, b, visited=()):
        if a == b:
            return 0.
        return min((math.dist(regions[a]['origin'], regions[c]['origin']) + detour(c, b, (*visited, a))
                    for x, y in selected for c in ([y] if x == a else [x] if y == a else []) if c not in visited), default=math.inf)

    for distance, a, b in edges:
        room = all(sum(index in edge for edge in selected) < (3 if index in (0, count - 1) else 4) for index in (a, b))
        if room and (a, b) not in selected and detour(a, b) > distance * 1.6 and rng.random() < .65:
            selected.append((a, b))
            break

    def add_entry(index, direction):
        half = extents[index] / 2
        for side, (axis, sign) in sorted(SIDES.items(), key=lambda item: -item[1][1] * direction[item[1][0]]):
            offset = round(max(-half + 18, min(half - 18, direction[1 - axis] / max(abs(direction[axis]), 1e-8) * half)) / 2) * 2
            for shift in (0, -24, 24, -48, 48):
                value = offset + shift
                if abs(value) <= half - 12 and all(entry['side'] != side or abs(entry['offset'] - value) >= 18
                                                  for entry in regions[index]['entries']):
                    regions[index]['entries'].append({'side': side, 'offset': value})
                    return len(regions[index]['entries']) - 1
        raise ValueError("지역 연결점 간격 부족")

    add_entry(0, (-1, 0))
    links = []
    for index, (a, b) in enumerate(sorted(selected)):
        direction = [regions[b]['origin'][axis] - regions[a]['origin'][axis] for axis in (0, 1)]
        links.append({'id': f'portal/{index + 1}', 'source': regions[a]['id'], 'destination': regions[b]['id'],
                      'source_entry': add_entry(a, direction),
                      'destination_entry': add_entry(b, [-value for value in direction])})
    center = [sum(region['origin'][axis] for region in regions) / count for axis in (0, 1)]
    add_entry(count - 1, [regions[-1]['origin'][axis] - center[axis] for axis in (0, 1)] if count > 1 else (1, 0))
    return regions, links
