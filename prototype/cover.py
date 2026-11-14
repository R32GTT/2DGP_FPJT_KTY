"""벽·장애물 형상에서 엄폐 후보를 한 번 추출한다."""

import math

from .geometry import Rect
from .spatial import BoundsIndex


class CoverFinder:
    def __init__(self, world):
        self.world = world
        self.points = []
        self.index = BoundsIndex(8)
        for collider in world.geometry.objects.values():
            if collider.kind == "door":
                continue
            rect = collider.rect
            offset = 0.4
            self.points.extend(((rect.x - offset, rect.y + rect.h / 2),
                                (rect.right + offset, rect.y + rect.h / 2),
                                (rect.x + rect.w / 2, rect.y - offset),
                                (rect.x + rect.w / 2, rect.bottom + offset)))
        for index, point in enumerate(self.points):
            self.index.add(index, Rect(point[0] - 0.01, point[1] - 0.01, 0.02, 0.02), point)

    def choose(self, actor, threat, occupied=()):
        nearby = self.index.query(Rect(actor.position.x - 8, actor.position.y - 8, 16, 16))
        for point in sorted(nearby, key=lambda point: math.dist(actor.xy, point)):
            if not 0.5 < math.dist(actor.xy, point) < 8:
                continue
            if any(math.dist(point, other) < 0.7 for other in occupied):
                continue
            body = Rect(point[0] - actor.radius, point[1] - actor.radius, actor.radius * 2, actor.radius * 2)
            if any(body.overlaps(collider.rect) for collider in self.world.geometry.query(body)):
                continue
            if (self.world.geometry.raycast(threat, point) is not None
                    and self.world.navigation.clear(actor.xy, point)):
                return point
        return None
