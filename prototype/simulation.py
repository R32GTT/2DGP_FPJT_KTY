"""로컬 고정 틱의 최소 플레이 조합. AI·이동·탄환·군수 연결을 분리한다."""

import math

from campaign.models import Position

from .actors import Actor
from .ai import PatrolAI
from .bridge import CampaignBridge
from .combat import Projectiles
from .movement import move_actor
from .world import DEFAULT_SCENE, World


FIXED_DT = 1 / 60


class PlaySimulation:
    def __init__(self, scene=DEFAULT_SCENE):
        self.world = World(scene)
        self.player = Actor("player", Position(*self.world.data["player_spawn"]), speed=3.0, hp=100, state="player")
        self.guards = [Actor(row["id"], Position(*row["position"])) for row in self.world.data["guards"]]
        self.ai = PatrolAI(self.world, self.guards, [row["patrol"] for row in self.world.data["guards"]])
        self.projectiles = Projectiles()
        self.campaign = CampaignBridge(self.world.data["campaign_facility"])
        self.time = 0.0
        self.message = "WASD 이동 / 마우스 조준·발사 / 휠 줌 / F 문 / E 보급품"

    def step(self, direction=(0, 0), aim=None, firing=False, sprint=False):
        self.time += FIXED_DT
        if aim is not None:
            self.player.facing = math.atan2(aim[1] - self.player.position.y, aim[0] - self.player.position.x)
        move_actor(self.player, direction, self.world, FIXED_DT, speed=5 if sprint else 3)
        intents = self.ai.update(self.guards, self.player, self.time)
        for actor in self.guards:
            if actor.hp > 0:
                move_actor(actor, intents.get(actor.id, (0, 0)), self.world, FIXED_DT)
        if firing:
            self.projectiles.fire(self.player, self.time)
        self.projectiles.update(FIXED_DT, self.world, self.guards)
        self.campaign.advance_to(self.time)

    def use_door(self):
        collider = self.world.toggle_door(self.player.xy)
        if collider is None:
            self.message = "문 가까이에서 F"
            return
        # 닫는 문과 몸체가 겹치면 닫기를 취소한다.
        if collider.active and any(collider.rect.expanded(actor.radius).segment_entry(actor.xy, actor.xy) is not None
                                   for actor in [self.player, *self.guards] if actor.hp > 0):
            collider.active = False
            self.message = "몸체가 문을 막고 있음"
        else:
            self.message = "문 닫힘" if collider.active else "문 열림"

    def take_supplies(self):
        if math.dist(self.player.xy, self.world.crate) <= 1.5:
            self.message = f"실제 창고에서 보급품 {self.campaign.take_supplies()}개 확보"
        else:
            self.message = "보급 상자 가까이에서 E"
