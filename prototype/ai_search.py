"""단서 조사와 실내 관찰 진행을 관리한다."""

import math

from .ai_state import SearchCue, Incident, ScanState, FailedClue, ScanPhase
from .geometry import Rect
from .indoor_search import (IndoorSweep, SCAN_HOLD_S, SCAN_RANGE_M, STATIONARY_SCAN_RANGE_M, choose_look, sample_views)
from .ai_motion import face, turn_toward

from . import ai_config as config


class SearchController:
    def __init__(self, world, brains, incidents):
        self.world, self.brains, self.incidents = world, brains, incidents

    def refresh_failure(self, actor, brain):
        failure = brain.search.failed_clue
        if failure is not None and (failure.revision != self.world.revision or math.dist(actor.xy, failure.origin) > 1):
            brain.search.failed_clue = None
            brain.search.cue = None

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
        self.incidents[key] = Incident(0.0, 0.0, point, indoor=IndoorSweep(area, point, 0.0) if area else None)
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
        if brain.search.indoor.sweep is not None:
            brain.search.indoor.sweep.claims.pop(actor.id, None)
            brain.search.indoor.sweep.corner_claims.pop(actor.id, None)
        incident = self.incidents.get(brain.incident)
        if incident is not None:
            incident.claims.pop(actor.id, None)
        brain.search.indoor.target = brain.motion.yield_point = None
        brain.search.indoor.phase = ScanPhase.IDLE
        brain.search.indoor.staging = False
        brain.search.indoor.sound_cue = None
        brain.search.scan_until = 0.0
        brain.motion.heading = brain.search.indoor.scan_origin = None
        self.reset_walking_look(brain)
        brain.motion.speed, brain.motion.depart_at = 0.0, None
        brain.search.cue = brain.search.target = brain.search.cover = None
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
            if (brain.search.investigating_priority and cue.kind == previous.kind and cue.point == previous.point):
                return
            self.interrupt_search(actor, brain)
            brain.search.priority_cue = cue
            brain.search.priority_checked = False
            incident = self.incidents.get(brain.incident)
            if incident is not None and incident.indoor is not None and incident.indoor.regroup_slots:
                incident.indoor.reset_regroup()

    def indoor_sound_sweep(self, actor, brain, cue, time):
        if brain.search.indoor.sound_cue == cue and brain.search.indoor.sweep is not None:
            return brain.search.indoor.sweep
        if cue.kind != "sound":
            return None
        area = self.world.indoor_search_area(cue.point)
        if area is None or not any(rect.segment_entry(cue.point, cue.point) is not None for rect in area.floors):
            return None
        key = f"sound/{area.id}/{cue.at}"
        incident = self.incidents.get(key)
        if incident is None:
            incident = self.incidents[key] = Incident(time, actor.facing, cue.point)
        if incident.indoor is None:
            sweep = incident.indoor = IndoorSweep(area, cue.point, cue.at)
            previous = brain.search.indoor.sweep
            if previous is not None and previous.area is area:
                room = sweep.room_near(cue.point)
                sweep.checked.update(point for point in previous.checked if area.rooms[point] != room)
                sweep.checked_at.update((point, stamp) for point, stamp in previous.checked_at.items()
                                        if point in sweep.checked)
                sweep.corner_checks.update((point, check) for point, check in previous.corner_checks.items()
                                           if point in sweep.checked)
        return incident.indoor

    def enter_indoor_sound_sweep(self, actor, brain, cue, sweep):
        if brain.search.indoor.sound_cue == cue:
            return True
        area = sweep.area
        if not any(rect.expanded(-actor.radius).segment_entry(actor.xy, actor.xy) is not None for rect in area.floors):
            return False
        distance = math.dist(actor.xy, cue.point)
        if (not self.world.navigation.clear(actor.xy, cue.point)
                or self.world.door_on_ai_path_from(actor.xy, cue.point, reach=distance,
                                                   padding=actor.radius + 0.25) is not None):
            return False
        brain.search.indoor.sound_cue = cue
        brain.incident = f"sound/{area.id}/{cue.at}"
        if actor.id not in sweep.entry_order:
            sweep.entry_order.append(actor.id)
        return True

    def indoor_sweep(self, actor, brain, point, evidence_at, time, *, new_contact=False, area=None, entering=False):
        if area is None:
            area = self.world.indoor_search_area(point)
        if area is None:
            if brain.search.indoor.sweep is not None:
                brain.search.indoor.sweep.claims.pop(actor.id, None)
                brain.search.indoor.sweep = brain.search.indoor.target = None
                brain.search.indoor.phase = ScanPhase.IDLE
            return None
        if brain.incident not in self.incidents:
            brain.incident = actor.id
            self.incidents[actor.id] = Incident(time, actor.facing, point)
        incident = self.incidents[brain.incident]
        sweep = incident.indoor
        if sweep is None or (evidence_at > sweep.evidence_at and sweep.area is not area):
            sweep = incident.indoor = IndoorSweep(area, point, evidence_at)
        elif evidence_at > sweep.evidence_at and (new_contact or math.dist(point, sweep.anchor) > 4):
            sweep.reopen_room(point)
            sweep.anchor = point
        sweep.evidence_at = max(sweep.evidence_at, evidence_at)
        area = sweep.area
        if brain.search.indoor.sweep is not sweep:
            if brain.search.indoor.sweep is not None:
                brain.search.indoor.sweep.claims.pop(actor.id, None)
                brain.search.indoor.sweep.corner_claims.pop(actor.id, None)
            brain.search.indoor.sweep = sweep
            brain.search.indoor.target = None
            brain.search.indoor.staging = False
            brain.search.indoor.blocked.clear()
            brain.search.indoor.phase = ScanPhase.IDLE
            brain.search.indoor.scan_origin = brain.motion.heading = None
            if not entering:
                self.reset_walking_look(brain)

        if entering:
            pose = sweep.entry_pose(actor.id)
            if pose is not None:
                brain.search.indoor.target = pose.position
                brain.search.indoor.staging = True

        revision = self.world.revision
        target = brain.search.indoor.target
        previous_room = area.room_of(target)
        if target is not None:
            if (brain.motion.failed(target, revision)
                    and brain.search.indoor.blocked.get(target) != revision):
                brain.search.indoor.blocked[target] = revision
                brain.search.indoor.scan.walk_look = None
                self.begin_indoor_look(actor, brain)
                brain.look.reason = "이동 불가 · 현재 위치 확인"
                return None, False
            if (brain.search.indoor.blocked.get(target) != revision and brain.search.indoor.phase is not ScanPhase.FINISHED
                  and (brain.search.indoor.staging or any(sample not in sweep.checked for sample in area.room_points[previous_room]))):
                sweep.claims[actor.id] = target, time + 1
                if math.dist(actor.xy, target) > 0.25:
                    return target, False
                self.begin_indoor_look(actor, brain)
                return None, False
            sweep.claims.pop(actor.id, None)
            brain.search.indoor.target = None
            brain.search.indoor.staging = False
            brain.search.indoor.phase = ScanPhase.IDLE

        available = {candidate for candidate in area.points
                     if sweep.observed_from.get(candidate) != revision
                     and brain.search.indoor.blocked.get(candidate) != revision}
        unchecked = [candidate for candidate in sweep.pending_for(actor.id, None) if candidate in available]
        reserved = {target for owner, (target, until) in sweep.claims.items()
                    if owner != actor.id and time < until}
        target = min((candidate for candidate in unchecked if candidate not in reserved),
                     key=lambda candidate: (area.rooms[candidate] != previous_room,
                                            math.dist(actor.xy, candidate) + 0.3 * math.dist(point, candidate),
                                            candidate), default=None)
        if target is not None:
            room = area.rooms[target]
            remaining = sweep.pending_for(actor.id, room)
            heading = actor.facing if brain.motion.heading is None else brain.motion.heading
            candidates = [candidate for candidate in area.room_points[room]
                          if candidate in available and candidate not in reserved]
            scan_origin = (actor.xy, room, revision)
            if (brain.search.indoor.scan_origin != scan_origin
                    and sweep.can_scan_here(actor.xy, remaining, self.world.geometry)):
                brain.search.indoor.target = target
                sweep.claims[actor.id] = target, time + 1
                self.begin_indoor_look(actor, brain)
                return None, False
            authored = (candidate for candidate, candidate_room in area.observations.items()
                        if candidate_room == room and candidate not in reserved
                        and sweep.observed_from.get(candidate) != revision
                        and brain.search.indoor.blocked.get(candidate) != revision
                        and sweep.can_scan_here(candidate, remaining, self.world.geometry))
            target = min(authored, key=lambda candidate: math.dist(actor.xy, candidate), default=None)
            if target is None:
                target = sweep.observation_point(actor.xy, heading, candidates, remaining, self.world.navigation)
            brain.search.indoor.target = target
            sweep.claims[actor.id] = target, time + 1
        return target, sweep.complete

    @staticmethod
    def begin_indoor_look(actor, brain):
        brain.search.indoor.staging = False
        brain.motion.speed = 0.0
        brain.search.indoor.phase = ScanPhase.SCANNING
        brain.look.reason = "정지 후 정면 확인"

    def advance_indoor_look(self, actor, brain, time):
        sweep, target = brain.search.indoor.sweep, brain.search.indoor.target
        room = sweep.area.room_of(target)
        sweep.claims[actor.id] = target, time + 1
        self.look_at_indoor_samples(actor, brain, time, sweep, room, stationary=True)
        if brain.look.point is not None:
            return
        if brain.search.indoor.scan.exposure:
            return
        revision = self.world.revision
        brain.search.indoor.scan_origin = (actor.xy, room, revision)
        if math.dist(actor.xy, target) <= 0.25:
            sweep.observed_from[target] = revision
        brain.search.indoor.phase = ScanPhase.FINISHED
        brain.look.reason = "현재 위치 관찰 종료"

    @staticmethod
    def reset_walking_look(brain):
        brain.search.indoor.scan = ScanState()
        brain.look.turn_rate = 0.0

    def look_at_indoor_samples(self, actor, brain, time, sweep, room=None, *, stationary=False):
        if brain.search.indoor.observation is not sweep:
            self.reset_walking_look(brain)
            brain.search.indoor.observation = sweep
        scan = brain.search.indoor.scan
        geometry, revision = self.world.geometry, self.world.revision
        if time - scan.sample_at > 1 / 30:
            scan.exposure.clear()
        scan.sample_at = time
        brain.search.indoor.sample_stationary = stationary
        hold = SCAN_HOLD_S if stationary else config.INDOOR_WALK_CONFIRM_S
        pending = sweep.pending_for(actor.id, room, revisit_corners=not brain.search.indoor.staging)
        corners = set(pending) & set(sweep.pending_corners(room))
        point = scan.walk_look
        scan_range = STATIONARY_SCAN_RANGE_M if stationary else SCAN_RANGE_M
        visible = (point in pending and math.dist(actor.xy, point) <= scan_range
                   and geometry.raycast(actor.xy, point, category="sight") is None)
        if visible and point in corners:
            visible = point in sweep.available_corners(corners, actor.id, time)

        candidates = (point,) if visible and point in corners else (
            () if corners and (not stationary or not visible) else tuple(sample for sample in pending if sample not in corners))
        views = dict(sample_views(actor.xy, actor.facing, candidates, geometry, stationary=stationary))
        seen = {sample: scan.exposure.get(sample, time) for sample, clear in views.items() if clear}
        blocked = {sample for sample, clear in views.items() if not clear}
        revealed = scan.occluded.intersection(seen)
        scan.occluded.intersection_update(pending)
        scan.occluded.difference_update(seen)
        scan.occluded.update(blocked)
        for sample, since in tuple(seen.items()):
            if time - since < hold:
                continue
            if sample in corners:
                sweep.confirm_corner(sample, actor.id, time)
            else:
                sweep.confirm(sample, time)
            del seen[sample]
        scan.exposure = seen
        scan.revealed.difference_update(blocked | sweep.checked)
        scan.revealed.update(revealed.intersection(seen))

        confirmed = point in sweep.checked
        settled = not visible or abs(math.remainder(
            math.atan2(point[1] - actor.xy[1], point[0] - actor.xy[0]) - actor.facing, math.tau
        )) <= config.INDOOR_REFOCUS_ANGLE
        newly_visible = (settled and not corners and bool(revealed.intersection(seen))
                         and point not in scan.revealed)
        if newly_visible or confirmed or (not visible and (point is not None or stationary
                or time >= scan.walk_look_at or revision != scan.walk_look_revision)):
            pending = sweep.pending_for(actor.id, room, revisit_corners=not brain.search.indoor.staging)
            corners = set(pending) & set(sweep.pending_corners(room))
            available = sweep.available_corners(corners, actor.id, time)
            choice = None
            if not corners and scan.revealed:
                choice = sweep.look(actor.xy, actor.facing, room, geometry, walking=not stationary,
                                    points=(sample for sample in pending if sample in scan.revealed))
            if choice is None:
                choice = sweep.look(actor.xy, actor.facing, room, geometry, walking=not stationary,
                                    continue_scan=confirmed, points=available if corners else pending)
            if choice is None and stationary and corners and not sweep.can_scan_here(actor.xy, corners, geometry):
                choice = sweep.look(actor.xy, actor.facing, room, geometry, points=(
                    sample for sample in pending if sample not in corners))
            point = choice.point if choice is not None else next(
                (sample for sample in available if stationary and math.dist(actor.xy, sample) < 0.15
                 and geometry.raycast(actor.xy, sample, category="sight") is None), None)
            visible = point is not None
            scan.walk_look = point
            scan.walk_look_at = time + config.INDOOR_WALK_LOOK_INTERVAL_S
            scan.walk_look_revision = revision

        brain.look.point = point if visible else None
        if visible and point in corners:
            sweep.corner_claims[actor.id] = point, time + config.INDOOR_WALK_LOOK_INTERVAL_S
        else:
            sweep.corner_claims.pop(actor.id, None)
        brain.look.reason = ("정지 중 공간 확인" if stationary else "이동 중 공간 확인") if visible else "주시 방향 유지"
        if visible:
            scan.walk_look_started = True
            dx, dy = point[0] - actor.xy[0], point[1] - actor.xy[1]
            if math.hypot(dx, dy) >= 0.15:
                desired = math.atan2(dy, dx)
                actor.facing = turn_toward(actor.facing, desired, 1 / 60, rate=config.INDOOR_SWEEP_TURN_RADIANS_PER_S)
        elif brain.search.indoor.staging:
            pose = sweep.entry_pose(actor.id)
            if pose is not None:
                face(actor, pose.look)
                brain.look.point = pose.look
                brain.look.reason = "수색 시작 위치에서 실내 주시" if stationary else "수색 시작 위치로 이동 · 실내 주시"

    def pace_indoor_movement(self, actor, brain, intent, time, *, room_sweep=False, independent_look=False):
        dx, dy = intent.direction
        if not (dx or dy):
            brain.motion.speed, brain.motion.depart_at = 0.0, None
            brain.search.indoor.scan.exposure.clear()
            return

        tick = 1 / 60
        heading = math.atan2(dy, dx)
        distance = math.hypot(dx, dy)
        bend = 0.0
        if len(brain.motion.path) > 1:
            point, following = brain.motion.path[:2]
            outgoing = math.atan2(following[1] - point[1], following[0] - point[0])
            bend = math.remainder(outgoing - heading, math.tau)
        if room_sweep:
            brain.motion.heading = heading
            sweep = brain.search.indoor.sweep
            self.look_at_indoor_samples(actor, brain, time, sweep, sweep.area.room_of(brain.search.indoor.target))
        else:
            if brain.motion.depart_at is None:
                brain.motion.depart_at = time + config.INDOOR_START_DELAY_S
            error = 0.0
            if not independent_look:
                actor.facing = turn_toward(actor.facing, heading, tick, rate=config.INDOOR_TURN_RADIANS_PER_S)
                error = abs(math.remainder(heading - actor.facing, math.tau))
                brain.look.point, brain.look.reason = brain.motion.path[0], "진행 방향 확인"
            if error > 1e-6 or time < brain.motion.depart_at:
                brain.motion.speed = 0.0
                brain.motion.progress_at, brain.motion.progress_point = time, actor.xy
                intent.direction, intent.open_door = (0, 0), False
                if not independent_look:
                    brain.look.reason = "진행 방향으로 회전 중"
                return

        speed_limit = actor.speed * config.INDOOR_MOVE_MULTIPLIER
        exit_speed = 0.0
        if len(brain.motion.path) > 1:
            bend = abs(bend)
            if bend <= math.radians(25):
                exit_speed = speed_limit
            elif (room_sweep or independent_look) and bend <= config.INDOOR_SMOOTH_BEND:
                exit_speed = speed_limit * math.cos(bend)
        speed_limit = min(speed_limit, math.sqrt(exit_speed ** 2 + 2 * config.INDOOR_BRAKE_MPS2 * distance))
        increase = config.INDOOR_ACCEL_MPS2 * tick
        decrease = config.INDOOR_BRAKE_MPS2 * tick
        brain.motion.speed = max(brain.motion.speed - decrease, min(speed_limit, brain.motion.speed + increase))
        intent.move_multiplier = brain.motion.speed / actor.speed
        intent.step_dt = min(tick, distance / brain.motion.speed)
        intent.move_straight = True

    def watch_visible_space(self, actor, brain, cue, time):
        geometry = self.world.geometry
        if (math.dist(actor.xy, cue) > 0.05 and geometry.raycast(actor.xy, cue, category="sight") is None):
            brain.look.point, brain.look.reason = cue, "보고 위치가 보임"
            face(actor, cue)
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
            brain.look.watch_point = choice.point if choice else None
            brain.look.watch_cue, brain.look.watch_at = cue, time + config.SEARCH_LOOK_UPDATE_S

        if brain.look.watch_point is not None:
            brain.look.point, brain.look.reason = brain.look.watch_point, "열린 공간 경계"
            face(actor, brain.look.watch_point)
        else:
            brain.look.reason = "보이는 경계 지점 없음"

    def search(self, actor, brain, cue, time, *, sound_only=False, confirmed=False, direct=False):
        incident = None if direct else self.incidents.get(brain.incident)
        if cue != brain.search.cue:
            brain.search.cue = cue
            brain.search.target = None
            brain.search.checked.clear()
            brain.search.cover = None
            nav = self.world.navigation
            x, y = cue[1]
            radius = min(36, 18 + brain.search.sweep_pass * 4) if confirmed else 6
            keys = nav.point_index.query(Rect(x - radius, y - radius, radius * 2, radius * 2))
            candidates = [nav.nodes[key] for key in keys if math.dist(nav.nodes[key], cue[1]) <= radius
                          and (not sound_only or nav.clear(cue[1], nav.nodes[key]))]
            heading = brain.memory.seen_heading
            forward = (math.cos(heading), math.sin(heading)) if heading is not None else (0, 0)
            candidates.sort(key=lambda point: (math.dist(point, cue[1]) -
                            sum((a - b) * direction for a, b, direction in zip(point, cue[1], forward)) * 0.3,
                            math.dist(actor.xy, point), point))
            if brain.search.sweep_pass and candidates:
                start = (brain.search.sweep_pass - 1) * 5 % len(candidates)
                candidates = candidates[start:] + candidates[:start]
            brain.search.points = list(dict.fromkeys([cue[1], *([] if direct else candidates[:5])]))
            brain.search.scan_until = 0
        if time < brain.search.scan_until:
            actor.facing = brain.search.scan_angle
            if not direct:
                actor.facing += math.sin((brain.search.scan_until - time) * 5) * 0.95
            if incident and brain.search.target is not None:
                incident.claims[actor.id] = brain.search.target, time + 0.65
            return None
        if brain.search.target is not None:
            point = brain.search.target
            if self.world.geometry.raycast(actor.xy, point, category="sight") is None:
                brain.search.checked.add(point)
                if incident:
                    incident.checked.append((point, time))
                    del incident.checked[:-32]
            if incident:
                incident.claims.pop(actor.id, None)
            brain.search.target = None
        while brain.search.points:
            point = brain.search.points[0]
            if incident and (any(math.dist(point, checked) < 2 for checked, _ in incident.checked)
                             or any(owner != actor.id and time < expires and math.dist(point, reserved) < 2
                                    for owner, (reserved, expires) in incident.claims.items())):
                brain.search.points.pop(0)
                continue
            if brain.motion.failed(point, self.world.revision):
                brain.search.points.pop(0)
                continue
            distance = math.dist(actor.xy, point)
            ended = brain.motion.arrived(point, self.world.revision)
            assigned = brain.search.arrival
            at_slot = assigned is not None and assigned[0] == point and math.dist(actor.xy, assigned[1].destination) <= 0.12
            if at_slot:
                assigned[1].passed = True
                report, current = brain.search.reported_check, brain.search.priority_cue
                if report is not None and current is not None and report.kind == current.kind and report.point == point and report.at >= current.at:
                    brain.search.checked.add(point)
                    brain.search.points.pop(0)
                    return None
            visible_at_arrival = (distance < 1 or (at_slot and distance <= STATIONARY_SCAN_RANGE_M)) and self.world.geometry.raycast(actor.xy, point, category="sight") is None
            if ended or ((distance < 0.35 or at_slot) and visible_at_arrival):
                brain.search.points.pop(0)
                if not visible_at_arrival:
                    continue
                brain.search.target = point
                face(actor, cue[1])
                duration = 2.0 if brain.role == "intercept" else 0.5 if confirmed else 0.85
                brain.search.scan_until, brain.search.scan_angle = time + duration, actor.facing
                return None
            if incident:
                incident.claims[actor.id] = point, time + 0.65
            return point
        return None
