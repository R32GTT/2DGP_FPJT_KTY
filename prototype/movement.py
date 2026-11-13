"""고정 틱의 몸체 이동과 벽 미끄러짐. 확대율을 사용하지 않는다."""

import math

from .geometry import Rect


def move_actor(actor, direction, world, dt, *, speed=None):
    magnitude = math.hypot(*direction)
    if magnitude < 1e-8:
        return
    distance = (speed if speed is not None else actor.speed) * dt
    delta_x, delta_y = direction[0] / magnitude * distance, direction[1] / magnitude * distance
    radius, position = actor.radius, actor.position
    # 몸체 크기 이하의 이동으로 분할해 얇은 벽도 건너뛰지 않게 한다.
    steps = max(1, math.ceil(distance / radius))
    for _ in range(steps):
        for axis, delta in (("x", delta_x / steps), ("y", delta_y / steps)):
            old = getattr(position, axis)
            setattr(position, axis, old + delta)
            bounds = world.bounds
            position.x = max(bounds.x + radius, min(bounds.right - radius, position.x))
            position.y = max(bounds.y + radius, min(bounds.bottom - radius, position.y))
            body = Rect(position.x - radius, position.y - radius, radius * 2, radius * 2)
            if any(body.overlaps(collider.rect) for collider in world.geometry.query(body)):
                setattr(position, axis, old)
