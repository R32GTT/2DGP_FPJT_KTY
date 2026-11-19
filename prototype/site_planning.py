"""지역 등급별 시설 예약 영역과 회전된 차량 진입점을 선택한다."""

import random

from .geometry import Rect
from .prefabs import Placement, rotate
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


def plan_sites(seed, version, region_size, importance, attempt=0, *, catalog=None):
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
    selected = []
    for kind in kinds:
        candidates = [(path, item) for path, item in (catalog or {}).items() if item["kind"] == kind
                      and region_size in item.get("region_sizes", REGION_PROFILES)
                      and importance in item.get("importance", IMPORTANCE)]
        path, definition = rng.choice(candidates) if candidates else (None, None)
        if definition:
            local = Rect(*definition["bounds"])
            connector = definition["connectors"]["vehicle"]
        else:
            width, height = SITE_SIZES[kind][size_index + 1]
            grade = 0 if kind == "safehouse" else IMPORTANCE.index(importance)
            width += 4 * rng.randint(grade, grade + 1)
            local = Rect(-width / 2, -height / 2, width, height)
            connector = {"position": [0, height / 2], "direction": [0, 1], "width_m": ROAD_WIDTH}
        selected.append((kind, path, definition, local, connector))
    selected.sort(key=lambda item: item[3].w * item[3].h, reverse=True)
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
    for index, (kind, path, definition, local, connector) in enumerate(selected):
        key = f"{kind}/{index}"
        local_gate, normal = connector["position"], connector["direction"]
        local_approach = [local_gate[i] + normal[i] * 12 for i in (0, 1)]
        left, top = min(local.x, local_approach[0]) - 6, min(local.y, local_approach[1]) - 6
        right, bottom = max(local.right, local_approach[0]) + 6, max(local.bottom, local_approach[1]) + 6
        for _ in range(160):
            placement = Placement(key, (rng.randrange(-half + 12, half - 12, 2),
                                        rng.randrange(-half + 12, half - 12, 2)), rng.randrange(4))
            area = Rect(*placement.rect((left, top, right - left, bottom - top)))
            if (area.x < bounds.x + 8 or area.y < bounds.y + 8 or area.right > bounds.right - 8
                    or area.bottom > bounds.bottom - 8 or any(area.overlaps(other) for other in reserved)):
                continue
            footprint = placement.rect((local.x, local.y, local.w, local.h))
            gate, approach = placement.point(local_gate), placement.point(local_approach)
            direction = rotate(normal, placement.turns)
            sites.append({"id": key, "kind": kind, "name": definition["name"] if definition else SITE_SIZES[kind][0],
                          "rect": footprint, "position": list(placement.position),
                          "reserved": [area.x, area.y, area.w, area.h], "rotation": placement.turns * 90,
                          "variant_id": definition["id"] if definition else f"{kind}/{region_size}/{importance}/{local.w}x{local.h}",
                          "prefab": path, "prefab_version": definition["version"] if definition else None,
                          "importance": importance, "placeholder": definition is None,
                          "connector": {"position": list(gate), "direction": list(direction), "width_m": connector["width_m"],
                                        "approach": list(approach)}})
            points[key], gates[key] = approach, gate
            stubs.append((gate, approach))
            reserved.append(area)
            break
        else:
            raise ValueError(f"시설 배치 후보 부족: {key}")
    return bounds, sites, points, gates, stubs
