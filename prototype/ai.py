"""관측 정보에서 순찰·조사·교전·수색·복귀를 선택한다. 그리기·입력과 독립적이다."""

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
    fire_at: float = 0.0
    burst: int = 0
    cover: tuple | None = None
    cover_until: float = 0.0
    search_index: int = 0
    path_revision: int = -1
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


class PatrolAI:
    def __init__(self, world, actors, patrols):
        self.world = world
        self.cover_finder = CoverFinder(world)
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.07)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}

    def receive(self, actors, events):
        for actor in actors:
            if actor.hp > 0:
                for event in events:
                    hear(actor, self.brains[actor.id].memory, event)

    def update(self, actors, player, time):
        intents, budget = {}, 1
        self.near_count = 0
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            if math.dist(actor.xy, player.xy) > 60:
                if time < brain.far_at:
                    continue
                elapsed = min(0.5, max(0, time - brain.far_updated))
                brain.far_at, brain.far_updated = time + 0.5, time
                brain.memory.visible = False
                goal = brain.patrol[brain.waypoint]
                if math.dist(actor.xy, goal) < 0.35:
                    brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
                    goal = brain.patrol[brain.waypoint]
                if budget and (brain.goal != goal or not brain.path or brain.path_revision != self.world.revision):
                    brain.path = self.world.navigation.route(actor.xy, goal, allow_doors=True)
                    brain.goal, brain.path_revision = goal, self.world.revision
                    budget -= 1
                while brain.path and math.dist(actor.xy, brain.path[0]) < 0.16:
                    brain.path.pop(0)
                actor.state = "patrol"
                if brain.path:
                    dx, dy = brain.path[0][0] - actor.position.x, brain.path[0][1] - actor.position.y
                    actor.facing = math.atan2(dy, dx)
                    intents[actor.id] = Intent((dx, dy), open_door=True,
                                               step_dt=min(elapsed, math.hypot(dx, dy) / actor.speed))
                continue
            self.near_count += 1
            memory = brain.memory
            if time >= brain.sense_at:
                observe(actor, player, self.world.geometry, memory, time)
                brain.sense_at = time + 0.15
                memory.suspicion = max(0, memory.suspicion - 0.01)
            old_state = actor.state
            if memory.visible:
                actor.state = "combat"
                goal = memory.last_seen
                if old_state != "combat":
                    brain.fire_at, brain.burst = time + 0.7, 0
                if time >= brain.fire_at and actor.weapon and not actor.weapon.reloading:
                    intent.fire = True
                    brain.burst += 1
                    brain.fire_at = time + (0.25 if brain.burst < 3 else 1.2)
                    if brain.burst == 3:
                        brain.burst = 0
            elif memory.last_seen is not None and time - memory.seen_at < 10:
                actor.state = "search"
                offsets = ((0, 0), (2, 0), (0, 2), (-2, 0), (0, -2))
                dx, dy = offsets[brain.search_index % len(offsets)]
                goal = memory.last_seen[0] + dx, memory.last_seen[1] + dy
                if math.dist(actor.xy, goal) < 0.5:
                    brain.search_index += 1
            elif memory.last_sound is not None and time - memory.sound_at < 8 and memory.suspicion > 0.2:
                actor.state, goal = "alert", memory.last_sound
            else:
                goal = brain.patrol[brain.waypoint]
                actor.state = "return" if old_state in {"combat", "search", "alert", "return", "retreat"} else "patrol"
                if math.dist(actor.xy, goal) < 0.35:
                    brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
                    actor.state, goal = "patrol", brain.patrol[brain.waypoint]
            weapon = actor.weapon
            if weapon and not weapon.chambered and not weapon.reloading:
                intent.reload = True
                if weapon.reserve <= 0 and weapon.stage == 0:
                    actor.state, intent.fire, intent.reload = "retreat", False, False
                    reference = memory.last_seen or actor.xy
                    goal = max(brain.patrol, key=lambda point: math.dist(point, reference))
            if (actor.hurt_until > time or actor.hp < 20) and memory.last_seen is not None and time >= brain.cover_until:
                occupied = [self.brains[other.id].cover for other in actors if other.id != actor.id and other.hp > 0
                            and self.brains[other.id].cover is not None and self.brains[other.id].cover_until > time]
                brain.cover = self.cover_finder.choose(actor, memory.last_seen, occupied)
                brain.cover_until = time + 2.5
            if brain.cover is not None and time < brain.cover_until:
                actor.state, goal = "retreat", brain.cover
                intent.fire = False
            elif actor.hp < 20 and memory.last_seen is not None:
                actor.state, intent.fire = "retreat", False
                goal = max(brain.patrol, key=lambda point: math.dist(point, memory.last_seen))
            if memory.last_seen is not None and actor.state in {"combat", "search", "retreat"}:
                actor.facing = math.atan2(memory.last_seen[1] - actor.position.y, memory.last_seen[0] - actor.position.x)
            needs_plan = brain.goal != goal or not brain.path or brain.path_revision != self.world.revision
            if needs_plan and time >= brain.plan_at and budget:
                # 자신이 사용할 수 있는 문은 열고 지나가도록 경로 후보에 포함한다.
                brain.path = self.world.navigation.route(actor.xy, goal, allow_doors=True)
                brain.goal, brain.plan_at = goal, time + 0.6
                brain.path_revision = self.world.revision
                budget -= 1
            while brain.path and math.dist(actor.xy, brain.path[0]) < 0.16:
                brain.path.pop(0)
            if brain.path and not (actor.state == "combat" and math.dist(actor.xy, goal) < 9):
                dx, dy = brain.path[0][0] - actor.position.x, brain.path[0][1] - actor.position.y
                intent.direction = dx, dy
                if actor.state not in {"combat", "search", "retreat"}:
                    actor.facing = math.atan2(dy, dx)
                intent.open_door = True
            intents[actor.id] = intent
        return intents
