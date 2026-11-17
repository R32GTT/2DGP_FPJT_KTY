"""분대 보고·배치·통과 대기와 수색 후 집결을 조정한다."""

from .ai_state import MoveRequest, ScanPhase
import math

from campaign.viewmodel import segment_distance
from .ai_state import Incident, set_role
from .squad_approach import SquadApproach, assign_approach

from . import ai_config as config


class SquadCoordinator:
    def __init__(self, world, brains, incidents, can_radio=None):
        self.world, self.brains, self.incidents = world, brains, incidents
        self.can_radio = can_radio
        self.approaches = {}
        self.assign_at = 0.0

    def report_sighting(self, actor, brain, actors, time):
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
        if brain.memory.seen_at >= incident.sighting_at:
            if point != incident.point:
                incident.point = point
                incident.checked = [(checked, stamp) for checked, stamp in incident.checked
                                    if math.dist(point, checked) >= 4]
                incident.claims.clear()
            incident.sighting_at, incident.heading, incident.reporter = brain.memory.seen_at, heading, actor.id
        incident.updated_at = time
        for member in (actor, *receivers):
            ally = self.brains[member.id]
            ally.combat.combat_alert_until = time + config.COMBAT_ALERT_SECONDS
            if ally.incident != key:
                ally.incident = key
                set_role(ally, "")
            if incident.sighting_at >= ally.memory.reported_at:
                if ally.memory.reported != incident.point:
                    ally.memory.reported = incident.point
                    ally.search.next_sweep_at = 0.0
                    ally.sense_at = min(ally.sense_at, time)
                ally.memory.report_source = incident.reporter
                ally.memory.reported_at = incident.sighting_at

    def assign_roles(self, actors, time):
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
        groups = {}
        for actor in actors:
            brain = self.brains[actor.id]
            if (actor.hp > 0 and brain.incident in self.incidents
                    and brain.memory.reported is not None and time < brain.combat.combat_alert_until):
                groups.setdefault(brain.incident, []).append(actor)
        for members in groups.values():
            available = []
            for actor in members:
                brain = self.brains[actor.id]
                weapon = actor.weapon
                can_fight = weapon is not None and (weapon.chambered or weapon.reserve > 0 or weapon.stage >= 2)
                if (actor.hp >= 20 and can_fight and brain.combat.suppression < 0.55
                        and actor.state != "retreat"):
                    available.append(actor)
                else:
                    set_role(brain, "hold")
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
                set_role(self.brains[current.id], role)
            for actor in available:
                set_role(self.brains[actor.id], "hold")

    def update_regroups(self, actors, time):
        nav = self.world.navigation
        for key, incident in self.incidents.items():
            sweep = incident.indoor
            if sweep is None or sweep.regroup_done or not sweep.complete:
                continue
            members = [actor for actor in actors if actor.hp > 0 and not actor.transport_boarding
                       and self.brains[actor.id].incident == key]
            if not members or any(self.brains[actor.id].visible or self.brains[actor.id].search.investigating_priority
                                  or self.brains[actor.id].search.indoor.scanning
                                  for actor in members):
                continue
            roster = {actor.id for actor in members}
            sweep.regroup_slots = {actor_id: point for actor_id, point in sweep.regroup_slots.items()
                                   if actor_id in roster and nav.walkable(point)}
            for actor in members:
                brain = self.brains[actor.id]
                if brain.motion.route_failed and brain.motion.path_revision == self.world.revision and brain.motion.goal is not None:
                    if sweep.regroup_slots.get(actor.id) == brain.motion.goal:
                        sweep.regroup_slots.pop(actor.id)
            if len(sweep.regroup_slots) < len(members) and time >= sweep.regroup_retry_at:
                sweep.regroup_retry_at = time + 0.5
                centers = sorted(sweep.area.points, key=lambda point: (math.dist(point, sweep.anchor), point))[:10]
                floors = sweep.area.floors
                extent = math.hypot(max(rect.right for rect in floors) - min(rect.x for rect in floors),
                                    max(rect.bottom for rect in floors) - min(rect.y for rect in floors))
                clearance = 2 * max(actor.radius for actor in members) + 0.1
                spacing = max(0.9, clearance + 0.06)
                center, slots = max(((point, self.rally_slots(nav, point, extent=extent, floors=floors,
                                                             spacing=spacing, required=len(members))) for point in centers),
                                    key=lambda entry: (min(len(entry[1]), len(members)),
                                                       -math.dist(entry[0], sweep.anchor)))
                slots = [slot for slot in slots if all(math.dist(slot, taken) >= clearance
                                                       for taken in sweep.regroup_slots.values())]
                # 같은 집결 좌표를 공유하면 몸체 충돌로 완료 판정에 도달하지 못한다.
                remaining = (actor for actor in members if actor.id not in sweep.regroup_slots)
                for actor in sorted(remaining, key=lambda item: (math.dist(item.xy, center), item.id)):
                    for slot in sorted(slots, key=lambda point: (math.dist(actor.xy, point), point)):
                        if nav.route(actor.xy, slot, allow_doors=True):
                            sweep.regroup_slots[actor.id] = slot
                            slots.remove(slot)
                            break
            self.unblock_regroup(sweep, members)
            arrived = all(actor.id in sweep.regroup_slots
                          and math.dist(actor.xy, sweep.regroup_slots[actor.id]) <= config.REGROUP_ARRIVAL_M for actor in members)
            if not arrived:
                sweep.regroup_until = None
            elif sweep.regroup_until is None:
                sweep.regroup_until = time + config.REGROUP_HOLD_S
            elif time >= sweep.regroup_until:
                sweep.regroup_done = True
                for actor in members:
                    brain = self.brains[actor.id]
                    brain.incident = ""
                    brain.combat.combat_alert_until = 0.0
                    brain.search.next_sweep_at = 0.0
                    brain.memory.last_seen = brain.memory.reported = None
                    brain.combat.incoming = None
                    brain.search.indoor.sweep = brain.search.indoor.target = None
                    brain.motion.yield_point = None
                    brain.search.indoor.phase = ScanPhase.IDLE
                    set_role(brain, "")

    def unblock_regroup(self, sweep, members):
        present = {actor.id: actor for actor in members}
        for actor in members:
            blocker = present.get(self.brains[actor.id].motion.blocked_by)
            if blocker is None or actor.id not in sweep.regroup_slots or blocker.id not in sweep.regroup_slots:
                continue
            pair = tuple(sorted((actor.id, blocker.id)))
            goal, occupied = sweep.regroup_slots[actor.id], sweep.regroup_slots[blocker.id]
            mutual = self.brains[blocker.id].motion.blocked_by == actor.id
            settled = (math.dist(blocker.xy, occupied) <= config.REGROUP_ARRIVAL_M
                       and math.dist(actor.xy, occupied) + 0.25 < math.dist(actor.xy, goal))
            if pair not in sweep.regroup_swaps and (mutual or settled):
                # 서 있거나 정면에서 마주친 NPC 때문에 막힌 집결 자리를 맞바꾼다.
                sweep.regroup_slots[actor.id], sweep.regroup_slots[blocker.id] = occupied, goal
                sweep.regroup_swaps.add(pair)

    def unblock_search(self, actors, time):
        living = {actor.id: actor for actor in actors if actor.hp > 0}
        nav = self.world.navigation
        for actor in living.values():
            brain = self.brains[actor.id]
            blocker = living.get(brain.motion.blocked_by)
            has_search_target = brain.search.indoor.target is not None or brain.search.investigating_priority
            if actor.state != "search" or not has_search_target or not brain.motion.path or blocker is None:
                continue
            other = self.brains[blocker.id]
            if (other.incident != brain.incident or other.visible or other.motion.yield_point is not None
                    or brain.motion.yield_point is not None or blocker.transport_boarding or other.search.indoor.scanning
                    or time < other.search.scan_until):
                continue
            if other.search.investigating_priority and not brain.search.investigating_priority:
                continue
            if brain.motion.door_wait or other.motion.door_wait or other.motion.door_stack is not None:
                continue
            if other.motion.path and not other.motion.door_wait and (other.motion.blocked_by != actor.id or actor.id > blocker.id):
                continue
            room_sweep = brain.search.indoor.target is not None and not brain.search.investigating_priority
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
        active = {}
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
            if len(members) > 1 and shared_target and (plan is None or time >= plan.retry_at
                                                       or any(actor.id not in plan.orders for actor in members)):
                distance = config.INVESTIGATION_SPREAD_DISTANCE if investigating else config.ADVANCE_DISTANCE - 0.05
                anchor = plan.target if plan is not None else target
                orders = assign_approach(nav, members, anchor, distance, investigating=investigating,
                                         previous=plan.orders if plan is not None else None)
                retry_at = time + config.APPROACH_RETRY_SECONDS if any(order is None for order in orders.values()) else math.inf
                plan = SquadApproach(anchor, revision, orders, retry_at)
            if plan is not None:
                active[key] = plan
            for move in requests:
                actor, brain, intent = move.actor, move.brain, move.intent
                order = plan.orders.get(actor.id) if plan is not None else None
                goal = move.goal
                if order is not None and not order.passed:
                    goal = order.goal_from(actor.xy)
                    if investigating and goal == order.destination and math.dist(actor.xy, goal) <= 0.12:
                        order.passed = True
                        goal = move.goal
                request = MoveRequest(
                    goal, face=investigating and not move.priority,
                    approximate=move.approximate and goal == move.goal,
                    precise=order is not None and not order.passed,
                )
                budget = motion.move(actor, brain, intent, request, time, budget)
                if move.priority:
                    search.pace_indoor_movement(actor, brain, intent, time, independent_look=True)
        self.approaches = active
