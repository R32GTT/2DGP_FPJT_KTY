"""미터 좌표의 충돌 판정과 정적 공간 조회. 화면 해상도에 의존하지 않는다."""

from dataclasses import dataclass, field
from shapely import LineString, MultiPoint, Point

from .spatial import BoundsIndex


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

    def add(self, collider):
        self.index.add(collider.id, collider.rect, collider)
        self.objects[collider.id] = collider

    def query(self, rect, category="movement"):
        return [item for item in self.index.query(rect) if item.active and category in item.blocks]

    def raycast(self, start, end, *, padding=0.0, category="sight", ignore_doors=False, ignore_ids=()):
        if padding:
            shape = MultiPoint([(x + dx, y + dy) for x, y in (start, end)
                                for dx in (-padding, padding) for dy in (-padding, padding)]).convex_hull
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
