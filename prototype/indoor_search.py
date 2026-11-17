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
CORNER_SHARE_WINDOW_S = 1.5
SEARCHER_ACTIVE_S = 0.2


@dataclass(frozen=True)
class SearchPose:
    position: tuple[float, float]
    look: tuple[float, float]
    room: int


@dataclass(frozen=True)
class SearchArea:
    id: str
    floors: tuple[Rect, ...]
    points: tuple[tuple[float, float], ...]
    rooms: dict[tuple[float, float], int]
    room_points: dict[int, tuple[tuple[float, float], ...]]
    room_corners: dict[int, tuple[tuple[float, float], ...]]
    corner_axis: int | None = None
    entry_poses: tuple[SearchPose, ...] = ()
    observations: dict[tuple[float, float], int] = field(default_factory=dict)

    def room_of(self, point):
        room = self.rooms.get(point, self.observations.get(point))
        if room is not None:
            return room
        return next((pose.room for pose in self.entry_poses if pose.position == point), None)


@dataclass(frozen=True)
class LookChoice:
    point: tuple[float, float]
    offset: float
    visible_count: int


def sample_views(xy, facing, samples, geometry, *, stationary=False, include_outside=False):
    forward_x, forward_y = math.cos(facing), math.sin(facing)
    scan_range = STATIONARY_SCAN_RANGE_M if stationary else SCAN_RANGE_M
    near = 0.15 if stationary else 0.075
    for point in samples:
        dx, dy = point[0] - xy[0], point[1] - xy[1]
        distance = math.hypot(dx, dy)
        if distance > scan_range:
            continue
        in_view = distance <= near or dx * forward_x + dy * forward_y >= distance * SCAN_CONE_COS
        if in_view or include_outside:
            clear = geometry.raycast(xy, point, category="sight") is None
            # 시야 밖에 있지만 장애물로 가려지지 않은 표본도 디버그에서 구별한다.
            yield point, (True if in_view else None) if clear else False


def choose_look(xy, facing, geometry, samples, *, max_offset=math.pi, scan_range=SCAN_RANGE_M,
                continue_scan=False):
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
        nearby_angle = continue_scan and abs(point_angle) <= math.radians(60)
        score = (nearby_angle, sum(row[3] for row in seen), sum(row[2] for row in seen),
                 -abs(offset), distance, point)
        if best is None or score > best[0]:
            best = score, LookChoice(point, offset, len(seen))
    return best[1] if best else None


def _assign_rooms(world, points, spacing):
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
            connected = [other for other in remaining if math.dist(point, other) <= spacing * 1.5
                         and world.navigation.clear(point, other, ignore_doors=True)
                         and not any(door.segment_entry(point, other) is not None for door in doors)]
            remaining.difference_update(connected)
            pending.extend(connected)
        room += 1
    return rooms


def _sample_corners(points):
    # 방의 수색 표본 격자에서 각 모서리에 가장 가까운 유효 표본을 사용한다.
    xs, ys = zip(*points)
    return tuple(dict.fromkeys(
        min(points, key=lambda point: (math.dist(point, (x, y)), point))
        for x in (min(xs), max(xs)) for y in (min(ys), max(ys))
    ))


