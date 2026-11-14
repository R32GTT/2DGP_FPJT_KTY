"""고정 틱의 플레이 조합. 총기·지각·AI·효과·군수 모듈을 이벤트로 연결한다."""

import math

from campaign.models import Position

from .actors import Actor
from .ai import EnemyAI
from .ai_scenarios import configure
from .bridge import CampaignBridge
from .combat import Combat
from .events import EventBus
from .feedback import Feedback
from .movement import move_actor
from .interactions import nearby_facility, use_facility
from .roster import CampaignRoster
from .weapons import WeaponState, load_weapons
from .world import DEFAULT_SCENE, World


FIXED_DT = 1 / 60


class PlaySimulation:
    def __init__(self, scene=DEFAULT_SCENE, *, ai_scenario=None):
        self.world = World(scene)
        self.specs = load_weapons()
        self.player = Actor("player", Position(*self.world.data["player_spawn"]), speed=3.0,
                            hp=100, armor=40, team="player", state="player")
        self.guard_records = [Actor(row["id"], Position(*row["position"]), armor=15,
                                     source_unit=row.get("source_unit", ""), member=row.get("member", -1),
                                     home_node=row.get("home_node", "")) for row in self.world.data["guards"]]
        self.player.loadout = {key: WeaponState.ready(self.specs[key])
                               for key in self.world.data.get("player_weapons", ["ar_a", "ar_b"])}
        self.player.active_weapon = next(iter(self.player.loadout), "")
        for actor, row in zip(self.guard_records, self.world.data["guards"]):
            key = row.get("weapon", "ar_a")
            actor.loadout = {key: WeaponState.ready(self.specs[key])}
            actor.active_weapon = key
        self.campaign = CampaignBridge(self.world.data["campaign_facility"], world=self.world)
        self.roster = CampaignRoster(self.campaign, self.guard_records, self.world)
        self.guards = self.roster.sync()
        self.ai = EnemyAI(self.world, self.guard_records, [row["patrol"] for row in self.world.data["guards"]],
                          can_radio=self.campaign.can_radio)
        self.events, self.feedback = EventBus(), Feedback()
        self.combat = Combat(self.events)
        self.time, self.footstep_at = 0.0, 0.0
        self.message = "R 재장전 / C 취소 / 1·2 총기 교체 / V 단발·연사 / F 문 / E 상호작용"
        self.respawn_target = None
        self.dropped = []
        self.kit_available = True
        self.ai_scenario, self.ai_hint = ai_scenario, ""
        if ai_scenario:
            configure(self, ai_scenario)
        elif self.world.data.get("training_facility"):
            self.message = "실내 AI 검증 · P 시작 · F2 재시작"
            self.ai_hint = "F 문 사용 · M 전체 평면도 · F1 적군 판단 표시"

    def _signals(self, actor, signals):
        for signal in signals:
            signal = dict(signal)
            kind = signal.pop("kind")
            self.events.emit(kind, self.time, actor, weapon=actor.weapon.spec.id, **signal)
            if kind == "reload_step_started" and signal["noise_radius"] > 0:
                self.events.emit("noise", self.time, actor, radius=signal["noise_radius"], strength=0.25, reason="reload")

    def reload(self, actor=None):
        actor = actor or self.player
        if actor.hp > 0 and actor.weapon is not None:
            self._signals(actor, actor.weapon.start_reload())

    def cancel_reload(self, actor=None):
        actor = actor or self.player
        if actor.weapon is not None:
            self._signals(actor, actor.weapon.cancel_reload())

    def switch_weapon(self, slot):
        keys = list(self.player.loadout)
        if self.player.hp <= 0 or not 0 <= slot < len(keys) or keys[slot] == self.player.active_weapon:
            return
        self.cancel_reload()
        self.player.active_weapon = keys[slot]
        self.player.equip_until = self.time + 0.25
        self.message = f"{self.player.weapon.spec.name} 장착 / 이전 총기의 완료 단계 보존"

    def _dispatch(self):
        events = self.events.drain()
        for event in events:
            if event.kind == "killed" and event.source in self.roster.by_id:
                actor = self.roster.by_id[event.source]
                self.roster.record_loss(actor)
                if actor.loadout:
                    self.dropped.append((actor.xy, actor.loadout))
                    actor.loadout, actor.active_weapon = {}, ""
        self.guards = self.roster.sync()
        self.ai.receive(self.guards, events)
        self.feedback.receive(events)
        if any(event.kind == "killed" and event.source == "player" for event in events):
            self._drop_equipment()
            self.message = "전투 불능 — Enter: 6시간 진행 후 무장 없이 복귀"

    def _drop_equipment(self):
        self.cancel_reload()
        if self.player.loadout:
            self.dropped.append((self.player.xy, self.player.loadout))
        self.player.loadout, self.player.active_weapon = {}, ""

    @property
    def danger(self):
        weights = {"combat": 1.0, "search": 0.65, "alert": 0.4, "retreat": 0.8}
        return max((weights.get(actor.state, 0) for actor in self.guards
                    if actor.hp > 0 and math.dist(actor.xy, self.player.xy) < 24), default=0.0)

    def step(self, direction=(0, 0), aim=None, firing=False, sprint=False, *, aiming=False,
             trigger_pressed=False, walking=False):
        if self.respawn_target is not None:
            current = self.campaign.simulation.state.world_time
            self.campaign.simulation.advance_to(min(self.respawn_target, current + 60))
            self.time = float(self.campaign.simulation.state.world_time)
            if self.time >= self.respawn_target:
                self.respawn_target = None
                self.player.position = Position(*self.world.safehouse)
                self.player.hp, self.player.armor = 100, 0
                self.roster.materialized = True
                self.guards = self.roster.sync(restore=True)
                self.ai.reset()
                for actor in self.guard_records:
                    if actor.hp > 0 and actor.weapon is not None:
                        actor.weapon.tick(21600)
                self.message = "무장 없이 안전가옥 복귀 — E: 훈련용 키트, 또는 바닥의 무기 회수"
            self.feedback.update(FIXED_DT, 0)
            return
        self.time += FIXED_DT
        if trigger_pressed and self.player.weapon is not None and self.player.weapon.reloading:
            self.cancel_reload()
        if aim is not None and self.player.hp > 0:
            self.player.facing = math.atan2(aim[1] - self.player.position.y, aim[0] - self.player.position.x)
        for actor in [self.player, *self.guards]:
            if actor.hp > 0 and actor.weapon is not None:
                self._signals(actor, actor.weapon.tick(FIXED_DT))
        previous = self.player.xy
        if self.player.hp > 0:
            speed = 1.5 if walking else 5 if sprint else 3
            if self.player.weapon is not None and self.player.weapon.reloading:
                speed *= self.player.weapon.spec.reload_move_multiplier
            move_actor(self.player, direction, self.world, FIXED_DT, speed=speed)
            if math.dist(previous, self.player.xy) > 0.001 and not walking and self.time >= self.footstep_at:
                self.events.emit("noise", self.time, self.player, radius=12 if sprint else 5,
                                 strength=0.45 if sprint else 0.25, reason="footstep")
                self.footstep_at = self.time + (0.35 if sprint else 0.5)
        self._dispatch()
        intents = self.ai.update(self.guards, self.player, self.time)
        for actor in self.guards:
            intent = intents.get(actor.id)
            if intent is not None:
                brain = self.ai.brains[actor.id]
                if intent.open_door and brain.path:
                    door = self.world.open_for_ai(actor, brain.path[0])
                    if door:
                        self.events.emit("door_opened", self.time, actor, door=door.id)
                        self.events.emit("noise", self.time, actor, radius=4, strength=0.25, reason="door")
                speed = actor.speed * (actor.weapon.spec.reload_move_multiplier
                                       if actor.weapon and actor.weapon.reloading else 1)
                move_actor(actor, intent.direction, self.world, intent.step_dt, speed=speed)
                if intent.reload:
                    self.reload(actor)
        self.combat.prepare([self.player, *self.guards], FIXED_DT)
        weapon = self.player.weapon
        if firing and weapon is not None and (weapon.automatic or trigger_pressed):
            self.combat.fire(self.player, self.world, self.time, aiming=aiming,
                             moving=math.dist(previous, self.player.xy) > 0.001, sprint=sprint and not walking)
        for actor in self.guards:
            if (actor.hp > 0 and self.player.hp > 0 and intents.get(actor.id) is not None
                    and intents[actor.id].fire):
                self.combat.fire(actor, self.world, self.time, moving=any(intents[actor.id].direction))
        self._dispatch()
        self.feedback.update(FIXED_DT, self.danger if self.player.hp > 0 else 0)
        self.campaign.advance_to(self.time)
        self.guards = self.roster.sync()

    def use_door(self):
        if self.player.hp <= 0:
            return
        collider = self.world.toggle_door(self.player.xy)
        if collider is None:
            self.message = "문 가까이에서 F"
            return
        if collider.active and any(collider.rect.expanded(actor.radius).segment_entry(actor.xy, actor.xy) is not None
                                   for actor in [self.player, *self.guards] if actor.hp > 0):
            collider.active = False
            self.message = "몸체가 문을 막고 있음"
        else:
            self.message = "문 닫힘" if collider.active else "문 열림"
            self.events.emit("door_changed", self.time, self.player, door=collider.id, closed=collider.active)
            self.events.emit("noise", self.time, self.player, radius=4, strength=0.25, reason="door")

    def take_supplies(self):
        if self.player.hp <= 0:
            return
        for index, (point, loadout) in enumerate(self.dropped):
            if math.dist(self.player.xy, point) < 1.5 and not self.player.loadout:
                self.player.loadout = loadout
                self.player.active_weapon = next(iter(loadout))
                self.dropped.pop(index)
                self.message = "떨어진 무기 회수 — 탄약·완료된 재장전 단계 보존"
                return
        facility = nearby_facility(self.world, self.player.xy)
        if facility is not None:
            self.message = use_facility(self, facility)
            return
        if self.world.facilities:
            self.message = "시설 단말·보급 상자 가까이에서 E"
            return
        if not self.player.loadout and self.kit_available and math.dist(self.player.xy, self.world.safehouse) < 2:
            self.player.loadout = {"ar_a": WeaponState.ready(self.specs["ar_a"])}
            self.player.active_weapon, self.kit_available = "ar_a", False
            self.message = "장면에 배치된 훈련용 예비 키트 확보"
        elif math.dist(self.player.xy, self.world.crate) <= 1.5:
            self.message = f"실제 창고에서 보급품 {self.campaign.take_supplies()}개 확보"
        else:
            self.message = "보급 상자·무기 가까이에서 E"

    def request_respawn(self):
        if self.player.hp <= 0 and self.respawn_target is None:
            self._drop_equipment()
            self.combat.traces.clear()
            self.roster.materialized = False
            self.guards = self.roster.sync()
            self.respawn_target = self.campaign.simulation.state.world_time + 21600
            self.message = "안전가옥으로 복귀 중 — 실제 군수 세계 시간 6시간 진행"
