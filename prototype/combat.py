"""PRD의 히트스캔·한정 관통. 궤적은 짧은 표시용이며 탄약 상태와 분리한다."""

from collections import deque
from dataclasses import dataclass
import math
import random

from campaign.viewmodel import segment_distance

from .geometry import Collider, Rect, SpatialIndex
from .weapons import weapon_pose


@dataclass
class Tracer:
    start: tuple
    end: tuple
    team: str
    ttl: float = 0.08


class Combat:
    def __init__(self, events):
        self.events = events
        self.traces = deque(maxlen=128)
        self.actor_index = SpatialIndex()
        self.actors = {}
        self.shots = self.hits = 0
        self.random = random.Random(42)

    def _pressure(self, shooter, world, start, end, time, damaged):
        reach = 1.25
        area = Rect(min(start[0], end[0]), min(start[1], end[1]),
                    abs(end[0] - start[0]), abs(end[1] - start[1])).expanded(reach)
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        for collider in self.actor_index.query(area, "bullet"):
            actor = self.actors[collider.id]
            if actor.team == shooter.team or actor.hp <= 0:
                continue
            distance = segment_distance(*actor.xy, *start, *end)
            if actor.id in damaged:
                strength = 0.7
            elif distance < reach and length_squared:
                progress = ((actor.xy[0] - start[0]) * dx + (actor.xy[1] - start[1]) * dy) / length_squared
                progress = max(0, min(1, progress))
                closest = (start[0] + progress * dx, start[1] + progress * dy)
                if world.geometry.raycast(actor.xy, closest, category="bullet") is not None:
                    continue
                strength = 0.4 * (1 - distance / reach)
            else:
                continue
            self.events.emit("pressure", time, actor, strength=strength, caliber=shooter.weapon.spec.damage)

    def prepare(self, actors, dt):
        remaining = deque(maxlen=128)
        for trace in self.traces:
            trace.ttl -= dt
            if trace.ttl > 0:
                remaining.append(trace)
        self.traces = remaining
        self.actor_index.objects.clear()
        self.actor_index.cells.clear()
        self.actors = {actor.id: actor for actor in actors if actor.hp > 0}
        for actor in self.actors.values():
            radius = actor.radius
            self.actor_index.add(Collider(actor.id, Rect(actor.position.x - radius, actor.position.y - radius,
                                                         radius * 2, radius * 2), blocks=("bullet",)))

    def fire(self, actor, world, time, *, aiming=False, moving=False, sprint=False):
        weapon = actor.weapon
        if actor.hp <= 0 or weapon is None or time < actor.equip_until or not weapon.fire(time):
            return False
        spec = weapon.spec
        self.shots += 1
        self.events.emit("weapon_fired", time, actor, weapon=spec.id)
        self.events.emit("noise", time, actor, radius=spec.noise_radius, strength=0.8, reason="gunshot")
        spread = spec.spread_degrees * (8 if actor.team == "enemy" else 1)
        spread *= (3 if moving else 1) * (2 if sprint else 1) * (0.55 if aiming else 1)
        angle = actor.facing + math.radians(self.random.uniform(-spread, spread))
        muzzle = weapon_pose(actor)[1]
        barrel_hit = world.geometry.raycast(actor.xy, muzzle, category="bullet")
        if barrel_hit is not None:
            fraction = barrel_hit[0]
            point = (actor.position.x + (muzzle[0] - actor.position.x) * fraction,
                     actor.position.y + (muzzle[1] - actor.position.y) * fraction)
            self.traces.append(Tracer(actor.xy, point, actor.team))
            self.events.emit("impact", time, actor, surface=barrel_hit[1])
            return True
        end = (muzzle[0] + math.cos(angle) * spec.range_m, muzzle[1] + math.sin(angle) * spec.range_m)
        ignored_actors, ignored_walls, endpoint = {actor.id}, set(), end
        damaged = set()
        for count in range(spec.penetration + 1):
            wall = world.geometry.raycast(muzzle, end, category="bullet", ignore_ids=ignored_walls)
            target = self.actor_index.raycast(muzzle, end, category="bullet", ignore_ids=ignored_actors)
            if target is not None and (wall is None or target[0] < wall[0]):
                hit, key = target
                victim = self.actors[key]
                endpoint = (muzzle[0] + (end[0] - muzzle[0]) * hit, muzzle[1] + (end[1] - muzzle[1]) * hit)
                if victim.team == actor.team:
                    break
                if victim.hp > 0:
                    absorbed = min(victim.armor, int(spec.damage * 0.6))
                    victim.armor -= absorbed
                    victim.hp = max(0, victim.hp - (spec.damage - absorbed))
                    victim.hurt_until = time + 2
                    damaged.add(key)
                    self.hits += 1
                    self.events.emit("damaged", time, victim, damage=spec.damage - absorbed)
                    if victim.hp == 0:
                        self.events.emit("killed", time, victim)
                ignored_actors.add(key)
            elif wall is not None:
                hit, key = wall
                endpoint = (muzzle[0] + (end[0] - muzzle[0]) * hit, muzzle[1] + (end[1] - muzzle[1]) * hit)
                if world.geometry.objects[key].material not in {"wood", "plaster", "plywood"}:
                    break
                ignored_walls.add(key)
            else:
                endpoint = end
                break
        self.traces.append(Tracer(muzzle, endpoint, actor.team))
        if actor.team == "player":
            self._pressure(actor, world, muzzle, endpoint, time, damaged)
        return True
