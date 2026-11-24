"""미터 좌표의 충돌 판정과 정적 공간 조회. 화면 해상도에 의존하지 않는다."""

from dataclasses import dataclass, field
import math
from shapely import LineString, Point, box, linestrings

from .spatial import BoundsIndex


def segment_distance(x, y, ax, ay, bx, by) -> float:
    length_squared = (bx - ax) ** 2 + (by - ay) ** 2
    fraction = (max(0.0, min(1.0, ((x - ax) * (bx - ax) + (y - ay) * (by - ay)) / length_squared))
                if length_squared else 0.0)
    return math.hypot(x - ax - fraction * (bx - ax), y - ay - fraction * (by - ay))


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    def expanded(self, radius):
        return Rect(self.x - radius, self.y - radius, self.w + radius * 2, self.h + radius * 2)

    def overlaps(self, other):
        return (self.x < other.right and self.right > other.x
                and self.y < other.bottom and self.bottom > other.y)

    def segment_entry(self, start, end):
        low, high = 0.0, 1.0
        for origin, target, minimum, maximum in (
                (start[0], end[0], self.x, self.right), (start[1], end[1], self.y, self.bottom)):
            delta = target - origin
            if abs(delta) < 1e-12:
                if not minimum <= origin <= maximum:
                    return None
                continue
            first, second = sorted(((minimum - origin) / delta, (maximum - origin) / delta))
            low, high = max(low, first), min(high, second)
            if low > high:
                return None
        return low


@dataclass
class Collider:
    id: str
    rect: Rect
    kind: str = "wall"
    active: bool = True
    blocks: tuple[str, ...] = ("movement", "sight", "bullet")
    material: str = "concrete"
    ai_access: bool = True
    visual_layer: str = ""
    rotation: int = 0


@dataclass
class SpatialIndex:
    objects: dict[str, Collider] = field(default_factory=dict)
    index: BoundsIndex = field(default_factory=BoundsIndex)
    smoke: list = field(default_factory=list)

    def add(self, collider):
        self.index.add(collider.id, collider.rect, collider)
        self.objects[collider.id] = collider

    def query(self, rect, category="movement"):
        return [item for item in self.index.query(rect) if item.active and category in item.blocks]

    def remove(self, key):
        del self.objects[key]
        self.index.remove(key)

    def sight_count(self, origin, targets):
        return sum(self.sight_flags(origin, targets))

    def smoke_blocks(self, origin, target, floor):
        return any(cloud.floor == floor and segment_distance(*cloud.position, *origin, *target) <= cloud.radius
                   for cloud in self.smoke)

    def sight_clear(self, origin, target, *, floor):
        return not self.smoke_blocks(origin, target, floor) and self.raycast(origin, target) is None

    def sight_flags(self, origin, targets, *, floor=None):
        if len(targets) <= 3:
            if self.smoke and floor is not None:
                return [self.sight_clear(origin, target, floor=floor) for target in targets]
            return [self.raycast(origin, target, category="sight") is None for target in targets]
        rays = linestrings([(origin, point) for point in targets])
        blocked = {row for row, collider in self.index.intersections(rays)
                   if collider.active and "sight" in collider.blocks
                   and collider.rect.segment_entry(origin, targets[row]) is not None}
        # 층을 받는 실시간 시야에만 연막을 적용한다. 정적 수색 지점 계산은 벽만 조회한다.
        if self.smoke and floor is not None:
            return [index not in blocked and not self.smoke_blocks(origin, target, floor)
                    for index, target in enumerate(targets)]
        return [index not in blocked for index in range(len(targets))]

    def raycast(self, start, end, *, padding=0.0, category="sight", ignore_doors=False, ignore_ids=()):
        if padding:
            # 넓은 후보 조회 뒤 기존 선분 판정에서 실제 충돌과 순서를 결정한다.
            shape = box(min(start[0], end[0]) - padding, min(start[1], end[1]) - padding,
                        max(start[0], end[0]) + padding, max(start[1], end[1]) + padding)
        else:
            shape = Point(start) if start == end else LineString((start, end))
        nearest = None
        for collider in self.index.query_shape(shape):
            if (collider.id in ignore_ids or not collider.active or category not in collider.blocks
                    or (ignore_doors and collider.kind == "door" and (ignore_doors == "all" or collider.ai_access))):
                continue
            fraction = collider.rect.expanded(padding).segment_entry(start, end)
            if fraction is not None and (nearest is None or fraction < nearest[0]):
                nearest = fraction, collider.id
        return nearest
