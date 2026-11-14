"""이미지 레이어·미터 판정·내비게이션을 별도로 읽는 목업 월드."""

from dataclasses import dataclass
import json
import math
from pathlib import Path

from .geometry import Collider, Rect, SpatialIndex
from .navigation import Navigation
from .region import build_region
from .spatial import BoundsIndex


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCENE = Path(__file__).parent / "scenarios" / "image_yard.json"
REGION_SCENE = Path(__file__).parent / "scenarios" / "rear_region.json"


@dataclass(frozen=True)
class ImageLayer:
    id: str
    rect: Rect
    image: str
    color: tuple
    fallback: str = ""
    style: str = ""
    variant: int = 0
    rotation: int = 0
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
            self.geometry.add(collider)
            self.collider_index.add(collider.id, rect, collider)
        self.navigation = Navigation({key: tuple(point) for key, point in self.data["navigation"].items()},
                                     self.data["navigation_edges"], self.geometry)
        self.crate = tuple(self.data["supply_point"])
        self.safehouse = tuple(self.data.get("safehouse_spawn", self.data["player_spawn"]))
        self.facilities = self.data.get("facilities", [])
        self.roads = self.data.get("roads", [])
        self.region_layout = self.data.get("region_layout")

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
        nearby = Rect(actor.position.x - 2, actor.position.y - 2, 4, 4)
        for collider in self.collider_index.query(nearby):
            center = (collider.rect.x + collider.rect.w / 2, collider.rect.y + collider.rect.h / 2)
            if (collider.kind == "door" and collider.active and collider.ai_access
                    and math.dist(actor.xy, center) < 1.6
                    and collider.rect.expanded(actor.radius).segment_entry(actor.xy, destination) is not None):
                collider.active = False
                self.revision += 1
                self.navigation.invalidate()
                return collider
        return None
