"""문 충돌 영역과 이동 가능 공간에서 NPC 대기 자리를 찾는다."""

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class DoorLayout:
    center: tuple[float, float]
    along: int
    half_width: float
    half_depth: float

    @classmethod
    def from_rect(cls, rect):
        center = (rect.x + rect.w / 2, rect.y + rect.h / 2)
        along = 0 if rect.w >= rect.h else 1
        return cls(center, along, max(rect.w, rect.h) / 2, min(rect.w, rect.h) / 2)

    @property
    def across(self):
        return 1 - self.along

    def point(self, along, across):
        xy = list(self.center)
        xy[self.along] += along
        xy[self.across] += across
        return tuple(xy)

    def waiting_slots(self, side, group, actors, navigation):
        slots = {}
        radius = max(actor.radius for actor in group)
        spacing = 2 * radius + 0.16
        setback = side * (self.half_depth + radius + 0.12)
        for actor in group:
            for level in range(len(group)):
                offset = self.half_width + spacing / 2 + level * spacing
                candidates = (self.point(-offset, setback), self.point(offset, setback))
                for point in sorted(candidates, key=lambda point: math.dist(actor.xy, point)):
                    if (point in slots.values() or not navigation.walkable(point)
                            or not navigation.clear(actor.xy, point, clearance=actor.radius)
                            or any(other.id != actor.id and other.hp > 0
                                   and math.dist(other.xy, point) < other.radius + actor.radius + 0.05
                                   for other in actors)):
                        continue
                    slots[actor.id] = point
                    break
                if actor.id in slots:
                    break
        return slots
