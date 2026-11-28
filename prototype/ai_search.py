"""단서 조사와 실내 관찰 진행을 관리한다."""

import math

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Sequence

from .ai_state import SearchCue, Incident, ScanState, FailedClue, InvestigationState
from .geometry import Rect
from .indoor_search import (IndoorSweep, SCAN_HOLD_S, SCAN_RANGE_M, STATIONARY_SCAN_RANGE_M, choose_look, sample_views)

from . import ai_config as config


class SearchController:
    def __init__(self, world, brains, incidents):
        self.world, self.brains, self.incidents = world, brains, incidents
        self.view_budget = None

    def begin_tick(self):
        self.view_budget = 3

    def refresh_failure(self, actor, brain):
        failure = brain.search.failed_clue
        if failure is not None and (failure.revision != self.world.revision or math.dist(actor.xy, failure.origin) > 1):
            brain.search.failed_clue = None
            brain.search.task.cue = None

    def finish_investigation(self, actor, brain, cue, time, verified):
        search = brain.search
        if verified:
            search.failed_clue = None
            if search.priority_cue == cue:
                search.priority_checked = True
        else:
            search.failed_clue = FailedClue(cue, actor.xy, self.world.revision)
        if cue.kind == "sound":
            if verified:
                search.checked_sound_at = cue.at
            search.sound_response = "checked" if verified else "unreachable"
            search.sound_response_at = time

    def start_search_drill(self, actors, point):
        key = "indoor_drill"
        area = self.world.indoor_search_area(point)
        self.incidents[key] = Incident(0.0, point, indoor=IndoorSweep(area, point, 0.0) if area else None)
        for actor in actors:
            brain = self.brains[actor.id]
            brain.search.priority_cue = None
            brain.search.priority_checked = False
            brain.incident = key
            brain.memory.reported = point
            brain.memory.reported_at = 0.0
            brain.memory.report_source = key
            brain.combat.combat_alert_until = config.COMBAT_ALERT_SECONDS

    def interrupt_search(self, actor, brain):
        if brain.search.task.execution is not None:
            brain.search.task.execution.stop(Status.INVALID)
        brain.search.task = InvestigationState(sweep=brain.search.task.sweep, blocked=brain.search.task.blocked)
        brain.motion.yield_point = None
        brain.motion.heading = None
        brain.look.turn_rate = 0.0
        brain.motion.speed = 0.0
        brain.search.task.cue = brain.search.task.target = brain.search.cover = None
        brain.search.arrival = brain.search.reported_check = None
        brain.search.next_sweep_at = 0.0
        brain.motion.cancel()
        brain.motion.plan_at = 0.0
        brain.motion.blocked_by = brain.motion.door_wait = ""

    def update_priority_cue(self, actor, brain, tracking, investigating, incoming):
        memory = brain.memory
        previous = brain.search.priority_cue
        latest = previous.at if previous is not None else -math.inf
        cue = None
        if tracking and memory.seen_at > latest:
            cue = SearchCue("seen", memory.last_seen, memory.seen_at)
        recent_gunshot = memory.gunshot_origin is not None and brain.combat.incoming_at <= memory.sound_heard_at + 0.25
        if incoming is not None and not recent_gunshot and brain.combat.incoming_at > (cue.at if cue else latest):
            cue = SearchCue("impact", incoming, brain.combat.incoming_at)
        if investigating and memory.sound_at > latest and (cue is None or memory.sound_at >= cue.at):
            cue = SearchCue("sound", memory.last_sound, memory.sound_at)
        if cue is not None:
            # 실내를 확인하는 중에는 약한 실외 소리를 후속 단서로 남긴다.
            # 수색이 끝나면 같은 최신 단서를 다음 틱에 다시 평가한다.
            sweep = brain.search.task.sweep
            if (cue.kind == "sound" and sweep is not None and not sweep.complete
                    and memory.gunshot_origin is None and memory.sound_priority < config.SEARCH_INTERRUPT_SOUND_PRIORITY
                    and any(floor.segment_entry(actor.xy, actor.xy) is not None for floor in sweep.area.floors)
                    and not any(floor.segment_entry(cue.point, cue.point) is not None for floor in sweep.area.floors)):
                return
            if (brain.search.investigating_priority and cue.kind == previous.kind and cue.point == previous.point):
                return
            self.interrupt_search(actor, brain)
            brain.search.priority_cue = cue
            brain.search.priority_checked = False
            incident = self.incidents.get(brain.incident)
            if incident is not None and incident.indoor is not None and incident.indoor.regroup_slots:
                incident.indoor.reset_regroup()

    def indoor_sound_sweep(self, actor, brain, cue, time):
        if brain.search.task.sound_cue == cue and brain.search.task.sweep is not None:
            return brain.search.task.sweep
        if cue.kind != "sound":
            return None
        area = self.world.indoor_search_area(cue.point)
        if area is None or not any(rect.segment_entry(cue.point, cue.point) is not None for rect in area.floors):
            return None
        key = f"sound/{area.id}/{cue.at}"
        incident = self.incidents.get(key)
        if incident is None:
            incident = self.incidents[key] = Incident(time, cue.point)
        if incident.indoor is None:
            sweep = incident.indoor = IndoorSweep(area, cue.point, cue.at)
            previous = brain.search.task.sweep
            if previous is not None and previous.area is area:
                room = sweep.room_near(cue.point)
                sweep.checked.update(point for point in previous.checked if area.rooms[point] != room)
                sweep.corner_checks.update((point, check) for point, check in previous.corner_checks.items()
                                           if point in sweep.checked)
        return incident.indoor

    def enter_indoor_sound_sweep(self, actor, brain, cue, sweep):
        if brain.search.task.sound_cue == cue:
            return True
        area = sweep.area
        if not any(rect.expanded(-actor.radius).segment_entry(actor.xy, actor.xy) is not None for rect in area.floors):
            return False
        distance = math.dist(actor.xy, cue.point)
        if (not self.world.navigation.clear(actor.xy, cue.point)
                or self.world.door_on_ai_path_from(actor.xy, cue.point, reach=distance,
                                                   padding=actor.radius + 0.25) is not None):
            return False
        brain.search.task.sound_cue = cue
        brain.incident = f"sound/{area.id}/{cue.at}"
        if actor.id not in sweep.entry_order:
            sweep.entry_order.append(actor.id)
        return True

    def investigate(self, actor, brain, cue, time, *, area=None, entering=False, new_contact=False,
                    sound_only=False, confirmed=False, direct=False):
        search, revision = brain.search, self.world.revision
        task = search.task
        key = (cue, area.id if area else None)
        if task.cue != key:
            if task.execution is not None:
                task.execution.stop(Status.INVALID)
            search.checked.clear()
            sweep = None
            if area is not None:
                brain.incident = brain.incident or actor.id
                incident = self.incidents.setdefault(brain.incident, Incident(time, cue[1]))
                sweep = incident.indoor
                evidence_at = cue[2] if len(cue) > 2 else max(brain.memory.seen_at, brain.memory.reported_at)
                if sweep is None or sweep.area is not area:
                    sweep = incident.indoor = IndoorSweep(area, cue[1], evidence_at)
                elif new_contact or math.dist(cue[1], sweep.anchor) > 4:
                    sweep.reopen_room(cue[1])
                    sweep.anchor = cue[1]
                sweep.evidence_at = max(sweep.evidence_at, evidence_at)
            task = search.task = InvestigationState(cue=key, sweep=sweep, sound_cue=task.sound_cue)
            if sweep is None:
                nav, point = self.world.navigation, cue[1]
                radius = min(36, 18 + search.sweep_pass * 4) if confirmed else 6
                candidates = (nav.nodes[k] for k in nav.point_index.query(Rect(point[0]-radius, point[1]-radius, 2*radius, 2*radius)))
                heading = brain.memory.seen_heading or 0
                candidates = sorted((p for p in candidates if math.dist(p, point) <= radius and
                                     (not sound_only or nav.clear(point, p))), key=lambda p:
                                    (math.dist(p, point) - .3*((p[0]-point[0])*math.cos(heading)+(p[1]-point[1])*math.sin(heading)), p))
                task.points = list(dict.fromkeys([point, *([] if direct else candidates[:5])]))
            elif entering and (pose := sweep.entry_pose(actor.id)) is not None:
                task.target, task.staging = pose.position, True
            search.cover = None
        lease = task.sweep or (None if direct else self.incidents.get(brain.incident))
        if task.target is None:
            if task.sweep is not None and self.view_budget is not None:
                if task.sweep.complete:
                    return None, True
                if time < task.select_at or not self.view_budget:
                    return None, False
                task.select_at = time + 0.1
                self.view_budget -= 1
            task.target = self.next_view(actor, brain, time, lease)
            task.probe_at = time
        if task.target is None:
            return None, task.sweep.complete if task.sweep else not task.points
        if task.execution is None:
            task.execution = Visit(self, actor, brain, confirmed)
        visit = task.execution
        visit.time, visit.lease = time, lease
        if lease is not None:
            lease.claims[actor.id] = task.target, time + 1
        visit.tick_once()
        if visit.status in {Status.SUCCESS, Status.FAILURE}:
            if task.sweep is not None:
                task.scan_origin = actor.xy, task.sweep.area.room_of(task.target), revision
                if math.dist(actor.xy, task.target) <= .25:
                    task.sweep.observed_from[task.target] = revision
            elif visit.status is Status.SUCCESS:
                search.checked.add(task.target)
                if lease is not None:
                    lease.checked.append((task.target, time))
                    del lease.checked[:-32]
            task.target, task.staging = None, False
        return visit.goal, False

    def next_view(self, actor, brain, time, lease):
        task, revision = brain.search.task, self.world.revision
        if task.sweep is None:
            while task.points:
                point = task.points.pop(0)
                occupied = lease is not None and (any(math.dist(point, p) < 2 for p, _ in lease.checked)
                            or any(math.dist(point, p) < 2 for p in lease.reserved(actor.id, time)))
                if not occupied and not brain.motion.failed(point, revision):
                    return point
            return None
        sweep, area = task.sweep, task.sweep.area
        reserved = sweep.reserved(actor.id, time)
        available = {p for p in (*area.points, *area.observations) if p not in reserved
                     and task.blocked.get(p) != revision and sweep.observed_from.get(p) != revision}
        pending = sweep.pending_for(actor.id, None)
        previous_room = task.scan_origin[1] if task.scan_origin else None
        target = min((p for p in pending if p in available), key=lambda p:
                     (area.rooms[p] != previous_room, math.dist(actor.xy, p) + .3 * math.dist(sweep.anchor, p), p), default=None)
        if target is None:
            target = min(pending, key=lambda p: math.dist(actor.xy, p), default=None)
        if target is None:
            return None
        room = area.rooms[target]
        remaining = sweep.pending_for(actor.id, room)
        if task.scan_origin != (actor.xy, room, revision) and sweep.can_scan_here(actor.xy, remaining, self.world.geometry):
            return target
        authored = (p for p in area.observations if p in available and area.observations[p] == room
                    and sweep.fixed_view_count(p, remaining, self.world.geometry, revision))
        candidates = [p for p in area.viewpoints.get(room, area.room_points[room]) if p in available]
        if not candidates:
            candidates = [p for p in area.room_points[room] if p in available]
        return min(authored, key=lambda p: math.dist(actor.xy, p), default=None) or (sweep.observation_point(
            actor.xy, actor.facing if brain.motion.heading is None else brain.motion.heading,
            candidates, remaining, self.world.navigation, revision=revision) if candidates else None)

    @staticmethod
    def reset_walking_look(brain):
        brain.search.task.scan = ScanState()
        brain.look.turn_rate = 0.0

    def look_at_indoor_samples(self, actor, brain, time, sweep, room=None, *, stationary=False):
        task = brain.search.task
        if task.observation is not sweep:
            self.reset_walking_look(brain)
            task.observation = sweep
        scan, geometry = task.scan, self.world.geometry
        task.sample_stationary = stationary
        brain.look.sample = sweep, room, stationary
        pending = sweep.pending_for(actor.id, room, revisit_corners=not task.staging)
        corners = set(pending) & set(sweep.pending_corners(room))
        available = sweep.available_corners(corners, actor.id, time)
        point = scan.walk_look
        scan_range = STATIONARY_SCAN_RANGE_M if stationary else SCAN_RANGE_M
        valid = (point in pending and math.dist(actor.xy, point) <= scan_range
                 and geometry.raycast(actor.xy, point, category="sight") is None
                 and (point not in corners or point in available))
        revealed = {p for p, clear in sample_views(actor.xy, actor.facing, scan.occluded & set(pending), geometry,
                                                   stationary=stationary) if clear}
        if not valid or (not corners and revealed and not scan.urgent_focus):
            priority = revealed if revealed and not corners else available if corners else pending
            point = sweep.look(actor.xy, actor.facing, room, geometry, walking=not stationary, points=priority)
            if point is None and stationary and corners and not sweep.can_scan_here(actor.xy, corners, geometry):
                point = sweep.look(actor.xy, actor.facing, room, geometry, points=set(pending) - corners)
            if point is None and stationary:
                point = next((p for p in available if math.dist(actor.xy, p) < .15
                              and geometry.raycast(actor.xy, p, category="sight") is None), None)
            scan.walk_look, scan.urgent_focus = point, point in revealed
        brain.look.point = point
        brain.look.reason = ("정지 중 공간 확인" if stationary else "이동 중 공간 확인") if point else "주시 방향 유지"
        if point in corners:
            sweep.corner_claims[actor.id] = point, time + config.INDOOR_WALK_LOOK_INTERVAL_S
        else:
            sweep.corner_claims.pop(actor.id, None)
        if point is not None:
            scan.walk_look_started = True
        elif task.staging and (pose := sweep.entry_pose(actor.id)) is not None:
            brain.look.point, brain.look.reason = pose.look, "수색 시작 위치로 이동 · 실내 주시"

    def confirm_view(self, actor, brain, time):
        sweep, room, stationary = brain.look.sample
        scan = brain.search.task.scan
        if time < actor.blinded_until:
            scan.exposure.clear()
            return
        pending = sweep.pending_for(actor.id, room, revisit_corners=not brain.search.task.staging)
        corners = set(pending) & set(sweep.pending_corners(room))
        candidates = (scan.walk_look,) if scan.walk_look in corners else (p for p in pending if p not in corners)
        views = dict(sample_views(actor.xy, actor.facing, candidates, self.world.geometry, stationary=stationary,
                                  floor=actor.position.floor))
        if time - scan.sample_at > 1 / 30:
            scan.exposure.clear()
        scan.sample_at = time
        seen = {p: scan.exposure.get(p, time) for p, clear in views.items() if clear}
        blocked = {p for p, clear in views.items() if not clear}
        scan.occluded.intersection_update(pending)
        scan.occluded.difference_update(seen)
        scan.occluded.update(blocked)
        hold = SCAN_HOLD_S if stationary else config.INDOOR_WALK_CONFIRM_S
        for point, since in tuple(seen.items()):
            if time - since >= hold:
                if point in corners:
                    sweep.confirm_corner(point, actor.id, time)
                else:
                    sweep.confirm(point, time)
                del seen[point]
        scan.exposure = seen

    def pace_indoor_movement(self, actor, brain, intent, time, *, room_sweep=False, independent_look=False):
        motion = brain.motion
        distance = math.hypot(*intent.direction)
        if distance < 1e-8:
            motion.speed = 0
            brain.search.task.scan.exposure.clear()
            return
        heading = math.atan2(intent.direction[1], intent.direction[0])
        if room_sweep:
            motion.heading = heading
            sweep = brain.search.task.sweep
            self.look_at_indoor_samples(actor, brain, time, sweep, sweep.area.room_of(brain.search.task.target))
        elif not independent_look:
            brain.look.point, brain.look.reason = motion.path[0], "진행 방향 확인"
        speed_limit = actor.speed * config.INDOOR_MOVE_MULTIPLIER
        exit_speed = 0
        if len(motion.path) > 1:
            point, following = motion.path[:2]
            bend = abs(math.remainder(math.atan2(following[1]-point[1], following[0]-point[0]) - heading, math.tau))
            if bend <= math.radians(25):
                exit_speed = speed_limit
            elif (room_sweep or independent_look) and bend <= config.INDOOR_SMOOTH_BEND:
                exit_speed = speed_limit * math.cos(bend)
        speed_limit = min(speed_limit, math.sqrt(exit_speed ** 2 + 2 * config.INDOOR_BRAKE_MPS2 * distance))
        motion.speed = max(motion.speed - config.INDOOR_BRAKE_MPS2 / 60,
                           min(speed_limit, motion.speed + config.INDOOR_ACCEL_MPS2 / 60))
        intent.move_multiplier = motion.speed / actor.speed
        intent.step_dt = min(1 / 60, distance / motion.speed)
        intent.move_straight = True

    def watch_visible_space(self, actor, brain, cue, time):
        geometry = self.world.geometry
        if (math.dist(actor.xy, cue) > 0.05 and geometry.raycast(actor.xy, cue, category="sight") is None):
            brain.look.point, brain.look.reason = cue, "보고 위치가 보임"
            return

        if (brain.look.watch_cue != cue or time >= brain.look.watch_at or (brain.look.watch_point is not None
                and geometry.raycast(actor.xy, brain.look.watch_point, category="sight") is not None)):
            nav = self.world.navigation
            x, y = actor.xy
            radius = SCAN_RANGE_M
            keys = nav.point_index.query(Rect(x - radius, y - radius, radius * 2, radius * 2))
            samples = {nav.nodes[key]: 1 for key in keys}
            area = self.world.indoor_search_area(actor.xy)
            if area is not None:
                samples.update({point: 1 for point in area.points})
            choice = choose_look(actor.xy, actor.facing, geometry, samples, max_offset=math.pi)
            brain.look.watch_point = choice
            brain.look.watch_cue, brain.look.watch_at = cue, time + config.SEARCH_LOOK_UPDATE_S

        if brain.look.watch_point is not None:
            brain.look.point, brain.look.reason = brain.look.watch_point, "열린 공간 경계"
        else:
            brain.look.reason = "보이는 경계 지점 없음"



