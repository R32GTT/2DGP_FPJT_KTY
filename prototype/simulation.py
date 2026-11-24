"""고정 틱의 플레이 조합. 총기·지각·AI·효과·군수 모듈을 이벤트로 연결한다."""

from dataclasses import dataclass
import math

from campaign.models import Position

from .actors import Actor
from .ai import EnemyAI
from .ai_scenarios import configure
from .bridge import CampaignBridge, create_campaign
from .combat import Combat
from .door_layout import DoorLayout
from .events import EventBus
from .feedback import Feedback
from .movement import move_actor
from .interactions import (
    disable_watch_post, exchange_safehouse_weapon, nearby_facility, nearby_road_control,
    nearby_watch_post, use_facility, use_road_control,
)
from .geometry import Rect
from .inventory import AMMUNITION, PICKUP_REACH, FieldInventory, weapon_slots
from .lighting import Light, OPTICS_MODES, actor_optics, beam_end, incident_light, sky_at
from .roster import CampaignRoster
from .throwables import BURST_SECONDS, SmokeCloud, Throwables
from .transport import TransportCrew
from .campaign_presence import CampaignPresence
from .weapons import WeaponState, load_attachments, load_weapons
from .world import DEFAULT_SCENE, World


FIXED_DT = 1 / 60
ALARM_SECONDS = 60
ALARM_NOTICE_RANGE = 100.0


@dataclass(frozen=True)
class RegionAlarm:
    position: tuple[float, float]
    until: float

    def near(self, point):
        return math.dist(point, self.position) <= ALARM_NOTICE_RANGE


