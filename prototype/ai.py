"""관측한 정보와 현재 임무로 적군의 이동과 전투를 결정한다."""

from dataclasses import dataclass, field
import math

from campaign.viewmodel import segment_distance

from .cover import CoverFinder
from .door_traffic import DoorTraffic
from .geometry import Rect
from .indoor_search import IndoorSweep, LookChoice, SCAN_HOLD_S, SCAN_RANGE_M, choose_look
from .perception import SOUND_MEMORY_SECONDS, Knowledge, hear, observe


COMBAT_ALERT_SECONDS = 330
RESEARCH_INTERVAL_SECONDS = 24
SEARCH_LOOK_UPDATE_S = 0.45
SEARCH_TURN_RADIANS_PER_S = math.radians(200)
INDOOR_MOVE_MULTIPLIER = 1.05
INDOOR_TURN_RADIANS_PER_S = math.radians(240)
INDOOR_START_DELAY_S = 0.14
INDOOR_ACCEL_MPS2 = 7.0
INDOOR_BRAKE_MPS2 = 9.0
INDOOR_WALK_SCAN_INTERVAL_S = 0.15
INDOOR_WALK_LOOK_INTERVAL_S = 0.45
INDOOR_YIELD_RETRY_S = 0.25
INDOOR_SMOOTH_BEND = math.radians(55)
REGROUP_HOLD_S = 1.0
REGROUP_ARRIVAL_M = 0.2


@dataclass(frozen=True)
class SearchCue:
    kind: str
    point: tuple[float, float]
    at: float


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
    blocked_by: str = ""
    door_wait: str = ""
    fire_at: float = 0.0
    fired_at: float = -1000.0
    burst: int = 0
    action: str = ""
    action_goal: tuple | None = None
    action_until: float = 0.0
    cover_threat: tuple | None = None
    cover_revision: int = -1
    peek_home: tuple | None = None
    peek_at: float = 0.0
    suppression: float = 0.0
    pressure_at: float = 0.0
    incoming: tuple | None = None
    incoming_at: float = -1000.0
    radio_at: float = 0.0
    contact_at: float = 0.0
    combat_alert_until: float = 0.0
    next_sweep_at: float = 0.0
    sweep_pass: int = 0
    search_cover: tuple | None = None
    cover_step_at: float = 0.0
    role: str = ""
    incident: str = ""
    role_changed: bool = False
    cue: tuple | None = None
    priority_cue: SearchCue | None = None
    priority_checked: bool = False
    search_points: list = field(default_factory=list)
    search_target: tuple | None = None
    scan_until: float = 0.0
    scan_angle: float = 0.0
    look_until: float = 0.0
    checked_sound_at: float = -1000.0
    indoor: IndoorSweep | None = None
    indoor_target: tuple | None = None
    indoor_blocked: dict = field(default_factory=dict)
    indoor_look_angle: float | None = None
    indoor_look_until: float = 0.0
    indoor_scan_at: float = 0.0
    indoor_heading: float | None = None
    indoor_walk_look_at: float = 0.0
    indoor_walk_look_point: tuple | None = None
    indoor_speed: float = 0.0
    indoor_depart_at: float | None = None
    indoor_yield: tuple | None = None
    indoor_yield_retry_at: float = 0.0
    indoor_focus: LookChoice | None = None
    watch_point: tuple | None = None
    watch_cue: tuple | None = None
    watch_at: float = 0.0
    look_point: tuple | None = None
    look_reason: str = ""
    sound_response: str = ""
    sound_response_at: float = -1000.0
    far_at: float = 0.0
    far_updated: float = 0.0

    @property
    def visible(self):
        return self.memory.visible

    def indoor_scanning(self, time):
        return self.indoor_look_angle is not None or time < self.indoor_look_until

    @property
    def investigating_priority(self):
        return self.priority_cue is not None and not self.priority_checked


@dataclass
class Intent:
    direction: tuple = (0, 0)
    fire: bool = False
    retreat_fire: bool = False
    reload: bool = False
    open_door: bool = False
    step_dt: float = 1 / 60
    move_multiplier: float = 1.0
    move_straight: bool = False


