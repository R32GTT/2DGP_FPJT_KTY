"""실내 수색은 맵의 걸을 수 있는 표본점과 실제 시야로 진행한다."""

from collections import defaultdict
from dataclasses import dataclass, field
import math

from .geometry import Rect


SAMPLE_M = 1.8
SCAN_RANGE_M = 8.0
STATIONARY_SCAN_RANGE_M = 16.0
OBSERVATION_SAMPLE_LIMIT = 32
SCAN_HOLD_S = 0.18
SCAN_HALF_CONE = math.radians(45)
SCAN_CONE_COS = math.cos(SCAN_HALF_CONE)


@dataclass(frozen=True)
class SearchArea:
    id: str
    floors: tuple[Rect, ...]
    points: tuple[tuple[float, float], ...]
    rooms: dict[tuple[float, float], int]
    room_points: dict[int, tuple[tuple[float, float], ...]]


@dataclass(frozen=True)
class LookChoice:
    point: tuple[float, float]
    offset: float
    visible_count: int


def choose_look(xy, facing, geometry, samples, *, max_offset=math.pi, scan_range=SCAN_RANGE_M):
    visible = []
    for point, weight in samples.items():
        dx, dy = point[0] - xy[0], point[1] - xy[1]
        distance = math.hypot(dx, dy)
        if not 0.15 <= distance <= scan_range:
            continue
        offset = math.remainder(math.atan2(dy, dx) - facing, math.tau)
        if (abs(offset) <= max_offset + SCAN_HALF_CONE
                and geometry.raycast(xy, point, category="sight") is None):
            visible.append((point, offset, distance, weight))
    best = None
    for point, point_angle, distance, _ in visible:
        offset = max(-max_offset, min(max_offset, point_angle))
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
            ordered = tuple(sorted(points))
            rooms = _assign_rooms(world, points)
            by_room = defaultdict(list)
            for point in ordered:
                by_room[rooms[point]].append(point)
            result.append(SearchArea(key, floors, ordered, rooms,
                                     {room: tuple(samples) for room, samples in by_room.items()}))
    return tuple(result)


@dataclass
class IndoorSweep:
    area: SearchArea
    anchor: tuple
    evidence_at: float
    checked: set = field(default_factory=set)
    claims: dict = field(default_factory=dict)
    observed_from: dict = field(default_factory=dict)
    regroup_slots: dict[str, tuple[float, float]] = field(default_factory=dict)
    regroup_swaps: set[tuple[str, str]] = field(default_factory=set)
    regroup_until: float | None = None
    regroup_done: bool = False

    def scan_forward(self, xy, facing, room, geometry, *, stationary=False):
        forward_x, forward_y = math.cos(facing), math.sin(facing)
        scan_range = STATIONARY_SCAN_RANGE_M if stationary else SCAN_RANGE_M
        for point in self.area.room_points[room]:
            if point in self.checked:
                continue
            dx, dy = point[0] - xy[0], point[1] - xy[1]
            distance = math.hypot(dx, dy)
            if (distance <= scan_range and (distance <= (0.15 if stationary else 0.075)
                    or dx * forward_x + dy * forward_y >= distance * SCAN_CONE_COS)
                    and geometry.raycast(xy, point, category="sight") is None):
                self.checked.add(point)

    def look(self, xy, facing, room, geometry, *, walking=False):
        samples = {point: 1 for point in self.area.room_points[room] if point not in self.checked}
        scan_range = SCAN_RANGE_M if walking else STATIONARY_SCAN_RANGE_M
        return choose_look(xy, facing, geometry, samples, scan_range=scan_range)

    @staticmethod
    def _visible_from(origin, point, geometry):
        return (math.dist(origin, point) <= STATIONARY_SCAN_RANGE_M
                and geometry.raycast(origin, point, category="sight") is None)

    def can_scan_here(self, origin, remaining, geometry):
        return any(self._visible_from(origin, point, geometry) for point in remaining)

    def entry_observation(self, origin, heading, candidates, remaining, navigation, *, clear_space=False):
        hidden = [point for point in remaining if not self._visible_from(origin, point, navigation.geometry)]
        if not hidden and not clear_space:
            return None
        nearby = [point for point in candidates if 0.5 <= math.dist(origin, point) <= 2 * SAMPLE_M
                  and navigation.clear(origin, point)]
        if not nearby:
            return None
        if hidden:
            point = self.observation_point(origin, heading, nearby, hidden, navigation)
            if self.can_scan_here(point, hidden, navigation.geometry):
                return point
        return min(nearby, key=lambda point: math.dist(origin, point)) if clear_space else None

    def observation_point(self, origin, heading, candidates, remaining, navigation):
        forward_x, forward_y = math.cos(heading), math.sin(heading)

        def travel_cost(point):
            dx, dy = point[0] - origin[0], point[1] - origin[1]
            # 실제로 확인할 표본이 남아 있다면 진행 방향 뒤쪽도 후보로 둔다.
            return math.hypot(dx, dy) + max(0, -(dx * forward_x + dy * forward_y))

        nearby = sorted(candidates, key=lambda point: (travel_cost(point), point))
        # 확인한 표본에도 다시 설 수 있다. 미확인 표본은 가려진 구역의 후보로 남긴다.
        pending = set(remaining)
        shortlist = dict.fromkeys(nearby[:4] + [point for point in nearby if point in pending][:4])
        stride = math.ceil(len(remaining) / OBSERVATION_SAMPLE_LIMIT)
        samples = remaining[::stride]

        def score(point):
            # 어느 지점에 서야 가구나 벽 뒤의 미확인 공간이 더 많이 드러나는지 비교한다.
            visible = sum(self._visible_from(point, other, navigation.geometry) for other in samples)
            gain = max(visible * stride, int(point in pending))
            # 우회 경로는 공용 경로 탐색에서 계산한다. 후보 평가에는 직행 여부만 사용한다.
            cost = travel_cost(point)
            if not navigation.clear(origin, point):
                cost += 2 * SAMPLE_M
            return gain > 0, gain - cost / (2 * SAMPLE_M), -cost, point

        return max(shortlist, key=score)
