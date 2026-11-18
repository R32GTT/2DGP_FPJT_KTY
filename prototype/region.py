"""지역 배치를 기존 그림 장면 데이터로 조립한다. 에셋 없이도 같은 경로를 사용한다."""

import json
import math
import random

from .geometry import Rect, segment_distance
from .prefabs import instantiate


def strip_layers(key, start, end, width, style, color):
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if not length:
        raise ValueError(f"길이가 0인 도로: {key}")
    cosine, sine = abs(dx) / length, abs(dy) / length
    angle = math.degrees(math.atan2(dy, dx))
    parts = math.ceil(length / 32)
    # 굽은 연결부의 바깥쪽에 틈이 생기지 않도록 비스듬한 구간은 조금 겹친다.
    section = length / parts + (min(width / 2, 1) if dx and dy else 0)
    extent_x = cosine * section + sine * width
    extent_y = sine * section + cosine * width
    for index in range(parts):
        fraction = (index + 0.5) / parts
        x, y = start[0] + dx * fraction, start[1] + dy * fraction
        yield {"id": f"{key}/{index}", "style": style, "image": "", "color": color,
               "rect": [x - extent_x / 2, y - extent_y / 2, extent_x, extent_y],
               "rotation": angle, "image_size": [section, width]}


def place_field_facility(row, definition):
    x, y = row["position"]
    prefix = row["id"] + "/"

    def rect(local):
        left, top, width, height = local
        return [x + left, y + top, width, height]

    layers = [{"id": prefix + "yard", "rect": rect(definition["yard"]), "style": "yard",
               "image": "", "color": [87, 94, 78]}]
    for index, floor in enumerate(definition["floors"]):
        layers.append({"id": prefix + f"floor_{index}", "rect": rect(floor), "style": "floor",
                       "image": "", "color": definition["color"]})
    colliders = [{"id": prefix + f"wall_{index}", "rect": rect(wall)}
                 for index, wall in enumerate(definition["walls"])]
    for index, door in enumerate(definition["doors"]):
        colliders.append({"id": prefix + f"door_{index}", "kind": "door", "rect": rect(door),
                          "active": row.get("closed", True), "ai_access": row.get("ai_access", True),
                          "material": "wood", "rotation": 90 if door[2] < door[3] else 0})
    for index, prop in enumerate(definition.get("props", [])):
        colliders.append({"kind": "cover", "material": "wood", **prop,
                          "id": prefix + f"prop_{index}", "rect": rect(prop["rect"])})
    nodes = {prefix + key: (x + dx, y + dy) for key, (dx, dy) in definition["nodes"].items()}
    edges = [(prefix + a, prefix + b) for a, b in definition["edges"]]
    edges.append((row["service_node"], prefix + "outside"))
    facility = {"id": row["id"], "name": row["name"], "kind": definition["kind"],
                "rect": rect(definition["bounds"]), "campaign_id": row.get("campaign_id", ""),
                "position": row["position"], "terminal": nodes[prefix + "terminal"],
                "service_node": row["service_node"]}
    return layers, colliders, nodes, edges, facility


def build_region(layout, root):
    bounds = Rect(*layout["bounds"])
    chunk = layout.get("chunk_size", 64)
    if "field_facilities" in layout:
        definitions = json.loads((root / layout["field_facilities"]).read_text(encoding="utf-8"))
        place_facility = place_field_facility
    else:
        definitions = json.loads((root / layout["prefabs"]).read_text(encoding="utf-8"))
        place_facility = instantiate
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
    default_dirt = ((44, -104, 156, 90), (-118, 22, 90, 64), (104, 4, 80, 60))
    for index, rect in enumerate(layout.get("dirt_patches", default_dirt)):
        layers.append({"id": f"dirt/{index}", "style": "dirt", "image": "", "rect": list(rect), "color": [111, 93, 64]})
    modules = [json.loads((root / path).read_text(encoding="utf-8")) for path in layout.get("field_modules", [])]
    for section in [layout, *modules]:
        for terrain in section.get("terrain", []):
            layers.append({**terrain, "image": ""})
            if terrain.get("blocks_movement"):
                colliders.append({"id": terrain["id"], "rect": terrain["rect"],
                                  "kind": "terrain", "blocks": ["movement"]})
        for feature in section.get("field_colliders", []):
            colliders.append(feature)
            if feature["kind"] == "building":
                layers.append({"id": feature["id"], "rect": feature["rect"], "style": "roof",
                               "image": "", "color": feature["color"]})
        nodes.update({key: tuple(point) for key, point in section.get("walk_nodes", {}).items()})
    feature_bounds = [Rect(*row["rect"]).expanded(2) for row in colliders]
    for section in [layout, *modules]:
        for a, b in section.get("walk_edges", []):
            edges.append((a, b))
        for path in section.get("footpaths", []):
            a, b = path["start"], path["end"]
            start, end = nodes[a], nodes[b]
            width = path.get("width", 2)
            style = path.get("style", "trail")
            color = [143, 130, 99] if style == "trail" else [123, 111, 89]
            edges.append((a, b))
            roads.append({"id": path["id"], "start": start, "end": end, "width": width, "kind": "footpath"})
            layers.extend(strip_layers(path["id"], start, end, width, style, color))
    for row in layout["roads"]:
        if row["start"] not in local_nodes or row["end"] not in local_nodes:
            continue
        start, end = local_nodes[row["start"]], local_nodes[row["end"]]
        edges.append((row["start"], row["end"]))
        width = row.get("width", 4)
        roads.append({"id": row["id"], "start": start, "end": end, "width": width})
        layers.extend(strip_layers(f"road/{row['id']}", start, end, width, "road_h", [105, 103, 84]))
    for row in layout["facilities"]:
        building_layers, building_colliders, building_nodes, building_edges, facility = place_facility(row, definitions[row["prefab"]])
        layers.append(building_layers[0])
        start, end = nodes[row["service_node"]], building_nodes[row["id"] + "/outside"]
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
                route = [(nodes[key][0] + offset, nodes[key][1] + offset) for key in group["patrol_nodes"]]
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
        if any(rect.segment_entry((x, y), (x, y)) is not None for rect in feature_bounds):
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
