"""미터 좌표의 단순 충돌과 공간 해시. 화면 해상도에 의존하지 않는다."""

from dataclasses import dataclass, field
import math


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
    cell_size: float = 2.0
    objects: dict[str, Collider] = field(default_factory=dict)
    cells: dict[tuple[int, int], set[str]] = field(default_factory=dict)
    candidate_tests: int = 0
    ray_queries: int = 0

    def _cell_range(self, rect):
        for x in range(math.floor(rect.x / self.cell_size), math.floor(rect.right / self.cell_size) + 1):
            for y in range(math.floor(rect.y / self.cell_size), math.floor(rect.bottom / self.cell_size) + 1):
                yield x, y

    def add(self, collider):
        if collider.id in self.objects:
            raise ValueError(f"중복 충돌 ID: {collider.id}")
        self.objects[collider.id] = collider
        for cell in self._cell_range(collider.rect):
            self.cells.setdefault(cell, set()).add(collider.id)

    def query(self, rect, category="movement"):
        ids = set()
        for cell in self._cell_range(rect):
            ids.update(self.cells.get(cell, ()))
        return [self.objects[key] for key in sorted(ids)
                if self.objects[key].active and category in self.objects[key].blocks]

    def _ray_cells(self, start, end):
        x, y = math.floor(start[0] / self.cell_size), math.floor(start[1] / self.cell_size)
        end_x, end_y = math.floor(end[0] / self.cell_size), math.floor(end[1] / self.cell_size)
        dx, dy = end[0] - start[0], end[1] - start[1]
        step_x, step_y = (1 if dx > 0 else -1), (1 if dy > 0 else -1)
        next_x = (((x + (dx > 0)) * self.cell_size - start[0]) / dx) if dx else math.inf
        next_y = (((y + (dy > 0)) * self.cell_size - start[1]) / dy) if dy else math.inf
        delta_x = self.cell_size / abs(dx) if dx else math.inf
        delta_y = self.cell_size / abs(dy) if dy else math.inf
        while True:
            yield x, y
            if (x, y) == (end_x, end_y):
                break
            # 끝점이 셀 경계에 놓여도 도달한 축을 다시 넘지 않는다.
            if y == end_y or (x != end_x and next_x < next_y):
                x, next_x = x + step_x, next_x + delta_x
            else:
                y, next_y = y + step_y, next_y + delta_y

    def raycast(self, start, end, *, padding=0.0, category="sight", ignore_doors=False, ignore_ids=()):
        """Ray가 통과하는 셀의 후보만 검사하고 가장 가까운 충돌을 반환한다."""
        self.ray_queries += 1
        seen, nearest = set(), None
        neighbors = math.ceil(padding / self.cell_size)
        for cell_x, cell_y in self._ray_cells(start, end):
            for x in range(cell_x - neighbors, cell_x + neighbors + 1):
                for y in range(cell_y - neighbors, cell_y + neighbors + 1):
                    for key in sorted(self.cells.get((x, y), ())):
                        if key in seen:
                            continue
                        seen.add(key)
                        collider = self.objects[key]
                        if (key in ignore_ids or not collider.active or category not in collider.blocks
                                or (ignore_doors and collider.kind == "door"
                                    and (ignore_doors == "all" or collider.ai_access))):
                            continue
                        self.candidate_tests += 1
                        fraction = collider.rect.expanded(padding).segment_entry(start, end)
                        if fraction is not None and (nearest is None or fraction < nearest[0]):
                            nearest = fraction, key
            # 진행 순서가 보장되는 무두께 ray는 첫 충돌 셀 이후를 생략한다.
            if nearest is not None and padding == 0:
                hit_x = start[0] + (end[0] - start[0]) * nearest[0]
                hit_y = start[1] + (end[1] - start[1]) * nearest[0]
                if (math.floor(hit_x / self.cell_size), math.floor(hit_y / self.cell_size)) == (cell_x, cell_y):
                    return nearest
        return nearest