def build_search_areas(world):
    areas = dict(world.data.get("search_areas", {}))
    for facility in world.facilities:
        prefix = f"{facility['id']}/"
        floors = [layer.rect for layer in world.layers if layer.style == "floor" and layer.id.startswith(prefix)]
        if floors and facility["id"] not in areas:
            areas[facility["id"]] = floors
    result = []
    for key, rows in areas.items():
        layout = world.data.get("search_layouts", {}).get(key, {})
        spacing = layout.get("sample_spacing", SAMPLE_M)
        if not math.isfinite(spacing) or spacing <= 0:
            raise ValueError(f"수색 표본 간격 오류: {key}")
        floors = tuple(row if isinstance(row, Rect) else Rect(*row) for row in rows)
        points = set()
        for rect in floors:
            columns, lines = math.ceil(rect.w / spacing), math.ceil(rect.h / spacing)
            for col in range(columns):
                for row in range(lines):
                    point = (rect.x + (col + 0.5) * rect.w / columns,
                             rect.y + (row + 0.5) * rect.h / lines)
                    if world.navigation.walkable(point):
                        points.add(point)
        authored_corners = tuple(tuple(point) for point in layout.get("corner_points", ()))
        for point in authored_corners:
            if (not any(rect.segment_entry(point, point) is not None for rect in floors)
                    or not world.navigation.walkable(point)):
                raise ValueError(f"수색 코너 위치에 접근할 수 없음: {key} {point}")
            points.add(point)
        if points:
            ordered = tuple(sorted(points))
            rooms = _assign_rooms(world, points, spacing)
            by_room = defaultdict(list)
            for point in ordered:
                by_room[rooms[point]].append(point)
            room_points = {room: tuple(samples) for room, samples in by_room.items()}
            corner_first = layout.get("corner_first", True)
            corners_by_room = defaultdict(list)
            for point in dict.fromkeys(authored_corners):
                corners_by_room[rooms[point]].append(point)
            room_corners = {room: (tuple(corners_by_room[room]) or _sample_corners(samples)) if corner_first else ()
                            for room, samples in room_points.items()}
            observations = {}
            for row in layout.get("observation_points", ()):
                point = tuple(row)
                if not world.navigation.walkable(point):
                    continue
                sample = min((p for p in ordered if world.navigation.clear(point, p)),
                             key=lambda p: (math.dist(point, p), p), default=None)
                if sample is not None:
                    observations[point] = rooms[sample]
            poses = []
            for row in layout.get("entry_poses", ()):
                position = tuple(row["position"])
                nearest = min((p for p in ordered if world.navigation.clear(position, p)),
                              key=lambda p: (math.dist(position, p), p), default=None)
                if nearest is None or not world.navigation.walkable(position):
                    raise ValueError(f"수색 시작 위치에 접근할 수 없음: {key} {position}")
                poses.append(SearchPose(position, tuple(row["look"]), rooms[nearest]))
            result.append(SearchArea(key, floors, ordered, rooms, room_points, room_corners,
                                     corner_axis=layout.get("corner_axis"), entry_poses=tuple(poses),
                                     observations=observations))
    return tuple(result)


