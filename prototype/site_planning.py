"""지역 등급별 시설 예약 영역과 회전된 차량 진입점을 선택한다."""

import math
import random

from .geometry import Rect
from .prefabs import Placement, rotate
from .road_generation import facility_road


# 실제 프리팹 제작 전의 임시 점유 치수·검증 맵 크기다.
REGION_PROFILES = {
    "small": (128, 1, 1, 0), "medium": (192, 2, 2, 1),
    "large": (256, 2, 3, 2), "xlarge": (320, 3, 4, 3),
}
IMPORTANCE = ("normal", "important", "core")
SAFEHOUSE_FACILITY_RADIUS = 24.0
SIDES = {"west": (0, -1), "east": (0, 1), "north": (1, -1), "south": (1, 1)}
SITE_SIZES = {
    "safehouse": ("안전가옥", (12, 8), (12, 8), (12, 8), (12, 8)),
    "hq": ("HQ", (24, 20), (28, 24), (32, 28), (36, 28)),
    "comms": ("통신시설", (16, 12), (20, 12), (20, 16), (24, 16)),
    "warehouse": ("물류창고", (24, 20), (28, 24), (36, 28), (44, 32)),
    "garrison": ("주둔지", (20, 16), (24, 20), (28, 24), (32, 24)),
    "sam": ("대공시설", (20, 16), (24, 20), (28, 24), (32, 24)),
}


def plan_sites(seed, version, region_size, importance, attempt=0, *, catalog=None, entries=None):
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
            connector = {"position": [0, height / 2], "direction": [0, 1], "width_m": facility_road(kind)["width"]}
        selected.append((kind, path, definition, local, connector))
    selected.sort(key=lambda item: item[3].w * item[3].h, reverse=True)
    bounds = Rect(-extent / 2, -extent / 2, extent, extent)
    half, sites, reserved = extent // 2, [], [Rect(-8, -8, 16, 16)]
    stub_length = 12 if entries is not None else 4
    if entries is None:
        sides = ("west", "east") if rng.randrange(2) == 0 else ("north", "south")
        entries = [{"side": side, "offset": rng.randrange(-half // 2, half // 2, 2)} for side in sides]
    if (not 1 <= len(entries) <= 4
            or any(entry['side'] not in SIDES or type(entry['offset']) not in (int, float)
                   or not math.isfinite(entry['offset']) or abs(entry['offset']) > half - 12 for entry in entries)
            or any(a['side'] == b['side'] and abs(a['offset'] - b['offset']) < 18
                   for index, a in enumerate(entries) for b in entries[index + 1:])):
        raise ValueError("잘못된 지역 연결점")
    gates, stubs, points = {}, [], {"spawn": (0, 0)}
    for index, entry in enumerate(entries):
        axis, side = SIDES[entry['side']]
        offset = entry['offset']
        gate, approach = [offset, offset], [offset, offset]
        gate[axis], approach[axis] = side * half, side * (half - stub_length)
        key = f"entry/{index}"
        gates[key], points[key] = tuple(gate), tuple(approach)
        stubs.append((tuple(gate), tuple(approach)))
        reserved.append(Rect(min(gate[0], approach[0]) - 6, min(gate[1], approach[1]) - 6,
                             abs(gate[0] - approach[0]) + 12, abs(gate[1] - approach[1]) + 12))
    ordered = sorted(enumerate(selected), key=lambda item: item[1][0] != "safehouse")
    budget = 8000

    def place_site(depth):
        nonlocal budget
        if depth == len(ordered):
            return True
        index, (kind, path, definition, local, connector) = ordered[depth]
        key = f"{kind}/{index}"
        local_gate, normal = connector["position"], connector["direction"]
        local_approach = [local_gate[i] + normal[i] * 12 for i in (0, 1)]
        left, top = min(local.x, local_approach[0]) - 6, min(local.y, local_approach[1]) - 6
        right, bottom = max(local.right, local_approach[0]) + 6, max(local.bottom, local_approach[1]) + 6
        for _ in range(160):
            if budget <= 0:
                return False
            budget -= 1
            placement = Placement(key, (rng.randrange(-half + 12, half - 12, 2),
                                        rng.randrange(-half + 12, half - 12, 2)), rng.randrange(4))
            # 연결 경계에서도 보호 반경을 확보해 이웃 지역 시설의 침범을 막는다.
            if kind == "safehouse" and any(sign * placement.position[axis] > half - SAFEHOUSE_FACILITY_RADIUS
                                           for axis, sign in (SIDES[entry['side']] for entry in entries)):
                continue
            area = Rect(*placement.rect((left, top, right - left, bottom - top)))
            if (area.x < bounds.x + 8 or area.y < bounds.y + 8 or area.right > bounds.right - 8
                    or area.bottom > bounds.bottom - 8 or any(area.overlaps(other) for other in reserved)):
                continue
            footprint = placement.rect((local.x, local.y, local.w, local.h))
            if sites:
                sx, sy = sites[0]["position"]
                x, y, w, h = footprint
                if math.hypot(max(x - sx, 0, sx - x - w), max(y - sy, 0, sy - y - h)) < SAFEHOUSE_FACILITY_RADIUS:
                    continue
            gate, approach = placement.point(local_gate), placement.point(local_approach)
            direction = rotate(normal, placement.turns)
            sites.append({"id": key, "kind": kind, "name": definition["name"] if definition else SITE_SIZES[kind][0],
                          "rect": footprint, "position": list(placement.position),
                          "reserved": [area.x, area.y, area.w, area.h], "rotation": placement.turns * 90,
                          "variant_id": definition["id"] if definition else f"{kind}/{region_size}/{importance}/{local.w}x{local.h}",
                          "prefab": path, "prefab_version": definition["version"] if definition else None,
                          "road": facility_road(kind),
                          "importance": importance, "placeholder": definition is None,
                          "connector": {"position": list(gate), "direction": list(direction), "width_m": connector["width_m"],
                                        "approach": list(approach)}})
            points[key], gates[key] = approach, gate
            stubs.append((gate, approach))
            reserved.append(area)
            if place_site(depth + 1):
                return True
            sites.pop()
            reserved.pop()
            stubs.pop()
            del points[key], gates[key]
        return False

    if not place_site(0):
        raise ValueError("시설 간격을 만족하는 배치 후보 부족")
    return bounds, sites, points, gates, stubs
