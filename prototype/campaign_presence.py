"""캠페인의 병력을 가까운 현장에 같은 멤버 ID로 연결한다."""

from collections import defaultdict
import math

from campaign.models import FOOT_OPERATIONS, QRF_OPERATIONS, Position
from campaign.resources import available, issue_ammunition
from campaign.routes import position
from .ai_state import Brain, Incident
from .ai_config import COMBAT_ALERT_SECONDS
from .geometry import Rect
from .perception import SIGHT_RANGE


class CampaignPresence:
    def __init__(self, world, bridge, roster, ai, weapon_roles):
        self.world, self.bridge, self.roster, self.ai = world, bridge, roster, ai
        self.weapon_roles = weapon_roles
        self.weapon = weapon_roles["patrol"]
        self.sites = {site["service_node"]: site for site in world.facilities if site["campaign_id"]}
        self.marches, self.received = {}, set()
        self._orders_at, self.completed = -1, []
        self._ammo_points = {}
        self.roles = {}
        for site in self.sites.values():
            key = site["campaign_id"]
            if site["kind"] == "hq":
                self.roles[f"{key}/unit/0:1"] = site, "commander_lost"
            if site.get("watch_post"):
                self.roles[f"{key}/unit/0:0"] = site, "watch_disabled"
        ai.resupply = self.resupply
        ai.sight_reach = self.sight_reach
        bridge.simulation.movement_ready = self.can_advance

    def _garrison_weapon(self, site, unit_id, member):
        role = self.roles.get(f"{unit_id}:{member}")
        kind = site["kind"]
        if role and role[1] == "commander_lost":
            key = "officer"
        elif role and kind == "hq":
            key = "watch"
        elif kind == "warehouse" and member == 0:
            key = "warehouse"
        elif kind in {"hq", "garrison"} and member == 2:
            key = "support"
        elif member % 3 == 1:
            key = "rifle_guard"
        else:
            key = "patrol"
        return self.weapon_roles[key]

    def sight_reach(self, actor):
        role = self.roles.get(actor.id)
        if role is not None and role[1] == "watch_disabled":
            site = role[0]
            facility = self.bridge.simulation.state.facilities[site["campaign_id"]]
            if facility.damage < 100 and not facility.watch_disabled and actor.last_node == site["service_node"]:
                if math.dist(actor.xy, site["watch_post"][0]) <= 1.5:
                    return 28.0
        return SIGHT_RANGE

    def record_role_loss(self, actor):
        role = self.roles.get(actor.id)
        if role is None:
            return
        site, component = role
        facility = self.bridge.simulation.state.facilities[site["campaign_id"]]
        if not getattr(facility, component):
            self.bridge.disable_component(facility.id, component)

    def sync_role_losses(self):
        for actor_id in self.roles:
            actor = self.roster.by_id.get(actor_id)
            if actor is not None and actor.hp <= 0:
                self.record_role_loss(actor)

    def alert_headquarters(self, region_id, point, seconds, source):
        command = self.bridge.local_command(region_id)
        if command is None:
            return
        state = self.bridge.simulation.state
        responders = []
        for actor in self.roster.actors:
            unit = state.units.get(actor.source_unit)
            brain = self.ai.brains.get(actor.id)
            if (actor.id == source or not actor.present or actor.hp <= 0 or brain is None or brain.visible
                    or unit is None or unit.node_id != command.node_id or unit.status != "available"
                    or actor.id in self.roles and self.roles[actor.id][1] == "commander_lost"):
                continue
            responders.append(actor)
        count = 1 if command.commander_lost else 3
        selected = sorted(responders, key=lambda actor: (math.dist(actor.xy, point), actor.id))[:count]
        if not selected:
            return
        key = f"headquarters/{region_id}"
        incident = self.ai.incidents.get(key)
        if incident is None or incident.point != point:
            incident = self.ai.incidents[key] = Incident(seconds, point)
        incident.updated_at = seconds
        for actor in selected:
            brain = self.ai.brains[actor.id]
            brain.incident = key
            brain.memory.reported, brain.memory.reported_at = point, seconds
            brain.memory.report_source = source
            brain.combat.combat_alert_until = seconds + COMBAT_ALERT_SECONDS

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
        previous = set(self.roster.in_transit)
        self.roster.in_transit.clear()
        if not self.roster.materialized:
            return
        state = self.bridge.simulation.state
        garrisons = defaultdict(list)
        for unit in sorted(state.units.values(), key=lambda unit: (unit.can_escort, unit.id)):
            if unit.node_id not in self.roster.active_nodes or unit.faction != "enemy" or not unit.soldiers:
                continue
            site = self.sites[unit.node_id]
            members = self.roster.members(unit.id, lambda member: self._garrison_weapon(site, unit.id, member))
            garrisons[unit.node_id].extend((actor, unit.status == "reserved") for actor in members)
        for node, members in garrisons.items():
            self._guard_posts(node, members)
        for view in self.bridge.convoys(seconds):
            operation = state.operations[view.id]
            marching = operation.kind in FOOT_OPERATIONS and operation.phase in {"outbound", "returning"}
            stranded = operation.phase == "stranded" and not view.vehicles
            if not (marching or stranded):
                continue
            if math.dist((view.x, view.y), player) > 60:
                continue
            members = self.roster.transport_members(operation, self.weapon)
            if marching:
                self.marches[operation.id] = members
            held = operation.waiting_reason.startswith("local_") or self._in_contact(members)
            for index, actor in enumerate(members):
                brain = self.ai.brains.setdefault(actor.id, Brain([actor.xy]))
                self.roster.in_transit.add(actor.id)
                if (marching and not held) or (stranded and actor.id not in previous):
                    x, y, heading = self.bridge.following_pose(view, index * 0.7)
                    point = next(self.world.navigation.nearby_goals((x, y), False), (view.x, view.y))
                    actor.position = Position(*point)
                    actor.facing = heading
                    brain.patrol, brain.waypoint, brain.post_facing = [point], 0, None
                actor.last_node = ""

    def _guard_posts(self, node, members):
        site = self.sites[node]
        posts = iter(site["guard_posts"])
        home = self.bridge.simulation.state.nodes[node].position
        for actor, reserved in members:
            post = None
            if not reserved:
                role = self.roles.get(actor.id)
                if role is not None and role[0] is site:
                    if role[1] == "commander_lost":
                        terminal = tuple(site["terminal"])
                        x, y = site["position"]
                        post = [terminal], math.atan2(y - terminal[1], x - terminal[0])
                    else:
                        point, facing = site["watch_post"]
                        post = [point], facing
                if post is None:
                    post = next(posts, None)
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
            if not site["guard_posts"] and post is None:
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
                              if operation.kind in QRF_OPERATIONS and operation.phase == "completed" and operation.report_xy is not None
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
