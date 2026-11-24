"""PRD의 히트스캔·한정 관통. 궤적은 짧은 표시용이며 탄약 상태와 분리한다."""

from collections import defaultdict, deque
from dataclasses import dataclass
import math
import random

from .geometry import Collider, Rect, SpatialIndex, segment_distance
from .weapons import weapon_pose


@dataclass
class Tracer:
    start: tuple
    end: tuple
    team: str
    ttl: float = 0.08


class Combat:
    def __init__(self, events, transports=None):
        self.events = events
        self.transports = transports
        self.traces = deque(maxlen=128)
        self.actor_indexes = defaultdict(SpatialIndex)
        self.actors = {}
        self.shots = self.hits = 0
        self.random = random.Random(42)

    def clear_fire_line(self, actor, target, world, *, origin=None):
        floor = actor.position.floor
        if target.position.floor != floor:
            return False
        if origin is None:
            origin, muzzle = actor.xy, weapon_pose(actor)[1]
        else:
            facing = math.atan2(target.xy[1] - origin[1], target.xy[0] - origin[0])
            muzzle = weapon_pose(actor, origin=origin, facing=facing)[1]
        if world.geometry.raycast(origin, muzzle, category="bullet") is not None:
            return False
        if world.geometry.raycast(muzzle, target.xy, category="bullet") is not None:
            return False
        if floor == 0 and self.transports is not None and self.transports.raycast(muzzle, target.xy) is not None:
            return False
        hit = self.actor_indexes[floor].raycast(muzzle, target.xy, category="bullet", ignore_ids={actor.id})
        return hit is not None and hit[1] == target.id

    def _pressure(self, shooter, world, start, end, time, damaged, *, impact=False):
        reach = 1.25
        area = Rect(min(start[0], end[0]), min(start[1], end[1]),
                    abs(end[0] - start[0]), abs(end[1] - start[1])).expanded(reach)
        dx, dy = end[0] - start[0], end[1] - start[1]
        length_squared = dx * dx + dy * dy
        for collider in self.actor_indexes[shooter.position.floor].query(area, "bullet"):
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
                blocked = world.geometry.raycast(actor.xy, closest, category="bullet") is not None
                impact_distance = math.dist(actor.xy, end)
                if blocked and (not impact or impact_distance >= 0.85):
                    continue
                strength = (0.4 * (1 - distance / reach) if not blocked
                            else 0.25 * (1 - impact_distance / 0.85))
            else:
                continue
            # 탄도에서 대략적인 유입 방향만 얻는다.
            strength *= shooter.weapon.spec.suppression_multiplier
            self.events.emit("pressure", time, actor, strength=strength, caliber=shooter.weapon.spec.damage,
                             incoming=math.atan2(-dy, -dx), hit=actor.id in damaged,
                             close=math.dist(actor.xy, start) <= 10)

    def prepare(self, actors, dt):
        remaining = deque(maxlen=128)
        for trace in self.traces:
            trace.ttl -= dt
            if trace.ttl > 0:
                remaining.append(trace)
        self.traces = remaining
        self.actor_indexes.clear()
        self.actors = {actor.id: actor for actor in actors if actor.hp > 0}
        for actor in self.actors.values():
            radius = actor.radius
            body = Rect(actor.position.x - radius, actor.position.y - radius, radius * 2, radius * 2)
            self.actor_indexes[actor.position.floor].add(Collider(actor.id, body, blocks=("bullet",)))

    def _damage(self, victim, damage, time, **context):
        if victim.hp <= 0 or damage <= 0:
            return False
        absorbed = min(victim.armor, int(damage * 0.6))
        victim.armor -= absorbed
        victim.hp = max(0, victim.hp - (damage - absorbed))
        victim.hurt_until = time + 2
        self.hits += 1
        self.events.emit("damaged", time, victim, damage=damage - absorbed)
        if victim.hp == 0:
            self.events.emit("killed", time, victim, **context)
        return True

    def fire(self, actor, world, time):
        weapon = actor.weapon
        if actor.hp <= 0 or weapon is None or time < actor.equip_until or not weapon.fire(time):
            return False
        spec = weapon.spec
        self.shots += 1
        self.events.emit("weapon_fired", time, actor, weapon=spec.id)
        suppressor = weapon.part("muzzle")
        radius = spec.noise_radius * (suppressor.noise_radius_multiplier if suppressor else 1.0)
        strength = 0.8 * (suppressor.sound_strength_multiplier if suppressor else 1.0)
        self.events.emit("noise", time, actor, radius=radius, strength=strength, reason="gunshot",
                         suppressed=suppressor is not None)
        spread = weapon.shot_spread_deg * (spec.enemy_spread_multiplier if actor.team == "enemy" else 1)
        angle = actor.facing + math.radians(self.random.uniform(-spread, spread))
        muzzle = weapon_pose(actor)[1]
        floor = actor.position.floor
        targets = self.actor_indexes[floor]
        transports = self.transports if floor == 0 else None
        barrel_hit = world.geometry.raycast(actor.xy, muzzle, category="bullet")
        barrel_truck = transports.raycast(actor.xy, muzzle) if transports is not None else None
        if barrel_hit is not None or barrel_truck is not None:
            fraction, surface = min((hit for hit in (barrel_hit, (barrel_truck[0], barrel_truck[1].id)
                                                     if barrel_truck else None) if hit is not None))
            point = (actor.position.x + (muzzle[0] - actor.position.x) * fraction,
                     actor.position.y + (muzzle[1] - actor.position.y) * fraction)
            self.traces.append(Tracer(actor.xy, point, actor.team))
            self.events.emit("impact", time, actor, surface=surface)
            return True
        damaged = set()
        for pellet in range(spec.pellets):
            dispersion = math.radians(self.random.uniform(-spec.pellet_spread_deg, spec.pellet_spread_deg)) if pellet else 0
            endpoint, impact = self._trace_round(actor, world, muzzle, angle + dispersion, time,
                                                  targets, transports, damaged)
            if pellet == 0:
                pressure_end, pressure_impact = endpoint, impact
        if actor.team == "player":
            self._pressure(actor, world, muzzle, pressure_end, time, damaged, impact=pressure_impact)
        return True

    def _trace_round(self, actor, world, muzzle, angle, time, targets, transports, damaged):
        spec = actor.weapon.spec
        end = (muzzle[0] + math.cos(angle) * spec.range_m, muzzle[1] + math.sin(angle) * spec.range_m)
        ignored_actors, ignored_walls, endpoint = {actor.id}, set(), end
        impact = False
        vehicle_hit = transports.raycast(muzzle, end) if transports is not None else None
        for count in range(spec.penetration + 1):
            wall = world.geometry.raycast(muzzle, end, category="bullet", ignore_ids=ignored_walls)
            target = targets.raycast(muzzle, end, category="bullet", ignore_ids=ignored_actors)
            if vehicle_hit is not None and all(hit is None or vehicle_hit[0] < hit[0] for hit in (wall, target)):
                hit, truck, victim = vehicle_hit
                endpoint = (muzzle[0] + (end[0] - muzzle[0]) * hit, muzzle[1] + (end[1] - muzzle[1]) * hit)
                impact = True
                if victim is not None and victim.team != actor.team:
                    damage = spec.damage_at(math.dist(muzzle, endpoint))
                    self._damage(victim, damage, time, vehicle_id=truck.id, incoming=angle + math.pi)
                break
            if target is not None and (wall is None or target[0] < wall[0]):
                impact = False
                hit, key = target
                victim = self.actors[key]
                endpoint = (muzzle[0] + (end[0] - muzzle[0]) * hit, muzzle[1] + (end[1] - muzzle[1]) * hit)
                if victim.team == actor.team:
                    break
                if self._damage(victim, spec.damage_at(math.dist(muzzle, endpoint)), time):
                    damaged.add(key)
                ignored_actors.add(key)
            elif wall is not None:
                impact = True
                hit, key = wall
                endpoint = (muzzle[0] + (end[0] - muzzle[0]) * hit, muzzle[1] + (end[1] - muzzle[1]) * hit)
                if world.geometry.objects[key].material not in {"wood", "plaster", "plywood"}:
                    break
                ignored_walls.add(key)
            else:
                impact = False
                endpoint = end
                break
        self.traces.append(Tracer(muzzle, endpoint, actor.team))
        return endpoint, impact