@dataclass
class Incident:
    updated_at: float
    heading: float
    point: tuple
    indoor: IndoorSweep | None = None
    checked: list = field(default_factory=list)
    claims: dict = field(default_factory=dict)


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
        self.door_traffic = DoorTraffic()

    def receive(self, actors, events):
        noises = [event for event in events if event.kind == "noise"]
        for event in events:
            if event.kind == "weapon_fired" and event.source in self.brains:
                self.brains[event.source].fired_at = event.time
            if event.kind == "pressure" and event.source in self.brains:
                brain = self.brains[event.source]
                strength = event.data["strength"] * min(1.5, event.data["caliber"] / 18)
                brain.suppression = min(1.0, brain.suppression + strength)
                brain.pressure_at = event.time
                bearing = round(event.data["incoming"] / (math.pi / 8)) * (math.pi / 8)
                distance = 6 if event.data["close"] else 11
                origin = event.position
                brain.incoming = (origin[0] + math.cos(bearing) * distance, origin[1] + math.sin(bearing) * distance)
                brain.incoming_at = event.time
                brain.sense_at = min(brain.sense_at, event.time)
                if event.data["hit"] or event.data["close"]:
                    brain.combat_alert_until = event.time + COMBAT_ALERT_SECONDS
        if not noises:
            return
        for actor in actors:
            if actor.hp <= 0:
                continue
            brain = self.brains[actor.id]
            for event in noises:
                result = hear(actor, brain.memory, event, self.world.navigation,
                              checked_sound_at=brain.checked_sound_at)
                if result is not None and event.data.get("reason") == "gunshot":
                    brain.sound_response = result
                    brain.sound_response_at = event.time
                if result in {"new", "renewed"}:
                    if result == "renewed":
                        brain.cue = None
                    brain.look_until = event.time + 0.65
                    brain.sense_at = min(brain.sense_at, event.time)

    @staticmethod
    def _face(actor, point):
        dx, dy = point[0] - actor.position.x, point[1] - actor.position.y
        if math.hypot(dx, dy) > 0.05:
            actor.facing = math.atan2(dy, dx)

    @staticmethod
    def _turn_toward(current, desired, dt, *, rate=SEARCH_TURN_RADIANS_PER_S):
        difference = math.remainder(desired - current, math.tau)
        step = rate * dt
        return current + max(-step, min(step, difference))

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
            incident.checked = [(checked, stamp) for checked, stamp in incident.checked
                                if math.dist(point, checked) >= 4]
            incident.claims.clear()
        incident.updated_at, incident.heading = time, heading
        for member in (actor, *receivers):
            ally = self.brains[member.id]
            ally.combat_alert_until = time + COMBAT_ALERT_SECONDS
            if ally.incident != key:
                ally.incident = key
                self._set_role(ally, "")
            ally.memory.report_source = actor.id
            if ally.memory.reported == incident.point:
                ally.memory.reported_at = time
                continue
            ally.memory.reported = incident.point
            ally.memory.reported_at = time
            ally.next_sweep_at = 0.0
            ally.sense_at = min(ally.sense_at, time)

    @staticmethod
    def _set_role(brain, role):
        if brain.role != role:
            brain.role = role
            brain.role_changed = True
            brain.cue = None
            brain.search_target = None

    def _assign_roles(self, actors, time):
        if time < self.assign_at:
            return
        self.assign_at = time + 0.35
        self.incidents = {key: incident for key, incident in self.incidents.items()
                          if time - incident.updated_at < COMBAT_ALERT_SECONDS}
        for incident in self.incidents.values():
            incident.checked = [(point, stamp) for point, stamp in incident.checked
                                if time - stamp < RESEARCH_INTERVAL_SECONDS * 3]
        groups = {}
        for actor in actors:
            brain = self.brains[actor.id]
            if (actor.hp > 0 and brain.incident in self.incidents
                    and brain.memory.reported is not None and time < brain.combat_alert_until):
                groups.setdefault(brain.incident, []).append(actor)
        for members in groups.values():
            available = []
            for actor in members:
                brain = self.brains[actor.id]
                weapon = actor.weapon
                can_fight = weapon is not None and (weapon.chambered or weapon.reserve > 0 or weapon.stage >= 2)
                if (actor.hp >= 20 and can_fight and brain.suppression < 0.55
                        and actor.state != "retreat"):
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

    def _has_support(self, actor, brain, actors, time):
        return any(other.id != actor.id and other.hp > 0
                   and (ally := self.brains[other.id]).incident == brain.incident
                   and ally.action in {"advance", "fire"} and ally.visible
                   and time - ally.fired_at < 1.5
                   and other.weapon is not None and other.weapon.chambered and not other.weapon.reloading
                   and ally.suppression < 0.55 and math.dist(other.xy, actor.xy) <= 18
                   and math.dist(other.xy, brain.memory.last_seen) <= other.weapon.spec.range_m
                   for other in actors)

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
                and (brain.action != "flank" or (math.dist(actor.xy, brain.action_goal) > 0.35
                                                and self._has_support(actor, brain, actors, time)))
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
        if not self._has_support(actor, brain, actors, time):
            return
        dx, dy = threat[0] - actor.position.x, threat[1] - actor.position.y
        nav = self.world.navigation
        for side in ((1, -1) if brain.role == "intercept" else (-1, 1)):
            for lateral in (4, 6):
                point = (actor.position.x + (dx - side * dy * lateral) / distance,
                         actor.position.y + (dy + side * dx * lateral) / distance)
                if (point != rejected and nav.walkable(point)
                        and nav.clear(actor.xy, point, ignore_doors=True)
                        and self.world.geometry.raycast(point, threat, category="bullet") is None):
                    brain.action, brain.action_goal, brain.action_until = "flank", point, time + 3.2
                    return

    def _peek(self, actor, brain, threat, time):
        if brain.peek_home is not None:
            if brain.action == "peek" and (time >= brain.action_until or brain.suppression >= 0.55
                                          or actor.weapon is None or not actor.weapon.chambered
                                          or (brain.goal == brain.action_goal and brain.route_failed)):
                brain.action, brain.action_goal = "peek_return", brain.peek_home
            if brain.action == "peek_return" and (
                    math.dist(actor.xy, brain.peek_home) <= 0.3
                    or (brain.goal == brain.peek_home and brain.route_failed)):
                brain.action, brain.action_goal = "cover", brain.peek_home
                brain.peek_home = None
                brain.peek_at = time + 1.35
                return False
            return True
        weapon = actor.weapon
        if (threat is None or brain.action != "cover" or brain.action_goal is None
                or math.dist(actor.xy, brain.action_goal) > 0.3 or time < brain.peek_at
                or brain.suppression >= 0.4 or actor.hp < 20
                or weapon is None or not weapon.chambered or weapon.reloading):
            return False
        brain.peek_at = time + 1.35
        point = self.cover_finder.peek(actor, threat)
        if point is None:
            return False
        brain.peek_home = brain.action_goal
        brain.action, brain.action_goal, brain.action_until = "peek", point, time + 1.65
        return True

    def start_search_drill(self, actors, point):
        key = "indoor_drill"
        area = self.world.indoor_search_area(point)
        self.incidents[key] = Incident(0.0, 0.0, point, indoor=IndoorSweep(area, point, 0.0) if area else None)
        for actor in actors:
            brain = self.brains[actor.id]
            brain.priority_cue = None
            brain.priority_checked = False
            brain.incident = key
            brain.memory.reported = point
            brain.memory.reported_at = 0.0
            brain.memory.report_source = key
            brain.combat_alert_until = COMBAT_ALERT_SECONDS

    def _interrupt_search(self, actor, brain):
        if brain.indoor is not None:
            brain.indoor.claims.pop(actor.id, None)
        incident = self.incidents.get(brain.incident)
        if incident is not None:
            incident.claims.pop(actor.id, None)
        brain.indoor_target = brain.indoor_yield = brain.indoor_look_angle = None
        brain.indoor_look_until = brain.scan_until = 0.0
        brain.indoor_scan_at, brain.indoor_heading = 0.0, None
        brain.indoor_walk_look_at, brain.indoor_walk_look_point = 0.0, None
        brain.indoor_speed, brain.indoor_depart_at = 0.0, None
        brain.cue = brain.search_target = brain.search_cover = None
        brain.next_sweep_at = 0.0

    def _update_priority_cue(self, actor, brain, tracking, investigating, incoming):
        memory = brain.memory
        previous = brain.priority_cue
        latest = previous.at if previous is not None else -math.inf
        cue = None
        if tracking and memory.seen_at > latest:
            cue = SearchCue("seen", memory.last_seen, memory.seen_at)
        recent_gunshot = memory.gunshot_origin is not None and brain.incoming_at <= memory.sound_heard_at + 0.25
        if incoming is not None and not recent_gunshot and brain.incoming_at > (cue.at if cue else latest):
            cue = SearchCue("impact", incoming, brain.incoming_at)
        if investigating and memory.sound_at > latest and (cue is None or memory.sound_at >= cue.at):
            cue = SearchCue("sound", memory.last_sound, memory.sound_at)
        if cue is not None:
            if (brain.investigating_priority and cue.kind == previous.kind and cue.point == previous.point):
                return
            self._interrupt_search(actor, brain)
            brain.priority_cue = cue
            brain.priority_checked = False
            incident = self.incidents.get(brain.incident)
            if incident is not None and incident.indoor is not None and incident.indoor.regroup_slots:
                incident.indoor = None

    def _indoor_sweep(self, actor, brain, point, evidence_at, time, *, new_contact=False):
        area = self.world.indoor_search_area(point)
        if area is None:
            if brain.indoor is not None:
                brain.indoor.claims.pop(actor.id, None)
                brain.indoor = brain.indoor_target = None
                brain.indoor_look_angle = None
                brain.indoor_look_until = 0.0
            return None
        if brain.incident not in self.incidents:
            brain.incident = actor.id
            self.incidents[actor.id] = Incident(time, actor.facing, point)
        incident = self.incidents[brain.incident]
        sweep = incident.indoor
        if (sweep is None or (evidence_at > sweep.evidence_at
                and (new_contact or sweep.area is not area or math.dist(point, sweep.anchor) > 4))):
            sweep = incident.indoor = IndoorSweep(area, point, evidence_at)
        sweep.evidence_at = max(sweep.evidence_at, evidence_at)
        area = sweep.area
        if brain.indoor is not sweep:
            if brain.indoor is not None:
                brain.indoor.claims.pop(actor.id, None)
            brain.indoor = sweep
            brain.indoor_target = None
            brain.indoor_blocked.clear()
            brain.indoor_look_angle = None
            brain.indoor_look_until = 0.0
            brain.indoor_scan_at, brain.indoor_heading = 0.0, None

        target = brain.indoor_target
        previous_room = area.rooms.get(target)
        if target is not None:
            if (time >= brain.indoor_scan_at and brain.goal == target and brain.path
                    and brain.indoor_speed > 0 and not brain.blocked_by and not brain.door_wait):
                sweep.scan_forward(actor.xy, actor.facing, previous_room, self.world.geometry)
                brain.indoor_scan_at = time + INDOOR_WALK_SCAN_INTERVAL_S
            if brain.goal == target and brain.route_failed and brain.path_revision == self.world.revision:
                brain.indoor_blocked[target] = self.world.revision
            elif (not brain.indoor_look_until
                  and any(sample not in sweep.checked for sample in area.room_points[previous_room])):
                sweep.claims[actor.id] = target, time + 1
                if math.dist(actor.xy, target) > 0.25:
                    return target, False
                self._begin_indoor_look(actor, brain)
                return None, False
            sweep.claims.pop(actor.id, None)
            brain.indoor_target = None
            brain.indoor_look_until = 0.0

        revision = self.world.revision
        available = [candidate for candidate in area.points
                     if sweep.observed_from.get(candidate) != revision
                     and brain.indoor_blocked.get(candidate) != revision]
        unchecked = [candidate for candidate in available if candidate not in sweep.checked]
        claimed_rooms = {area.rooms[target] for owner, (target, until) in sweep.claims.items()
                         if owner != actor.id and time < until}
        target = min((candidate for candidate in unchecked if area.rooms[candidate] not in claimed_rooms),
                     key=lambda candidate: (area.rooms[candidate] != previous_room,
                                            math.dist(actor.xy, candidate) + 0.3 * math.dist(point, candidate),
                                            candidate), default=None)
        if target is not None:
            room = area.rooms[target]
            brain.indoor_walk_look_at, brain.indoor_walk_look_point = 0.0, None
            remaining = tuple(sample for sample in area.room_points[room] if sample not in sweep.checked)
            if sweep.can_finish_here(actor.xy, remaining, self.world.geometry):
                brain.indoor_target = target
                sweep.claims[actor.id] = target, time + 1
                self._begin_indoor_look(actor, brain)
                return None, False
            heading = actor.facing if brain.indoor_heading is None else brain.indoor_heading
            candidates = [candidate for candidate in available if area.rooms[candidate] == room]
            target = sweep.observation_point(actor.xy, heading, candidates, remaining, self.world.navigation)
            brain.indoor_target = target
            sweep.claims[actor.id] = target, time + 1
        return target, not unchecked

    @staticmethod
    def _begin_indoor_look(actor, brain):
        brain.indoor_speed = 0.0
        brain.indoor_focus = None
        brain.indoor_look_angle = actor.facing
        brain.look_reason = "정지 후 정면 확인"

    def _advance_indoor_look(self, actor, brain, time):
        sweep, target = brain.indoor, brain.indoor_target
        angle = brain.indoor_look_angle
        self._show_search_look(brain)
        actor.facing = self._turn_toward(actor.facing, angle, 1 / 60, rate=INDOOR_TURN_RADIANS_PER_S)
        if abs(math.remainder(angle - actor.facing, math.tau)) > 1e-6:
            brain.indoor_look_until = 0.0
        elif not brain.indoor_look_until:
            # 목표 방향에 도달한 뒤부터 확인 시간을 센다.
            brain.indoor_look_until = time + SCAN_HOLD_S
        elif time >= brain.indoor_look_until:
            geometry = self.world.geometry
            room = sweep.area.rooms[target]
            sweep.scan_forward(actor.xy, actor.facing, room, geometry, stationary=True)
            brain.indoor_focus = sweep.look(actor.xy, actor.facing, room, geometry)
            if brain.indoor_focus is not None:
                brain.indoor_look_angle = actor.facing + brain.indoor_focus.offset
                brain.indoor_look_until = 0.0
            else:
                if math.dist(actor.xy, target) <= 0.25:
                    sweep.observed_from[target] = self.world.revision
                brain.indoor_look_angle = None
                brain.indoor_look_until = time
                brain.look_reason = "스캔 완료"
        sweep.claims[actor.id] = target, time + 1

    def _look_during_indoor_move(self, actor, brain, heading, time):
        if time >= brain.indoor_walk_look_at:
            sweep = brain.indoor
            room = sweep.area.rooms[brain.indoor_target]
            choice = sweep.look(actor.xy, heading, room, self.world.geometry, walking=True)
            brain.indoor_walk_look_point = choice.point if choice else None
            brain.indoor_walk_look_at = time + INDOOR_WALK_LOOK_INTERVAL_S

        point = brain.indoor_walk_look_point
        desired = heading
        if point is not None and math.dist(actor.xy, point) > 0.15:
            desired = math.atan2(point[1] - actor.xy[1], point[0] - actor.xy[0])
            brain.look_point, brain.look_reason = point, "이동 중 미확인 공간 관찰"
        else:
            brain.look_point, brain.look_reason = brain.path[0], "진행 방향 확인"
        actor.facing = self._turn_toward(actor.facing, desired, 1 / 60, rate=INDOOR_TURN_RADIANS_PER_S)

    def _pace_indoor_movement(self, actor, brain, intent, time, *, room_sweep=False):
        dx, dy = intent.direction
        if not (dx or dy):
            brain.indoor_speed, brain.indoor_depart_at = 0.0, None
            return

        tick = 1 / 60
        heading = math.atan2(dy, dx)
        if room_sweep:
            brain.indoor_heading = heading
            self._look_during_indoor_move(actor, brain, heading, time)
        else:
            actor.facing = self._turn_toward(actor.facing, heading, tick, rate=INDOOR_TURN_RADIANS_PER_S)
            if brain.indoor_depart_at is None:
                brain.indoor_depart_at = time + INDOOR_START_DELAY_S
            error = abs(math.remainder(heading - actor.facing, math.tau))
            if error > 1e-6 or time < brain.indoor_depart_at:
                brain.indoor_speed = 0.0
                brain.progress_at, brain.progress_point = time, actor.xy
                intent.direction, intent.open_door = (0, 0), False
                brain.look_reason = "진행 방향으로 회전 중"
                return
            brain.look_point, brain.look_reason = brain.path[0], "진행 방향 확인"

        distance = math.hypot(dx, dy)
        speed_limit = actor.speed * INDOOR_MOVE_MULTIPLIER
        exit_speed = 0.0
        if len(brain.path) > 1:
            point, following = brain.path[:2]
            outgoing = math.atan2(following[1] - point[1], following[0] - point[0])
            bend = abs(math.remainder(outgoing - heading, math.tau))
            if bend <= math.radians(25):
                exit_speed = speed_limit
            elif room_sweep and bend <= INDOOR_SMOOTH_BEND:
                exit_speed = speed_limit * math.cos(bend)
        speed_limit = min(speed_limit, math.sqrt(exit_speed ** 2 + 2 * INDOOR_BRAKE_MPS2 * distance))
        increase = INDOOR_ACCEL_MPS2 * tick
        decrease = INDOOR_BRAKE_MPS2 * tick
        brain.indoor_speed = max(brain.indoor_speed - decrease, min(speed_limit, brain.indoor_speed + increase))
        intent.move_multiplier = brain.indoor_speed / actor.speed
        intent.step_dt = min(tick, distance / brain.indoor_speed)
        intent.move_straight = True

    @staticmethod
    def _show_search_look(brain):
        if brain.indoor_focus is not None:
            brain.look_point = brain.indoor_focus.point
            brain.look_reason = f"미확인 {brain.indoor_focus.visible_count}곳"
        else:
            brain.look_reason = "정지 후 정면 확인"

    def _watch_visible_space(self, actor, brain, cue, time):
        geometry = self.world.geometry
        if (math.dist(actor.xy, cue) > 0.05 and geometry.raycast(actor.xy, cue, category="sight") is None):
            brain.look_point, brain.look_reason = cue, "보고 위치가 보임"
            self._face(actor, cue)
            return

        if (brain.watch_cue != cue or time >= brain.watch_at or (brain.watch_point is not None
                and geometry.raycast(actor.xy, brain.watch_point, category="sight") is not None)):
            nav = self.world.navigation
            x, y = actor.xy
            radius = SCAN_RANGE_M
            keys = nav.point_index.query(Rect(x - radius, y - radius, radius * 2, radius * 2))
            samples = {nav.nodes[key]: 1 for key in keys}
            area = self.world.indoor_search_area(actor.xy)
            if area is not None:
                samples.update({point: 1 for point in area.points})
            choice = choose_look(actor.xy, actor.facing, geometry, samples, max_offset=math.pi)
            brain.watch_point = choice.point if choice else None
            brain.watch_cue, brain.watch_at = cue, time + SEARCH_LOOK_UPDATE_S

        if brain.watch_point is not None:
            brain.look_point, brain.look_reason = brain.watch_point, "열린 공간 경계"
            self._face(actor, brain.watch_point)
        else:
            brain.look_reason = "보이는 경계 지점 없음"

    def _update_regroups(self, actors, time):
        nav = self.world.navigation
        for key, incident in self.incidents.items():
            sweep = incident.indoor
            if sweep is None or sweep.regroup_done or len(sweep.checked) != len(sweep.area.points):
                continue
            members = [actor for actor in actors if actor.hp > 0 and not actor.transport_boarding
                       and self.brains[actor.id].incident == key]
            if not members or any(self.brains[actor.id].visible or self.brains[actor.id].investigating_priority
                                  or self.brains[actor.id].indoor_scanning(time)
                                  for actor in members):
                continue
            if not sweep.regroup_slots:
                centers = sorted(sweep.area.points, key=lambda point: (math.dist(point, sweep.anchor), point))[:10]
                center, slots = max(((point, self._rally_slots(nav, point)) for point in centers),
                                    key=lambda entry: (min(len(entry[1]), len(members)),
                                                       -math.dist(entry[0], sweep.anchor)))
                # 같은 집결 좌표를 공유하면 몸체 충돌로 완료 판정에 도달하지 못한다.
                for actor in sorted(members, key=lambda item: (math.dist(item.xy, center), item.id)):
                    for slot in sorted(slots, key=lambda point: (math.dist(actor.xy, point), point)):
                        if nav.route(actor.xy, slot, allow_doors=True):
                            sweep.regroup_slots[actor.id] = slot
                            slots.remove(slot)
                            break
            self._unblock_regroup(sweep, members)
            arrived = all(actor.id not in sweep.regroup_slots
                          or math.dist(actor.xy, sweep.regroup_slots[actor.id]) <= REGROUP_ARRIVAL_M for actor in members)
            if not arrived:
                sweep.regroup_until = None
            elif sweep.regroup_until is None:
                sweep.regroup_until = time + REGROUP_HOLD_S
            elif time >= sweep.regroup_until:
                sweep.regroup_done = True
                for actor in members:
                    brain = self.brains[actor.id]
                    brain.incident = ""
                    brain.combat_alert_until = 0.0
                    brain.next_sweep_at = 0.0
                    brain.memory.last_seen = brain.memory.reported = None
                    brain.incoming = None
                    brain.indoor = brain.indoor_target = None
                    brain.indoor_yield = None
                    brain.indoor_look_angle = None
                    brain.indoor_look_until = 0.0
                    self._set_role(brain, "")

    def _unblock_regroup(self, sweep, members):
        present = {actor.id: actor for actor in members}
        for actor in members:
            blocker = present.get(self.brains[actor.id].blocked_by)
            if blocker is None or actor.id not in sweep.regroup_slots or blocker.id not in sweep.regroup_slots:
                continue
            pair = tuple(sorted((actor.id, blocker.id)))
            goal, occupied = sweep.regroup_slots[actor.id], sweep.regroup_slots[blocker.id]
            mutual = self.brains[blocker.id].blocked_by == actor.id
            settled = (math.dist(blocker.xy, occupied) <= REGROUP_ARRIVAL_M
                       and math.dist(actor.xy, occupied) + 0.25 < math.dist(actor.xy, goal))
            if pair not in sweep.regroup_swaps and (mutual or settled):
                # 서 있거나 정면에서 마주친 NPC 때문에 막힌 집결 자리를 맞바꾼다.
                sweep.regroup_slots[actor.id], sweep.regroup_slots[blocker.id] = occupied, goal
                sweep.regroup_swaps.add(pair)

    def _unblock_search(self, actors, time):
        living = {actor.id: actor for actor in actors if actor.hp > 0}
        nav = self.world.navigation
        for actor in living.values():
            brain = self.brains[actor.id]
            blocker = living.get(brain.blocked_by)
            has_search_target = brain.indoor_target is not None or brain.investigating_priority
            if actor.state != "search" or not has_search_target or not brain.path or blocker is None:
                continue
            other = self.brains[blocker.id]
            if (other.incident != brain.incident or other.visible or other.indoor_yield is not None
                    or brain.indoor_yield is not None or blocker.transport_boarding or other.indoor_scanning(time)
                    or time < other.scan_until):
                continue
            if other.path and (other.blocked_by != actor.id or actor.id > blocker.id):
                continue
            room_sweep = brain.indoor_target is not None and not brain.investigating_priority
            if room_sweep:
                if time < brain.indoor_yield_retry_at:
                    continue
                brain.indoor_yield_retry_at = time + INDOOR_YIELD_RETRY_S
            gap = actor.radius + blocker.radius
            # 아군이 바로 앞 구간을 막으면 걸어서 비킬 좌표를 고른다.
            for point in self._rally_slots(nav, blocker.xy)[1:]:
                if segment_distance(*point, *actor.xy, *brain.path[0]) < gap + 0.2:
                    continue
                if any(segment_distance(*member.xy, *blocker.xy, *point) < member.radius + blocker.radius - 1e-6
                       for member in living.values() if member is not blocker):
                    continue
                other.indoor_yield = point
                if room_sweep and other.indoor_target is not None and not other.investigating_priority:
                    # 이전 이동이 서로 막혔더라도 새로 고른 양보 경로는 한 번 시도한다.
                    other.blocked_by = ""
                break

    @staticmethod
    def _rally_slots(nav, center):
        slots = [center]
        for radius in (0.9, 1.8, 2.7):
            for index in range(8):
                angle = index * math.tau / 8
                point = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
                if nav.walkable(point) and nav.clear(center, point):
                    slots.append(point)
        return slots

    def _search(self, actor, brain, cue, time, *, sound_only=False, confirmed=False, direct=False):
        incident = None if direct else self.incidents.get(brain.incident)
        if cue != brain.cue:
            brain.cue = cue
            brain.search_target = None
            brain.search_cover = None
            heading = brain.memory.seen_heading if cue[0] == "search" else None
            angle = heading if heading is not None else incident.heading if incident else actor.facing
            x, y = cue[1]
            if brain.role == "intercept":
                offsets = ((8, 0), (7, 3), (7, -3))
            elif brain.role == "sweep":
                offsets = ((5, -3), (8, -4), (4, 4))
            else:
                offsets = ((5, 0), (9, 0), (5, 3), (5, -3), (0, 0)) if confirmed else (
                    (0, 0), (4, 0), (3, 3), (3, -3))
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            brain.search_points = [(x + dx * cos_a - dy * sin_a, y + dx * sin_a + dy * cos_a)
                                   for dx, dy in offsets]
            nav = self.world.navigation
            if direct:
                brain.search_points = [cue[1]]
            elif sound_only:
                brain.search_points = [point for point in brain.search_points
                                       if nav.walkable(point) and nav.clear(cue[1], point)]
                if cue[1] not in brain.search_points:
                    brain.search_points.append(cue[1])
            elif confirmed:
                radius = min(36, 18 + brain.sweep_pass * 4)
                keys = nav.point_index.query(Rect(x - radius, y - radius, radius * 2, radius * 2))
                suffixes = ("/outside",) if brain.role == "intercept" else ("/hall", "/inside", "/terminal", "/outside")
                landmarks = [nav.nodes[key] for key in keys if key.endswith(suffixes)
                             and math.dist(nav.nodes[key], cue[1]) <= radius]
                if brain.sweep_pass:
                    landmarks = [nav.nodes[key] for key in keys
                                 if math.dist(nav.nodes[key], cue[1]) <= radius
                                 and (brain.role != "intercept" or key.endswith("/outside"))]
                predicted = brain.search_points[0]
                landmarks.sort(key=lambda point: (math.dist(point, predicted), math.dist(actor.xy, point), point))
                if brain.sweep_pass and len(landmarks) > 5:
                    start = (brain.sweep_pass - 1) * 5 % len(landmarks)
                    landmarks = landmarks[start:] + landmarks[:start]
                index = 1
                brain.search_points[index:index] = landmarks[:5]
            brain.scan_until = 0
        if time < brain.scan_until:
            actor.facing = brain.scan_angle
            if not direct:
                actor.facing += math.sin((brain.scan_until - time) * 5) * 0.95
            if incident and brain.search_target is not None:
                incident.claims[actor.id] = brain.search_target, time + 0.65
            return None
        if brain.search_target is not None:
            if incident:
                incident.checked.append((brain.search_target, time))
                del incident.checked[:-32]
                incident.claims.pop(actor.id, None)
            brain.search_target = None
        while brain.search_points:
            point = brain.search_points[0]
            if incident and (any(math.dist(point, checked) < 2 for checked, _ in incident.checked)
                             or any(owner != actor.id and time < expires and math.dist(point, reserved) < 2
                                    for owner, (reserved, expires) in incident.claims.items())):
                brain.search_points.pop(0)
                continue
            if (brain.goal == point and not brain.path and brain.route_failed
                    and brain.path_revision == self.world.revision):
                brain.search_points.pop(0)
                continue
            if (math.dist(actor.xy, point) < 0.35
                    or (brain.goal == point and not brain.path and not brain.route_failed
                        and brain.path_revision == self.world.revision)):
                brain.search_points.pop(0)
                brain.search_target = point if math.dist(actor.xy, point) < 1 else None
                self._face(actor, cue[1])
                duration = 2.0 if brain.role == "intercept" else 0.5 if confirmed else 0.85
                brain.scan_until, brain.scan_angle = time + duration, actor.facing
                return None
            if incident:
                incident.claims[actor.id] = point, time + 0.65
            if not confirmed or direct:
                return point
            # 수색 지점까지 확보한 경로는 다음 엄폐 후보를 계산해도 유지한다.
            if brain.goal == point and brain.path and not brain.route_failed:
                return point
            if brain.search_cover is not None:
                cover = brain.search_cover
                if (math.dist(actor.xy, cover) > 0.35
                        and not (brain.goal == cover and brain.route_failed)):
                    return cover
                brain.search_cover = None
                brain.cover_step_at = time + 0.75
            if time >= brain.cover_step_at:
                brain.cover_step_at = time + 1.0
                reserved = [ally.search_cover for key, ally in self.brains.items()
                            if key != actor.id and ally.search_cover is not None]
                brain.search_cover = self.cover_finder.advance(actor, cue[1], point, reserved)
                if brain.search_cover is not None:
                    return brain.search_cover
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
        if (brain.blocked_by and len(brain.path) > 1 and math.dist(actor.xy, brain.path[0]) <= 0.75
                and nav.clear(actor.xy, brain.path[1], ignore_doors=True)):
            # 아군이 밟고 있는 가까운 경유점은 다음 구간이 열려 있으면 건너뛴다.
            brain.path.pop(0)
        if not brain.path:
            return budget
        point = brain.path[0]
        if face:
            self._face(actor, point)
        intent.direction = point[0] - actor.position.x, point[1] - actor.position.y
        intent.step_dt = min(intent.step_dt, math.hypot(*intent.direction) / (actor.speed * intent.move_multiplier))
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
        state = "return" if actor.state in {"combat", "combat_guard", "search", "alert", "return", "retreat"} else "patrol"
        return state, goal

    def reset(self):
        self.brains = {key: Brain(brain.patrol) for key, brain in self.brains.items()}
        self.incidents.clear()
        self.assign_at = 0.0
        self.door_traffic = DoorTraffic()

    def update(self, actors, player, time):
        intents, budget = {}, 1
        self._assign_roles(actors, time)
        self._unblock_search(actors, time)
        self.near_count = 0
        previous_facing = {actor.id: actor.facing for actor in actors}
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            brain.look_point, brain.look_reason = None, ""
            memory = brain.memory
            brain.suppression = max(0, brain.suppression - 0.2 * (time - brain.pressure_at))
            brain.pressure_at = time
            investigating = (memory.last_sound is not None and memory.sound_at > brain.checked_sound_at
                             and time - memory.sound_heard_at < SOUND_MEMORY_SECONDS
                             and (memory.suspicion >= 0.3 or actor.state in {"alert", "search", "regroup"}))
            confirmed = time < brain.combat_alert_until
            incoming = brain.incoming if time - brain.incoming_at < (12 if confirmed else 4) else None
            tracking = confirmed and memory.last_seen is not None
            reported = confirmed and memory.reported is not None
            if math.dist(actor.xy, player.xy) > 60 and not (investigating or confirmed or incoming is not None):
                if time < brain.far_at:
                    continue
                intent.step_dt = min(0.5, time - brain.far_updated)
                brain.far_at, brain.far_updated = time + 0.5, time
                memory.visible = False
                memory.last_seen = memory.reported = None
                brain.priority_cue = None
                brain.search_cover = None
                brain.cue = None
                self._set_role(brain, "")
                actor.state, goal = self._patrol(actor, brain)
                budget = self._move(actor, brain, intent, goal, time, budget, approximate=True)
                intents[actor.id] = intent
                continue
            brain.far_updated = time
            self.near_count += 1
            fresh_sound = time < brain.look_until
            looking_for_sound = not (tracking or investigating or reported) and fresh_sound
            if time >= brain.sense_at:
                was_visible = memory.visible
                observe(actor, player, self.world.geometry, memory, time)
                # 순찰 방향에서 먼저 확인하고, 발견하지 못했을 때 소리 쪽도 살핀다.
                fresh_incoming = incoming is not None and time - brain.incoming_at < 1
                if not memory.visible and (fresh_incoming or fresh_sound):
                    look = incoming if fresh_incoming else memory.last_sound
                    self._face(actor, look)
                    actor.facing = self._turn_toward(previous_facing[actor.id], actor.facing, 1 / 60)
                    observe(actor, player, self.world.geometry, memory, time)
                if memory.visible and not was_visible:
                    brain.contact_at = time
                brain.sense_at = time + (0.10 if confirmed or memory.visible else 0.18)
                memory.suspicion = max(0, memory.suspicion - 0.01)
            old_state = actor.state
            urgent = actor.hp < 20 or actor.hurt_until > time or brain.suppression >= 0.55
            if memory.visible:
                if old_state not in {"combat", "retreat"} or brain.indoor_target is not None:
                    self._interrupt_search(actor, brain)
            elif confirmed:
                self._update_priority_cue(actor, brain, tracking, investigating, incoming)
            else:
                brain.priority_cue = None
            priority_search = brain.investigating_priority
            if brain.indoor_scanning(time):
                if memory.visible or actor.transport_boarding or urgent:
                    brain.indoor_look_angle = None
                    brain.indoor_look_until = 0.0
                else:
                    actor.state = "search"
                    actor.facing = previous_facing[actor.id]
                    self._advance_indoor_look(actor, brain, time)
                    weapon = actor.weapon
                    intent.reload = (weapon is not None and not weapon.chambered and not weapon.reloading
                                     and (weapon.reserve > 0 or weapon.stage >= 2))
                    intents[actor.id] = intent
                    continue
            if brain.indoor_yield is not None:
                goal = brain.indoor_yield
                if (memory.visible or actor.transport_boarding or brain.blocked_by or math.dist(actor.xy, goal) <= 0.2
                        or not self.world.navigation.clear(actor.xy, goal)):
                    brain.indoor_yield = None
                else:
                    budget = self._move(actor, brain, intent, goal, time, budget, face=False)
                    self._pace_indoor_movement(actor, brain, intent, time)
                    intents[actor.id] = intent
                    continue
            incident = self.incidents.get(brain.incident)
            active_sweep = incident.indoor if incident is not None else None
            if active_sweep is not None and active_sweep.regroup_slots and not active_sweep.regroup_done:
                if memory.visible:
                    incident.indoor = None
                elif actor.id in active_sweep.regroup_slots and not actor.transport_boarding and not priority_search:
                    actor.state = "regroup"
                    goal = active_sweep.regroup_slots[actor.id]
                    intent.move_multiplier = INDOOR_MOVE_MULTIPLIER
                    if math.dist(actor.xy, goal) <= REGROUP_ARRIVAL_M:
                        goal = None
                    budget = self._move(actor, brain, intent, goal, time, budget, face=False)
                    self._pace_indoor_movement(actor, brain, intent, time)
                    intents[actor.id] = intent
                    continue
            if actor.transport_boarding and not memory.visible:
                actor.state = "boarding"
                budget = self._move(actor, brain, intent, actor.boarding_goal, time, budget, approximate=True)
                intents[actor.id] = intent
                continue
            threat = None
            if memory.visible:
                brain.combat_alert_until = time + COMBAT_ALERT_SECONDS
                brain.next_sweep_at, brain.sweep_pass = 0.0, 0
                brain.search_cover = None
                actor.state = "combat"
                brain.cue = None
                brain.search_target = None
                self._face(actor, memory.last_seen)
                if time >= brain.radio_at and time - brain.contact_at >= 0.55:
                    self._report_sighting(actor, brain, actors, time)
                if old_state not in {"combat", "retreat"}:
                    brain.fire_at, brain.burst = time + 0.28, 0
                    brain.action_until = time
                if not self._peek(actor, brain, memory.last_seen, time):
                    self._combat_action(actor, brain, actors, memory.last_seen, time)
                goal = brain.action_goal
                if brain.action in {"cover", "retreat", "pin", "reload", "peek_return"}:
                    actor.state = "retreat"
                elif (time >= brain.fire_at and actor.weapon and not actor.weapon.reloading
                      and actor.weapon.chambered and brain.action in {"fire", "advance", "peek"}):
                    intent.fire = True
                    burst_size = 2 if brain.suppression >= 0.3 or brain.action == "peek" else 4
                    brain.burst = (brain.burst + 1) % burst_size
                    brain.fire_at = time + (0.20 if brain.burst else 0.58) * (1 + brain.suppression * 2)
            elif priority_search:
                cue = brain.priority_cue
                actor.state = "search"
                threat = cue.point
                if urgent:
                    brain.cue = None
                    self._combat_action(actor, brain, actors, cue.point, time)
                    actor.state, goal = "retreat", brain.action_goal
                else:
                    goal = self._search(actor, brain, (cue.kind, cue.point, cue.at), time, confirmed=True, direct=True)
                    if not brain.search_points and time >= brain.scan_until:
                        brain.priority_checked = True
                        if cue.kind == "sound":
                            brain.checked_sound_at = memory.sound_at if memory.last_sound == cue.point else cue.at
                            brain.sound_response, brain.sound_response_at = "checked", time
            elif tracking or investigating or reported or incoming is not None or (confirmed and brain.incoming is not None):
                actor.state = "search" if confirmed else "alert"
                point = memory.last_seen if tracking else None
                contact_at = memory.seen_at if tracking else -math.inf
                if reported and (point is None or (memory.report_source != actor.id and memory.reported_at > contact_at)):
                    point, contact_at = memory.reported, memory.reported_at
                cue = brain.priority_cue
                if (cue is not None and brain.priority_checked and cue.kind != "seen"
                        and cue.at >= contact_at and time - cue.at < SOUND_MEMORY_SECONDS):
                    point, contact_at = cue.point, cue.at
                elif investigating and (point is None or memory.sound_at > contact_at + 0.5):
                    point = memory.last_sound
                if point is None:
                    point = incoming or brain.incoming
                threat = point
                sound_only = investigating and point == memory.last_sound
                indoor = None
                search_finished = False
                holding = confirmed and not investigating and not priority_search and (
                    time < brain.next_sweep_at or (brain.role == "hold" and reported))
                cover_context = brain.peek_home is not None or (brain.action == "cover" and old_state == "retreat")
                continuing_cover = cover_context and self._peek(actor, brain, point, time)
                if not continuing_cover and cover_context:
                    self._combat_action(actor, brain, actors, point, time)
                    continuing_cover = brain.action in {"cover", "retreat", "pin", "reload"}
                if continuing_cover:
                    actor.state, goal = "retreat", brain.action_goal
                elif holding:
                    actor.state = "combat_guard"
                    goal = brain.search_cover
                    if goal is None or math.dist(actor.xy, goal) <= 0.35 or (brain.goal == goal and brain.route_failed):
                        goal = brain.search_cover = None
                    self._watch_visible_space(actor, brain, point, time)
                else:
                    if confirmed:
                        intent.move_multiplier = 1.3
                    if brain.next_sweep_at and (investigating or time >= brain.next_sweep_at):
                        brain.cue = None
                        brain.next_sweep_at = 0.0
                    if confirmed and not sound_only and math.isfinite(contact_at):
                        new_contact = cue is not None and cue.at == contact_at and brain.priority_checked
                        indoor = self._indoor_sweep(actor, brain, point, contact_at, time, new_contact=new_contact)
                    if indoor is None:
                        goal = self._search(actor, brain, (actor.state, point), time,
                                            sound_only=sound_only, confirmed=confirmed)
                        search_finished = not brain.search_points
                    else:
                        intent.move_multiplier = INDOOR_MOVE_MULTIPLIER
                        goal, search_finished = indoor
                        brain.search_cover = None
                if not continuing_cover and not holding and search_finished and time >= brain.scan_until:
                    if sound_only:
                        brain.checked_sound_at = memory.sound_at
                        if brain.sound_response != "out_of_range":
                            brain.sound_response = "checked"
                            brain.sound_response_at = time
                    if confirmed:
                        brain.next_sweep_at = time + RESEARCH_INTERVAL_SECONDS
                        brain.sweep_pass += 1
                        actor.state = "combat_guard"
                        brain.search_cover = self.cover_finder.choose(actor, point)
                        goal = brain.search_cover
                        self._watch_visible_space(actor, brain, point, time)
                    else:
                        actor.state, goal = self._patrol(actor, brain)
                elif not continuing_cover and (brain.suppression >= 0.55 or actor.hurt_until > time or actor.hp < 20
                      or (brain.action in {"cover", "pin", "retreat"} and time < brain.action_until)):
                    self._combat_action(actor, brain, actors, point, time)
                    if brain.action in {"cover", "retreat", "pin"}:
                        actor.state, goal = "retreat", brain.action_goal
            elif looking_for_sound:
                actor.state, goal = "idle", None
                self._face(actor, memory.last_sound)
            else:
                brain.cue = None
                brain.search_target = None
                brain.search_cover = None
                brain.peek_home = None
                brain.incoming = None
                memory.last_seen = memory.reported = None
                self._set_role(brain, "")
                actor.state, goal = self._patrol(actor, brain)
            if actor.state == "retreat" and not memory.visible:
                watch_point = incoming if incoming is not None else memory.last_sound if fresh_sound and not tracking else threat
                if watch_point is not None:
                    self._face(actor, watch_point)
            weapon = actor.weapon
            if weapon and not weapon.chambered and not weapon.reloading:
                moving_to_cover = (actor.state == "retreat" and brain.action == "cover" and goal is not None
                                   and math.dist(actor.xy, goal) > 0.3)
                intent.reload = not moving_to_cover and (weapon.reserve > 0 or weapon.stage >= 2)
            if actor.state == "retreat" and brain.action == "cover" and goal is not None and math.dist(actor.xy, goal) <= 0.3:
                goal = None
            approximate = actor.state not in {"combat", "retreat", "combat_guard"} and goal not in self.exits
            if goal == brain.search_cover:
                approximate = False
            if goal == brain.indoor_target:
                approximate = False
            if not priority_search and brain.cue is not None and brain.cue[1] == memory.last_sound:
                approximate = False
            moving_sweep = (actor.state == "search" and goal is not None
                            and (goal == brain.indoor_target or priority_search))
            budget = self._move(actor, brain, intent, goal, time, budget,
                                face=not moving_sweep and not memory.visible and actor.state not in {"retreat", "combat_guard"}
                                and brain.action != "peek",
                                approximate=approximate)
            if moving_sweep:
                self._pace_indoor_movement(actor, brain, intent, time, room_sweep=not priority_search)
            else:
                brain.indoor_speed, brain.indoor_depart_at = 0.0, None
            if (actor.state == "retreat" and memory.visible and any(intent.direction)
                    and brain.action in {"cover", "retreat"} and brain.suppression < 0.85
                    and weapon is not None and weapon.chambered and not weapon.reloading
                    and time >= brain.fire_at):
                intent.retreat_fire = True
            intents[actor.id] = intent
        self._update_regroups(actors, time)
        for actor in actors:
            if actor.id in intents and not self.brains[actor.id].memory.visible:
                # 순찰·수색·경계 전환 때도 새 목표 방향으로 즉시 순간 회전하지 않는다.
                brain = self.brains[actor.id]
                rate = (INDOOR_TURN_RADIANS_PER_S if actor.state == "regroup" or brain.indoor_yield is not None
                        or (actor.state == "search" and (brain.indoor is not None or brain.investigating_priority))
                        else SEARCH_TURN_RADIANS_PER_S)
                actor.facing = self._turn_toward(previous_facing[actor.id], actor.facing, 1 / 60, rate=rate)
        self.door_traffic.update(self.world, actors, self.brains, intents, time)
        return intents
