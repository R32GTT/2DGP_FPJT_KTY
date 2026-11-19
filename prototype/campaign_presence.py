"""캠페인의 병력을 가까운 현장에 같은 멤버 ID로 연결한다."""

import math

from campaign.models import Position
from .ai_state import Brain


class CampaignPresence:
    def __init__(self, world, bridge, roster, ai, weapon):
        self.world, self.bridge, self.roster, self.ai, self.weapon = world, bridge, roster, ai, weapon
        self.sites = {site["service_node"]: site for site in world.facilities if site["campaign_id"]}

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
        if not self.roster.materialized:
            return
        state = self.bridge.simulation.state
        for unit in sorted(state.units.values(), key=lambda unit: unit.id):
            if unit.node_id not in self.roster.active_nodes or unit.faction != "enemy" or not unit.soldiers:
                continue
            for actor in self.roster.members(unit.id, self.weapon):
                if actor.id not in self.ai.brains:
                    self._place(actor, unit.node_id)
                    self.ai.brains[actor.id] = Brain([actor.xy])