@dataclass
class IndoorSweep:
    area: SearchArea
    anchor: tuple
    evidence_at: float
    checked: set = field(default_factory=set)
    checked_at: dict[tuple[float, float], float] = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    corner_claims: dict[str, tuple[tuple[float, float], float]] = field(default_factory=dict)
    corner_checks: dict[tuple[float, float], tuple[str, float]] = field(default_factory=dict)
    sound_searchers: dict[str, float] = field(default_factory=dict)
    entry_order: list[str] = field(default_factory=list)
    entry_ready: bool = False
    observed_from: dict = field(default_factory=dict)
    regroup_slots: dict[str, tuple[float, float]] = field(default_factory=dict)
    regroup_swaps: set[tuple[str, str]] = field(default_factory=set)
    regroup_until: float | None = None
    regroup_done: bool = False

    def entry_pose(self, actor_id):
        return next((pose for member, pose in zip(self.entry_order, self.area.entry_poses) if member == actor_id), None)

    def pending_view(self, observers, geometry, *, samples=None):
        samples = self.area.points if samples is None else samples
        pending = tuple(point for point in samples if point not in self.checked)
        visible, clear_line, blocked = set(), set(), set()
        for xy, facing, stationary in observers:
            for point, status in sample_views(xy, facing, pending, geometry, stationary=stationary,
                                              include_outside=True):
                if status is False:
                    blocked.add(point)
                else:
                    clear_line.add(point)
                    if status is True:
                        visible.add(point)
        return visible, blocked - clear_line

    def pending_corners(self, room):
        if room is None:
            points = (point for corners in self.area.room_corners.values() for point in corners)
        else:
            points = self.area.room_corners[room]
        return tuple(point for point in points if point not in self.checked)

    def pending_for(self, actor_id, room, *, revisit_corners=True):
        rooms = self.area.room_points if room is None else (room,)
        result = []
        for key in rooms:
            points = self.area.room_points[key]
            corners = self.area.room_corners[key]
            pending = tuple(point for point in points if point not in self.checked)
            if any(self.corner_checks.get(point, (None,))[0] == actor_id for point in corners):
                inside = tuple(point for point in pending if point not in corners)
                pending = inside if inside or not revisit_corners else pending
            result.extend(pending)
        return tuple(result)

    def available_corners(self, corners, actor_id, time):
        if not corners:
            return ()
        claimed = {point for owner, (point, until) in self.corner_claims.items()
                   if owner != actor_id and time < until}
        available = set(corners) - claimed
        if any(owner != actor_id and time - at < SEARCHER_ACTIVE_S for owner, at in self.sound_searchers.items()):
            for room in {self.area.rooms[point] for point in available}:
                samples = self.area.room_corners[room]
                checks = [point for point in self.corner_checks if point in samples]
                if checks:
                    first = checks[0]
                    leader, at = self.corner_checks[first]
                    if (time - at >= CORNER_SHARE_WINDOW_S
                            or any(self.corner_checks[point][0] != leader for point in checks)):
                        continue
                else:
                    current = next(((owner, point) for owner, (point, until) in self.corner_claims.items()
                                    if point in samples and time < until), None)
                    if current is None or current[0] == actor_id:
                        continue
                    leader, first = current
                axis = self.area.corner_axis
                if axis is None:
                    axis = max(range(2), key=lambda i: max(p[i] for p in samples) - min(p[i] for p in samples))
                middle = (min(p[axis] for p in samples) + max(p[axis] for p in samples)) / 2
                opposite = {p for p in samples if (p[axis] - middle) * (first[axis] - middle) < 0}
                if actor_id == leader:
                    available.difference_update(samples)
                elif available & opposite:
                    available.difference_update(set(samples) - opposite)
        return tuple(point for point in corners if point in available)

    def confirm_corner(self, point, actor_id, time):
        self.confirm(point, time)
        self.corner_checks[point] = actor_id, time

    def confirm(self, point, time):
        self.checked.add(point)
        self.checked_at[point] = time

    def room_near(self, point):
        sample = min(self.area.points, key=lambda sample: (math.dist(point, sample), sample))
        return self.area.rooms[sample]

    def reopen_room(self, point):
        room = self.room_near(point)
        for sample in self.area.room_points[room]:
            self.checked.discard(sample)
            self.checked_at.pop(sample, None)
            self.corner_checks.pop(sample, None)
        for sample in tuple(self.observed_from):
            if self.area.room_of(sample) == room:
                self.observed_from.pop(sample)
        self.reset_regroup()

    def reset_regroup(self):
        self.regroup_slots.clear()
        self.regroup_swaps.clear()
        self.regroup_until = None
        self.regroup_done = False

    def look(self, xy, facing, room, geometry, *, walking=False, continue_scan=False, points=None):
        if points is None:
            points = self.area.points if room is None else self.area.room_points[room]
        samples = {point: 1 for point in points if point not in self.checked}
        scan_range = SCAN_RANGE_M if walking else STATIONARY_SCAN_RANGE_M
        return choose_look(xy, facing, geometry, samples, scan_range=scan_range, continue_scan=continue_scan)

    @staticmethod
    def _visible_from(origin, point, geometry):
        return (math.dist(origin, point) <= STATIONARY_SCAN_RANGE_M
                and geometry.raycast(origin, point, category="sight") is None)

    def can_scan_here(self, origin, remaining, geometry):
        return any(self._visible_from(origin, point, geometry) for point in remaining)

    def entry_observation(self, origin, heading, candidates, remaining, navigation, *, reserved=()):
        geometry = navigation.geometry
        nearby = [point for point in candidates if point not in reserved
                  and 0.5 <= math.dist(origin, point) <= 2 * SAMPLE_M
                  and navigation.clear(origin, point)]
        if not nearby:
            return None

        def separation(point):
            return min(math.dist(point, other) for other in reserved)

        if reserved:
            separated = [point for point in nearby if separation(point) >= SAMPLE_M]
            nearby = separated or nearby

        hidden = [point for point in remaining if not self._visible_from(origin, point, geometry)
                  and not any(self._visible_from(other, point, geometry) for other in reserved)]
        if hidden:
            point = self.observation_point(origin, heading, nearby, hidden, navigation)
            if self.can_scan_here(point, hidden, geometry):
                return point
        if reserved:
            # 다른 지점에서 새 표본이 드러나지 않아도 관찰 위치와 통로를 서로 차지하지 않게 한다.
            return max(nearby, key=lambda point: min(separation(point), 2 * SAMPLE_M) - math.dist(origin, point) / 2)
        return None

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
