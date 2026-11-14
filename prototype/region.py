"""지역 배치를 기존 그림 장면 데이터로 조립한다. 에셋 없이도 같은 경로를 사용한다."""

import json
import math
import random

from campaign.viewmodel import segment_distance

from .geometry import Rect
from .prefabs import instantiate


def build_region(layout, root):
    bounds = Rect(*layout["bounds"])
    chunk = layout.get("chunk_size", 64)
    definitions = json.loads((root / layout["prefabs"]).read_text(encoding="utf-8"))
    layers, colliders, nodes, edges, facilities, roads, guards = [], [], {}, [], [], [], []
    local_nodes = {key: tuple(value) for key, value in layout["road_nodes"].items()
                   if bounds.x <= value[0] <= bounds.right and bounds.y <= value[1] <= bounds.bottom}
    nodes.update(local_nodes)
    for x in range(math.floor(bounds.x / chunk), math.ceil(bounds.right / chunk)):
        for y in range(math.floor(bounds.y / chunk), math.ceil(bounds.bottom / chunk)):
            left, top = max(bounds.x, x * chunk), max(bounds.y, y * chunk)
            right, bottom = min(bounds.right, (x + 1) * chunk), min(bounds.bottom, (y + 1) * chunk)
            layers.append({"id": f"ground/{x}/{y}", "style": "grass", "variant": (x * 7 + y * 11) % 4,
                           "rect": [left, top, right - left, bottom - top], "image": "", "color": [45, 65, 48]})
    for index, rect in enumerate(((44, -104, 156, 90), (-118, 22, 90, 64), (104, 4, 80, 60))):
        layers.append({"id": f"dirt/{index}", "style": "dirt", "image": "", "rect": list(rect), "color": [111, 93, 64]})
    for row in layout["roads"]:
        if row["start"] not in local_nodes or row["end"] not in local_nodes:
            continue
        start, end = local_nodes[row["start"]], local_nodes[row["end"]]
        if start[0] != end[0] and start[1] != end[1]:
            raise ValueError("이번 지역의 플레이스홀더 도로는 축 정렬 구간이어야 함")
        edges.append((row["start"], row["end"]))
        roads.append({"id": row["id"], "start": start, "end": end, "width": 4})
        length = math.dist(start, end)
        parts = max(1, math.ceil(length / 32))
        for index in range(parts):
            a = tuple(start[axis] + (end[axis] - start[axis]) * index / parts for axis in (0, 1))
            b = tuple(start[axis] + (end[axis] - start[axis]) * (index + 1) / parts for axis in (0, 1))
            horizontal = start[1] == end[1]
            rect = [min(a[0], b[0]), a[1] - 2, abs(a[0] - b[0]), 4] if horizontal else [a[0] - 2, min(a[1], b[1]), 4, abs(a[1] - b[1])]
            layers.append({"id": f"road/{row['id']}/{index}", "style": "road_h" if horizontal else "road_v",
                           "rect": rect, "image": "", "color": [105, 103, 84]})
    for row in layout["facilities"]:
        building_layers, building_colliders, building_nodes, building_edges, facility = instantiate(row, definitions[row["prefab"]])
        layers.append(building_layers[0])
        start, end = local_nodes[row["service_node"]], building_nodes[row["id"] + "/outside"]
        if start != end:
            horizontal = start[1] == end[1]
            if start[0] == end[0] or horizontal:
                rect = ([min(start[0], end[0]), start[1] - 1.5, abs(start[0] - end[0]), 3]
                        if horizontal else [start[0] - 1.5, min(start[1], end[1]), 3, abs(start[1] - end[1])])
                layers.append({"id": row["id"] + "/driveway", "style": "road_h" if horizontal else "road_v",
                               "rect": rect, "image": "", "color": [105, 103, 84]})
                roads.append({"id": row["id"] + "/driveway", "start": start, "end": end, "width": 3})
        layers.extend(building_layers[1:])
        colliders.extend(building_colliders)
        nodes.update(building_nodes)
        edges.extend(building_edges)
        facilities.append(facility)
    occupied_slots = {}
    for group in layout["garrisons"]:
        facility_id = group["facility"]
        patrol = [nodes[f"{facility_id}/corner_{index}"] for index in range(4)]
        for member in range(group["count"]):
            slot = occupied_slots.get(facility_id, 0)
            occupied_slots[facility_id] = slot + 1
            start = patrol[slot % 4]
            route = patrol[slot % 4:] + patrol[:slot % 4]
            position = [start[0] + slot // 4 * 0.6, start[1]]
            if member >= group["count"] - group.get("patrol_members", 0):
                offset = -0.45 if member % 2 == 0 else 0.45
                route = [(local_nodes[key][0] + offset, local_nodes[key][1] + offset) for key in group["patrol_nodes"]]
                position = list(route[0])
            guards.append({"id": f"{group['unit']}:{member}", "source_unit": group["unit"], "member": member,
                           "home_node": facility_id, "position": position,
                           "patrol": route})
    rng = random.Random(layout.get("seed", 42))
    attempts = 0
    tree_count = layout.get("tree_count", 0)
    placed = []
    while len(placed) < tree_count and attempts < tree_count * 30:
        attempts += 1
        clusters = layout.get("tree_clusters", [])
        if clusters and rng.random() < 0.7:
            cx, cy, spread = rng.choice(clusters)
            x, y = rng.gauss(cx, spread), rng.gauss(cy, spread)
        else:
            x, y = rng.uniform(bounds.x + 8, bounds.right - 8), rng.uniform(bounds.y + 8, bounds.bottom - 8)
        if not bounds.x + 8 <= x <= bounds.right - 8 or not bounds.y + 8 <= y <= bounds.bottom - 8:
            continue
        if any(Rect(*facility["rect"]).expanded(14).segment_entry((x, y), (x, y)) is not None for facility in facilities):
            continue
        if any(segment_distance(x, y, *nodes[a], *nodes[b]) < 5 for a, b in edges):
            continue
        if any(math.dist((x, y), point) < 4 for point in placed):
            continue
        placed.append((x, y))
        colliders.append({"id": f"tree/{len(placed)}", "rect": [x - 0.45, y - 0.45, 0.9, 0.9],
                          "kind": "tree", "material": "tree_trunk"})
    depot = next(f for f in facilities if f["campaign_id"] == layout["campaign_facility"])
    result = {**layout, "format": "revenant.image_scene.v1", "layers": layers, "colliders": colliders,
              "navigation": nodes, "navigation_edges": edges, "guards": guards, "facilities": facilities,
              "roads": roads, "supply_point": depot["terminal"], "region_layout": layout}
    return result