class PlaySimulation:
    def __init__(self, scene=DEFAULT_SCENE, *, ai_scenario=None, search_drill=False,
                 stack_drill=False, stack_variant=0, campaign=None, materialized=True, equipment_range=False):
        self.world = World(scene)
        self.equipment_range, self.range_night = equipment_range, False
        range_setup = self.world.data.get("equipment_range")
        if equipment_range and (not range_setup or ai_scenario or search_drill or stack_drill):
            raise ValueError("선택한 장면이나 훈련과 장비 시험장을 함께 사용할 수 없습니다")
        drill = self.world.data.get("search_drill")
        if search_drill and (drill is None or ai_scenario is not None):
            raise ValueError("이 장면에서는 실내 수색 훈련을 시작할 수 없습니다")
        self.search_drill = search_drill
        stack_setup = self.world.data.get("stack_drill")
        if stack_drill and (stack_setup is None or search_drill or ai_scenario is not None):
            raise ValueError("이 장면에서는 문 옆 집결 훈련을 시작할 수 없습니다")
        if stack_drill and not 0 <= stack_variant < len(stack_setup["layouts"]):
            raise ValueError("문 집결 배치 번호가 범위를 벗어났습니다")
        self.stack_drill = stack_drill
        self.stack_variant = stack_variant
        self.stack_title = ""
        player_spawn = drill["observer_spawn"] if search_drill else self.world.data["player_spawn"]
        parts = load_attachments()
        self.specs = load_weapons(attachments=parts)
        self.player = Actor("player", Position(*player_spawn), speed=3.0,
                            hp=100, armor=40, team="player", state="player")
        guard_rows = self.world.data["guards"]
        if stack_drill:
            left, right = stack_setup["layouts"][stack_variant]
            self.stack_title = f"{left + right}인 집결 · {left}+{right}"
            layout = DoorLayout.from_rect(self.world.geometry.objects[stack_setup["door"]].rect)
            side = stack_setup["approach_side"]
            positions = []
            for flank, count in ((-1, left), (1, right)):
                for level in range(count):
                    point = list(layout.waiting_position(side, flank, level, Actor.radius))
                    point[layout.across] += side * 0.72
                    positions.append(point)
            guard_rows = [dict(row, position=point) for row, point in
                          zip(guard_rows + stack_setup["extra_guards"], positions)]
        self.guard_records = [Actor(row["id"], Position(*row["position"]), armor=15,
                                     source_unit=row.get("source_unit", ""), member=row.get("member", -1),
                                     home_node=row.get("home_node", "")) for row in guard_rows]
        self.player.loadout = {key: WeaponState.ready(self.specs[key])
                               for key in self.world.data.get("player_weapons", ["ar_a", "ar_b"])}
        self.player.active_weapon = next(iter(self.player.loadout), "")
        for actor, row in zip(self.guard_records, guard_rows):
            key = row.get("weapon", "ar_a")
            actor.loadout = {key: WeaponState.ready(self.specs[key])}
            actor.active_weapon = key
        campaign = campaign if campaign is not None else create_campaign(self.world)
        self.campaign = CampaignBridge(campaign, self.world.data["campaign_facility"])
        self.roster = CampaignRoster(self.campaign, self.guard_records, self.world)
        self.roster.materialized = materialized
        self.roster.track_player(self.player.xy)
        self.guards = self.roster.sync()
        self.events, self.feedback = EventBus(), Feedback()
        self.alarms = {}
        authored_lights = {instance["id"]: instance["lights"] for instance in self.world.data.get("placed_prefabs", ())
                           if instance.get("lights")}
        self.fixed_lights = (
            *(Light(**row) for fixtures in authored_lights.values() for row in fixtures),
            *(Light(f"facility/{facility['id']}", tuple(facility["terminal"]), 6.5, (255, 221, 167), 0.75)
              for facility in self.world.facilities if facility["id"] not in authored_lights),
        )
        self._player_exposure = None
        self.ai = EnemyAI(self.world, self.guard_records, [row["patrol"] for row in guard_rows],
                          can_radio=self._can_radio, report_contact=self._report_contact, emit=self.events.emit,
                          light_at=self._light_at)
        weapon_roles = {role: spec for spec in self.specs.values() for role in spec.issue_roles}
        self.presence = (CampaignPresence(self.world, self.campaign, self.roster, self.ai, weapon_roles)
                         if self.world.data.get("generation") else None)
        self.transports = TransportCrew(self.world, self.campaign, self.roster, self.ai, weapon_roles["escort"], self.events)
        self.combat = Combat(self.events, self.transports)
        self.time, self.footstep_at = float(campaign.state.world_time), 0.0
        self.message = "R 재장전 / C 취소 / 1·2·3 총기 교체 / V 단발·연사 / F 문 / E 상호작용"
        if self.world.territories:
            self.message = "M 지도에서 동서 보급로 확인 · 차단기 가까이에서 E"
        self.respawn_target = None
        self.respawn_site_id = None
        self.respawn_selecting = False
        self.respawn_sites = {site["id"]: site for site in self.world.facilities if site["kind"] == "safehouse"}
        self.dropped = []
        self.safehouse_stash = {}
        self.inventory = FieldInventory(self, parts)
        self.throwables = Throwables(self.inventory.throwable_specs)
        self.world.geometry.smoke = self.throwables.smoke
        self.throwable_bursts = []
        self._throwable_effects = {"smoke": self._activate_smoke, "flash": self._activate_flash, "frag": self._activate_frag}
        for spec in self.throwables.specs.values():
            if spec.effect.kind not in self._throwable_effects:
                raise ValueError(f"등록되지 않은 투척 효과: {spec.effect.kind}")
        self.kit_available = True
        self.ai_scenario, self.ai_hint = ai_scenario, ""
        if self.presence:
            self._sync_guards()
        if equipment_range:
            self.fixed_lights += tuple(Light(**{**row, "position": tuple(row["position"]), "color": tuple(row["color"])})
                                       for row in range_setup["lights"])
            for guard in self.guards:
                optics = range_setup["guard_optics"].get(guard.id, {})
                guard.lamp = optics.get("lamp", "off")
                guard.night_vision = optics.get("night_vision", False)
                guard.ir_laser = optics.get("ir_laser", False)
            self.restock_range()
            self.message = "장비 시험장 · F2 초기화 / F3 낮·밤 / F4 보충"
        elif ai_scenario:
            configure(self, ai_scenario)
        elif search_drill:
            self.ai.start_search_drill(self.guards, tuple(drill["last_seen"]))
            self.message = "실내 수색 훈련 · P 시작 · F2 다시 보기"
            self.ai_hint = "관찰 위치에서 M 평면도 · F1 경로 및 판단 표시"
        elif stack_drill:
            source = Actor("stack_drill_sound", Position(*stack_setup["sound"]), team="player")
            self.events.emit("noise", self.time, source, radius=30, strength=0.8, reason="gunshot")
            self.message = f"{self.stack_title} · P 시작"
            self.ai_hint = "M 평면도 · F2 다시 보기 · F3 다음 배치 · 집결 시 자동 일시 정지"
        elif self.world.data.get("training_facility"):
            self.message = "실내 AI 검증 · P 시작 · F2 재시작"
            action_hint = "F 문 사용"
            if drill:
                action_hint = "F3 실내 수색 훈련"
            elif stack_setup:
                action_hint = "F3 문 옆 집결 훈련"
            self.ai_hint = f"{action_hint} · M 평면도 · F1 적군 판단 표시"

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
            if actor is self.player:
                self.inventory.feed_reserve(actor.weapon)
            self._signals(actor, actor.weapon.start_reload())

    def cancel_reload(self, actor=None):
        actor = actor or self.player
        if actor.weapon is not None:
            self._signals(actor, actor.weapon.cancel_reload())

    def switch_weapon(self, slot):
        keys = weapon_slots(self.player.loadout)
        if self.player.hp <= 0 or not 0 <= slot < len(keys) or keys[slot] in (None, self.player.active_weapon):
            return
        self.cancel_reload()
        self.player.active_weapon = keys[slot]
        self.player.equip_until = self.time + 0.25
        self.message = f"{self.player.weapon.spec.name} 장착 / 이전 총기의 완료 단계 보존"

    def _dispatch(self):
        events = self.events.drain()
        stops = []
        for event in events:
            if event.kind == "facility_damaged" and self.presence:
                key = event.data["facility"]
                region = self.campaign.simulation.state.facilities[key].region_id
                point = event.data.get("point", event.position)
                result = self.campaign.report_facility_attack(key, point, event.time)
                if result is not None:
                    self.presence.alert_headquarters(region, point, event.time, key)
            if event.kind == "killed" and event.source in self.roster.by_id:
                actor = self.roster.by_id[event.source]
                self.ai.cancel_radio(actor, self.time, "killed")
                truck = self.transports.driver_shot(actor, event.data.get("vehicle_id"))
                if truck is None:
                    self.roster.record_loss(actor, seated="vehicle_id" in event.data)
                else:
                    operation = self.campaign.simulation.state.operations[truck.convoy.id]
                    progress = (truck.convoy.progress_m
                                if operation.edge_paid and operation.edge_index == truck.convoy.edge_index else None)
                    self.roster.record_loss(actor, seated=True, driver_vehicle_id=truck.id,
                                            progress_m=progress)
                    stops.append((truck, event.data["incoming"]))
                    self.message = "운전자 전투 불능 · 수송 정차"
                if self.presence:
                    self.presence.record_role_loss(actor)
                if actor.loadout:
                    if "vehicle_id" not in event.data:
                        self.dropped.append((actor.xy, actor.loadout))
                    actor.loadout, actor.active_weapon = {}, ""
        if stops:
            self._sync_guards()
            for truck, incoming in stops:
                self.transports.react_to_stop(truck, self.time, incoming)
            events.extend(self.events.drain())
        else:
            self.guards = self.roster.sync() + [actor for actor in self.transports.dismounted if actor.hp > 0]
        self.ai.receive(self.guards, events)
        self.feedback.receive(events)
        if any(event.kind == "killed" and event.source == "player" for event in events):
            self._drop_equipment()
            self.message = "전투 불능 — Enter: 6시간 진행 후 무장 없이 복귀"

    def _sync_guards(self, *, restore=False):
        previous = self.guards
        self.world.update_chunks(self.player.xy)
        self.roster.track_player(self.player.xy)
        if self.presence:
            self.presence.prepare(self.time, self.player.xy)
        self.transports.sync(self.time)
        self.guards = self.roster.sync(restore=restore) + self.transports.dismounted
        active = {actor.id for actor in self.guards}
        for actor in previous:
            if actor.id not in active:
                self.ai.cancel_radio(actor, self.time, "unavailable")
        if self.presence:
            self.presence.sync_role_losses()
            self.presence.receive_orders(self.time)

    def _radio_region(self, actor):
        territory = self.world.territory_at(actor.xy)
        if territory in self.campaign.simulation.state.regions:
            return territory
        return self.campaign.actor_region(actor)

    def _can_radio(self, sender, receiver):
        region = self._radio_region(sender)
        if region != self._radio_region(receiver):
            return False
        command = self.campaign.local_command(region)
        if self.campaign.has_signal(region) and (command is None or not command.relay_disabled):
            return True
        return command is not None and math.dist(sender.xy, receiver.xy) <= (18 if command.commander_lost else 28)

    def _alarm_position(self, region_id, actor):
        state = self.campaign.simulation.state
        facilities = [state.facilities[key] for key in state.regions[region_id].facility_ids]
        sites = {site["campaign_id"]: site["terminal"] for site in self.world.facilities}
        speakers = [facility for facility in facilities if facility.damage < 100 and facility.id in sites]
        if not speakers:
            return actor.xy
        speaker = min(speakers, key=lambda facility: (math.dist(actor.xy, sites[facility.id]), facility.id))
        return tuple(sites[speaker.id])

    def _report_contact(self, actor, point, seconds):
        region = self._radio_region(actor)
        result = (self.campaign.report_contact(actor, region, point, seconds)
                  if self.presence else None)
        if not self.campaign.can_radio_locally(region):
            self.message = "무전 전달 실패 · 통신 연결 없음"
            return False
        if result is not None and self.presence:
            self.presence.alert_headquarters(region, point, seconds, actor.id)
        self.events.emit("radio_completed", seconds, actor, report=point)
        previous = self.alarms.get(region)
        if previous is None or previous.until <= seconds:
            position = self._alarm_position(region, actor)
            self.events.emit("alarm_started", seconds, actor, region=region, speaker=position)
            self.message = "적이 목격 보고를 접수했습니다 · 지역 경보"
        else:
            position = previous.position
        self.alarms[region] = RegionAlarm(position, seconds + ALARM_SECONDS)
        if result and result[0] == "ordered":
            self.message = "목격 보고 전달 · 다른 지역에서 지원 병력 출발 준비"
            self.events.emit("support_ordered", seconds, actor, operation_id=result[1], report=point)
        return True

    def _expire_alarms(self):
        for region in [key for key, alarm in self.alarms.items() if alarm.until <= self.time]:
            del self.alarms[region]

    def _drop_equipment(self):
        self.cancel_reload()
        if self.player.loadout:
            self.dropped.append((self.player.xy, self.player.loadout))
        self.player.loadout, self.player.active_weapon = {}, ""
        self.inventory.drop_carried()

    @property
    def danger(self):
        weights = {"combat": 1.0, "radio": 0.85, "search": 0.65, "combat_guard": 0.55, "alert": 0.4, "retreat": 0.8}
        return max((weights.get(actor.state, 0) for actor in self.guards
                    if actor.hp > 0 and math.dist(actor.xy, self.player.xy) < 24), default=0.0)

    @property
    def lighting_time(self):
        if self.equipment_range:
            return 0 if self.range_night else 12 * 3600
        return self.campaign.simulation.state.world_time

    @property
    def sky(self):
        return sky_at(self.lighting_time)

    def light_sources(self, sky, floor, *, infrared=False):
        if floor == 0:
            yield from self.fixed_lights
            if self.alarms:
                pulse = 0.5 + 0.5 * math.sin(self.time * math.tau)
                for key, alarm in self.alarms.items():
                    if alarm.until > self.time:
                        yield Light(f"alarm/{key}", alarm.position, 5.0, (255, 56, 40), 0.35 + pulse * 0.55)
        for actor in (self.player, *self.guards):
            if actor.position.floor != floor:
                continue
            lamp, _, _ = actor_optics(actor, sky)
            if lamp == "white":
                yield Light(f"actor/{actor.id}", actor.xy, 11.0, (255, 241, 214),
                            0.95, actor.facing, math.radians(28), floor=floor)
            elif lamp == "infrared" and infrared and sky.infrared:
                yield Light(f"actor/{actor.id}", actor.xy, 12.0, (240, 240, 240),
                            1.0, actor.facing, math.radians(25), infrared=True, floor=floor)

    def laser_targets(self, sky, viewport):
        if (not self.player.night_vision or not sky.infrared or self.player.hp <= 0
                or self.time < self.player.blinded_until):
            return
        reach = 30.0
        for actor in (self.player, *self.guards):
            if (actor.position.floor != self.player.position.floor or not actor_optics(actor, sky)[2]
                    or not Rect(*actor.xy, 0, 0).expanded(reach).overlaps(viewport)):
                continue
            point = beam_end(self.world, actor.xy, actor.facing, reach)
            if point is None or self.world.geometry.smoke_blocks(self.player.xy, point, self.player.position.floor):
                continue
            dx, dy = point[0] - self.player.position.x, point[1] - self.player.position.y
            distance = math.hypot(dx, dy)
            if distance <= 0.01 or beam_end(self.world, self.player.xy, math.atan2(dy, dx), distance) is None:
                yield point

    def _light_at(self, observer, position):
        sky = self.sky
        if self._player_exposure is None:
            self._player_exposure = incident_light(self.world, (position.x, position.y), sky,
                                                   self.light_sources(sky, position.floor, infrared=True))
        visible, infrared = self._player_exposure
        if actor_optics(observer, sky)[1]:
            visible = 1.9 * visible + infrared
        # 근접한 위협은 어둠에서도 조금씩 식별할 수 있다.
        close = 0.46 if math.dist(observer.xy, (position.x, position.y)) <= 2 else 0.0
        return min(1.2, max(0.10, close, visible))

    def toggle_optics(self, action):
        player = self.player
        if player.hp <= 0:
            return
        if action == "night_vision":
            player.night_vision = not player.night_vision
        elif action == "ir_laser":
            player.ir_laser = not player.ir_laser
        elif action == "lamp":
            player.lamp = OPTICS_MODES[(OPTICS_MODES.index(player.lamp) + 1) % len(OPTICS_MODES)]

    def throw_selected(self, target):
        player = self.player
        if player.hp <= 0:
            return False
        key = self.inventory.active_throwable
        if key is None:
            self.message = "휴대한 투척물이 없습니다 · Tab 인벤토리"
            return False
        self.throwables.launch(key.removeprefix("throwable:"), source=player.id, floor=player.position.floor,
                               origin=player.xy, target=target, time=self.time, team=player.team)
        self.inventory.consume_throwable(key)
        return True

    def _trace_throw(self, floor, start, end):
        wall = self.world.geometry.raycast(start, end, category="movement")
        truck = self.transports.raycast(start, end) if floor == 0 else None
        return min((hit[0] for hit in (wall, truck) if hit is not None), default=None)

    def _activate_smoke(self, activation):
        effect = activation.spec.effect
        expires_at = activation.time + effect.duration
        if expires_at <= self.time:
            return
        self.throwables.smoke.append(SmokeCloud(activation.floor, activation.position, effect.radius, expires_at))
        for brain in self.ai.brains.values():
            if brain.memory.visible:
                brain.sense_at = self.time

    def _activate_flash(self, activation):
        effect = activation.spec.effect
        expires_at = activation.time + effect.duration
        if expires_at <= self.time:
            return
        for actor in (self.player, *self.guards):
            if (actor.hp <= 0 or actor.position.floor != activation.floor
                    or math.dist(actor.xy, activation.position) > effect.radius
                    or not self.world.geometry.sight_clear(actor.xy, activation.position, floor=activation.floor)
                    or (activation.floor == 0 and self.transports.raycast(actor.xy, activation.position) is not None)):
                continue
            actor.blinded_until = max(actor.blinded_until, expires_at)
            brain = self.ai.brains.get(actor.id)
            if brain:
                brain.memory.visible, brain.memory.exposed = False, False
                brain.sense_at = self.time

    def _activate_frag(self, activation):
        self.combat.blast(self.world, activation.position, activation.floor, activation.spec.effect.radius,
                          (self.player, *self.guards), self.time, activation.source)

    def _update_throwables(self):
        if self.throwable_bursts:
            self.throwable_bursts[:] = [burst for burst in self.throwable_bursts
                                        if self.time - burst.time < BURST_SECONDS]
        if not self.throwables.flights and not self.throwables.smoke:
            return
        for activation in self.throwables.update(self.time, self._trace_throw):
            effect = activation.spec.effect
            self._throwable_effects[effect.kind](activation)
            if effect.kind != "smoke" and self.time - activation.time < BURST_SECONDS:
                self.throwable_bursts.append(activation)
            if effect.noise_radius:
                self.events.emit_at("noise", self.time, activation.source, activation.position,
                                    {"team": activation.team, "floor": activation.floor, "radius": effect.noise_radius,
                                     "strength": min(1.0, effect.noise_radius / 20), "reason": effect.kind})

    def step(self, direction=(0, 0), aim=None, firing=False, sprint=False, *, aiming=False,
             trigger_pressed=False, walking=False, aim_offset=None):
        if self.respawn_target is not None:
            current = self.campaign.simulation.state.world_time
            self.campaign.simulation.advance_to(min(self.respawn_target, current + 60))
            self.time = float(self.campaign.simulation.state.world_time)
            self._expire_alarms()
            self._update_throwables()
            if self.events.pending:
                self._dispatch()
            if self.time >= self.respawn_target:
                site = self.respawn_sites.get(self.respawn_site_id)
                self.respawn_target = None
                self.respawn_site_id = None
                self.player.position = Position(*(site["position"] if site else self.world.safehouse))
                self.player.hp, self.player.armor = 100, 0
                self.player.lamp, self.player.night_vision, self.player.ir_laser = "off", False, False
                self.roster.materialized = True
                self.ai.reset()
                self._sync_guards(restore=True)
                for actor in self.guard_records:
                    if actor.hp > 0 and actor.weapon is not None:
                        actor.weapon.tick(21600)
                self.message = f"{site['name'] if site else '안전가옥'}에 무장 없이 복귀"
            self.feedback.update(FIXED_DT, 0)
            return
        self.time += FIXED_DT
        self._expire_alarms()
        self.world.update_chunks(self.player.xy)
        if trigger_pressed and self.player.weapon is not None and self.player.weapon.reloading:
            self.cancel_reload()
        if aim_offset is not None and self.player.hp > 0:
            self.player.facing = math.atan2(aim_offset[1], aim_offset[0])
        elif aim is not None and self.player.hp > 0:
            self.player.facing = math.atan2(aim[1] - self.player.position.y, aim[0] - self.player.position.x)
        for actor in [self.player, *self.guards]:
            if actor.hp > 0 and actor.weapon is not None:
                self._signals(actor, actor.weapon.tick(FIXED_DT))
        previous = self.player.xy
        player_motion = 1.0
        if self.player.hp > 0:
            speed = 1.5 if walking else 5 if sprint else 3
            speed *= self.inventory.movement_multiplier
            if self.player.weapon is not None and self.player.weapon.reloading:
                speed *= self.player.weapon.spec.reload_move_multiplier
            move_actor(self.player, direction, self.world, FIXED_DT, speed=speed)
            moved = math.dist(previous, self.player.xy)
            if moved > 0.001:
                player_motion = 0.72 if walking else 1.25 if sprint else 1.0
            if moved > 0.001 and not walking and self.time >= self.footstep_at:
                self.events.emit("noise", self.time, self.player, radius=12 if sprint else 5,
                                 strength=0.45 if sprint else 0.25, reason="footstep")
                self.footstep_at = self.time + (0.35 if sprint else 0.5)
            if self.player.weapon is not None:
                self.player.weapon.update_accuracy(
                    FIXED_DT, aiming=aiming, movement_speed_mps=moved / FIXED_DT,
                    sprinting=sprint and not walking)
        self._update_throwables()
        self._dispatch()
        self._player_exposure = None
        intents = self.ai.update(self.guards, self.player, self.time, player_motion=player_motion)
        active_guards = [actor for actor in self.guards if actor.hp > 0]
        # 이동 방향과 무관하게 처리 순서가 특정 병사에게 계속 유리하지 않도록 번갈아 적용한다.
        if int(round(self.time / FIXED_DT)) % 2:
            active_guards.reverse()
        for actor in active_guards:
            intent = intents.get(actor.id)
            if intent is not None:
                brain = self.ai.brains[actor.id]
                if intent.open_door and brain.motion.path:
                    door = self.world.open_for_ai(actor, brain.motion.path[0])
                    if door:
                        self.events.emit("door_opened", self.time, actor, door=door.id)
                        self.events.emit("noise", self.time, actor, radius=4, strength=0.25, reason="door")
                speed = actor.speed * intent.move_multiplier * (actor.weapon.spec.reload_move_multiplier
                                       if actor.weapon and actor.weapon.reloading else 1)
                guard_previous = actor.xy
                blocker = move_actor(actor, intent.direction, self.world, intent.step_dt, speed=speed,
                                     neighbors=self.ai.crowd.neighbors.get(actor.id, ()), allow_slide=not intent.move_straight)
                if actor.weapon is not None:
                    movement_speed = math.dist(guard_previous, actor.xy) / max(FIXED_DT, intent.step_dt)
                    actor.weapon.update_accuracy(FIXED_DT, movement_speed_mps=movement_speed)
                blocker = blocker or self.ai.crowd.blockers.get(actor.id)
                self.ai.motion.record_step(brain, blocker, self.time)
                if intent.reload:
                    self.reload(actor)
        self.combat.prepare([self.player, *self.guards], FIXED_DT)
        weapon = self.player.weapon
        if firing and weapon is not None and (weapon.automatic or trigger_pressed):
            self.combat.fire(self.player, self.world, self.time)
        for actor in self.guards:
            intent = intents.get(actor.id)
            if actor.hp <= 0 or self.player.hp <= 0 or intent is None or self.time < actor.blinded_until:
                continue
            if intent.retreat_fire:
                if (self.combat.clear_fire_line(actor, self.player, self.world)
                        and self.combat.fire(actor, self.world, self.time)):
                    self.ai.brains[actor.id].combat.fire_at = self.time + 0.7
            elif intent.fire:
                if self.combat.clear_fire_line(actor, self.player, self.world):
                    if self.combat.fire(actor, self.world, self.time):
                        self.ai.combat.record_shot(actor, self.time)
                else:
                    self.ai.combat.record_blocked_shot(
                        actor, self.player.xy, self.time,
                        lambda point: self.combat.clear_fire_line(actor, self.player, self.world, origin=point),
                    )
        self._dispatch()
        self.ai.finish_radios(self.guards, self.time)
        if self.transports.try_resume(self.time) and self.player.hp > 0:
            self.message = "생존 호위가 운전석에 복귀 · 수송 재출발"
        self.feedback.update(FIXED_DT, self.danger if self.player.hp > 0 else 0)
        self.campaign.advance_to(self.time)
        self._sync_guards()

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

    def restock_range(self):
        if not self.equipment_range:
            return
        point = self.world.crate
        guns = next((loadout for location, loadout in self.dropped if location == point), None)
        if guns is None:
            guns = {}
            if self.player.loadout.keys() != self.specs.keys():
                self.dropped.append((point, guns))
        for key, spec in self.specs.items():
            if key not in self.player.loadout and key not in guns:
                guns[key] = WeaponState.ready(spec)

        inventory = self.inventory
        items = next((pile for location, pile in inventory.ground if location == point), None)
        if items is None:
            items = {}
            inventory.ground.append((point, items))
        stock = {**dict.fromkeys(inventory.parts, 1),
                 **{f"ammo:{key}": row[2] for key, row in AMMUNITION.items()},
                 **{f"throwable:{key}": 2 for key in inventory.throwable_specs}}
        for key, count in stock.items():
            items[key] = max(items.get(key, 0), count)
        self.message = "입구 보급함 재고 보충"

    def take_supplies(self):
        if self.player.hp <= 0:
            return
        if self.inventory.take_gun():
            return
        if self.equipment_range:
            self.message = "입구 보급함 가까이에서 Tab"
            return
        facility = nearby_facility(self.world, self.player.xy)
        if facility is not None:
            self.message = use_facility(self, facility)
            return
        watch = nearby_watch_post(self.world, self.player.xy)
        if watch is not None:
            self.message = disable_watch_post(self, watch)
            return
        control = nearby_road_control(self.world, self.player.xy)
        if control is not None:
            self.message = use_road_control(self.campaign, control)
            return
        if self.world.facilities:
            self.message = "시설 단말·보급 상자 가까이에서 E"
            return
        if not self.player.loadout and self.kit_available and math.dist(self.player.xy, self.world.safehouse) < 2:
            self.player.loadout = {"ar_a": WeaponState.ready(self.specs["ar_a"])}
            self.player.active_weapon, self.kit_available = "ar_a", False
            self.message = "장면에 배치된 훈련용 예비 키트 확보"
        elif math.dist(self.player.xy, self.world.crate) <= PICKUP_REACH:
            self.message = f"실제 창고에서 보급품 {self.campaign.take_supplies()}개 확보"
        else:
            self.message = "보급 상자·무기 가까이에서 E"

    def use_facility_alternate(self):
        if self.player.hp <= 0:
            return
        facility = nearby_facility(self.world, self.player.xy)
        kind = facility["kind"] if facility else None
        if kind == "safehouse":
            self.message = exchange_safehouse_weapon(self, facility)
        elif kind in {"warehouse", "hq"}:
            self.message = use_facility(self, facility, sabotage=True)
        else:
            self.message = "안전가옥·물류창고·HQ 단말 가까이에서 Shift+E"

    def request_respawn(self, slot=None):
        if self.player.hp > 0 or self.respawn_target is not None:
            return
        if slot is None and len(self.respawn_sites) > 1:
            self.respawn_selecting = True
            return
        sites = tuple(self.respawn_sites)
        if slot is not None and not 0 <= slot < len(sites):
            return
        self.respawn_selecting = False
        self.respawn_site_id = sites[slot or 0] if sites else None
        self._drop_equipment()
        self.combat.traces.clear()
        self.roster.materialized = False
        self._sync_guards()
        self.respawn_target = self.campaign.simulation.state.world_time + 21600
        self.message = "안전가옥으로 복귀 중 — 실제 군수 세계 시간 6시간 진행"
