"""실내 수색은 맵의 걸을 수 있는 표본점과 실제 시야로 진행한다."""

from dataclasses import dataclass, field
import math

from .geometry import Rect


SAMPLE_M = 2.4
SCAN_RANGE_M = 8.0
SCAN_SECONDS = 0.85
SCAN_INTERVAL = 0.2
SCAN_HALF_CONE = math.radians(45)
SCAN_CONE_COS = math.cos(SCAN_HALF_CONE)
MAX_LOOK_OFFSET = math.radians(85)


@dataclass(frozen=True)
class SearchArea:
    id: str
    floors: tuple[Rect, ...]
    points: tuple[tuple[float, float], ...]
    rooms: dict[tuple[float, float], int]


@dataclass(frozen=True)
class LookChoice:
    point: tuple[float, float]
    offset: float
    visible_count: int


def choose_look(xy, facing, geometry, samples, *, max_offset=MAX_LOOK_OFFSET):
    visible = []
    for point, weight in samples.items():
        dx, dy = point[0] - xy[0], point[1] - xy[1]
        distance = math.hypot(dx, dy)
        if not 0.15 <= distance <= SCAN_RANGE_M:
            continue
        offset = math.remainder(math.atan2(dy, dx) - facing, math.tau)
        if (abs(offset) <= max_offset + SCAN_HALF_CONE
                and geometry.raycast(xy, point, category="sight") is None):
            visible.append((point, offset, distance, weight))
    best = None
    for point, offset, distance, _ in visible:
        if abs(offset) > max_offset:
            continue
        seen = [row for row in visible if abs(math.remainder(row[1] - offset, math.tau)) <= SCAN_HALF_CONE]
        # 가까운 표본 하나보다 시야로 확인할 수 있는 공간 전체를 비교한다.
        score = (sum(row[3] for row in seen), sum(row[2] for row in seen), -abs(offset), distance, point)
        if best is None or score > best[0]:
            best = score, LookChoice(point, offset, len(seen))
    return best[1] if best else None


def _assign_rooms(world, points):
    # 문을 열어도 이미 나눈 수색 구역의 경계는 유지한다.
    doors = [item.rect for item in world.geometry.objects.values() if item.kind == "door"]
    remaining = set(points)
    rooms = {}
    room = 0
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        pending = [start]
        while pending:
            point = pending.pop()
            rooms[point] = room
            connected = [other for other in remaining if math.dist(point, other) <= SAMPLE_M * 1.5
                         and world.navigation.clear(point, other, ignore_doors=True)
                         and not any(door.segment_entry(point, other) is not None for door in doors)]
            remaining.difference_update(connected)
            pending.extend(connected)
        room += 1
    return rooms


def build_search_areas(world):
    areas = dict(world.data.get("search_areas", {}))
    for facility in world.facilities:
        prefix = f"{facility['id']}/"
        floors = [layer.rect for layer in world.layers if layer.style == "floor" and layer.id.startswith(prefix)]
        if floors and facility["id"] not in areas:
            areas[facility["id"]] = floors
    result = []
    for key, rows in areas.items():
        floors = tuple(row if isinstance(row, Rect) else Rect(*row) for row in rows)
        points = set()
        for rect in floors:
            columns, lines = math.ceil(rect.w / SAMPLE_M), math.ceil(rect.h / SAMPLE_M)
            for col in range(columns):
                for row in range(lines):
                    point = (rect.x + (col + 0.5) * rect.w / columns,
                             rect.y + (row + 0.5) * rect.h / lines)
                    if world.navigation.walkable(point):
                        points.add(point)
        if points:
            result.append(SearchArea(key, floors, tuple(sorted(points)), _assign_rooms(world, points)))
    return tuple(result)


@dataclass
class IndoorSweep:
    area: SearchArea
    anchor: tuple
    evidence_at: float
    checked: set = field(default_factory=set)
    claims: dict = field(default_factory=dict)
    regroup_slots: dict[str, tuple[float, float]] = field(default_factory=dict)
    regroup_until: float | None = None
    regroup_done: bool = False

    def scan_forward(self, xy, facing, geometry):
        forward_x, forward_y = math.cos(facing), math.sin(facing)
        for point in self.area.points:
            if point in self.checked:
                continue
            dx, dy = point[0] - xy[0], point[1] - xy[1]
            distance = math.hypot(dx, dy)
            if (distance <= SCAN_RANGE_M and (distance <= 0.075
                    or dx * forward_x + dy * forward_y >= distance * SCAN_CONE_COS)
                    and geometry.raycast(xy, point, category="sight") is None):
                self.checked.add(point)

    def look(self, xy, facing, room, geometry):
        samples = {point: 2 if self.area.rooms[point] == room else 1
                   for point in self.area.points if point not in self.checked}
        return choose_look(xy, facing, geometry, samples)
