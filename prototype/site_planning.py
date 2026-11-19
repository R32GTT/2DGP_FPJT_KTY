"""지역 등급별 시설 예약 영역과 회전된 차량 진입점을 선택한다."""

import random

from .geometry import Rect
from .prefabs import Placement
from .road_generation import ROAD_WIDTH


# 실제 프리팹 제작 전의 임시 점유 치수·검증 맵 크기다.
REGION_PROFILES = {
    "small": (128, 1, 1, 0), "medium": (192, 2, 2, 1),
    "large": (256, 2, 3, 2), "xlarge": (320, 3, 4, 3),
}
IMPORTANCE = ("normal", "important", "core")
SITE_SIZES = {
    "safehouse": ("안전가옥", (12, 8), (12, 8), (12, 8), (12, 8)),
    "hq": ("HQ", (24, 20), (28, 24), (32, 28), (36, 28)),
    "comms": ("통신시설", (16, 12), (20, 12), (20, 16), (24, 16)),
    "warehouse": ("물류창고", (24, 20), (28, 24), (36, 28), (44, 32)),
    "garrison": ("주둔지", (20, 16), (24, 20), (28, 24), (32, 24)),
    "sam": ("대공시설", (20, 16), (24, 20), (28, 24), (32, 24)),
}


def plan_sites(seed, version, region_size, importance, attempt=0):
    if region_size not in REGION_PROFILES or importance not in IMPORTANCE:
        raise ValueError("지원하지 않는 지역 크기 또는 중요도")
    extent, comms, warehouses, garrisons = REGION_PROFILES[region_size]
    rng = random.Random(f"sites:{version}:{seed}:{attempt}")
    if region_size == "medium":
        comms = rng.randint(1, 2)
    kinds = ["safehouse", "hq"] + ["comms"] * comms + ["warehouse"] * warehouses + ["garrison"] * garrisons
    if region_size in {"large", "xlarge"} or importance != "normal":
        kinds.append("sam")
    size_index = tuple(REGION_PROFILES).index(region_size)
    kinds.sort(key=lambda kind: SITE_SIZES[kind][size_index + 1][0] * SITE_SIZES[kind][size_index + 1][1], reverse=True)
    bounds = Rect(-extent / 2, -extent / 2, extent, extent)
    half, sites, reserved = extent // 2, [], [Rect(-8, -8, 16, 16)]
    axis = rng.randrange(2)
    gates, stubs, points = {}, [], {"spawn": (0, 0)}
    for index, side in enumerate((-1, 1)):
        offset = rng.randrange(-half // 2, half // 2, 2)
        gate, approach = [offset, offset], [offset, offset]
        gate[axis], approach[axis] = side * half, side * (half - 4)
        key = f"entry/{index}"
        gates[key], points[key] = tuple(gate), tuple(approach)
        stubs.append((tuple(gate), tuple(approach)))
        reserved.append(Rect(min(gate[0], approach[0]) - 6, min(gate[1], approach[1]) - 6,
                             abs(gate[0] - approach[0]) + 12, abs(gate[1] - approach[1]) + 12))
    for index, kind in enumerate(kinds):
        key = f"{kind}/{index}"
        name, *sizes = SITE_SIZES[kind]
        width, height = sizes[size_index]
        # 중요도별 선택 풀의 임시 확장형. 완성 프리팹의 실측 치수로 교체한다.
        grade = 0 if kind == "safehouse" else IMPORTANCE.index(importance)
        width += 4 * rng.randint(grade, grade + 1)
        for _ in range(160):
            placement = Placement(key, (rng.randrange(-half + 12, half - 12, 2),
                                        rng.randrange(-half + 12, half - 12, 2)), rng.randrange(4))
            area = Rect(*placement.rect((-width / 2 - 6, -height / 2 - 6, width + 12, height + 24)))
            if (area.x < bounds.x + 8 or area.y < bounds.y + 8 or area.right > bounds.right - 8
                    or area.bottom > bounds.bottom - 8 or any(area.overlaps(other) for other in reserved)):
                continue
            footprint = placement.rect((-width / 2, -height / 2, width, height))
            gate, approach = placement.point((0, height / 2)), placement.point((0, height / 2 + 12))
            direction = ((0, 1), (-1, 0), (0, -1), (1, 0))[placement.turns]
            sites.append({"id": key, "kind": kind, "name": name, "rect": footprint,
                          "reserved": [area.x, area.y, area.w, area.h], "rotation": placement.turns * 90,
                          "variant_id": f"{kind}/{region_size}/{importance}/{width}x{height}",
                          "importance": importance, "placeholder": True,
                          "connector": {"position": list(gate), "direction": list(direction), "width_m": ROAD_WIDTH,
                                        "approach": list(approach)}})
            points[key], gates[key] = approach, gate
            stubs.append((gate, approach))
            reserved.append(area)
            break
        else:
            raise ValueError(f"시설 배치 후보 부족: {key}")
    return bounds, sites, points, gates, stubs
