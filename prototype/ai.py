"""관측한 정보와 현재 임무로 적군의 이동과 전투를 결정한다."""

from dataclasses import dataclass, field
import math

from .cover import CoverFinder
from .geometry import Rect
from .perception import SOUND_MEMORY_SECONDS, Knowledge, hear, observe


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
    route_origin: tuple | None = None
    progress_point: tuple | None = None
    progress_at: float = 0.0
    pending_goal: tuple | None = None
    fire_at: float = 0.0
    burst: int = 0
    action: str = ""
    action_goal: tuple | None = None
    action_until: float = 0.0
    cover_threat: tuple | None = None
    cover_revision: int = -1
    suppression: float = 0.0
    pressure_at: float = 0.0
    radio_at: float = 0.0
    contact_at: float = 0.0
    role: str = ""
    incident: str = ""
    role_changed: bool = False
    completed_report_at: float = -1000.0
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


@dataclass
class Incident:
    updated_at: float
    heading: float
    point: tuple
    checked: list = field(default_factory=list)


class EnemyAI:
    def __init__(self, world, actors, patrols, *, can_radio=None):
        self.world = world
        self.cover_finder = CoverFinder(world)
        self.can_radio = can_radio
        self.exits = {point for key, point in world.navigation.nodes.items() if key.endswith("/outside")}
        self.incidents = {}
        self.assign_at = 0.0
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.01)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}

    def receive(self, actors, events):
        noises = [event for event in events if event.kind == "noise"]
        for event in events:
            if event.kind == "pressure" and event.source in self.brains:
                brain = self.brains[event.source]
                strength = event.data["strength"] * min(1.5, event.data["caliber"] / 18)
                brain.suppression = min(1.0, brain.suppression + strength)
                brain.pressure_at = event.time
        if not noises:
            return
        for actor in actors:
            if actor.hp <= 0:
                continue
            brain = self.brains[actor.id]
            for event in noises:
                if hear(actor, brain.memory, event):
                    brain.look_until = event.time + 0.65
                    brain.sense_at = min(brain.sense_at, event.time)

    @staticmethod
    def _face(actor, point):
        dx, dy = point[0] - actor.position.x, point[1] - actor.position.y
        if math.hypot(dx, dy) > 0.05:
            actor.facing = math.atan2(dy, dx)

    def _report_sighting(self, actor, brain, actors, time):
        brain.radio_at = time + 4
        point = tuple(round(axis / 4) * 4 for axis in brain.memory.last_seen)
        receivers = []
        for other in actors:
            if other.id == actor.id or other.hp <= 0:
                continue
            distance = math.dist(actor.xy, other.xy)
            local = distance <= 14 and self.world.geometry.raycast(actor.xy, other.xy) is None
            radio = not local and distance <= 75 and self.can_radio and self.can_radio(actor, other)
            if local or radio:
                receivers.append(other)
        if not receivers:
            return
        key = brain.incident if brain.incident in self.incidents else actor.id
        heading = brain.memory.seen_heading
        if heading is None:
            heading = actor.facing
        incident = self.incidents.get(key)
        if incident is None:
            incident = self.incidents[key] = Incident(time, heading, point)
        elif math.dist(point, incident.point) > 4:
            incident.point = point
            incident.checked = [checked for checked in incident.checked if math.dist(point, checked) >= 4]
        incident.updated_at, incident.heading = time, heading
        for member in (actor, *receivers):
            ally = self.brains[member.id]
            if ally.incident != key:
                ally.incident = key
                self._set_role(ally, "")
                ally.completed_report_at = -1000.0
            if ally.memory.reported == incident.point:
                if ally.completed_report_at >= ally.memory.reported_at:
                    ally.completed_report_at = time
                ally.memory.reported_at = time
                continue
            ally.memory.reported = incident.point
            ally.memory.reported_at = time
            ally.sense_at = min(ally.sense_at, time)

    @staticmethod
    def _set_role(brain, role):
        if brain.role != role:
            brain.role = role
            brain.role_changed = True
            brain.cue = None

    def _assign_roles(self, actors, time):
        if time < self.assign_at:
            return
        self.assign_at = time + 0.35
        self.incidents = {key: incident for key, incident in self.incidents.items()
                          if time - incident.updated_at < 18}
        groups = {}
        for actor in actors:
            brain = self.brains[actor.id]
            if (actor.hp > 0 and brain.incident in self.incidents
                    and brain.memory.reported is not None and time - brain.memory.reported_at < 18):
                groups.setdefault(brain.incident, []).append(actor)
        for members in groups.values():
            available = []
            for actor in members:
                brain = self.brains[actor.id]
                weapon = actor.weapon
                can_fight = weapon is not None and (weapon.chambered or weapon.reserve > 0 or weapon.stage >= 2)
                if (actor.hp >= 20 and can_fight and brain.suppression < 0.55
                        and actor.state != "retreat" and (brain.visible
                             or brain.completed_report_at < brain.memory.reported_at)):
                    available.append(actor)
                else:
                    self._set_role(brain, "hold")
            for role in ("pursue", "intercept", "sweep"):
                if not available:
                    break
                current = next((actor for actor in available if self.brains[actor.id].role == role), None)
                if current is None:
                    def priority(actor):
                        brain = self.brains[actor.id]
                        return (not brain.memory.visible if role == "pursue" else brain.memory.visible,
                                math.dist(actor.xy, brain.memory.reported), actor.id)

                    current = min(available, key=priority)
                available.remove(current)
                self._set_role(self.brains[current.id], role)
            for actor in available:
                self._set_role(self.brains[actor.id], "hold")

    def _combat_action(self, actor, brain, actors, threat, time):
        weapon = actor.weapon
        empty = weapon is None or (not weapon.chambered and weapon.stage < 2 and weapon.reserve <= 0)
        urgent = empty or actor.hp < 20 or actor.hurt_until - time > 1.75 or brain.suppression >= 0.55
        route_failed = brain.action_goal is not None and brain.action_goal == brain.goal and brain.route_failed
        safe_action = brain.action in {"cover", "retreat", "pin"}
        needs_reload = weapon is not None and (weapon.reloading or not weapon.chambered)
        if brain.action == "cover" and brain.action_goal is not None and not route_failed:
            if ((brain.cover_threat != threat or brain.cover_revision != self.world.revision)
                    and self.world.geometry.raycast(threat, brain.action_goal, category="bullet") is None):
                route_failed = True
            else:
                brain.cover_threat = threat
                brain.cover_revision = self.world.revision
                if math.dist(actor.xy, brain.action_goal) > 0.3 or (needs_reload and not empty) or urgent:
                    return
        elif (time < brain.action_until and not route_failed and not brain.role_changed
                and (not urgent or safe_action) and (not needs_reload or safe_action or brain.action == "reload")):
            return
        brain.role_changed = False
        rejected = brain.action_goal if route_failed else None
        brain.action_goal = None
        if urgent or needs_reload:
            occupied = [ally.action_goal for other in actors if other.id != actor.id and other.hp > 0
                        and (ally := self.brains[other.id]).action == "cover"
                        and ally.action_goal is not None and other.state == "retreat"]
            if rejected is not None:
                occupied.append(rejected)
            cover = self.cover_finder.choose(actor, threat, occupied)
            if cover is not None:
                brain.action, brain.action_goal = "cover", cover
                brain.cover_threat = threat
                brain.cover_revision = self.world.revision
            elif empty or actor.hp < 20:
                brain.action = "retreat"
                brain.action_goal = max(brain.patrol, key=lambda point: math.dist(point, threat), default=None)
            elif not urgent:
                brain.action = "reload"
            else:
                brain.action = "pin"
            brain.action_until = time + 1.65
            return

        distance = math.dist(actor.xy, threat)
        brain.action, brain.action_until = "fire", time + 1.3
        if distance > 13:
            brain.action, brain.action_goal = "advance", threat
            return
        if not 3 < distance <= 13 or brain.role not in {"intercept", "sweep"}:
            return
        if not any(other.id != actor.id and other.hp > 0
                   and self.brains[other.id].action in {"advance", "fire"}
                   and math.dist(other.xy, actor.xy) <= 18
                   and self.world.geometry.raycast(actor.xy, other.xy) is None for other in actors):
            return
        dx, dy = threat[0] - actor.position.x, threat[1] - actor.position.y
        for side in (1, -1) if brain.role == "intercept" else (-1, 1):
            point = (actor.position.x - side * dy / distance * 4, actor.position.y + side * dx / distance * 4)
            nav = self.world.navigation
            if nav.walkable(point, True) and nav.clear(actor.xy, point, ignore_doors=True):
                brain.action, brain.action_goal, brain.action_until = "flank", point, time + 2.4
                break

    def _search(self, actor, brain, cue, time):
        incident = self.incidents.get(brain.incident)
        if cue != brain.cue:
            brain.cue = cue
            heading = brain.memory.seen_heading if cue[0] == "search" else None
            angle = heading if heading is not None else incident.heading if incident else actor.facing
            x, y = cue[1]
            if brain.role == "intercept":
                offsets = ((7, 0), (5, 3), (5, -3))
            elif brain.role == "sweep":
                offsets = ((3, -4), (0, -6), (-4, -3))
            else:
                offsets = ((0, 0), (4, 0), (3, 3), (3, -3), (-2, 4), (-2, -4))
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            brain.search_points = [(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)
                                   for dx, dy in offsets]
            nav = self.world.navigation
            if brain.role == "intercept":
                exits = nav.point_index.query(Rect(x - 14, y - 14, 28, 28))
                exits = [nav.nodes[key] for key in exits if key.endswith("/outside")
                         and math.dist(nav.nodes[key], cue[1]) <= 14]
                if exits:
                    predicted = brain.search_points[0]
                    exits.sort(key=lambda point: (math.dist(point, predicted), math.dist(actor.xy, point), point))
                    brain.search_points[:0] = exits
            brain.scan_until = 0
        if time < brain.scan_until:
            actor.facing = brain.scan_angle + math.sin((brain.scan_until - time) * 5) * 0.95
            return None
        while brain.search_points:
            point = brain.search_points[0]
            if incident and any(math.dist(point, checked) < 2 for checked in incident.checked):
                brain.search_points.pop(0)
                continue
            if (brain.goal == point and not brain.path and brain.route_failed
                    and brain.path_revision == self.world.revision):
                brain.search_points.pop(0)
                continue
            if (math.dist(actor.xy, point) < 0.35
                    or (brain.goal == point and not brain.path and not brain.route_failed)):
                brain.search_points.pop(0)
                if incident and math.dist(actor.xy, point) < 1:
                    incident.checked.append(point)
                    del incident.checked[:-32]
                self._face(actor, cue[1])
                duration = 3.0 if brain.role == "intercept" else 0.85
                brain.scan_until, brain.scan_angle = time + duration, actor.facing
                return None
            return point
        return None

    def _move(self, actor, brain, intent, goal, time, budget, *, face=True, approximate=False):
        brain.pending_goal = None
        if goal is None:
            brain.path.clear()
            brain.goal = None
            brain.progress_point = None
            return budget
        nav = self.world.navigation
        changed = brain.goal is None or math.dist(brain.goal, goal) > (0.9 if actor.state == "combat" else 0.15)
        invalid = brain.path_revision != self.world.revision
        relocated = brain.route_origin is not None and math.dist(actor.xy, brain.route_origin) > 2
        if relocated:
            brain.path.clear()
            brain.path_revision = -1
            invalid = True
        brain.route_origin = actor.xy
        if invalid and not relocated and brain.path:
            points = [actor.xy, *brain.path]
            if all(nav.clear(start, end, ignore_doors=True) for start, end in zip(points, points[1:])):
                brain.path_revision = self.world.revision
                invalid = False
        if brain.path and brain.progress_point is not None and not changed:
            if math.dist(actor.xy, brain.progress_point) >= 0.18:
                brain.progress_at, brain.progress_point = time, actor.xy
            elif time - brain.progress_at >= 0.9:
                brain.path.clear()
                brain.path_revision = -1
                invalid = True
        if budget and (changed or invalid) and time >= brain.plan_at:
            options = nav.nearby_goals(goal, True) if approximate else [goal]
            brain.path = []
            for point in options:
                brain.path = nav.route(actor.xy, point, allow_doors=True)
                if brain.path:
                    break
            brain.goal, brain.path_revision, brain.plan_at = goal, self.world.revision, time + 0.2
            brain.route_failed = not brain.path
            brain.progress_at, brain.progress_point = time, actor.xy
            budget -= 1
        elif changed or invalid:
            brain.pending_goal = goal
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
        if (math.dist(actor.xy, goal) < 0.25
                or (brain.goal == goal and not brain.path and brain.path_revision == self.world.revision)):
            brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
            return "patrol", None
        state = "return" if actor.state in {"combat", "search", "alert", "return", "retreat"} else "patrol"
        return state, goal

    def reset(self):
        self.brains = {key: Brain(brain.patrol) for key, brain in self.brains.items()}
        self.incidents.clear()
        self.assign_at = 0.0

    def update(self, actors, player, time):
        intents, budget = {}, 1
        self._assign_roles(actors, time)
        self.near_count = 0
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            memory = brain.memory
            brain.suppression = max(0, brain.suppression - 0.2 * (time - brain.pressure_at))
            brain.pressure_at = time
            investigating = (memory.last_sound is not None and memory.sound_at > brain.checked_sound_at
                             and time - memory.sound_heard_at < SOUND_MEMORY_SECONDS
                             and (memory.suspicion >= 0.3 or actor.state == "alert"))
            tracking = memory.last_seen is not None and time - memory.seen_at < 16
            reported = memory.reported is not None and time - memory.reported_at < 18
            if math.dist(actor.xy, player.xy) > 60 and not (investigating or tracking or reported):
                if time < brain.far_at:
                    continue
                intent.step_dt = min(0.5, time - brain.far_updated)
                brain.far_at, brain.far_updated = time + 0.5, time
                memory.visible = False
                actor.state, goal = self._patrol(actor, brain)
                budget = self._move(actor, brain, intent, goal, time, budget, approximate=True)
                intents[actor.id] = intent
                continue
            brain.far_updated = time
            self.near_count += 1
            looking_for_sound = not (tracking or investigating or reported) and time < brain.look_until
            if time >= brain.sense_at:
                was_visible = memory.visible
                observe(actor, player, self.world.geometry, memory, time)
                # 순찰 방향에서 먼저 확인하고, 발견하지 못했을 때 소리 쪽도 살핀다.
                if not memory.visible and looking_for_sound:
                    self._face(actor, memory.last_sound)
                    observe(actor, player, self.world.geometry, memory, time)
                if memory.visible and not was_visible:
                    brain.contact_at = time
                brain.sense_at = time + (0.10 if actor.state in {"combat", "search"} else 0.18)
                memory.suspicion = max(0, memory.suspicion - 0.01)
                tracking = memory.last_seen is not None and time - memory.seen_at < 16
            old_state = actor.state
            if memory.visible:
                actor.state = "combat"
                brain.cue = None
                self._face(actor, memory.last_seen)
                if time >= brain.radio_at and time - brain.contact_at >= 0.55:
                    self._report_sighting(actor, brain, actors, time)
                if old_state not in {"combat", "retreat"}:
                    brain.fire_at, brain.burst = time + 0.7, 0
                    brain.action_until = time
                self._combat_action(actor, brain, actors, memory.last_seen, time)
                goal = brain.action_goal
                if brain.action in {"cover", "retreat", "pin", "reload"}:
                    actor.state = "retreat"
                elif (time >= brain.fire_at and actor.weapon and not actor.weapon.reloading
                      and actor.weapon.chambered and brain.action in {"fire", "advance"}):
                    intent.fire = True
                    brain.burst = (brain.burst + 1) % 3
                    brain.fire_at = time + (0.25 if brain.burst else 1.2)
            elif tracking or investigating or reported:
                actor.state = "search" if tracking else "alert"
                point = memory.last_seen if tracking else memory.reported
                if investigating and (point is None or memory.sound_at > (
                        memory.seen_at + 0.5 if tracking else memory.reported_at)):
                    point = memory.last_sound
                holding = brain.role == "hold" and reported and not (tracking or investigating)
                continuing_cover = brain.action == "cover" and old_state == "retreat"
                if continuing_cover:
                    self._combat_action(actor, brain, actors, point, time)
                    continuing_cover = brain.action in {"cover", "retreat", "pin", "reload"}
                if continuing_cover:
                    actor.state, goal = "retreat", brain.action_goal
                elif holding:
                    goal = None
                    self._face(actor, point)
                else:
                    goal = self._search(actor, brain, (actor.state, point), time)
                if not continuing_cover and not holding and not brain.search_points and time >= brain.scan_until:
                    brain.checked_sound_at = memory.sound_at
                    memory.last_seen = None
                    if reported:
                        brain.completed_report_at = memory.reported_at
                        self._set_role(brain, "hold")
                    else:
                        actor.state, goal = self._patrol(actor, brain)
                elif not continuing_cover and (brain.suppression >= 0.55 or actor.hurt_until > time or actor.hp < 20
                      or (brain.action in {"cover", "pin", "retreat"} and time < brain.action_until)):
                    self._combat_action(actor, brain, actors, point, time)
                    if brain.action in {"cover", "retreat", "pin"}:
                        actor.state, goal = "retreat", brain.action_goal
            elif looking_for_sound:
                actor.state, goal = "idle", None
            else:
                brain.cue = None
                self._set_role(brain, "")
                actor.state, goal = self._patrol(actor, brain)
            weapon = actor.weapon
            if weapon and not weapon.chambered and not weapon.reloading:
                moving_to_cover = (actor.state == "retreat" and brain.action == "cover" and goal is not None
                                   and math.dist(actor.xy, goal) > 0.3)
                intent.reload = not moving_to_cover and (weapon.reserve > 0 or weapon.stage >= 2)
            if actor.state == "retreat" and brain.action == "cover" and goal is not None and math.dist(actor.xy, goal) <= 0.3:
                goal = None
            budget = self._move(actor, brain, intent, goal, time, budget,
                                face=not memory.visible, approximate=actor.state != "combat" and goal not in self.exits)
            intents[actor.id] = intent
        return intents
