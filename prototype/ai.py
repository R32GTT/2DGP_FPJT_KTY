"""관측 정보로 행동을 선택하고 각 담당의 실행 요청을 연결한다."""

import math

from .ai_state import (
    ActionPlan, ApproachMove, Brain, Incident, Intent, MoveRequest, ScanPhase, SearchCue, Situation, set_role,
)
from .ai_combat import CombatController
from .ai_search import SearchController
from .ai_squad import SquadCoordinator
from .ai_motion import MotionController, face, turn_toward, turn_indoor_gaze
from .cover import CoverFinder
from .door_traffic import DoorTraffic
from .perception import SOUND_MEMORY_SECONDS, hear, observe

from . import ai_config as config


class EnemyAI:
    def __init__(self, world, actors, patrols, *, can_radio=None):
        self.world = world
        self.cover_finder = CoverFinder(world)
        self.can_radio = can_radio
        self.exits = {point for key, point in world.navigation.nodes.items() if key.endswith("/outside")}
        self.incidents = {}
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.01)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}
        self.door_traffic = DoorTraffic()
        self.motion = MotionController(world)
        self.search = SearchController(world, self.brains, self.incidents)
        self.combat = CombatController(world, self.brains, self.cover_finder)
        self.squad = SquadCoordinator(world, self.brains, self.incidents, self.can_radio)

    def start_search_drill(self, actors, point):
        self.search.start_search_drill(actors, point)

    def record_burst_shot(self, actor, time):
        self.combat.record_shot(actor, time)

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
                        brain.search.cue = None
                    brain.search.look_until = event.time + 0.65
                    brain.sense_at = min(brain.sense_at, event.time)

    def _patrol(self, actor, brain):
        if actor.boarding_goal is not None:
            return "boarding", actor.boarding_goal
        goal = brain.patrol[brain.waypoint] if brain.patrol else actor.xy
        if not brain.patrol or len(brain.patrol) == 1 and math.dist(actor.xy, goal) < 0.25:
            return "idle", None
        if (math.dist(actor.xy, goal) < 0.25
                or brain.motion.arrived(goal, self.world.revision)):
            brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
            return "patrol", None
        state = "return" if actor.state in {"combat", "combat_guard", "search", "alert", "return", "retreat"} else "patrol"
        return state, goal

    def reset(self):
        patrols = {key: brain.patrol for key, brain in self.brains.items()}
        self.brains.clear()
        self.incidents.clear()
        self.squad.approaches.clear()
        self.squad.assign_at = 0.0
        self.search.coverage_budgets.clear()
        self.door_traffic = DoorTraffic()
        for key, patrol in patrols.items():
            self.brains[key] = Brain(patrol)

    def _sense_actor(self, actor, brain, player, time, previous_facing):
        memory = brain.memory
        brain.look.point, brain.look.reason = None, ""
        self.search.refresh_failure(actor, brain)
        brain.combat.suppression = max(0, brain.combat.suppression - 0.2 * (time - brain.combat.pressure_at))
        brain.combat.pressure_at = time
        searching_sound = brain.search.indoor.sound_cue is not None
        investigating = (memory.last_sound is not None and memory.sound_at > brain.search.checked_sound_at
                         and (brain.search.failed_clue is None or brain.search.failed_clue.cue != SearchCue("sound", memory.last_sound, memory.sound_at))
                         and (searching_sound or time - memory.sound_heard_at < SOUND_MEMORY_SECONDS)
                         and (memory.suspicion >= 0.3 or actor.state in {"alert", "search", "regroup"}))
        confirmed = time < brain.combat.combat_alert_until
        incoming = brain.combat.incoming if time - brain.combat.incoming_at < (12 if confirmed else 4) else None
        tracking = confirmed and memory.last_seen is not None
        reported = confirmed and memory.reported is not None
        fresh_sound = time < brain.search.look_until
        looking_for_sound = not (tracking or investigating or reported) and fresh_sound
        old_state = actor.state
        urgent = actor.hp < 20 or actor.hurt_until > time or brain.combat.suppression >= 0.55
        distant = math.dist(actor.xy, player.xy) > 60 and not (investigating or confirmed or incoming is not None)
        if not distant:
            brain.far_updated = time
            self.near_count += 1
            if time >= brain.sense_at:
                was_visible = memory.visible
                last_seen_at = memory.seen_at
                observe(actor, player, self.world.geometry, memory, time)
                # 순찰 방향에서 먼저 확인하고, 발견하지 못했을 때 소리 쪽도 살핀다.
                fresh_incoming = incoming is not None and time - brain.combat.incoming_at < 1
                if not memory.visible and (fresh_incoming or fresh_sound):
                    look = incoming if fresh_incoming else memory.last_sound
                    face(actor, look)
                    actor.facing = turn_toward(previous_facing[actor.id], actor.facing, 1 / 60)
                    observe(actor, player, self.world.geometry, memory, time)
                if memory.visible and not was_visible and time - last_seen_at > config.CONTACT_INTERRUPTION_SECONDS:
                    brain.contact_at = time
                brain.sense_at = time + (0.10 if confirmed or memory.visible else 0.18)
                memory.suspicion = max(0, memory.suspicion - 0.01)
            if memory.visible:
                if old_state not in {"combat", "retreat"} or brain.search.indoor.target is not None:
                    self.search.interrupt_search(actor, brain)
            elif confirmed or (investigating and memory.gunshot_origin is not None):
                self.search.update_priority_cue(actor, brain, tracking and brain.combat.peek_home is None,
                                          investigating, incoming if confirmed else None)
            else:
                brain.search.priority_cue = None
        return Situation(
            confirmed=confirmed,
            investigating=investigating,
            incoming=incoming,
            tracking=tracking,
            reported=reported,
            fresh_sound=fresh_sound,
            looking_for_sound=looking_for_sound,
            urgent=urgent,
            old_state=old_state,
            priority_search=brain.search.investigating_priority,
            distant=distant,
        )

    def _continue_special_action(self, actor, brain, intent, situation, time, budget, previous_facing):
        memory = brain.memory
        if brain.search.indoor.scanning:
            if memory.visible or actor.transport_boarding or situation.urgent:
                brain.search.indoor.phase = ScanPhase.IDLE
            else:
                actor.state = "search"
                actor.facing = previous_facing[actor.id]
                self.search.advance_indoor_look(actor, brain, time)
                weapon = actor.weapon
                intent.reload = (weapon is not None and not weapon.chambered and not weapon.reloading
                                 and (weapon.reserve > 0 or weapon.stage >= 2))
                return budget
        if brain.motion.yield_point is not None:
            goal = brain.motion.yield_point
            if (memory.visible or actor.transport_boarding or brain.motion.blocked_by or math.dist(actor.xy, goal) <= 0.2
                    or not self.world.navigation.clear(actor.xy, goal)):
                brain.motion.yield_point = None
            else:
                budget = self.motion.move(actor, brain, intent, MoveRequest(goal, face=False), time, budget)
                sweep = brain.search.indoor.sweep if actor.state == "search" else None
                self.search.pace_indoor_movement(actor, brain, intent, time, independent_look=sweep is not None)
                if sweep is not None:
                    self.search.look_at_indoor_samples(actor, brain, time, sweep)
                return budget
        incident = self.incidents.get(brain.incident)
        active_sweep = incident.indoor if incident is not None else None
        if active_sweep is not None and active_sweep.regroup_slots and not active_sweep.regroup_done:
            if memory.visible:
                incident.indoor = None
            elif actor.id in active_sweep.regroup_slots and not actor.transport_boarding and not situation.priority_search:
                actor.state = "regroup"
                goal = active_sweep.regroup_slots[actor.id]
                intent.move_multiplier = config.INDOOR_MOVE_MULTIPLIER
                if math.dist(actor.xy, goal) <= config.REGROUP_ARRIVAL_M:
                    goal = None
                budget = self.motion.move(actor, brain, intent, MoveRequest(goal, face=False), time, budget)
                self.search.pace_indoor_movement(actor, brain, intent, time)
                return budget
        if actor.transport_boarding and not memory.visible:
            actor.state = "boarding"
            budget = self.motion.move(actor, brain, intent, MoveRequest(actor.boarding_goal, approximate=True), time, budget)
            return budget
        return None

    def _choose_action(self, actor, brain, actors, intent, situation, time):
        memory = brain.memory
        threat = sound_sweep = None
        if memory.visible:
            brain.combat.combat_alert_until = time + config.COMBAT_ALERT_SECONDS
            brain.search.next_sweep_at, brain.search.sweep_pass = 0.0, 0
            brain.search.cover = None
            actor.state = "combat"
            brain.search.cue = None
            brain.search.target = None
            face(actor, memory.last_seen)
            if time >= brain.radio_at and time - brain.contact_at >= 0.55:
                self.squad.report_sighting(actor, brain, actors, time)
            if situation.old_state not in {"combat", "retreat"}:
                brain.combat.fire_at, brain.combat.burst = time + 0.28, 0
                if brain.combat.peek_home is None:
                    brain.combat.action_until = time
            if not self.combat.peek(actor, brain, memory.last_seen, time):
                self.combat.choose(actor, brain, actors, memory.last_seen, time)
            goal = brain.combat.action_goal
            if brain.combat.action in {"cover", "retreat", "pin", "reload", "peek_return"}:
                actor.state = "retreat"
            elif (time >= brain.combat.fire_at and time >= actor.equip_until and actor.weapon and not actor.weapon.reloading
                  and time >= actor.weapon.next_shot
                  and actor.weapon.chambered and brain.combat.action in {"fire", "advance", "peek"}):
                intent.fire = True
        elif brain.combat.peek_home is not None and self.combat.peek(actor, brain, memory.last_seen, time):
            actor.state, goal = "retreat", brain.combat.action_goal
            threat = memory.last_seen or brain.combat.cover_threat
        elif situation.priority_search:
            cue = brain.search.priority_cue
            threat = cue.point
            if brain.combat.action == "cover" and self.combat.continue_cover(actor, brain, actors, threat, time):
                actor.state, goal = "retreat", brain.combat.action_goal
            elif situation.urgent:
                brain.search.cue = None
                self.combat.choose(actor, brain, actors, cue.point, time)
                actor.state, goal = "retreat", brain.combat.action_goal
            else:
                actor.state = "search"
                entering = brain.search.indoor.sound_cue != cue
                sound_sweep = self.search.indoor_sound_sweep(actor, brain, cue, time)
                if sound_sweep is not None:
                    sound_sweep.sound_searchers[actor.id] = time
                indoor_search = sound_sweep is not None and self.search.enter_indoor_sound_sweep(actor, brain, cue, sound_sweep)
                if indoor_search:
                    goal, finished = self.search.indoor_sweep(actor, brain, cue.point, cue.at, time,
                                                        area=sound_sweep.area, entering=entering)
                else:
                    goal = self.search.search(actor, brain, (cue.kind, cue.point, cue.at), time, confirmed=True, direct=True)
                    finished = not brain.search.points
                if finished and time >= brain.search.scan_until:
                    brain.search.indoor.sound_cue = None
                    verified = indoor_search or cue.point in brain.search.checked
                    self.search.finish_investigation(actor, brain, cue, time, verified)
        elif (situation.tracking or situation.investigating or situation.reported
              or situation.incoming is not None or (situation.confirmed and brain.combat.incoming is not None)):
            actor.state = "search" if situation.confirmed else "alert"
            point = memory.last_seen if situation.tracking else None
            contact_at = memory.seen_at if situation.tracking else -math.inf
            if situation.reported and (point is None or (memory.report_source != actor.id and memory.reported_at > contact_at)):
                point, contact_at = memory.reported, memory.reported_at
            cue = brain.search.priority_cue
            if (cue is not None and brain.search.priority_checked and cue.kind != "seen"
                    and cue.at >= contact_at and time - cue.at < SOUND_MEMORY_SECONDS):
                point, contact_at = cue.point, cue.at
            elif situation.investigating and (point is None or memory.sound_at > contact_at + 0.5):
                point = memory.last_sound
            if point is None:
                point = situation.incoming or brain.combat.incoming
            threat = point
            sound_only = situation.investigating and point == memory.last_sound
            indoor = None
            search_finished = False
            holding = situation.confirmed and not situation.investigating and not situation.priority_search and (
                time < brain.search.next_sweep_at or (brain.role == "hold" and situation.reported))
            cover_context = brain.combat.peek_home is not None or (brain.combat.action == "cover" and situation.old_state == "retreat")
            continuing_cover = cover_context and self.combat.continue_cover(actor, brain, actors, point, time)
            if continuing_cover:
                actor.state, goal = "retreat", brain.combat.action_goal
            elif holding:
                actor.state = "combat_guard"
                goal = brain.search.cover
                if goal is None or math.dist(actor.xy, goal) <= 0.35 or (brain.motion.goal == goal and brain.motion.route_failed):
                    goal = brain.search.cover = None
                self.search.watch_visible_space(actor, brain, point, time)
            else:
                if situation.confirmed:
                    intent.move_multiplier = 1.3
                if brain.search.next_sweep_at and (situation.investigating or time >= brain.search.next_sweep_at):
                    brain.search.cue = None
                    brain.search.next_sweep_at = 0.0
                if situation.confirmed and not sound_only and math.isfinite(contact_at):
                    new_contact = cue is not None and cue.at == contact_at and brain.search.priority_checked
                    indoor = self.search.indoor_sweep(actor, brain, point, contact_at, time, new_contact=new_contact)
                if indoor is None:
                    goal = self.search.search(actor, brain, (actor.state, point), time,
                                        sound_only=sound_only, confirmed=situation.confirmed)
                    search_finished = not brain.search.points
                else:
                    intent.move_multiplier = config.INDOOR_MOVE_MULTIPLIER
                    goal, search_finished = indoor
                    brain.search.cover = None
            if not continuing_cover and not holding and search_finished and time >= brain.search.scan_until:
                if sound_only:
                    cue = SearchCue("sound", point, memory.sound_at)
                    self.search.finish_investigation(actor, brain, cue, time, point in brain.search.checked)
                if situation.confirmed:
                    brain.search.next_sweep_at = time + config.RESEARCH_INTERVAL_SECONDS
                    brain.search.sweep_pass += 1
                    actor.state = "combat_guard"
                    brain.search.cover = self.cover_finder.choose(actor, point)
                    goal = brain.search.cover
                    self.search.watch_visible_space(actor, brain, point, time)
                else:
                    actor.state, goal = self._patrol(actor, brain)
            elif not continuing_cover and (brain.combat.suppression >= 0.55 or actor.hurt_until > time or actor.hp < 20
                  or (brain.combat.action in {"cover", "pin", "retreat"} and time < brain.combat.action_until)):
                self.combat.choose(actor, brain, actors, point, time)
                if brain.combat.action in {"cover", "retreat", "pin"}:
                    actor.state, goal = "retreat", brain.combat.action_goal
        elif situation.looking_for_sound:
            actor.state, goal = "idle", None
            face(actor, memory.last_sound)
        else:
            brain.search.cue = None
            brain.search.target = None
            brain.search.cover = None
            brain.combat.peek_home = None
            brain.combat.incoming = None
            memory.last_seen = memory.reported = None
            set_role(brain, "")
            actor.state, goal = self._patrol(actor, brain)
        return ActionPlan(goal, threat, sound_sweep)

    def _execute_plan(self, actor, brain, intent, plan, situation, time, budget, previous_facing, approaches, approach_looks):
        memory = brain.memory
        goal, threat, sound_sweep = plan.goal, plan.threat, plan.sound_sweep
        if actor.state == "retreat" and not memory.visible:
            watch_point = threat
            if brain.combat.peek_home is None:
                if situation.incoming is not None:
                    watch_point = situation.incoming
                elif situation.fresh_sound and not situation.tracking:
                    watch_point = memory.last_sound
            if watch_point is not None:
                face(actor, watch_point)
        weapon = actor.weapon
        if weapon and not weapon.chambered and not weapon.reloading:
            moving_to_cover = (actor.state == "retreat" and brain.combat.action == "cover" and goal is not None
                               and math.dist(actor.xy, goal) > 0.3)
            intent.reload = not moving_to_cover and (weapon.reserve > 0 or weapon.stage >= 2)
        if actor.state == "retreat" and brain.combat.action == "cover" and goal is not None and math.dist(actor.xy, goal) <= 0.3:
            goal = None
        approximate = actor.state not in {"combat", "retreat", "combat_guard"} and goal not in self.exits
        if goal == brain.search.cover:
            approximate = False
        if goal == brain.search.indoor.target:
            approximate = False
        if not situation.priority_search and brain.search.cue is not None and brain.search.cue[1] == memory.last_sound:
            approximate = False
        room_sweep = actor.state == "search" and goal is not None and goal == brain.search.indoor.target
        moving_sweep = room_sweep or (actor.state == "search" and goal is not None and situation.priority_search)
        starting_peek = brain.combat.peek_home is not None and brain.combat.action == "peek" and not brain.combat.peek_departed
        if starting_peek and not memory.visible and threat is not None:
            # 최초 옆걸음 전에 이전 관측 방향으로 몸을 돌리는 시간을 확보한다.
            turn = abs(math.remainder(actor.facing - previous_facing[actor.id], math.tau))
            if turn > math.radians(15):
                brain.combat.action_until += 1 / 60
                goal = None
        group = actor.source_unit or brain.incident
        approach = None
        if group and memory.visible and brain.combat.action == "advance":
            approach = "advance"
        elif goal is not None and actor.state in {"search", "alert"} and not room_sweep:
            cue = brain.search.priority_cue if situation.priority_search else None
            if group and cue is not None and cue.point == goal and cue.kind == "seen":
                approach = "seen"
            elif ((cue is not None and cue.point == goal and cue.kind == "sound")
                  or (not situation.priority_search and goal == memory.last_sound)):
                approach = ("sound", memory.sound_at)
        if approach is not None:
            key = (actor.team, actor.position.floor, bool(actor.source_unit), group, approach)
            approaches.setdefault(key, []).append(ApproachMove(actor, brain, intent, goal, approximate,
                                                                priority=moving_sweep))
        else:
            face_path = (not moving_sweep and not memory.visible
                         and actor.state not in {"retreat", "combat_guard"} and brain.combat.action != "peek")
            request = MoveRequest(goal, face=face_path, approximate=approximate)
            budget = self.motion.move(actor, brain, intent, request, time, budget)
        if starting_peek and any(intent.direction):
            brain.combat.peek_departed = True
        if moving_sweep:
            approach_cue = brain.search.priority_cue if situation.priority_search and not room_sweep else None
            if approach is None:
                self.search.pace_indoor_movement(actor, brain, intent, time, room_sweep=room_sweep,
                                           independent_look=approach_cue is not None)
            if approach_cue is not None:
                approach_looks[actor.id] = approach_cue, sound_sweep
        else:
            brain.motion.speed, brain.motion.depart_at = 0.0, None
            if not brain.search.indoor.staging:
                brain.search.indoor.scan.exposure.clear()
        if (actor.state == "retreat" and memory.visible and any(intent.direction)
                and brain.combat.action in {"cover", "retreat"} and brain.combat.suppression < 0.85
                and weapon is not None and weapon.chambered and not weapon.reloading
                and time >= brain.combat.fire_at):
            intent.retreat_fire = True
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
                        actor.facing = previous_facing[actor.id]
                        self.search.look_at_indoor_samples(actor, brain, time, sweep)
                    if sweep is None or not brain.search.indoor.scan.walk_look_started:
                        face(actor, cue.point)
                        brain.look.point = cue.point
                        brain.look.reason = "소리 방향 확인" if cue.kind == "sound" else "마지막 단서 방향 확인"
                rate = (config.INDOOR_TURN_RADIANS_PER_S if actor.state == "regroup" or brain.motion.yield_point is not None
                        or (actor.state == "search" and (brain.search.indoor.sweep is not None or brain.search.investigating_priority))
                        else config.SEARCH_TURN_RADIANS_PER_S)
                if brain.motion.door_stack is not None:
                    brain.search.indoor.scan.exposure.clear()
                    rate = config.INDOOR_TURN_RADIANS_PER_S
                elif intent.face_path:
                    rate = config.PATH_TURN_RADIANS_PER_S
                elif actor.state == "search" and (brain.search.indoor.target is not None and brain.motion.goal == brain.search.indoor.target
                                                or approach_look is not None and brain.search.indoor.scan.walk_look_started):
                    rate = config.INDOOR_SWEEP_TURN_RADIANS_PER_S
                if actor.state == "search" and brain.motion.door_stack is None and brain.search.indoor.scan.sample_at == time:
                    actor.facing = turn_indoor_gaze(actor, brain, previous_facing[actor.id], rate)
                else:
                    brain.look.turn_rate = 0.0
                    actor.facing = turn_toward(previous_facing[actor.id], actor.facing, 1 / 60, rate=rate)

    def update(self, actors, player, time):
        intents, budget = {}, 1
        approach_looks = {}
        approaches = {}
        tick = round(time * 60)
        searching = [(i, actor.id) for i, actor in enumerate(actors) if actor.hp > 0
                     and (actor.state == "search" or self.brains[actor.id].search.investigating_priority)]
        period = 2 if len(searching) == 1 else config.COVERAGE_SCAN_PERIOD_TICKS
        due = [actor_id for i, actor_id in searching if i % period == tick % period]
        quota, extra = divmod(config.COVERAGE_CELL_BUDGET, len(due)) if due else (0, 0)
        self.search.coverage_budgets = {actor_id: quota + int(index < extra) for index, actor_id in enumerate(due)}
        self.squad.assign_roles(actors, time)
        self.search.advance_entry_poses(actors, time)
        self.squad.unblock_search(actors, time)
        self.near_count = 0
        previous_facing = {actor.id: actor.facing for actor in actors}
        for actor in sorted(actors, key=lambda actor: math.dist(actor.xy, player.xy)):
            if actor.hp <= 0:
                continue
            brain, intent = self.brains[actor.id], Intent()
            situation = self._sense_actor(actor, brain, player, time, previous_facing)
            if situation.distant:
                if time < brain.far_at:
                    continue
                intent.step_dt = min(0.5, time - brain.far_updated)
                brain.far_at, brain.far_updated = time + 0.5, time
                brain.memory.visible = False
                brain.memory.last_seen = brain.memory.reported = None
                brain.search.priority_cue = None
                brain.search.cover = None
                brain.search.cue = None
                set_role(brain, "")
                actor.state, goal = self._patrol(actor, brain)
                budget = self.motion.move(actor, brain, intent, MoveRequest(goal, approximate=True), time, budget)
            else:
                special_budget = self._continue_special_action(
                    actor, brain, intent, situation, time, budget, previous_facing)
                if special_budget is not None:
                    budget = special_budget
                else:
                    plan = self._choose_action(actor, brain, actors, intent, situation, time)
                    budget = self._execute_plan(
                        actor, brain, intent, plan, situation, time, budget, previous_facing, approaches, approach_looks)
            intents[actor.id] = intent
        self.squad.move_approaches(approaches, time, budget, self.motion, self.search)
        self.squad.update_regroups(actors, time)
        self.door_traffic.update(self.world, actors, self.brains, intents, time, previous_facing=previous_facing)
        self._finish_looks(actors, intents, approach_looks, time, previous_facing)
        return intents
