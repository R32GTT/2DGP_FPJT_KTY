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

    def reachable(self, nav, origin, target, *, investigating):
        if self.passed:
            return True
        points = (self.waypoint, self.destination) if self.waypoint is not None else (self.destination,)
        if any(not nav.walkable(point) or not nav.clear(previous, point)
               for previous, point in zip((origin, *points), points)):
            return False
        if investigating:
            return nav.clear(self.destination, target)
        return all(nav.geometry.raycast(point, target, category="sight") is None for point in points)


@dataclass
class SquadApproach:
    target: tuple
    revision: int
    orders: dict[str, ApproachOrder | None]
    retry_at: float = math.inf

    def matches(self, target):
        return math.dist(self.target, target) <= 1.5


def assign_approach(nav, members, target, distance, *, investigating=False, previous=None):
    orders = {actor.id: previous.get(actor.id) if previous is not None else None for actor in members}
    x = sum(actor.xy[0] for actor in members) / len(members)
    y = sum(actor.xy[1] for actor in members) / len(members)
    dx, dy = target[0] - x, target[1] - y
    length = math.hypot(dx, dy)
    if length <= 0.1:
        return orders
    dx, dy = dx / length, dy / length
    minimum_spacing = 2 * max(actor.radius for actor in members) + 0.15
    nearest = sorted(members, key=lambda actor: (math.dist(actor.xy, target), actor.id))
    best, best_count = orders, max(1, sum(order is not None for order in orders.values()))
    reserved = [order.destination for order in orders.values() if order is not None]

    for preferred_spacing, shift in ((2.8, 0), (2.8, -0.5), (2.8, 0.5), (1.8, 0), (1.0, 0)):
        spacing = max(preferred_spacing, minimum_spacing)
        # 가로 폭을 다 쓰면 다음 줄로 이어서 자리를 만든다.
        columns = max(1, 1 + int(distance * 1.2 / spacing))
        candidate = orders.copy()
        count = len(reserved)
        for row, start in enumerate(range(0, len(nearest), columns)):
            band = sorted(nearest[start:start + columns],
                          key=lambda actor: (-dy * actor.xy[0] + dx * actor.xy[1], actor.id))
            for index, actor in enumerate(band):
                if orders[actor.id] is not None:
                    continue
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
                order = ApproachOrder(goal, via)
                if (any(math.dist(goal, point) < minimum_spacing for point in reserved)
                        or not order.reachable(nav, actor.xy, target, investigating=investigating)):
                    continue
                candidate[actor.id] = order
                count += 1
        if count == len(members):
            return candidate
        if count > best_count:
            best = candidate
            best_count = count
    return best
