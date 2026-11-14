"""고정 틱의 몸체 이동과 벽 미끄러짐. 확대율을 사용하지 않는다."""

import math

from .geometry import Rect


def _position_blocked(actor, world, point):
    radius = actor.radius
    body = Rect(point[0] - radius, point[1] - radius, radius * 2, radius * 2)
    return any(body.overlaps(collider.rect) for collider in world.geometry.query(body))


def _body_blocker(actor, start, point, neighbors):
    for other in neighbors:
        separation = actor.radius + other.radius
        # 초기 배치에서 겹쳐 있던 병사도 서로 멀어지는 방향으로 이동할 수 있다.
        distance = math.dist(point, other.xy)
        if distance < separation - 1e-8 and distance < math.dist(start, other.xy) - 1e-8:
            return other
    return None


def move_actor(actor, direction, world, dt, *, speed=None, neighbors=()):
    magnitude = math.hypot(*direction)
    if magnitude < 1e-8:
        return None
    distance = (speed if speed is not None else actor.speed) * dt
    delta_x, delta_y = direction[0] / magnitude * distance, direction[1] / magnitude * distance
    radius, position = actor.radius, actor.position
    neighbors = [other for other in neighbors if other is not actor and other.hp > 0
                 and other.position.floor == position.floor
                 and math.dist(actor.xy, other.xy) <= distance + radius + other.radius]
    bounds = world.bounds
    blocked_by = None

    def try_step(dx, dy):
        start = actor.xy
        point = (max(bounds.x + radius, min(bounds.right - radius, start[0] + dx)),
                 max(bounds.y + radius, min(bounds.bottom - radius, start[1] + dy)))
        if point == start or _position_blocked(actor, world, point):
            return False, None
        other = _body_blocker(actor, start, point, neighbors)
        if other is not None:
            return False, other
        position.x, position.y = point
        return True, None

    # 몸체 크기 이하의 이동으로 분할해 얇은 벽도 건너뛰지 않게 한다.
    steps = max(1, math.ceil(distance / radius))
    dx, dy = delta_x / steps, delta_y / steps
    for _ in range(steps):
        moved, other = try_step(dx, dy)
        if moved:
            continue
        if other is not None:
            blocked_by = other
            # 서로 마주 보면 각자의 오른쪽이 반대쪽이 된다.
            moved, _ = try_step(-dy, dx)
            if not moved:
                try_step(dy, -dx)
            continue
        for step_x, step_y in ((dx, 0), (0, dy)):
            _, other = try_step(step_x, step_y)
            if other is not None:
                blocked_by = other
    return blocked_by
