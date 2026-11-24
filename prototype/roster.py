"""동일한 캠페인 병력의 개별 멤버를 유지한다. 이탈·재접근으로 손실을 초기화하지 않는다."""

import math

from campaign.models import Position

from .actors import Actor
from .weapons import WeaponState


class CampaignRoster:
    def __init__(self, bridge, actors, world):
        self.bridge, self.actors, self.world = bridge, actors, world
        self.by_id = {actor.id: actor for actor in actors}
        self.spawns = {actor.id: actor.xy for actor in actors}
        self.local_deaths = set()
        self.seated_deaths = set()
        self.materialized = True
        self.nodes = {facility["service_node"] for facility in world.facilities if facility["campaign_id"]}
        self.node_territories = {facility["service_node"]: world.territory_at(facility["position"])
                                 for facility in world.facilities if facility["campaign_id"]}
        self.active_nodes = self.nodes
        self.unit_ids = sorted({actor.source_unit for actor in actors if actor.source_unit})
        self.in_transit = set()
        self._stamp, self._active = None, []
        self.bridge.simulation.departure_ready = self.ready

    def track_player(self, point):
        if not self.world.territories:
            return
        nearby = {key for key, rect in self.world.territories.items()
                  if rect.expanded(48).segment_entry(point, point) is not None}
        self.active_nodes = {key for key, territory in self.node_territories.items()
                             if territory is None or territory in nearby}
        if self.world.chunks:
            nodes = self.bridge.simulation.state.nodes
            self.active_nodes = {key for key in self.active_nodes
                                 if self.world.detail_loaded((nodes[key].position.x, nodes[key].position.y))}

    def ready(self, operation):
        if not self.materialized or operation.home_node not in self.active_nodes:
            return True
        state = self.bridge.simulation.state
        point = state.nodes[operation.home_node].position
        for unit_id in operation.unit_ids:
            if unit_id not in self.unit_ids:
                continue
            members = [actor for actor in self.actors if actor.source_unit == unit_id and actor.hp > 0]
            if len(members) != state.units[unit_id].soldiers:
                return False
            if any(math.dist(actor.xy, (point.x, point.y)) > 3.5
                   or actor.state in {"alert", "combat", "combat_guard", "search", "retreat", "radio"} for actor in members):
                return False
        return True

    def members(self, unit_id, weapon):
        state = self.bridge.simulation.state
        unit = state.units[unit_id]
        if unit_id not in self.unit_ids:
            node = unit.node_id or state.operations[unit.operation_id].home_node
            home = state.nodes[node].position
            for index in range(unit.soldiers):
                actor = Actor(f"{unit_id}:{index}", Position(home.x, home.y, home.floor), armor=15,
                              source_unit=unit_id, member=index, home_node=node, present=False)
                actor.loadout = {weapon.id: WeaponState.ready(weapon)}
                actor.active_weapon = weapon.id
                self.actors.append(actor)
                self.by_id[actor.id] = actor
                self.spawns[actor.id] = actor.xy
            self.unit_ids.append(unit_id)
        return [actor for actor in self.actors if actor.source_unit == unit_id and actor.hp > 0][:unit.soldiers]

    def transport_members(self, operation, weapon):
        return [actor for unit_id in operation.unit_ids for actor in self.members(unit_id, weapon)]

    def record_loss(self, actor, *, seated=False, driver_vehicle_id=None, progress_m=None):
        if actor.source_unit and actor.id not in self.local_deaths:
            self.bridge.lose_soldier(actor.source_unit, driver_vehicle_id=driver_vehicle_id, progress_m=progress_m)
            self.local_deaths.add(actor.id)
            if seated:
                self.seated_deaths.add(actor.id)

    def sync(self, *, restore=False):
        state = self.bridge.simulation.state
        stamp = (self.materialized, self.active_nodes, frozenset(self.in_transit), len(self.local_deaths),
                 self.world.revision,
                 tuple((key, state.units[key].node_id, state.units[key].soldiers,
                        state.units[key].status, state.units[key].operation_id) for key in self.unit_ids))
        if not restore and stamp == self._stamp:
            return self._active
        repositioned = []
        for unit_id in self.unit_ids:
            members = [actor for actor in self.actors if actor.source_unit == unit_id]
            unit = state.units[unit_id]
            alive = [actor for actor in members if actor.hp > 0]
            # 원거리 군수 사건에서 난 손실도 다시 생성하지 않는다.
            for actor in alive[unit.soldiers:]:
                actor.hp, actor.present = 0, False
            for actor in members:
                actor.boarding_goal = None
                if actor.id in self.local_deaths:
                    actor.present = (self.materialized and actor.id not in self.seated_deaths
                                     and self.world.detail_loaded(actor.xy))
                    continue
                if actor.id in self.in_transit:
                    actor.present = self.materialized and actor.hp > 0
                    continue
                actor.present = self.materialized and actor.hp > 0 and unit.node_id in self.active_nodes
                if not actor.present:
                    continue
                if restore or actor.last_node != unit.node_id:
                    point = state.nodes[unit.node_id].position
                    actor.position = Position(*(self.spawns[actor.id] if unit.node_id == actor.home_node else (point.x, point.y)))
                    repositioned.append(actor)
                actor.last_node = unit.node_id
                if unit.status == "reserved":
                    point = state.nodes[unit.node_id].position
                    side = -1 if unit_id.endswith("1") else 1
                    actor.boarding_goal = (point.x + (actor.member - 0.5) * 0.65, point.y + side * 1.8)
        for actor in self.actors:
            if not actor.source_unit:
                actor.present = self.materialized or actor.hp <= 0
        self._stamp = stamp
        self._active = [actor for actor in self.actors if actor.present]
        nav = self.world.navigation
        for actor in repositioned:
            others = [other for other in self._active if other is not actor and other.hp > 0
                      and other.position.floor == actor.position.floor]
            x, y = actor.xy
            if all(math.dist(actor.xy, other.xy) >= actor.radius + other.radius for other in others):
                continue
            for step in range(1, 7):
                distance = step * (2 * actor.radius + 0.1)
                for index in range(8):
                    angle = index * math.pi / 4
                    candidate = x + math.cos(angle) * distance, y + math.sin(angle) * distance
                    if (nav.walkable(candidate) and nav.clear((x, y), candidate, clearance=actor.radius)
                            and all(math.dist(candidate, other.xy) >= actor.radius + other.radius for other in others)):
                        actor.position.x, actor.position.y = candidate
                        break
                else:
                    continue
                break
        return self._active
