"""관측한 정보와 현재 임무로 적군의 이동과 전투를 결정한다."""

from dataclasses import dataclass, field
import math

from .cover import CoverFinder
from .perception import Knowledge, hear, observe


@dataclass
class Brain:
    patrol: list
    memory: Knowledge = field(default_factory=Knowledge)
    waypoint: int = 0
    sense_at: float = 0.0
    plan_at: float = 0.0
    path: list = field(default_factory=list)
    goal: tuple | None = None
    path_revision: int = -1
    route_failed: bool = False
    fire_at: float = 0.0
    burst: int = 0
    cover: tuple | None = None
    cover_until: float = 0.0
    cue: tuple | None = None
    search_points: list = field(default_factory=list)
    scan_until: float = 0.0
    scan_angle: float = 0.0
    look_until: float = 0.0
    checked_sound_at: float = -1000.0
    far_at: float = 0.0
    far_updated: float = 0.0

    @property
    def visible(self):
        return self.memory.visible


@dataclass
class Intent:
    direction: tuple = (0, 0)
    fire: bool = False
    reload: bool = False
    open_door: bool = False
    step_dt: float = 1 / 60


class EnemyAI:
    def __init__(self, world, actors, patrols):
        self.world = world
        self.cover_finder = CoverFinder(world)
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.01)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}

    def receive(self, actors, events):
        noises = [event for event in events if event.kind == "noise"]
        for actor in actors:
            if actor.hp <= 0:
                continue
            brain = self.brains[actor.id]
            for event in noises:
                old_sound_at = brain.memory.sound_at
                hear(actor, brain.memory, event)
                if brain.memory.sound_at != old_sound_at:
                    brain.look_until = event.time + 0.65
                    brain.sense_at = min(brain.sense_at, event.time)

    @staticmethod
    def _face(actor, point):
        dx, dy = point[0] - actor.position.x, point[1] - actor.position.y
        if math.hypot(dx, dy) > 0.05:
            actor.facing = math.atan2(dy, dx)

    def _search(self, actor, brain, cue, time):
        if cue != brain.cue:
            brain.cue = cue
            heading = brain.memory.seen_heading if cue[0] == "search" else None
            angle = actor.facing if heading is None else heading
            x, y = cue[1]
            offsets = ((0, 0), (4, 0), (3, 3), (3, -3), (-2, 4), (-2, -4))
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            brain.search_points = [(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)
                                   for dx, dy in offsets]
            brain.scan_until = 0
        if time < brain.scan_until:
            actor.facing = brain.scan_angle + math.sin((brain.scan_until - time) * 5) * 0.95
            return None
        while brain.search_points:
            point = brain.search_points[0]
            if brain.goal == point and not brain.path and brain.route_failed:
                brain.search_points.pop(0)
                continue
            if math.dist(actor.xy, point) < 0.35 or (brain.goal == point and not brain.path):
                brain.search_points.pop(0)
                brain.scan_until, brain.scan_angle = time + 0.85, actor.facing
                return None
            return point
        return None

    def _move(self, actor, brain, intent, goal, time, budget, *, face=True, approximate=False):
        if goal is None:
            brain.path.clear()
            brain.goal = None
            return budget
        nav = self.world.navigation
        changed = brain.goal is None or math.dist(brain.goal, goal) > (0.9 if actor.state == "combat" else 0.15)
        invalid = brain.path_revision != self.world.revision
        if budget and (changed or invalid) and time >= brain.plan_at:
            options = nav.nearby_goals(goal, True) if approximate else [goal]
            brain.path = []
            for point in options:
                brain.path = nav.route(actor.xy, point, allow_doors=True)
                if brain.path:
                    break
            brain.goal, brain.path_revision, brain.plan_at = goal, self.world.revision, time + 0.2
            brain.route_failed = not brain.path
            if brain.route_failed and not approximate:
                brain.path_revision = -1
            budget -= 1
        if changed and brain.goal != goal and actor.state != "combat":
            return budget
        if invalid and brain.path_revision != self.world.revision:
            return budget
        while brain.path and math.dist(actor.xy, brain.path[0]) < 0.045:
            brain.path.pop(0)
        if not brain.path:
            return budget
        point = brain.path[0]
        if face:
            self._face(actor, point)
        intent.direction = point[0] - actor.position.x, point[1] - actor.position.y
        intent.step_dt = min(intent.step_dt, math.hypot(*intent.direction) / actor.speed)
        intent.open_door = True
        return budget

    def _patrol(self, actor, brain):
        if actor.boarding_goal is not None:
            return "boarding", actor.boarding_goal
        goal = brain.patrol[brain.waypoint] if brain.patrol else actor.xy
        if not brain.patrol or len(brain.patrol) == 1 and math.dist(actor.xy, goal) < 0.25:
            return "idle", None
        if math.dist(actor.xy, goal) < 0.25:
            brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
            return "patrol", None
        state = "return" if actor.state in {"combat", "search", "alert", "return", "retreat"} else "patrol"
        return state, goal

    def update(self, actors, player, time):
        intents, budget = {}, 1
        self.near_count = 0
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            memory = brain.memory
            investigating = (memory.last_sound is not None and memory.sound_at > brain.checked_sound_at
                             and time - memory.sound_at < 14
                             and (memory.suspicion >= 0.3 or actor.state == "alert"))
            tracking = memory.last_seen is not None and time - memory.seen_at < 16
            if math.dist(actor.xy, player.xy) > 60 and not (investigating or tracking):
                if time < brain.far_at:
                    continue
                intent.step_dt = min(0.5, time - brain.far_updated)
                brain.far_at, brain.far_updated = time + 0.5, time
                memory.visible = False
                actor.state, goal = self._patrol(actor, brain)
                budget = self._move(actor, brain, intent, goal, time, budget)
                intents[actor.id] = intent
                continue
            brain.far_updated = time
            self.near_count += 1
            if not (tracking or investigating) and time < brain.look_until:
                self._face(actor, memory.last_sound)
            if time >= brain.sense_at:
                observe(actor, player, self.world.geometry, memory, time)
                brain.sense_at = time + (0.10 if actor.state in {"combat", "search"} else 0.18)
                memory.suspicion = max(0, memory.suspicion - 0.01)
            old_state = actor.state
            if memory.visible:
                actor.state = "combat"
                brain.cue = None
                goal = memory.last_seen if math.dist(actor.xy, memory.last_seen) >= 9 else None
                self._face(actor, memory.last_seen)
                if old_state != "combat":
                    brain.fire_at, brain.burst = time + 0.7, 0
                if time >= brain.fire_at and actor.weapon and not actor.weapon.reloading:
                    intent.fire = True
                    brain.burst = (brain.burst + 1) % 3
                    brain.fire_at = time + (0.25 if brain.burst else 1.2)
            elif tracking or investigating:
                actor.state = "search" if tracking else "alert"
                point = memory.last_seen if tracking else memory.last_sound
                if investigating and (not tracking or memory.sound_at > memory.seen_at + 0.5):
                    point = memory.last_sound
                goal = self._search(actor, brain, (actor.state, point), time)
                if not brain.search_points and time >= brain.scan_until:
                    brain.checked_sound_at = memory.sound_at
                    memory.last_seen = None
                    actor.state, goal = self._patrol(actor, brain)
            elif time < brain.look_until:
                actor.state, goal = "idle", None
            else:
                brain.cue = None
                actor.state, goal = self._patrol(actor, brain)
            weapon = actor.weapon
            if weapon and not weapon.chambered and not weapon.reloading:
                intent.reload = True
                if weapon.reserve <= 0 and weapon.stage == 0:
                    actor.state, intent.fire, intent.reload = "retreat", False, False
                    reference = memory.last_seen or memory.last_sound or actor.xy
                    goal = max(brain.patrol, key=lambda point: math.dist(point, reference), default=None)
            threat = memory.last_seen if tracking or memory.visible else memory.last_sound if investigating else None
            if threat is not None and (actor.hurt_until > time or actor.hp < 20) and time >= brain.cover_until:
                occupied = [self.brains[other.id].cover for other in actors if other.id != actor.id and other.hp > 0
                            and self.brains[other.id].cover is not None and self.brains[other.id].cover_until > time]
                brain.cover = self.cover_finder.choose(actor, threat, occupied)
                brain.cover_until = time + 2.5
            if brain.cover is not None and time < brain.cover_until:
                actor.state, goal = "retreat", brain.cover
                intent.fire = False
            budget = self._move(actor, brain, intent, goal, time, budget, face=actor.state != "combat",
                                approximate=actor.state in {"alert", "search"})
            intents[actor.id] = intent
        return intents
