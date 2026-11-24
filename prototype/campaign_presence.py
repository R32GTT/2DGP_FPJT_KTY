"""캠페인의 병력을 가까운 현장에 같은 멤버 ID로 연결한다."""

from collections import defaultdict
import math

from campaign.models import FOOT_OPERATIONS, Position
from campaign.resources import available, issue_ammunition
from campaign.routes import position
from .ai_state import Brain, Incident
from .ai_config import COMBAT_ALERT_SECONDS
from .geometry import Rect


class CampaignPresence:
    def __init__(self, world, bridge, roster, ai, weapon):
        self.world, self.bridge, self.roster, self.ai, self.weapon = world, bridge, roster, ai, weapon
        self.sites = {site["service_node"]: site for site in world.facilities if site["campaign_id"]}
        self.marches, self.received = {}, set()
        self._orders_at, self.completed = -1, []
        self._ammo_points = {}
        ai.resupply = self.resupply
        bridge.simulation.movement_ready = self.can_advance

    def resupply(self, actor, brain, seconds):
        weapon = actor.weapon
        if weapon is None or not actor.present or weapon.reloading or seconds < brain.resupply_at:
            return None
        spec = weapon.spec
        rounds_per_issue = min(spec.capacity, spec.reserve)
        if not rounds_per_issue or spec.reserve - weapon.reserve < rounds_per_issue:
            return None
        state = self.bridge.simulation.state
        unit = state.units.get(actor.source_unit)
        if unit is None or unit.status != "available" or unit.faction != "enemy" or not unit.soldiers:
            return None
        facility = self.bridge.node_facilities.get(unit.node_id)
        site = self.sites.get(unit.node_id)
        if (facility is None or site is None or facility.damage == 100 or not facility.inventory["ammunition"]
                or state.regions[facility.region_id].owner != "enemy"
                or actor.position.floor != state.nodes[unit.node_id].position.floor):
            brain.resupply_at = seconds + 5
            return None

        revision, point = self._ammo_points.get(facility.id, (-1, None))
        if revision != self.world.revision:
            bounds = Rect(*site["rect"])
            point = next((goal for goal in self.world.navigation.nearby_goals(tuple(site["terminal"]), True)
                          if bounds.segment_entry(goal, goal) is not None), None)
            self._ammo_points[facility.id] = self.world.revision, point
        if point is None:
            brain.resupply_at = seconds + 5
            return None
        if math.dist(actor.xy, point) > 1.0 or not self.world.navigation.clear(actor.xy, point, clearance=0):
            if (not brain.motion.failed(point, self.world.revision)
                    and (brain.motion.goal == point or available(state, facility.id, "ammunition") > 0)):
                return point
            brain.resupply_at = seconds + 5
            return None

        requested = (spec.reserve - weapon.reserve) // rounds_per_issue
        granted = issue_ammunition(state, facility.id, requested)
        weapon.reserve += granted * rounds_per_issue
        brain.resupply_at = seconds + (2 if granted else 5)
        return None

    def _place(self, actor, node):
        site = self.sites[node]
        home = self.bridge.simulation.state.nodes[node].position
        dx, dy = site["position"][0] - home.x, site["position"][1] - home.y
        length = max(math.hypot(dx, dy), 1)
        center = home.x + dx / length * 1.5, home.y + dy / length * 1.5
        occupied = [other.xy for other in self.roster.actors if other is not actor and other.hp > 0]
        for radius in (0.7, 1.4, 2.1, 2.8, 3.5, 5, 7):
            for index in range(8):
                angle = index * math.pi / 4
                point = center[0] + math.cos(angle) * radius, center[1] + math.sin(angle) * radius
                if (self.world.navigation.walkable(point)
                        and self.world.navigation.clear((home.x, home.y), point, ignore_doors=True)
                        and all(math.dist(point, other) >= 0.65 for other in occupied)):
                    actor.position = Position(*point)
                    actor.facing = math.atan2(-dy, -dx)
                    self.roster.spawns[actor.id] = point
                    return
        raise ValueError(f"시설 주둔 병력 배치 공간 부족: {site['id']}")

    def prepare(self, seconds, player):
        self.marches = {}
        self.roster.in_transit.clear()
        if not self.roster.materialized:
            return
        state = self.bridge.simulation.state
        garrisons = defaultdict(list)
        for unit in sorted(state.units.values(), key=lambda unit: (unit.can_escort, unit.id)):
            if unit.node_id not in self.roster.active_nodes or unit.faction != "enemy" or not unit.soldiers:
                continue
            garrisons[unit.node_id].extend((actor, unit.status == "reserved")
                                           for actor in self.roster.members(unit.id, self.weapon))
        for node, members in garrisons.items():
            self._guard_posts(node, members)
        for view in self.bridge.convoys(seconds):
            operation = state.operations[view.id]
            if operation.kind not in FOOT_OPERATIONS or operation.phase not in {"outbound", "returning"}:
                continue
            if math.dist((view.x, view.y), player) > 60:
                continue
            members = self.roster.transport_members(operation, self.weapon)
            self.marches[operation.id] = members
            held = operation.waiting_reason.startswith("local_") or self._in_contact(members)
            for index, actor in enumerate(members):
                brain = self.ai.brains.setdefault(actor.id, Brain([actor.xy]))
                self.roster.in_transit.add(actor.id)
                if not held:
                    x, y, heading = self.bridge.following_pose(view, index * 0.7)
                    point = next(self.world.navigation.nearby_goals((x, y), False), (view.x, view.y))
                    actor.position = Position(*point)
                    actor.facing = heading
                    brain.patrol, brain.waypoint, brain.post_facing = [point], 0, None
                actor.last_node = ""

    def _guard_posts(self, node, members):
        posts = iter(self.sites[node]["guard_posts"])
        home = self.bridge.simulation.state.nodes[node].position
        for actor, reserved in members:
            post = None if reserved else next(posts, None)
            brain = self.ai.brains.get(actor.id)
            if brain is None:
                if post and all(math.dist(post[0][0], other.xy) >= .65 for other in self.roster.actors
                                if other is not actor and other.hp > 0):
                    actor.position = Position(*post[0][0])
                    actor.facing = post[1]
                    self.roster.spawns[actor.id] = actor.xy
                else:
                    self._place(actor, node)
                brain = self.ai.brains[actor.id] = Brain([actor.xy])
            if not self.sites[node]["guard_posts"]:
                continue
            fallback = self.roster.spawns[actor.id] if actor.home_node == node else (home.x, home.y)
            route, facing = post if post else ([fallback], None)
            if brain.patrol != route:
                brain.patrol, brain.waypoint = route, 0
            brain.post_facing = facing if len(route) == 1 else None

    def _in_contact(self, members):
        return any(actor.hp > 0 and (self.ai.brains.get(actor.id) and self.ai.brains[actor.id].visible
                                    or actor.state in {"combat", "combat_guard", "retreat", "search", "alert", "regroup", "radio"})
                   for actor in members)

    def can_advance(self, operation):
        members = [actor for actor in self.marches.get(operation.id, ()) if actor.hp > 0]
        if not self.roster.materialized or not members:
            if operation.waiting_reason.startswith("local_"):
                operation.waiting_reason = ""
            return True
        point = position(self.bridge.simulation.state, operation)
        ready = not self._in_contact(members) and all(math.dist(actor.xy, (point.x, point.y)) <= 3.5 for actor in members)
        if not ready:
            operation.waiting_reason = "local_engagement"
            for actor in members:
                brain = self.ai.brains[actor.id]
                brain.patrol, brain.waypoint = [(point.x, point.y)], 0
        elif operation.waiting_reason.startswith("local_"):
            operation.waiting_reason = ""
        return ready

    def receive_orders(self, seconds):
        state = self.bridge.simulation.state
        if self._orders_at != state.world_time:
            self._orders_at = state.world_time
            self.completed = [operation for operation in state.operations.values()
                              if operation.kind == "qrf" and operation.phase == "completed" and operation.report_xy is not None
                              and seconds - operation.completed_at <= COMBAT_ALERT_SECONDS]
            active = {operation.id for operation in self.completed}
            self.received = {key for key in self.received if key[0] in active}
        for operation in self.completed:
            point = tuple(operation.report_xy)
            for unit_id in operation.unit_ids:
                for actor in self.roster.actors:
                    if actor.source_unit != unit_id or not actor.present or actor.hp <= 0 or (operation.id, actor.id) in self.received:
                        continue
                    brain = self.ai.brains[actor.id]
                    self.ai.incidents.setdefault(operation.id, Incident(seconds, point))
                    brain.incident = operation.id
                    brain.memory.reported, brain.memory.reported_at = point, float(operation.created_at)
                    brain.memory.report_source = operation.id
                    brain.combat.combat_alert_until = operation.completed_at + COMBAT_ALERT_SECONDS
                    brain.patrol, brain.waypoint = [actor.xy], 0
                    self.received.add((operation.id, actor.id))
