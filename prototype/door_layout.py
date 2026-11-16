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

    def waiting_position(self, side, flank, level, radius):
        spacing = 2 * radius + 0.16
        offset = self.half_width + spacing / 2 + level * spacing
        setback = self.half_depth + radius + 0.12
        return self.point(flank * offset, side * setback)

    def waiting_slots(self, side, group, actors, navigation):
        slots = {}
        radius = max(actor.radius for actor in group)
        rows = [[self.waiting_position(side, flank, level, radius) for level in range(len(group))]
                for flank in (-1, 1)]
        for actor in group:
            candidates = []
            for row in rows:
                for point in row:
                    if (point in slots.values() or not navigation.walkable(point)
                            or not navigation.clear(actor.xy, point, clearance=actor.radius)
                            or any(other.id != actor.id and other.hp > 0
                                   and math.dist(other.xy, point) < other.radius + actor.radius + 0.05
                                   for other in actors)):
                        continue
                    candidates.append(point)
                    break
            if candidates:
                slots[actor.id] = min(candidates, key=lambda point: math.dist(actor.xy, point))
        return slots

    def waiting_looks(self, side, slots):
        looks = dict.fromkeys(slots, self.center)
        for flank in (-1, 1):
            candidates = [(abs(point[self.along] - self.center[self.along]), actor_id)
                          for actor_id, point in slots.items()
                          if flank * (point[self.along] - self.center[self.along]) > 0]
            if candidates:
                _, first = min(candidates)
                looks[first] = self.point(-flank * self.half_width * 0.7, -side * (self.half_depth + 0.12))
        return looks
