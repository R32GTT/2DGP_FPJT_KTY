"""이미지 레이어·미터 판정·내비게이션을 별도로 읽는 목업 월드."""

from dataclasses import dataclass
import json
import math
from functools import cached_property
from pathlib import Path
from types import SimpleNamespace

from .geometry import Collider, Rect, SpatialIndex
from .indoor_search import build_search_area, search_area_layouts
from .navigation import Navigation
from .region import build_region
from .prefabs import expand_instances
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
    polygon: tuple = ()
    triangles: tuple = ()

    @property
    def role(self):
        return self.style or self.id


class World:
    def __init__(self, path=DEFAULT_SCENE, *, world_root=None, initial_position=None):
        # 생성기는 기존 장면 형식의 데이터를 직접 전달할 수 있다.
        self.path = None if isinstance(path, dict) else Path(path)
        self.source = path if self.path is None else self.path
        self.data = self.source if self.path is None else json.loads(self.path.read_text(encoding="utf-8"))
        if self.data.get("format") == "revenant.region_scene.v1":
            self.data = build_region(self.data, ROOT)
        if self.data.get("format") != "revenant.image_scene.v1":
            raise ValueError("지원하지 않는 그림 맵 형식")
        if self.data.get("prefab_instances"):
            self.data = expand_instances(self.data, ROOT)
        if self.data.get("generation"):
            from .generated_campaign import bind_sites
            self.data = bind_sites(self.data)
        self.bounds = Rect(*self.data["bounds"])
        self.layers = []
        self.layer_index, self.collider_index = BoundsIndex(), BoundsIndex()
        self.geometry = SpatialIndex()
        self.revision = 0
        for index, row in enumerate(self.data["layers"]):
            self.add_layer(row, index)
        self.layers = [item[1] for item in self.layer_index.items.values()]
        for row in self.data["colliders"]:
            self.add_collider(row)
        self.navigation = Navigation({key: tuple(point) for key, point in self.data["navigation"].items()},
                                     self.data["navigation_edges"], self.geometry, bounds=self.bounds)
        self.crate = tuple(self.data["supply_point"])
        self.safehouse = tuple(self.data.get("safehouse_spawn", self.data["player_spawn"]))
        self.facilities = self.data.get("facilities", [])
        self.roads = self.data.get("roads", [])
        self.region_layout = self.data.get("region_layout")
        self.campaign_map = bool(self.region_layout or self.data.get("generation"))
        self.territories = {key: Rect(*area["bounds"]) for key, area in self.data.get("territories", {}).items()}
        self.road_controls = self.data.get("road_controls", [])
        if self.data.get("road_network") and self.data.get("prefab_instances"):
            from .road_generation import validate_roads
            validate_roads(self.data["road_network"], self.data["planned_facilities"], self.bounds, self.data["colliders"])
        self._search_layouts = search_area_layouts(self)
        self._search_samples = {}
        self._search_floor_index = BoundsIndex()
        for order, (key, (floors, _)) in enumerate(self._search_layouts.items()):
            for index, rect in enumerate(floors):
                self._search_floor_index.add((order, index), rect, key)
        self._search_world = self
        self.light_roofs = BoundsIndex()
        roofs = {layer.rect for layer in self.layers if layer.role in {"floor", "roof"}}
        if self.data.get("stream_terrain"):
            # 수색 표본은 문을 조작하거나 청크를 떠나도 처음 배치된 실내 형상을 기준으로 한다.
            geometry = SpatialIndex()
            for collider in self.geometry.objects.values():
                geometry.add(collider)
            self._search_world = SimpleNamespace(geometry=geometry,
                                                 navigation=Navigation({}, (), geometry, bounds=self.bounds))
            roofs.update(rect for floors, _ in self._search_layouts.values() for rect in floors)
        else:
            roofs.update(rect for area in self.search_areas for rect in area.floors)
        for index, rect in enumerate(sorted(roofs, key=lambda item: (item.x, item.y, item.w, item.h))):
            self.light_roofs.add(index, rect, rect)
        self._authored_layers = self.layers
        self.overview_landmarks = tuple(layer for layer in self.layers if layer.role in {"water", "roof"})
        self.chunks = None
        self.map_background_path = None
        if self.data.get('stream_terrain'):
            from .chunks import WorldChunks
            from .world_files import WorldFiles
            scene = self.source if self.path is None else json.loads(self.path.read_text(encoding="utf-8"))
            files = WorldFiles.for_scene(world_root, scene) if world_root is not None else None
            if files is not None:
                from .map_background import create_background
                self.map_background_path = create_background(scene, files.map_path)
            self.chunks = WorldChunks(self, files)
            self.update_chunks(self.data['player_spawn'] if initial_position is None else initial_position)

    @cached_property
    def map_layers(self):
        if self.chunks is None:
            return self._authored_layers
        ground = [self.make_layer(row, -2 if row['style'] == 'grass' else -1)
                  for row in self.chunks.terrain.ground(self.bounds)]
        return ground + self._authored_layers

    @staticmethod
    def make_layer(row, order):
        return ImageLayer(row['id'], Rect(*row['rect']), row['image'], tuple(row['color']),
                          row.get('fallback', ''), row.get('style', ''), row.get('variant', 0),
                          row.get('rotation', 0), tuple(row.get('image_size', ())), order,
                          tuple(map(tuple, row.get('polygon', ()))), tuple(map(tuple, row.get('triangles', ()))))

    def add_layer(self, row, order):
        layer = self.make_layer(row, order)
        self.layer_index.add(layer.id, layer.rect, layer)

    def add_collider(self, row):
        rect = Rect(*row['rect'])
        if not all(math.isfinite(value) for value in (rect.x, rect.y, rect.w, rect.h)) or min(rect.w, rect.h) <= 0:
            raise ValueError('충돌 영역 치수 오류')
        collider = Collider(row['id'], rect, row.get('kind', 'wall'), row.get('active', True),
                            material=row.get('material', 'concrete'), ai_access=row.get('ai_access', True),
                            visual_layer=row.get('visual_layer', ''), rotation=row.get('rotation', 0))
        if 'blocks' in row:
            collider.blocks = tuple(row['blocks'])
        self.geometry.add(collider)
        self.collider_index.add(collider.id, rect, collider)

    def update_chunks(self, point, velocity=(0.0, 0.0)):
        if self.chunks:
            self.chunks.update(point, velocity)

    def detail_loaded(self, point):
        return self.chunks is None or self.chunks.contains(point)

    def detail_overlaps(self, rect):
        return self.chunks is None or self.chunks.overlaps(rect)

    def collider_changes(self):
        if self.chunks:
            return self.chunks.remember()
        return {row['id']: self.geometry.objects[row['id']].active for row in self.data['colliders']
                if self.geometry.objects[row['id']].active != row.get('active', True)}

    def restore_changes(self, changes):
        for key, active in changes.items():
            if type(active) is not bool:
                raise ValueError('잘못된 오브젝트 변경 상태')
            if self.chunks and ('collider', key) not in self.chunks.static.items:
                parts = key.split('/')
                if len(parts) != 4 or parts[0] != 'terrain':
                    raise ValueError('알 수 없는 지형 오브젝트')
                candidate = self.chunks.terrain.candidate(int(parts[1]), int(parts[2]))
                if candidate is None or candidate[3]['id'] != key:
                    raise ValueError('알 수 없는 지형 오브젝트')
            if self.chunks:
                self.chunks.changes[key] = active
            if key in self.geometry.objects:
                self.geometry.objects[key].active = active
            elif not self.chunks:
                raise ValueError('알 수 없는 충돌 오브젝트')
        self.revision += bool(changes)
        self.navigation.invalidate()

    def territory_at(self, point):
        return next((key for key, rect in self.territories.items()
                     if rect.segment_entry(point, point) is not None), None)

    def _search_area(self, key):
        if key not in self._search_samples:
            self._search_samples[key] = build_search_area(self._search_world, key, *self._search_layouts[key])
        return self._search_samples[key]

    @property
    def search_areas(self):
        return tuple(area for key in self._search_layouts if (area := self._search_area(key)) is not None)

    def indoor_search_area(self, point):
        nearby = []
        candidates = self._search_floor_index.query(Rect(*point, 0, 0).expanded(3.000001))
        for key in dict.fromkeys(candidates):
            area = self._search_area(key)
            if area is None:
                continue
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
        return self.door_on_ai_path_from(actor.xy, destination, reach=1.2, padding=actor.radius + 0.08,
                                         clearance=actor.radius)

    def door_on_ai_path_from(self, origin, destination, *, reach, padding, clearance=0, ignore=""):
        x, y = origin
        dx, dy = destination[0] - x, destination[1] - y
        length = math.hypot(dx, dy)
        if length == 0:
            return None
        reach = min(reach, length)
        end = x + dx * reach / length, y + dy * reach / length
        bounds = Rect(min(x, end[0]), min(y, end[1]), abs(end[0] - x), abs(end[1] - y)).expanded(padding)
        candidates = []
        for collider in self.collider_index.query(bounds):
            if collider.id == ignore or collider.kind != "door" or (collider.active and not collider.ai_access):
                continue
            hit = collider.rect.expanded(padding).segment_entry(origin, end)
            if hit is not None:
                candidates.append((hit, collider.id))
        if not candidates:
            return None
        hit, door_id = min(candidates)
        # 열린 문도 같은 통로를 사용한다. 벽 뒤의 문은 요청하지 않는다.
        entry = x + (end[0] - x) * hit, y + (end[1] - y) * hit
        if not self.navigation.clear(origin, entry, clearance=clearance, ignore_doors=True):
            return None
        return door_id, reach * hit
