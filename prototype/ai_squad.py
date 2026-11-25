"""분대 보고·배치·통과 대기와 수색 후 집결을 조정한다."""

from .ai_state import MoveRequest
import math

from scipy.optimize import linear_sum_assignment
from scipy.spatial import cKDTree

from .ai_state import Incident, SearchCue
from .geometry import segment_distance
from .squad_approach import SquadApproach, assign_approach

from . import ai_config as config


class SquadCoordinator:
    def __init__(self, world, brains, incidents, can_radio=None):
        self.world, self.brains, self.incidents = world, brains, incidents
        self.can_radio = can_radio
        self.approaches = {}
        self.assign_at = 0.0

    @staticmethod
    def report_point(brain):
        return tuple(round(axis / 4) * 4 for axis in brain.memory.last_seen)

    def report_sighting(self, actor, brain, actors, time, *, local_only=False):
        point = self.report_point(brain)
        receivers = self.receivers(actor, actors, local_only=local_only)
        if not receivers:
            return
        key = brain.incident if brain.incident in self.incidents else actor.id
        incident = self.incidents.get(key)
        if incident is None:
            incident = self.incidents[key] = Incident(time, point)
        if brain.memory.seen_at >= incident.sighting_at:
            if point != incident.point:
                incident.point = point
                incident.checked = [(checked, stamp) for checked, stamp in incident.checked
                                    if math.dist(point, checked) >= 4]
                incident.claims.clear()
            incident.sighting_at, incident.reporter = brain.memory.seen_at, actor.id
        incident.updated_at = time
        for member in (actor, *receivers):
            ally = self.brains[member.id]
            ally.combat.combat_alert_until = time + config.COMBAT_ALERT_SECONDS
            ally.incident = key
            if incident.sighting_at >= ally.memory.reported_at:
                if ally.memory.reported != incident.point:
                    ally.memory.reported = incident.point
                    ally.search.next_sweep_at = 0.0
                    ally.sense_at = min(ally.sense_at, time)
                ally.memory.report_source = incident.reporter
                ally.memory.reported_at = incident.sighting_at

    def receivers(self, actor, actors, *, local_only=False):
        return [other for other in actors if other.id != actor.id and other.hp > 0 and other.team == actor.team
            and ((other.position.floor == actor.position.floor and math.dist(actor.xy, other.xy) <= 14
                  and self.world.geometry.raycast(actor.xy, other.xy) is None)
                 or (not local_only and math.dist(actor.xy, other.xy) <= 75
                     and self.can_radio and self.can_radio(actor, other)))]

    def report_inspection(self, actor, brain, actors, cue, time):
        report = brain.search.reported_check
        if report is None or report.kind != cue.kind or report.point != cue.point or report.at < cue.at:
            report = SearchCue(cue.kind, cue.point, time)
        for other in self.receivers(actor, actors):
            ally = self.brains[other.id]
            same_squad = actor.source_unit == other.source_unit if actor.source_unit else brain.incident and brain.incident == ally.incident
            if (same_squad and ally.search.priority_cue is not None and ally.search.priority_cue.point == cue.point
                    and (ally.search.reported_check is None or report.at >= ally.search.reported_check.at)):
                ally.search.reported_check = report

    def maintain_incidents(self, time):
        if time < self.assign_at:
            return
        self.assign_at = time + 0.35
        expired = [key for key, incident in self.incidents.items()
                   if time - incident.updated_at >= config.COMBAT_ALERT_SECONDS]
        for key in expired:
            del self.incidents[key]
        for incident in self.incidents.values():
            incident.checked = [(point, stamp) for point, stamp in incident.checked
                                if time - stamp < config.RESEARCH_INTERVAL_SECONDS * 3]

    def update_regroups(self, actors, time):
        nav, revision = self.world.navigation, self.world.revision
        for key, incident in self.incidents.items():
            sweep = incident.indoor
            if sweep is None or sweep.regroup_done or not sweep.complete:
                continue
            members = [a for a in actors if a.hp > 0 and not a.transport_boarding and self.brains[a.id].incident == key]
            if not members or any(self.brains[a.id].visible or self.brains[a.id].search.investigating_priority
                                  or self.brains[a.id].search.task.scanning for a in members):
                sweep.regroup_advanced_at = time
                sweep.regroup_until = None
                sweep.regroup_progress.clear()
                continue
            if sweep.regroup_revision != revision:
                sweep.regroup_revision = revision
                sweep.regroup_rejected.clear()
            if sweep.regroup_advanced_at is None:
                sweep.regroup_advanced_at = time
            live = {actor.id for actor in members}
            sweep.regroup_slots = {key: point for key, point in sweep.regroup_slots.items() if key in live}
            sweep.regroup_progress = {key: progress for key, progress in sweep.regroup_progress.items()
                                      if key in sweep.regroup_slots}
            for actor in members:
                point = sweep.regroup_slots.get(actor.id)
                if point is None:
                    continue
                motion = self.brains[actor.id].motion
                direct = math.dist(actor.xy, point)
                path = motion.path if motion.goal == point and motion.path_revision == revision else []
                remaining = sum(math.dist(a, b) for a, b in zip([actor.xy, *path], [*path, point]))
                best, advanced_at, had_path = sweep.regroup_progress.get(actor.id, (None, time, False))
                if best is None or (path and not had_path):
                    sweep.regroup_progress[actor.id] = remaining, advanced_at, bool(path)
                    if direct <= config.REGROUP_ARRIVAL_M:
                        sweep.regroup_advanced_at = time
                elif ((path or not had_path) and remaining <= best - config.REGROUP_PROGRESS_M
                      or direct <= config.REGROUP_ARRIVAL_M < best):
                    sweep.regroup_progress[actor.id] = remaining, time, bool(path)
                    sweep.regroup_advanced_at = advanced_at = time
                if (not nav.walkable(point) or motion.failed(point, revision)
                        or direct > config.REGROUP_ARRIVAL_M and time - advanced_at >= config.REGROUP_REASSIGN_S):
                    sweep.regroup_rejected.add((actor.id, point))
                    del sweep.regroup_slots[actor.id]
                    sweep.regroup_progress.pop(actor.id, None)
            if len(sweep.regroup_slots) < len(members) and time >= sweep.regroup_retry_at:
                sweep.regroup_retry_at = time + 0.5
                positions = [a.xy for a in members]
                spacing = max(0.9, 2 * max(a.radius for a in members) + 0.16)
                if (sweep.regroup_anchor is None and not sweep.regroup_rejected
                        and min(cKDTree(positions).query(positions, k=2)[0][:, 1]) >= spacing):
                    slots = positions
                else:
                    center = sweep.regroup_anchor or tuple(sum(p[i] for p in positions) / len(positions) for i in range(2))
                    if not nav.walkable(center):
                        center = min(sweep.area.points, key=lambda p: math.dist(p, center))
                    slots = self.rally_slots(nav, center, floors=sweep.area.floors, spacing=spacing,
                        extent=max(math.hypot(r.w, r.h) for r in sweep.area.floors),
                        required=len(members) + min(len(sweep.regroup_rejected), len(members)))
                slots = [p for p in slots if all(math.dist(p, taken) >= spacing for taken in sweep.regroup_slots.values())]
                remaining = [a for a in members if a.id not in sweep.regroup_slots]
                if remaining and slots:
                    self._assign_regroup(nav, sweep, remaining, slots)
            arrived = [a for a in members if a.id in sweep.regroup_slots
                       and math.dist(a.xy, sweep.regroup_slots[a.id]) <= config.REGROUP_ARRIVAL_M]
            if len(arrived) == len(members):
                sweep.regroup_until = sweep.regroup_until or time + config.REGROUP_HOLD_S
                if time >= sweep.regroup_until:
                    self._release_regroup(sweep, members)
                    sweep.regroup_done = True
            else:
                sweep.regroup_until = None
                if time - sweep.regroup_advanced_at >= config.REGROUP_STALL_S:
                    if arrived:
                        sweep.regroup_anchor = min(arrived, key=lambda actor: math.dist(actor.xy, sweep.anchor)).xy
                        self._release_regroup(sweep, arrived)
                        sweep.regroup_advanced_at = time
                    else:
                        self._release_regroup(sweep, members)
                        sweep.regroup_done = True

    @staticmethod
    def _assign_regroup(nav, sweep, members, slots):
        distances = [[math.dist(actor.xy, point) for point in slots] for actor in members]
        unassigned_cost = 1 + sum(max(row) for row in distances)
        costs = [[2 * unassigned_cost if (actor.id, point) in sweep.regroup_rejected else distance
                  for point, distance in zip(slots, row)] + [unassigned_cost] * len(members)
                 for actor, row in zip(members, distances)]
        reachable = set()
        while True:
            rows, columns = linear_sum_assignment(costs)
            pairs = [(i, j) for i, j in zip(rows, columns) if j < len(slots) and costs[i][j] < unassigned_cost]
            failed = False
            for i, j in pairs:
                if (i, j) in reachable:
                    continue
                if nav.route(members[i].xy, slots[j], allow_doors=True):
                    reachable.add((i, j))
                else:
                    sweep.regroup_rejected.add((members[i].id, slots[j]))
                    costs[i][j] = 2 * unassigned_cost
                    failed = True
            if not failed:
                sweep.regroup_slots.update((members[i].id, slots[j]) for i, j in pairs)
                return

    def _release_regroup(self, sweep, actors):
        for actor in actors:
            sweep.regroup_slots.pop(actor.id, None)
            sweep.regroup_progress.pop(actor.id, None)
            brain = self.brains[actor.id]
            brain.incident, brain.combat.combat_alert_until, brain.search.next_sweep_at = "", 0.0, 0.0
            brain.clear_contact()
            brain.search.task.sweep = brain.search.task.target = brain.motion.yield_point = None
            brain.search.task.execution = None

    def unblock_search(self, actors, time):
        living = {actor.id: actor for actor in actors if actor.hp > 0}
        nav = self.world.navigation
        for actor in living.values():
            brain = self.brains[actor.id]
            blocker = living.get(brain.motion.blocked_by)
            has_search_target = brain.search.task.target is not None or brain.search.investigating_priority or actor.state == "regroup"
            if actor.state not in {"search", "regroup"} or not has_search_target or not brain.motion.path or blocker is None:
                continue
            other = self.brains[blocker.id]
            if (other.incident != brain.incident or other.visible or other.motion.yield_point is not None
                    or brain.motion.yield_point is not None or blocker.transport_boarding or other.search.task.scanning):
                continue
            if other.search.investigating_priority and not brain.search.investigating_priority:
                continue
            if brain.motion.door_wait or other.motion.door_wait or other.motion.door_stack is not None:
                continue
            if other.motion.path and not other.motion.door_wait and (other.motion.blocked_by != actor.id or actor.id > blocker.id):
                continue
            room_sweep = brain.search.task.target is not None and not brain.search.investigating_priority
            if room_sweep:
                if time < brain.motion.yield_retry_at:
                    continue
                brain.motion.yield_retry_at = time + config.INDOOR_YIELD_RETRY_S
            gap = actor.radius + blocker.radius
            # 아군이 바로 앞 구간을 막으면 걸어서 비킬 좌표를 고른다.
            for point in self.rally_slots(nav, blocker.xy)[1:]:
                if segment_distance(*point, *actor.xy, *brain.motion.path[0]) < gap + 0.2:
                    continue
                if any(segment_distance(*member.xy, *blocker.xy, *point) < member.radius + blocker.radius - 1e-6
                       for member in living.values() if member is not blocker):
                    continue
                other.motion.yield_point = point
                other.motion.blocked_by = ""
                break

    @staticmethod
    def rally_slots(nav, center, *, extent=2.7, floors=(), spacing=0.9, required=0):
        slots = [center] if not required or nav.walkable(center) else []
        for ring in range(1, math.floor(extent / spacing + 1e-8) + 1):
            radius = ring * spacing
            samples = max(8, int(math.tau * ring)) if required else 8
            for index in range(samples):
                angle = index * math.tau / samples
                point = center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle)
                if (not floors or any(rect.segment_entry(point, point) is not None for rect in floors)) and (
                        nav.walkable(point) and nav.clear(center, point)):
                    slots.append(point)
            if required and len(slots) >= required:
                break
        return slots

    def move_approaches(self, groups, time, budget, motion, search):
        active = {key: plan for key, plan in self.approaches.items() if time < plan.valid_until}
        nav, revision = self.world.navigation, self.world.revision
        for key, requests in groups.items():
            investigating = key[-1] != "advance"
            members = [request.actor for request in requests]
            target = requests[0].goal if investigating else max(
                (request.brain.memory for request in requests), key=lambda memory: memory.seen_at).last_seen
            shared_target = not investigating or all(math.dist(move.goal, target) <= 1.5 for move in requests)
            plan = self.approaches.get(key) if shared_target else None
            if plan is not None and (not plan.matches(target) or (
                    not investigating and math.dist(plan.target, target) > 0.045 and any(
                        (order := plan.orders.get(actor.id)) is not None and order.waypoint is None
                        and math.dist(actor.xy, order.destination) <= 0.12
                        and math.dist(actor.xy, target) > config.ADVANCE_DISTANCE for actor in members))):
                plan = None
            if plan is not None and plan.revision != revision:
                for actor in members:
                    order = plan.orders.get(actor.id)
                    if order is not None and not order.reachable(nav, actor.xy, plan.target,
                                                                  investigating=investigating):
                        plan.orders[actor.id] = None
                        plan.retry_at = time
                plan.revision = revision
            if plan is not None and any(a.id not in plan.orders for a in members):
                plan.orders.update((a.id, None) for a in members if a.id not in plan.orders)
                plan.retry_at = min(plan.retry_at, time + 0.1)
            if len(members) > 1 and shared_target and (plan is None or time >= plan.retry_at):
                distance = config.INVESTIGATION_SPREAD_DISTANCE if investigating else config.ADVANCE_DISTANCE - 0.05
                anchor = plan.target if plan is not None else target
                occupied = [order.destination for other_key, other_plan in (self.approaches | active).items()
                            if other_key != key and other_key in groups and other_key[1] == key[1]
                            for order in other_plan.orders.values() if order is not None]
                center = tuple(sum(a.xy[i] for a in members) / len(members) for i in range(2))
                heading = plan.heading if plan is not None else math.atan2(anchor[1] - center[1], anchor[0] - center[0])
                orders = assign_approach(nav, members, anchor, distance, investigating=investigating,
                    previous=plan.orders if plan is not None else None, occupied=occupied, heading=heading)
                retry_at = time + config.APPROACH_RETRY_SECONDS if any(order is None for order in orders.values()) else math.inf
                plan = SquadApproach(anchor, revision, orders, retry_at, heading=heading)
            if plan is not None:
                plan.valid_until = time + 14
                active[key] = plan
            for move in requests:
                actor, brain, intent = move.actor, move.brain, move.intent
                order = plan.orders.get(actor.id) if plan is not None else None
                goal = move.goal
                if order is not None:
                    goal = order.goal_from(actor.xy, nav)
                    if not investigating:
                        brain.combat.formation = plan.target, order.destination
                        brain.combat.action_goal = order.destination
                    if investigating:
                        indoor = self.world.indoor_search_area(move.goal) is not None
                        brain.search.arrival = None if indoor else (move.goal, order)
                        if (not indoor and order.waypoint is None) or (goal == order.destination and math.dist(actor.xy, goal) <= 0.12):
                            order.passed = True
                        if indoor and order.passed:
                            goal = move.goal
                request = MoveRequest(
                    goal, face=investigating and not move.priority,
                    approximate=move.approximate and goal == move.goal,
                    precise=order is not None and (not investigating or brain.search.arrival is not None or not order.passed),
                )
                budget = motion.move(actor, brain, intent, request, time, budget)
                if move.priority:
                    search.pace_indoor_movement(actor, brain, intent, time, independent_look=True)
        self.approaches = active
