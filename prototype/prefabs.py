"""시설 한 단위의 그림·벽·문·노드를 같은 변환으로 배치한다."""

from dataclasses import dataclass

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
    return layers, colliders, nodes, edges, facility
