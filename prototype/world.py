"""이미지 레이어·미터 판정·내비게이션을 별도로 읽는 목업 월드."""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .geometry import Collider, Rect, SpatialIndex
from .indoor_search import build_search_areas
from .navigation import Navigation
from .region import build_region
from .spatial import BoundsIndex


ROOT = Path(__file__).resolve().parent.parent
REGION_SCENE = Path(__file__).parent / "scenarios" / "rear_region.json"
DEFAULT_SCENE = REGION_SCENE


@dataclass(frozen=True)
class ImageLayer:
    id: str
    rect: Rect
    image: str
    color: tuple
    fallback: str = ""
    style: str = ""
    variant: int = 0
    rotation: float = 0
    image_size: tuple = ()
    order: int = 0

    @property
    def role(self):
        return self.style or self.id


class World:
    def __init__(self, path=DEFAULT_SCENE):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        if self.data.get("format") == "revenant.region_scene.v1":
            self.data = build_region(self.data, ROOT)
        if self.data.get("format") != "revenant.image_scene.v1":
            raise ValueError("지원하지 않는 그림 맵 형식")
        self.bounds = Rect(*self.data["bounds"])
        self.layers = [ImageLayer(row["id"], Rect(*row["rect"]), row["image"],
                                  tuple(row["color"]), row.get("fallback", ""), row.get("style", ""),
                                  row.get("variant", 0), row.get("rotation", 0), tuple(row.get("image_size", ())), index)
                       for index, row in enumerate(self.data["layers"])]
        self.layer_index, self.collider_index = BoundsIndex(64), BoundsIndex(32)
        for layer in self.layers:
            self.layer_index.add(layer.id, layer.rect, layer)
        self.geometry = SpatialIndex()
        self.revision = 0
        for row in self.data["colliders"]:
            rect = Rect(*row["rect"])
            if not all(math.isfinite(value) for value in (rect.x, rect.y, rect.w, rect.h)) or min(rect.w, rect.h) <= 0:
                raise ValueError("충돌 영역 치수 오류")
            collider = Collider(row["id"], rect, row.get("kind", "wall"), row.get("active", True),
                                material=row.get("material", "concrete"), ai_access=row.get("ai_access", True),
                                visual_layer=row.get("visual_layer", ""), rotation=row.get("rotation", 0))
            if "blocks" in row:
                collider.blocks = tuple(row["blocks"])
            self.geometry.add(collider)
            self.collider_index.add(collider.id, rect, collider)
        self.navigation = Navigation({key: tuple(point) for key, point in self.data["navigation"].items()},
                                     self.data["navigation_edges"], self.geometry, bounds=self.bounds)
        self.crate = tuple(self.data["supply_point"])
        self.safehouse = tuple(self.data.get("safehouse_spawn", self.data["player_spawn"]))
        self.facilities = self.data.get("facilities", [])
        self.roads = self.data.get("roads", [])
        self.region_layout = self.data.get("region_layout")
        self.search_areas = build_search_areas(self)

    def indoor_search_area(self, point):
        nearby = []
        for area in self.search_areas:
            for rect in area.floors:
                if rect.segment_entry(point, point) is not None:
                    return area
                entry = (max(rect.x + 0.3, min(rect.right - 0.3, point[0])),
                         max(rect.y + 0.3, min(rect.bottom - 0.3, point[1])))
                distance = math.dist(point, entry)
                if distance <= 3 and self.navigation.clear(point, entry, clearance=0, ignore_doors=True):
                    nearby.append((distance, area.id, area))
        return min(nearby, key=lambda row: row[:2])[2] if nearby else None

    def visible_layers(self, rect):
        return sorted(self.layer_index.query(rect), key=lambda layer: layer.order)

    def visible_colliders(self, rect):
        return self.collider_index.query(rect)

    def toggle_door(self, position):
        nearby = Rect(position[0] - 2, position[1] - 2, 4, 4)
        for collider in self.collider_index.query(nearby):
            center = (collider.rect.x + collider.rect.w / 2, collider.rect.y + collider.rect.h / 2)
            if collider.kind == "door" and math.dist(position, center) <= 1.8:
                collider.active = not collider.active
                self.revision += 1
                self.navigation.invalidate()
                return collider
        return None

    def open_for_ai(self, actor, destination):
        # 현재 이동 구간의 첫 장애물만 확인한다. 문 가장자리에 접근해도 열 수 있다.
        x, y = actor.xy
        dx, dy = destination[0] - x, destination[1] - y
        distance = math.hypot(dx, dy)
        if distance > 1.2:
            fraction = 1.2 / distance
            destination = (x + dx * fraction, y + dy * fraction)
        hit = self.geometry.raycast(actor.xy, destination, padding=actor.radius, category="movement")
        if hit is None:
            return None
        collider = self.geometry.objects[hit[1]]
        if collider.kind != "door" or not collider.ai_access:
            return None
        collider.active = False
        self.revision += 1
        self.navigation.invalidate()
        return collider

    def door_on_ai_path(self, actor, destination):
        x, y = actor.xy
        dx, dy = destination[0] - x, destination[1] - y
        length = math.hypot(dx, dy)
        if length == 0:
            return None
        reach = min(1.2, length)
        end = x + dx * reach / length, y + dy * reach / length
        padding = actor.radius + 0.08
        bounds = Rect(min(x, end[0]), min(y, end[1]), abs(end[0] - x), abs(end[1] - y)).expanded(padding)
        candidates = []
        for collider in self.collider_index.query(bounds):
            if collider.kind != "door" or (collider.active and not collider.ai_access):
                continue
            hit = collider.rect.expanded(padding).segment_entry(actor.xy, end)
            if hit is not None:
                candidates.append((hit, collider.id))
        if not candidates:
            return None
        hit, door_id = min(candidates)
        # 열린 문도 같은 통로를 사용한다. 벽 뒤의 문은 요청하지 않는다.
        entry = x + (end[0] - x) * hit, y + (end[1] - y) * hit
        if not self.navigation.clear(actor.xy, entry, clearance=actor.radius, ignore_doors=True):
            return None
        return door_id, reach * hit
