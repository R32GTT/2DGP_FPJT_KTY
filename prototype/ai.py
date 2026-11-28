"""관측 정보로 행동을 선택하고 각 담당의 실행 요청을 연결한다."""

import math

from py_trees.behaviour import Behaviour
from py_trees.common import Status
from py_trees.composites import Selector

from .ai_state import (
    ActionPlan, ApproachMove, Brain, Incident, Intent, MoveRequest, SearchCue, Situation,
)
from .ai_combat import CombatController
from .ai_search import SearchController
from .ai_squad import SquadCoordinator
from .ai_motion import MotionController, face, turn_toward, turn_indoor_gaze
from .ai_crowd import CrowdMotion
from .cover import CoverFinder
from .door_traffic import DoorTraffic
from .perception import (INSPECT_THRESHOLD, NOTICE_THRESHOLD, SOUND_MEMORY_SECONDS, SIGHT_RANGE,
                         hear, sight_sample, update_vision)

from . import ai_config as config


class EnemyAI:
    def __init__(self, world, actors, patrols, *, can_radio=None, report_contact=None, emit=None, light_at=None):
        self.world = world
        self.cover_finder = CoverFinder(world)
        self.can_radio = can_radio
        self.report_contact, self.emit = report_contact, emit
        self.light_at = light_at
        self.resupply = None
        self.sight_reach = None
        self.exits = {point for key, point in world.navigation.nodes.items() if key.endswith("/outside")}
        self.incidents = {}
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.01)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}
        self.decisions = {}
        self.door_traffic = DoorTraffic()
        self.motion = MotionController(world)
        self.crowd = CrowdMotion(world)
        self.search = SearchController(world, self.brains, self.incidents)
        self.combat = CombatController(world, self.brains, self.cover_finder)
        self.squad = SquadCoordinator(world, self.brains, self.incidents, self.can_radio)

    def start_search_drill(self, actors, point):
        self.search.start_search_drill(actors, point)

    def receive(self, actors, events):
        noises = [event for event in events if event.kind == "noise"]
        for event in events:
            if event.kind == "weapon_fired" and event.source in self.brains:
                self.brains[event.source].combat.fired_at = event.time
            if event.kind == "pressure" and event.source in self.brains:
                brain = self.brains[event.source]
                strength = event.data["strength"] * min(1.5, event.data["caliber"] / 18)
                brain.combat.suppression = min(1.0, brain.combat.suppression + strength)
                brain.combat.pressure_at = event.time
                bearing = round(event.data["incoming"] / (math.pi / 8)) * (math.pi / 8)
                distance = 6 if event.data["close"] else 11
                origin = event.position
                brain.combat.incoming = (origin[0] + math.cos(bearing) * distance, origin[1] + math.sin(bearing) * distance)
                brain.combat.incoming_at = event.time
                brain.sense_at = min(brain.sense_at, event.time)
                if event.data["hit"] or event.data["close"]:
                    brain.combat.combat_alert_until = event.time + config.COMBAT_ALERT_SECONDS
        if not noises:
            return
        for actor in actors:
            if actor.hp <= 0:
                continue
            brain = self.brains[actor.id]
            for event in noises:
                result = hear(actor, brain.memory, event, self.world.navigation,
                              checked_sound_at=brain.search.checked_sound_at)
                if result is not None and event.data.get("reason") == "gunshot":
                    brain.search.sound_response = result
                    brain.search.sound_response_at = event.time
                if result in {"new", "renewed"}:
                    if result == "renewed":
                        brain.search.task.cue = None
                    brain.search.look_until = event.time + 0.65
                    brain.sense_at = min(brain.sense_at, event.time)

    def _patrol(self, actor, brain, time):
        if actor.boarding_goal is not None:
            return "boarding", actor.boarding_goal
        if (self.resupply is not None and not brain.visible and time >= brain.combat.combat_alert_until
                and time >= actor.hurt_until and brain.combat.suppression < 0.3):
            supply_point = self.resupply(actor, brain, time)
            if supply_point is not None:
                return "return", supply_point
        goal = brain.patrol[brain.waypoint] if brain.patrol else actor.xy
        if not brain.patrol or len(brain.patrol) == 1 and math.dist(actor.xy, goal) < 0.25:
            if brain.post_facing is not None:
                actor.facing = brain.post_facing
            return "idle", None
        if (math.dist(actor.xy, goal) < 0.25
                or brain.motion.arrived(goal, self.world.revision)):
            brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
            return "patrol", None
        state = "return" if actor.state in {"combat", "combat_guard", "search", "alert", "return", "retreat"} else "patrol"
        return state, goal

    def reset(self):
        patrols = {key: brain.patrol for key, brain in self.brains.items()}
        self.decisions.clear()
        self.brains.clear()
        self.incidents.clear()
        self.squad.approaches.clear()
        self.squad.assign_at = 0.0
        self.door_traffic = DoorTraffic()
        self.crowd = CrowdMotion(self.world)
        for key, patrol in patrols.items():
            self.brains[key] = Brain(patrol)

    def _sense_actor(self, actor, brain, player, time, previous_facing, player_motion):
        memory = brain.memory
        brain.look.point, brain.look.reason, brain.look.sample = None, "", None
        self.search.refresh_failure(actor, brain)
        brain.combat.suppression = max(0, brain.combat.suppression - 0.2 * (time - brain.combat.pressure_at))
        brain.combat.pressure_at = time
        situation = Situation(actor, brain, player, time)
        if not situation.distant:
            brain.far_updated = time
            self.near_count += 1
            if time >= brain.sense_at:
                was_visible = memory.visible
                last_seen_at = memory.seen_at
                reach = self.sight_reach(actor) if self.sight_reach else SIGHT_RANGE
                sample = sight_sample(actor, player, self.world.geometry, memory, time=time, reach=reach)
                # 순찰 방향에서 먼저 확인하고, 발견하지 못했을 때 소리 쪽도 살핀다.
                fresh_incoming = situation.incoming is not None and time - brain.combat.incoming_at < 1
                if sample is None and (fresh_incoming or situation.fresh_sound):
                    look = situation.incoming if fresh_incoming else memory.last_sound
                    face(actor, look)
                    actor.facing = turn_toward(previous_facing[actor.id], actor.facing, 1 / 60)
                    sample = sight_sample(actor, player, self.world.geometry, memory, time=time, reach=reach)
                light = self.light_at(actor, player.position) if sample is not None and self.light_at else 1.0
                update_vision(player, memory, time, sample,
                              alert=situation.investigating or situation.confirmed or memory.suspicion >= 0.3,
                              reacquire=situation.confirmed and (memory.last_seen is not None or fresh_incoming),
                              motion=player_motion, light=light)
                new_contact = brain.contact_at is None or time - last_seen_at > config.RADIO_CONTACT_MAX_AGE
                if memory.visible and not was_visible and new_contact:
                    brain.contact_at = time
                    if self.emit:
                        self.emit("contact_confirmed", time, actor)
                if memory.visible:
                    brain.combat.combat_alert_until = time + config.COMBAT_ALERT_SECONDS
                brain.sense_at = time + (0.10 if situation.confirmed or memory.exposed else 0.18)
            if memory.visible:
                if situation.old_state not in {"combat", "retreat"} or brain.search.task.target is not None:
                    self.search.interrupt_search(actor, brain)
            elif situation.confirmed or (situation.investigating and memory.gunshot_origin is not None):
                self.search.update_priority_cue(actor, brain, situation.tracking and brain.combat.peek_home is None,
                                          situation.investigating, situation.incoming if situation.confirmed else None)
            else:
                brain.search.priority_cue = None
        elif memory.exposed or memory.recognition or memory.suspicion:
            update_vision(player, memory, time, None)
        situation.priority_search = brain.search.investigating_priority
        return situation

    def _act_yield(self, actor, brain, actors, intent, situation, time):
        goal = brain.motion.yield_point
        if goal is None:
            return None
        if (brain.visible or actor.transport_boarding or brain.motion.blocked_by
                or math.dist(actor.xy, goal) <= 0.2 or not self.world.navigation.clear(actor.xy, goal)):
            brain.motion.yield_point = None
            return None
        return ActionPlan(goal, mode="yield")

    def _act_regroup(self, actor, brain, actors, intent, situation, time):
        incident = self.incidents.get(brain.incident)
        sweep = incident.indoor if incident is not None else None
        if sweep is None or not sweep.regroup_slots or sweep.regroup_done:
            return None
        if brain.visible:
            incident.indoor = None
        elif actor.id in sweep.regroup_slots and not actor.transport_boarding and not situation.priority_search:
            actor.state = "regroup"
            goal = sweep.regroup_slots[actor.id]
            return ActionPlan(goal if math.dist(actor.xy, goal) > config.REGROUP_ARRIVAL_M else None, mode="regroup")
        return None

    def _act_boarding(self, actor, brain, actors, intent, situation, time):
        if actor.transport_boarding and not brain.visible:
            actor.state = "boarding"
            return ActionPlan(actor.boarding_goal, mode="boarding", face=True, approximate=True)
        return None

    def _act_contact(self, actor, brain, actors, intent, situation, time):
        memory = brain.memory
        if not memory.visible:
            return None
        brain.combat.combat_alert_until = time + config.COMBAT_ALERT_SECONDS
        brain.search.next_sweep_at, brain.search.sweep_pass = 0.0, 0
        brain.search.cover = None
        actor.state = "combat"
        brain.search.task.cue = None
        brain.search.task.target = None
        brain.look.point, brain.look.reason = memory.last_seen, "교전 대상"
        if situation.old_state not in {"combat", "retreat"}:
            brain.combat.blocked_fire_at = None
            brain.combat.fire_at, brain.combat.burst = time + 0.28, 0
            if brain.combat.peek_home is None:
                brain.combat.action_until = time
        if not self.combat.peek(actor, brain, memory.last_seen, time):
            self.combat.choose(actor, brain, actors, memory.last_seen, time)
        goal = brain.combat.action_goal
        if brain.combat.action in {"cover", "retreat", "pin", "reload", "peek_return"}:
            actor.state = "retreat"
        if (time >= brain.combat.fire_at and time >= actor.equip_until and actor.weapon and not actor.weapon.reloading
              and time >= actor.weapon.next_shot
              and actor.weapon.chambered and brain.combat.action in {"fire", "advance", "peek", "flank", "reposition", "pin"}):
            intent.fire = True
        return ActionPlan(goal, approach="advance" if brain.combat.action == "advance" else None)

    @staticmethod
    def _radio_blocked(actor, brain, time):
        return (actor.hp <= 0 or not actor.present or actor.transport_boarding or actor.hurt_until > time
                or brain.combat.suppression >= 0.55 or brain.motion.door_stack is not None
                or brain.combat.peek_home is not None
                or (brain.visible and math.dist(actor.xy, brain.memory.last_seen) < 2.0))

    def cancel_radio(self, actor, time, reason):
        brain = self.brains.get(actor.id)
        if brain is None or brain.radio.started_at is None:
            return
        brain.radio.started_at = None
        brain.radio_at = time + config.RADIO_RETRY_SECONDS
        if self.emit:
            self.emit("radio_interrupted", time, actor, reason=reason)

    def _act_radio(self, actor, brain, actors, intent, situation, time):
        memory, radio = brain.memory, brain.radio
        if brain.contact_at is None or memory.last_seen is None or time - memory.seen_at > config.RADIO_CONTACT_MAX_AGE:
            self.cancel_radio(actor, time, "stale_contact")
            return None
        since_contact = time - brain.contact_at
        if since_contact >= config.LOCAL_REPORT_DELAY_SECONDS and radio.shouted_contact_at != brain.contact_at:
            self.squad.report_sighting(actor, brain, actors, time, local_only=True)
            radio.shouted_contact_at = brain.contact_at
        if self._radio_blocked(actor, brain, time):
            self.cancel_radio(actor, time, "pressure")
            return None
        if radio.started_at is None:
            point = self.squad.report_point(brain)
            if (since_contact + 1e-8 < config.RADIO_GRACE_SECONDS or time < brain.radio_at
                    or (point == radio.sent_point and memory.seen_at - radio.sent_at < config.RADIO_CONTACT_MAX_AGE)):
                return None
            radio.started_at = time
            if self.emit:
                self.emit("radio_started", time, actor)
        actor.state = "radio"
        brain.look.point, brain.look.reason = memory.last_seen, "목격 무전"
        return ActionPlan(None, mode="radio")

    def finish_radios(self, actors, time):
        """이 틱의 피격·사망을 처리한 뒤에만 보고를 전달한다."""
        for actor in actors:
            brain = self.brains[actor.id]
            radio = brain.radio
            if radio.started_at is None:
                continue
            if brain.memory.last_seen is None:
                self.cancel_radio(actor, time, "stale_contact")
                continue
            if self._radio_blocked(actor, brain, time):
                self.cancel_radio(actor, time, "pressure")
                continue
            if time - radio.started_at + 1e-8 < config.RADIO_TRANSMIT_SECONDS:
                continue
            point = self.squad.report_point(brain)
            success = self.report_contact(actor, point, time) if self.report_contact else True
            radio.started_at = None
            brain.radio_at = time + (config.RADIO_UPDATE_SECONDS if success else config.RADIO_CONTACT_MAX_AGE)
            if success:
                self.squad.report_sighting(actor, brain, actors, time)
                radio.sent_point, radio.sent_at = point, time
            if self.emit and (not success or not self.report_contact):
                self.emit("radio_completed" if success else "radio_failed", time, actor, report=point)

    def _act_peek(self, actor, brain, actors, intent, situation, time):
        memory = brain.memory
        if not (brain.combat.peek_home is not None and self.combat.peek(actor, brain, memory.last_seen, time)):
            return None
        actor.state = "retreat"
        return ActionPlan(brain.combat.action_goal, memory.last_seen or brain.combat.cover_threat)

    def _act_formation(self, actor, brain, actors, intent, situation, time):
        formation = brain.combat.formation
        if formation is None or situation.urgent or brain.combat.action in {"cover", "peek", "peek_return"}:
            return None
        anchor, goal = formation
        if math.dist(anchor, goal) <= SIGHT_RANGE or (situation.priority_search and brain.search.priority_cue.kind != "seen"):
            return None
        memory = brain.memory
        known = memory.reported if memory.reported_at > memory.seen_at else memory.last_seen
        if (known is None or time - max(memory.seen_at, memory.reported_at) > 8
                or math.dist(anchor, known) > 1.5 or not self.world.navigation.walkable(goal)
                or brain.motion.failed(goal, self.world.revision)):
            brain.combat.formation = None
            return None
        actor.state = "search" if math.dist(actor.xy, goal) > 0.12 else "combat_guard"
        brain.look.point, brain.look.reason = anchor, "분대 목표"
        return ActionPlan(goal if actor.state == "search" else None, anchor)

    def _act_search(self, actor, brain, actors, intent, situation, time):
        memory, search, combat = brain.memory, brain.search, brain.combat
        priority = situation.priority_search
        if not (priority or situation.tracking or situation.investigating or situation.reported
                or situation.incoming is not None or (situation.confirmed and combat.incoming is not None)):
            return None
        cue = search.priority_cue
        point = memory.last_seen if situation.tracking else None
        contact_at = memory.seen_at if situation.tracking else -math.inf
        if situation.reported and (point is None or (memory.report_source != actor.id and memory.reported_at > contact_at)):
            point, contact_at = memory.reported, memory.reported_at
        if cue is not None and (priority or (search.priority_checked and cue.kind != "seen"
                and cue.at >= contact_at and time - cue.at < SOUND_MEMORY_SECONDS)):
            point, contact_at = cue.point, cue.at
        elif situation.investigating and (point is None or memory.sound_at > contact_at + 0.5):
            point = memory.last_sound
        point = point if point is not None else situation.incoming or combat.incoming
        sound_only = situation.investigating and point == memory.last_sound
        actor.state = "search" if priority or situation.confirmed else "alert"
        cover_context = combat.peek_home is not None or (combat.action == "cover" and (priority or situation.old_state == "retreat"))
        if cover_context and self.combat.continue_cover(actor, brain, actors, point, time):
            actor.state = "retreat"
            return ActionPlan(combat.action_goal, point)
        if combat.suppression >= 0.55 or (priority and situation.urgent):
            search.task.cue = None
            self.combat.choose(actor, brain, actors, point, time)
            actor.state = "retreat"
            return ActionPlan(combat.action_goal, point)
        holding = not priority and situation.confirmed and not situation.investigating and (
            time < search.next_sweep_at)
        if holding:
            goal = search.cover
            if goal is None or math.dist(actor.xy, goal) <= 0.35 or brain.motion.failed(goal, self.world.revision):
                goal = search.cover = None
            actor.state = "combat_guard"
            self.search.watch_visible_space(actor, brain, point, time)
            return ActionPlan(goal, point)
        if situation.confirmed:
            intent.move_multiplier = config.INDOOR_MOVE_MULTIPLIER
        if search.next_sweep_at and (situation.investigating or time >= search.next_sweep_at):
            search.task.cue, search.next_sweep_at = None, 0.0
        sweep = self.search.indoor_sound_sweep(actor, brain, cue, time) if priority else None
        area, entering = None, False
        if sweep is not None:
            entering = search.task.sound_cue != cue
            if self.search.enter_indoor_sound_sweep(actor, brain, cue, sweep):
                area = sweep.area
        elif not priority and situation.confirmed and not sound_only and math.isfinite(contact_at):
            area = self.world.indoor_search_area(point)
        key = (cue.kind, cue.point, cue.at) if priority else (actor.state, point)
        goal, finished = self.search.investigate(actor, brain, key, time, area=area, entering=entering,
            new_contact=cue is not None and cue.at == contact_at and search.priority_checked,
            sound_only=sound_only, confirmed=priority or situation.confirmed, direct=priority)
        if area is not None:
            intent.move_multiplier = config.INDOOR_MOVE_MULTIPLIER
        if finished:
            if priority or sound_only:
                checked_cue = cue if priority else SearchCue("sound", point, memory.sound_at)
                verified = area is not None or point in search.checked
                self.search.finish_investigation(actor, brain, checked_cue, time, verified)
                if verified:
                    self.squad.report_inspection(actor, brain, actors, checked_cue, time)
            if priority:
                search.task.sound_cue = None
            elif situation.confirmed:
                search.next_sweep_at = time + config.RESEARCH_INTERVAL_SECONDS
                search.sweep_pass += 1
                actor.state = "combat_guard"
                goal = search.cover = self.cover_finder.choose(actor, point)
                self.search.watch_visible_space(actor, brain, point, time)
            else:
                actor.state, goal = self._patrol(actor, brain, time)
        elif not priority and (situation.urgent or (combat.action in {"cover", "pin", "retreat"} and time < combat.action_until)):
            self.combat.choose(actor, brain, actors, point, time)
            if combat.action in {"cover", "retreat", "pin"}:
                actor.state, goal = "retreat", combat.action_goal
        room = actor.state == "search" and goal is not None and search.task.sweep is not None and goal == search.task.target
        priority = actor.state == "search" and goal is not None and situation.priority_search and not room
        approach = None
        if goal is not None and actor.state in {"search", "alert"} and not room:
            if priority and cue.point == goal and cue.kind in {"seen", "reported"}:
                approach = "seen"
            elif (priority and cue.point == goal and cue.kind == "sound") or (not situation.priority_search and goal == memory.last_sound):
                approach = ("sound", memory.sound_at)
        return ActionPlan(goal, point, sweep, room_sweep=room, priority=priority, approach=approach,
            approximate=actor.state in {"search", "alert"} and not room and not priority and goal != memory.last_sound and goal not in self.exits,
            face=not (room or priority) and actor.state not in {"retreat", "combat_guard"} and combat.action != "peek")

    def _act_sound(self, actor, brain, actors, intent, situation, time):
        memory = brain.memory
        if not situation.looking_for_sound:
            return None
        actor.state = "idle"
        brain.look.point, brain.look.reason = memory.last_sound, "소리 방향 확인"
        return ActionPlan(None)

    def _act_notice(self, actor, brain, actors, intent, situation, time):
        memory = brain.memory
        if (situation.confirmed or situation.incoming is not None or memory.glimpse is None
                or memory.recognition < NOTICE_THRESHOLD):
            return None
        actor.state = "alert"
        brain.look.point, brain.look.reason = memory.glimpse, "수상한 움직임 확인"
        inspecting = memory.recognition >= INSPECT_THRESHOLD and math.dist(actor.xy, memory.glimpse) > 1.5
        return ActionPlan(memory.glimpse if inspecting else None, approximate=inspecting)

    def _act_patrol(self, actor, brain, actors, intent, situation, time):
        brain.clear_contact()
        actor.state, goal = self._patrol(actor, brain, time)
        return ActionPlan(goal, face=True, approximate=goal not in self.exits)

    def _execute_plan(self, actor, brain, intent, plan, situation, time, budget, previous_facing, approaches, approach_looks):
        goal, weapon, combat = plan.goal, actor.weapon, brain.combat
        if actor.state not in {"search", "alert"} and brain.search.task.execution is not None:
            brain.search.task.execution.stop(Status.INVALID)
        if plan.mode == "radio":
            return self.motion.move(actor, brain, intent, MoveRequest(None), time, budget)
        moving_to_cover = actor.state == "retreat" and combat.action == "cover" and goal is not None and math.dist(actor.xy, goal) > 0.3
        intent.reload = bool(weapon and not weapon.chambered and not weapon.reloading
                             and not moving_to_cover and (weapon.reserve > 0 or weapon.stage >= 2
                                                          or actor.inventory.get(f"ammo:{weapon.spec.ammo_type}", 0) > 0))
        if brain.search.task.scanning and actor.state in {"search", "alert"}:
            return self.motion.move(actor, brain, intent, MoveRequest(None), time, budget)
        if actor.state == "retreat" and not brain.visible:
            watch = plan.threat
            if combat.peek_home is None:
                watch = situation.incoming or (brain.memory.last_sound if situation.fresh_sound and not situation.tracking else watch)
            if watch is not None:
                brain.look.point, brain.look.reason = watch, "엄폐 중 경계"
        if actor.state == "retreat" and combat.action == "cover" and not moving_to_cover:
            goal = None
        starting_peek = combat.peek_home is not None and combat.action == "peek" and not combat.peek_departed
        if starting_peek and not brain.visible and plan.threat is not None and abs(math.remainder(
                math.atan2(plan.threat[1] - actor.xy[1], plan.threat[0] - actor.xy[0]) - previous_facing[actor.id], math.tau)) > math.radians(15):
            combat.action_until += 1 / 60
            goal = None
        group = actor.source_unit or brain.incident
        grouped = group and plan.approach is not None
        if grouped:
            key = (actor.team, actor.position.floor, bool(actor.source_unit), group, plan.approach)
            approaches.setdefault(key, []).append(ApproachMove(actor, brain, intent, goal, plan.approximate, priority=plan.priority))
        else:
            budget = self.motion.move(actor, brain, intent, MoveRequest(goal, face=plan.face, approximate=plan.approximate), time, budget)
        if starting_peek and any(intent.direction):
            combat.peek_departed = True
        if plan.room_sweep or plan.priority or plan.mode in {"yield", "regroup"}:
            sweep = brain.search.task.sweep if plan.mode == "yield" and actor.state == "search" else None
            if not grouped:
                self.search.pace_indoor_movement(actor, brain, intent, time,
                    room_sweep=plan.room_sweep, independent_look=plan.priority or sweep is not None)
            if sweep is not None:
                self.search.look_at_indoor_samples(actor, brain, time, sweep)
            if plan.priority:
                approach_looks[actor.id] = brain.search.priority_cue, plan.sound_sweep
        else:
            brain.motion.speed = 0.0
            if not brain.search.task.staging:
                brain.search.task.scan.exposure.clear()
        intent.retreat_fire = bool(not plan.mode and actor.state == "retreat" and brain.visible and any(intent.direction)
            and combat.action in {"cover", "retreat"} and combat.suppression < 0.85 and time >= combat.fire_at
            and weapon and weapon.chambered and not weapon.reloading)
        return budget

    def _finish_looks(self, actors, intents, approach_looks, time, previous_facing):
        for actor in actors:
            if actor.id in intents and not self.brains[actor.id].memory.visible:
                brain = self.brains[actor.id]
                intent = intents[actor.id]
                approach_look = approach_looks.get(actor.id)
                if approach_look is not None and brain.motion.door_stack is None:
                    cue, sweep = approach_look
                    if sweep is not None and (any(intent.direction) or brain.motion.door_wait):
                        self.search.look_at_indoor_samples(actor, brain, time, sweep)
                    if sweep is None or not brain.search.task.scan.walk_look_started:
                        brain.look.point = cue.point
                        brain.look.reason = "소리 방향 확인" if cue.kind == "sound" else "마지막 단서 방향 확인"
                rate = (config.INDOOR_TURN_RADIANS_PER_S if actor.state == "regroup" or brain.motion.yield_point is not None
                        or (actor.state == "search" and (brain.search.task.sweep is not None or brain.search.investigating_priority))
                        else config.SEARCH_TURN_RADIANS_PER_S)
                if brain.motion.door_stack is not None:
                    brain.search.task.scan.exposure.clear()
                    rate = config.INDOOR_TURN_RADIANS_PER_S
                elif intent.face_path:
                    rate = config.PATH_TURN_RADIANS_PER_S
                elif actor.state == "search" and (brain.search.task.target is not None and brain.motion.goal == brain.search.task.target
                                                or approach_look is not None and brain.search.task.scan.walk_look_started):
                    rate = config.INDOOR_SWEEP_TURN_RADIANS_PER_S
                if brain.look.point is not None:
                    face(actor, brain.look.point)
                if brain.look.sample is not None and brain.motion.door_stack is None:
                    actor.facing = turn_indoor_gaze(actor, brain, previous_facing[actor.id], rate)
                else:
                    brain.look.turn_rate = 0.0
                    actor.facing = turn_toward(previous_facing[actor.id], actor.facing, 1 / 60, rate=rate)
                if brain.look.sample is not None:
                    self.search.confirm_view(actor, brain, time)
            elif actor.id in intents and self.brains[actor.id].look.point is not None:
                face(actor, self.brains[actor.id].look.point)

    def update(self, actors, player, time, *, player_motion=1.0):
        self.search.begin_tick()
        intents, budget = {}, 1
        approach_looks = {}
        approaches = {}
        self.squad.maintain_incidents(time)
        self.squad.unblock_search(actors, time)
        self.near_count = 0
        previous_facing = {actor.id: actor.facing for actor in actors}
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            situation = self._sense_actor(actor, brain, player, time, previous_facing, player_motion)
            if situation.distant:
                if time < brain.far_at:
                    continue
                intent.step_dt = min(0.5, time - brain.far_updated)
                brain.far_at, brain.far_updated = time + 0.5, time
                brain.memory.visible = False
                brain.clear_contact()
                actor.state, goal = self._patrol(actor, brain, time)
                budget = self.motion.move(actor, brain, intent, MoveRequest(goal, approximate=True), time, budget)
            else:
                decision = self.decisions.get(actor.id)
                if decision is None:
                    decision = self.decisions[actor.id] = ActorDecision([
                        self._act_radio, self._act_yield, self._act_regroup, self._act_boarding,
                        self._act_contact, self._act_peek, self._act_formation, self._act_notice,
                        self._act_search, self._act_sound, self._act_patrol,
                    ])
                plan = decision.choose(actor, brain, actors, intent, situation, time)
                budget = self._execute_plan(
                    actor, brain, intent, plan, situation, time, budget, previous_facing, approaches, approach_looks)
            intents[actor.id] = intent
        self.squad.move_approaches(approaches, time, budget, self.motion, self.search)
        self.squad.update_regroups(actors, time)
        self.door_traffic.update(self.world, actors, self.brains, intents, time, previous_facing=previous_facing)
        self.crowd.apply(actors, intents, time, self.brains,
                         controlled={passage.actor_id for passage in self.door_traffic.passages.values()})
        self._finish_looks(actors, intents, approach_looks, time, previous_facing)
        return intents


class DecisionTask(Behaviour):
    """진행 중인 행동을 선택하고, 우선순위 변경 때 해당 예약만 반납한다."""

    def __init__(self, decision, action):
        super().__init__(action.__name__.removeprefix("_act_"))
        self.decision, self.action = decision, action

    def update(self):
        plan = self.action(*self.decision.frame)
        if plan is None:
            return Status.FAILURE
        self.decision.plan = plan
        return Status.RUNNING

    def terminate(self, new_status):
        if new_status is Status.INVALID and self.name == "search":
            actor, brain, *_ = self.decision.frame
            execution = brain.search.task.execution
            if execution is not None:
                execution.stop(Status.INVALID)


class ActorDecision:
    def __init__(self, actions):
        self.root = Selector("enemy", memory=False, children=[DecisionTask(self, action) for action in actions])

    def choose(self, *frame):
        self.frame = frame
        self.root.tick_once()
        return self.plan
