"""주변 벽과 장애물에서 엄폐 후보를 만든다."""

import math

from .geometry import Rect


class CoverFinder:
    def __init__(self, world):
        self.world = world

    def choose(self, actor, threat, occupied=()):
        x, y = actor.xy
        offset = 0.4
        bounds = Rect(x - 8, y - 8, 16, 16).expanded(offset)
        nearby = []
        for collider in self.world.geometry.index.query(bounds):
            if collider.kind == "door" or "bullet" not in collider.blocks:
                continue
            rect = collider.rect
            nearby.extend(((rect.x - offset, rect.y + rect.h / 2),
                           (rect.right + offset, rect.y + rect.h / 2),
                           (rect.x + rect.w / 2, rect.y - offset),
                           (rect.x + rect.w / 2, rect.bottom + offset)))
        for point in sorted(nearby, key=lambda point: math.dist(actor.xy, point)):
            if math.dist(actor.xy, point) >= 8:
                continue
            if any(math.dist(point, other) < 0.7 for other in occupied):
                continue
            body = Rect(point[0] - actor.radius, point[1] - actor.radius, actor.radius * 2, actor.radius * 2)
            if any(body.overlaps(collider.rect) for collider in self.world.geometry.query(body)):
                continue
            if (self.world.geometry.raycast(threat, point, category="bullet") is not None
                    and self.world.navigation.clear(actor.xy, point)):
                return point
        return None

    def withdraw(self, actor, threat, occupied):
        x, y = actor.xy
        away = math.atan2(y - threat[1], x - threat[0])
        nav = self.world.navigation
        for distance in (3.5, 1.5):
            for angle in (away, away + math.pi / 3, away - math.pi / 3):
                point = (x + math.cos(angle) * distance, y + math.sin(angle) * distance)
                if (nav.walkable(point) and nav.clear(actor.xy, point)
                        and all(math.dist(point, other) >= 0.7 for other in occupied)):
                    return point
        return None

    def peek(self, actor, threat, *, clear_from=None):
        x, y = actor.xy
        distance = math.dist(actor.xy, threat)
        if distance < 0.1:
            return None
        dx, dy = (threat[0] - x) / distance, (threat[1] - y) / distance
        nav = self.world.navigation
        for offset in (0.65, 1.1, 1.7, 2.3):
            for side in (-1, 1):
                point = (x - side * dy * offset, y + side * dx * offset)
                if not (nav.walkable(point) and nav.clear(actor.xy, point)):
                    continue
                if (clear_from(point) if clear_from else self.world.geometry.raycast(point, threat, category="bullet") is None):
                    return point
        return None