class VisitStep(Behaviour):
    def __init__(self, name, update):
        super().__init__(name)
        self.callback = update

    def update(self):
        return self.callback()


class Visit(Sequence):
    """실내·실외가 공유하는 이동→관찰 행동. 종료와 중단 때 예약을 반납한다."""

    def __init__(self, controller, actor, brain, confirmed):
        self.controller, self.actor, self.brain = controller, actor, brain
        self.time, self.lease, self.goal, self.since = 0, None, None, None
        self.hold = .5 if confirmed else .85
        super().__init__("investigate", memory=True, children=[VisitStep("move", self.move), VisitStep("observe", self.observe)])

    def initialise(self):
        self.goal, self.since, self.started = None, None, None

    def move(self):
        actor, brain = self.actor, self.brain
        task, world = brain.search.task, self.controller.world
        point = task.target
        self.goal = point
        if brain.motion.failed(point, world.revision):
            task.blocked[point] = world.revision
            return Status.SUCCESS if task.sweep else Status.FAILURE
        if task.sweep is not None:
            room = task.sweep.area.room_of(point)
            arrived = math.dist(actor.xy, point) <= .25
            if (not arrived and not task.staging and self.time >= task.probe_at
                    and task.scan_origin != (actor.xy, room, world.revision)):
                # 이동 중 관찰 지점으로 전환할 수 있는지만 검사한다. 실제 시야 확인은 기존 주기를 쓴다.
                task.probe_at = self.time + .05
                arrived = task.sweep.can_scan_here(actor.xy, task.sweep.pending_for(actor.id, room), world.geometry)
        else:
            assigned = brain.search.arrival
            arrived = math.dist(actor.xy, point) < .35 or brain.motion.arrived(point, world.revision)
            if assigned is not None and assigned[0] == point and math.dist(actor.xy, assigned[1].destination) <= .12:
                assigned[1].passed = arrived = True
        return Status.SUCCESS if arrived else Status.RUNNING

    def observe(self):
        actor, brain = self.actor, self.brain
        task, world = brain.search.task, self.controller.world
        self.goal, task.staging = None, False
        self.started = self.time if self.started is None else self.started
        brain.motion.speed = 0
        if self.time < actor.blinded_until:
            self.started, self.since = self.time, None
            return Status.RUNNING
        if task.sweep is not None:
            self.controller.look_at_indoor_samples(actor, brain, self.time, task.sweep,
                                                  task.sweep.area.room_of(task.target), stationary=True)
            return Status.RUNNING if brain.look.point is not None or task.scan.exposure else Status.SUCCESS
        cue, report = brain.search.priority_cue, brain.search.reported_check
        if cue is not None and report is not None and report.kind == cue.kind and report.point == task.target and report.at >= cue.at:
            return Status.SUCCESS
        brain.look.point, brain.look.reason = task.target, "단서 위치 확인"
        views = dict(sample_views(actor.xy, actor.facing, (task.target,), world.geometry, stationary=True,
                                  floor=actor.position.floor))
        self.since = (self.time if self.since is None else self.since) if views.get(task.target) else None
        if self.since is not None and self.time - self.since >= self.hold:
            return Status.SUCCESS
        return Status.FAILURE if self.since is None and self.time - self.started > 1.5 else Status.RUNNING

    def terminate(self, new_status):
        self.goal = None
        if self.lease is not None:
            self.lease.release(self.actor.id)
        if new_status is Status.INVALID:
            self.brain.search.task.scan.exposure.clear()
