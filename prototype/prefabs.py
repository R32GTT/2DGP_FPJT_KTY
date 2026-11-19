"""시설 한 단위의 그림·벽·문·노드를 같은 변환으로 배치한다."""

from dataclasses import dataclass
from copy import deepcopy
import json
import math

def rotate(point, turns):
    x, y = point
    return ((x, y), (-y, x), (-x, -y), (y, -x))[turns % 4]


@dataclass(frozen=True)
class Placement:
    id: str
    position: tuple
    turns: int = 0

    def point(self, value):
        x, y = rotate(value, self.turns)
        return self.position[0] + x, self.position[1] + y

    def rect(self, values):
        x, y, w, h = values
        points = [self.point(point) for point in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
        left, top = min(p[0] for p in points), min(p[1] for p in points)
        return [left, top, max(p[0] for p in points) - left, max(p[1] for p in points) - top]


def load_catalog(root):
    catalog = {}
    for path in sorted((root / "prototype/data/prefabs").glob("*.json")):
        definition = json.loads(path.read_text(encoding="utf-8"))
        if definition.get("format") != "revenant.facility_prefab.v1":
            raise ValueError(f"지원하지 않는 시설 프리팹: {path}")
        catalog[path.relative_to(root).as_posix()] = definition
    return catalog


def expand_instances(scene, root):
    scene = deepcopy(scene)
    definitions = {}
    scene["placed_prefabs"] = []
    for row in scene.get("prefab_instances", ()):
        path = root / row["prefab"]
        if path not in definitions:
            definitions[path] = json.loads(path.read_text(encoding="utf-8"))
        definition = definitions[path]
        if definition.get("format") != "revenant.facility_prefab.v1":
            raise ValueError(f"지원하지 않는 시설 프리팹: {path}")
        if row.get("version", definition["version"]) != definition["version"]:
            raise ValueError(f"시설 프리팹 버전 불일치: {path}")
        angle = row.get("rotation", 0)
        if angle % 90:
            raise ValueError("시설 배치는 90도 회전 단위여야 함")
        placement = Placement(row["id"], tuple(row["position"]), angle // 90)
        prefix = row["id"] + "/"
        if any(item["id"] == row["id"] for item in scene["placed_prefabs"]):
            raise ValueError(f"중복 프리팹 배치 ID: {row['id']}")
        floors = []
        for layer in definition["layers"]:
            rect = placement.rect(layer["rect"])
            scene["layers"].append({**layer, "id": prefix + layer["id"], "rect": rect,
                                    "rotation": (layer.get("rotation", 0) + angle) % 360,
                                    "image_size": layer.get("image_size", layer["rect"][2:])})
            if layer.get("style") == "floor":
                floors.append(rect)
        for collider in definition["colliders"]:
            placed = {**collider, "id": prefix + collider["id"], "rect": placement.rect(collider["rect"]),
                      "rotation": (collider.get("rotation", 0) + angle) % 360}
            if collider.get("visual_layer"):
                placed["visual_layer"] = prefix + collider["visual_layer"]
            scene["colliders"].append(placed)
        for key, point in definition["navigation"].items():
            if prefix + key in scene["navigation"]:
                raise ValueError(f"중복 프리팹 이동 노드: {prefix + key}")
            scene["navigation"][prefix + key] = placement.point(point)
        scene["navigation_edges"].extend((prefix + a, prefix + b) for a, b in definition["navigation_edges"])
        scene.setdefault("search_areas", {})[row["id"]] = floors
        connectors = {key: {**connector, "position": placement.point(connector["position"]),
                            "direction": rotate(connector["direction"], placement.turns),
                            "node": prefix + connector["node"]}
                      for key, connector in definition["connectors"].items()}
        if row.get("road_node"):
            road_node = row["road_node"]
            vehicle_node = connectors["vehicle"]["node"]
            if math.dist(scene["navigation"][road_node], scene["navigation"][vehicle_node]) > 1e-6:
                raise ValueError(f"도로와 프리팹 차량 진입점 불일치: {row['id']}")
            scene["navigation_edges"].append((road_node, vehicle_node))
        scene["placed_prefabs"].append({"id": row["id"], "prefab_id": definition["id"],
                                       "version": definition["version"], "kind": definition["kind"],
                                       "rect": placement.rect(definition["bounds"]), "connectors": connectors,
                                       "terminal": prefix + definition["terminal_node"]})
    return scene


def instantiate(row, definition):
    angle = row.get("rotation", 0)
    if angle % 90:
        raise ValueError("시설 배치는 90도 회전 단위여야 함")
    placement = Placement(row["id"], tuple(row["position"]), angle // 90)
    width, height = definition["size"]
    thick, door = definition.get("wall_thickness", 0.25), definition.get("door_width", 1.8)
    rect = [-width / 2, -height / 2, width, height]
    prefix = row["id"] + "/"
    layers = [{"id": prefix + "yard", "rect": placement.rect([-width / 2 - 5, -height / 2 - 5, width + 10, height + 10]),
               "style": "yard", "image": "", "color": [87, 94, 78]}]
    for role in ("floor", "walls"):
        layers.append({"id": prefix + role, "rect": placement.rect(rect), "style": role,
                       "image": f"assets/region/{row['prefab']}_{role}.png", "color": definition.get("color", [121, 130, 119]),
                       "rotation": angle, "image_size": [width, height]})
    walls = [(-width / 2, -height / 2, width, thick), (-width / 2, -height / 2, thick, height),
             (width / 2 - thick, -height / 2, thick, height),
             (-width / 2, height / 2 - thick, (width - door) / 2, thick),
             (door / 2, height / 2 - thick, (width - door) / 2, thick)]
    colliders = [{"id": prefix + f"wall_{index}", "rect": placement.rect(wall),
                  "visual_layer": prefix + "walls"} for index, wall in enumerate(walls)]
    colliders.append({"id": prefix + "door", "rect": placement.rect([-door / 2, height / 2 - thick, door, thick]),
                      "kind": "door", "active": row.get("closed", True),
                      "ai_access": row.get("ai_access", True), "material": "wood", "rotation": angle})
    for index, prop in enumerate(definition.get("props", [])):
        colliders.append({"id": prefix + f"prop_{index}", "rect": placement.rect(prop["rect"]),
                          "kind": prop.get("kind", "cover"), "material": prop.get("material", "wood"),
                          "rotation": angle})
    nodes = {prefix + "hall": placement.point((0, 0)),
             prefix + "inside": placement.point((0, height / 2 - 1.2)),
             prefix + "outside": placement.point((0, height / 2 + 1.2)),
             prefix + "terminal": placement.point(definition.get("terminal", (width / 4, 0)))}
    # 실외 순찰과 접근로를 시설 프리팹에 함께 둔다.
    corners = [(x, y) for x, y in ((-width / 2 - 4, -height / 2 - 4),
                                  (width / 2 + 4, -height / 2 - 4),
                                  (width / 2 + 4, height / 2 + 4),
                                  (-width / 2 - 4, height / 2 + 4))]
    for index, point in enumerate(corners):
        nodes[prefix + f"corner_{index}"] = placement.point(point)
    edges = [(prefix + "hall", prefix + "inside"), (prefix + "inside", prefix + "outside"),
             (prefix + "hall", prefix + "terminal"), (row["service_node"], prefix + "outside")]
    for index in range(4):
        edges.append((prefix + f"corner_{index}", prefix + f"corner_{(index + 1) % 4}"))
    edges.extend(((prefix + "corner_2", prefix + "outside"), (prefix + "corner_3", prefix + "outside")))
    facility = {"id": row["id"], "name": row["name"], "kind": row["prefab"],
                "rect": placement.rect(rect), "campaign_id": row.get("campaign_id", ""),
                "position": list(placement.position), "terminal": list(nodes[prefix + "terminal"]),
                "service_node": row["service_node"]}
    layout = definition.get("search_layout")
    if layout:
        facility["search_layout"] = {
            **layout,
            "corner_points": [placement.point(point) for point in layout.get("corner_points", ())],
            "observation_points": [placement.point(point) for point in layout.get("observation_points", ())],
            "entry_poses": [{"position": placement.point(pose["position"]), "look": placement.point(pose["look"])}
                            for pose in layout.get("entry_poses", ())],
        }
    return layers, colliders, nodes, edges, facility
