"""지형에서 후보를 만들고 분대원을 배정한다. 기존 배정은 그대로 예약한다."""

from dataclasses import dataclass
from itertools import count, islice
import math

from scipy.optimize import linear_sum_assignment


@dataclass
class ApproachOrder:
    destination: tuple
    waypoint: tuple | None
    passed: bool = False

    def goal_from(self, position, nav):
        if self.waypoint is not None and (math.dist(position, self.waypoint) <= 0.08
                or (math.dist(position, self.waypoint) <= 0.5 and nav.clear(position, self.destination))):
            self.waypoint = None
        return self.waypoint or self.destination

    def reachable(self, nav, origin, target, *, investigating):
        points = (self.waypoint, self.destination) if self.waypoint is not None else (self.destination,)
        return (all(nav.walkable(p) and nav.clear(a, p) for a, p in zip((origin, *points), points))
            and (nav.clear(self.destination, target) if investigating else
                 all(nav.geometry.raycast(p, target, category="sight") is None for p in points)))


@dataclass
class SquadApproach:
    target: tuple
    revision: int
    orders: dict[str, ApproachOrder | None]
    retry_at: float = math.inf
    valid_until: float = 0.0
    heading: float = 0.0

    def matches(self, target):
        return math.dist(self.target, target) <= 1.5


def _bands():
    for shell in count():
        for row in range(shell + 1):
            for column in (range(-shell, shell + 1) if row == shell else (-shell, shell)):
                yield row, column


def assign_approach(nav, members, target, distance, *, investigating=False, previous=None, occupied=(), heading=None):
    orders = {a.id: previous.get(a.id) if previous else None for a in members}
    pending = [a for a in members if orders[a.id] is None]
    center = tuple(sum(a.xy[i] for a in members) / len(members) for i in range(2))
    length = math.dist(center, target)
    if not pending or length <= 0.1:
        return orders
    heading = math.atan2(target[1] - center[1], target[0] - center[0]) if heading is None else heading
    dx, dy = math.cos(heading), math.sin(heading)
    clearance = 2 * max(a.radius for a in members) + 0.15
    reserved = [*occupied, *(order.destination for order in orders.values() if order is not None)]
    best, best_count = orders, sum(order is not None for order in orders.values())
    for preferred in (2.8, 1.8, 1.0):
        spacing = max(preferred, clearance)
        slots = []
        for row, column in islice(_bands(), max(24, len(members) * 8)):
            side = column * spacing
            depth = math.sqrt(max(0, distance ** 2 - side ** 2)) + row * spacing
            goal = (target[0] - dx * depth - dy * side, target[1] - dy * depth + dx * side)
            if nav.walkable(goal) and all(math.dist(goal, p) >= spacing - 1e-8 for p in reserved):
                slots.append((goal, side, depth, row))
        if not slots:
            continue
        options, costs = [], []
        for actor in pending:
            row_options, row_costs = [], []
            observed = math.dist(actor.xy, target)
            spread_points = [(actor.xy[0] - dy * side, actor.xy[1] + dx * side) for side in (-spacing / 2, spacing / 2)]
            can_spread = any(nav.walkable(p) and nav.clear(actor.xy, p) for p in spread_points)
            for goal, side, depth, row in slots:
                forward = (goal[0] - actor.xy[0]) * dx + (goal[1] - actor.xy[1]) * dy
                gap = min(forward * 0.65, math.sqrt(max(0, observed ** 2 - side ** 2)) - depth)
                via = (goal[0] - dx * gap, goal[1] - dy * gap) if gap >= 1 else None
                order = ApproachOrder(goal, via)
                valid = can_spread and order.reachable(nav, actor.xy, target, investigating=investigating)
                row_options.append(order if valid else None)
                row_costs.append(math.dist(actor.xy, goal) + row * distance * 2 + max(0, abs(side) - distance) * 1e3 if valid else 1e12)
            options.append(row_options)
            costs.append(row_costs)
        rows, columns = linear_sum_assignment(costs)
        candidate = orders.copy()
        for i, j in zip(rows, columns):
            if options[i][j] is not None:
                candidate[pending[i].id] = options[i][j]
        assigned = sum(o is not None for o in candidate.values())
        if assigned > best_count:
            best, best_count = candidate, assigned
        if all(best.values()):
            break
    return best
