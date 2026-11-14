"""동일한 캠페인 병력의 개별 멤버를 유지한다. 이탈·재접근으로 손실을 초기화하지 않는다."""

import math

from campaign.models import Position


class CampaignRoster:
    def __init__(self, bridge, actors, world):
        self.bridge, self.actors, self.world = bridge, actors, world
        self.by_id = {actor.id: actor for actor in actors}
        self.spawns = {actor.id: actor.xy for actor in actors}
        self.local_deaths = set()
        self.materialized = True
        self.nodes = {facility["service_node"] for facility in world.facilities if facility["campaign_id"]}
        self.unit_ids = sorted({actor.source_unit for actor in actors if actor.source_unit})
        self._stamp, self._active = None, []
        self.bridge.simulation.departure_ready = self.ready

    def ready(self, operation):
        if not self.materialized or operation.home_node not in self.nodes:
            return True
        state = self.bridge.simulation.state
        point = state.nodes[operation.home_node].position
        for unit_id in operation.unit_ids:
            members = [actor for actor in self.actors if actor.source_unit == unit_id and actor.hp > 0]
            if len(members) != state.units[unit_id].soldiers:
                return False
            if any(math.dist(actor.xy, (point.x, point.y)) > 3.5 or actor.state in {"combat", "retreat"} for actor in members):
                return False
        return True

    def record_loss(self, actor):
        if actor.source_unit and actor.id not in self.local_deaths:
            self.local_deaths.add(actor.id)
            self.bridge.lose_soldier(actor.source_unit)

    def sync(self, *, restore=False):
        state = self.bridge.simulation.state
        stamp = (self.materialized, len(self.local_deaths),
                 tuple((key, state.units[key].node_id, state.units[key].soldiers,
                        state.units[key].status, state.units[key].operation_id) for key in self.unit_ids))
        if not restore and stamp == self._stamp:
            return self._active
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
                    actor.present = True
                    continue
                actor.present = self.materialized and actor.hp > 0 and unit.node_id in self.nodes
                if not actor.present:
                    continue
                if restore or actor.last_node != unit.node_id:
                    point = state.nodes[unit.node_id].position
                    actor.position = Position(*(self.spawns[actor.id] if unit.node_id == actor.home_node else (point.x, point.y)))
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
        return self._active
