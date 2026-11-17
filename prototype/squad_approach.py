"""같은 대상을 본 분대원의 접근 위치를 이동 가능한 공간에 배정한다."""

from dataclasses import dataclass
import math


@dataclass
class ApproachOrder:
    destination: tuple
    waypoint: tuple | None
    passed: bool = False

    def goal_from(self, position):
        if self.waypoint is not None and math.dist(position, self.waypoint) <= 0.08:
            self.waypoint = None
        return self.waypoint or self.destination


@dataclass
class SquadApproach:
    target: tuple
    revision: int
    orders: dict[str, ApproachOrder | None]
    retry_at: float = math.inf

    def matches(self, target, revision, members):
        return (self.revision == revision and math.dist(self.target, target) <= 1.5
                and all(actor.id in self.orders for actor in members))


def assign_approach(nav, members, target, distance, *, investigating=False):
    orders = dict.fromkeys(actor.id for actor in members)
    x = sum(actor.xy[0] for actor in members) / len(members)
    y = sum(actor.xy[1] for actor in members) / len(members)
    dx, dy = target[0] - x, target[1] - y
    length = math.hypot(dx, dy)
    if length <= 0.1:
        return orders
    dx, dy = dx / length, dy / length
    minimum_spacing = 2 * max(actor.radius for actor in members) + 0.15
    nearest = sorted(members, key=lambda actor: (math.dist(actor.xy, target), actor.id))
    best_count = 1

    for preferred_spacing, shift in ((2.8, 0), (2.8, -0.5), (2.8, 0.5), (1.8, 0), (1.0, 0)):
        spacing = max(preferred_spacing, minimum_spacing)
        # 가로 폭을 다 쓰면 다음 줄로 이어서 자리를 만든다.
        columns = max(1, 1 + int(distance * 1.2 / spacing))
        candidate = dict.fromkeys(orders)
        count = 0
        for row, start in enumerate(range(0, len(nearest), columns)):
            band = sorted(nearest[start:start + columns],
                          key=lambda actor: (-dy * actor.xy[0] + dx * actor.xy[1], actor.id))
            for index, actor in enumerate(band):
                side = (index - (len(band) - 1) / 2 + shift) * spacing
                depth = math.sqrt(max(0, distance ** 2 - side ** 2)) + row * spacing
                goal = (target[0] - dx * depth - dy * side, target[1] - dy * depth + dx * side)
                ax, ay = actor.xy
                forward = (goal[0] - ax) * dx + (goal[1] - ay) * dy
                observed_distance = math.dist(actor.xy, target)
                if forward < -spacing or math.dist(actor.xy, goal) > observed_distance:
                    continue
                # 초반에 옆으로 벌어진 뒤 같은 줄을 따라 마저 전진한다.
                # 옆으로 이동하면서 현재 관측 거리보다 더 멀어지지 않게 한다.
                available_depth = math.sqrt(max(0, observed_distance ** 2 - side ** 2)) - depth
                gap = min(forward * 0.65, available_depth)
                via = (goal[0] - dx * gap, goal[1] - dy * gap) if gap >= 1.0 else None
                points = (via, goal) if via is not None else (goal,)
                if any(not nav.walkable(point) or not nav.clear(previous, point)
                       for previous, point in zip((actor.xy, *points), points)):
                    continue
                if investigating and not nav.clear(goal, target):
                    continue
                if not investigating and any(nav.geometry.raycast(point, target, category="sight") is not None
                                             for point in points):
                    continue
                candidate[actor.id] = ApproachOrder(goal, via)
                count += 1
        if count == len(members):
            return candidate
        if count > best_count:
            orders = candidate
            best_count = count
    return orders
