"""좁은 출입구로 향하는 NPC의 이동 순서를 정한다."""

from dataclasses import dataclass
from itertools import pairwise
import math

from .door_layout import DoorLayout
from .geometry import segment_distance


MAX_STACK_MEMBERS = 4
STACK_APPROACH_M = 3.8
PASSAGE_PROGRESS_M = 0.08
PASSAGE_STALL_S = 1.4


@dataclass
class Passage:
    actor_id: str
    side: int
    progress: float
    progressed_at: float
    retry_at: float = 0.0


@dataclass
class DoorRecovery:
    door_id: str
    goal: tuple[float, float]
    point: tuple[float, float]
    remaining: float
    progressed_at: float


@dataclass
class Stack:
    slots: dict[str, tuple[float, float]]
    looks: dict[str, tuple[float, float]]
    deadline: float
    ready_at: float | None = None


class DoorTraffic:
    def __init__(self):
        self.passages: dict[str, Passage] = {}
        self.requested_at: dict[tuple[str, str], float] = {}
        self.stacks: dict[str, Stack] = {}
        self.staged: dict[tuple[str, str], tuple] = {}
        self.yielding: dict[str, tuple[str, str]] = {}
        self.recoveries: dict[str, DoorRecovery] = {}

    @staticmethod
    def _ticket(actor, brain):
        goal = brain.motion.pending_goal or brain.motion.goal
        if (actor.state in {"alert", "search"} and not brain.visible and goal is not None
                and (brain.motion.path or brain.motion.pending_goal is not None) and brain.motion.yield_point is None):
            if brain.search.investigating_priority and brain.search.priority_cue.kind == "sound":
                party = ("sound", brain.search.priority_cue.at)
            elif brain.search.investigating_priority:
                party = brain.incident or ("cue", brain.search.priority_cue)
            elif brain.search.task.cue and brain.search.task.cue[0][1] == brain.memory.last_sound:
                party = ("sound", brain.memory.sound_at)
            else:
                party = brain.incident
            if party:
                return party, goal, brain.search.priority_cue
        return None

    @staticmethod
    def _stack_approach(world, actor, path):
        # 문 앞의 짧은 경유점에 아직 닿지 않아도 바로 다음 구간의 문을 찾는다.
        walked = 0.0
        for start, end in pairwise([actor.xy, *path]):
            hit = world.door_on_ai_path_from(start, end, reach=STACK_APPROACH_M - walked,
                                            padding=actor.radius + 0.08, clearance=actor.radius)
            if hit is not None:
                return hit[0], walked + hit[1]
            walked += math.dist(start, end)
            if walked >= STACK_APPROACH_M:
                break
        return None

    def _start_stack(self, world, door_id, contenders, actors, brains, tickets, now):
        if len(contenders) < 2 or any(distance == 0 for _, distance in contenders):
            return
        layout = DoorLayout.from_rect(world.geometry.objects[door_id].rect)
        across = layout.across
        groups = {}
        for actor, distance in contenders:
            ticket = tickets[actor.id]
            if ticket is None or ticket == self.staged.get((door_id, actor.id)):
                continue
            party, goal, _ = ticket
            side = -1 if actor.xy[across] < layout.center[across] else 1
            if side * (goal[across] - layout.center[across]) >= 0:
                continue
            # 목표까지 가는 길에 문을 더 지난다면 이 문은 중간 통로다.
            if any(world.door_on_ai_path_from(start, end, reach=math.dist(start, end), padding=0,
                                             ignore=door_id) is not None
                   for start, end in pairwise([actor.xy, *brains[actor.id].motion.path])):
                continue
            groups.setdefault((party, side), []).append((distance, actor.id, actor))

        for (party, side), group in groups.items():
            if len(group) < 2:
                continue
            # 경로 계산 예산 때문에 같은 조사조의 경로가 몇 틱에 걸쳐 준비될 수 있다.
            if len(group) < MAX_STACK_MEMBERS and any(
                brains[actor.id].motion.pending_goal is not None and not brains[actor.id].motion.path
                and tickets[actor.id] is not None and tickets[actor.id][0] == party
                and math.dist(actor.xy, layout.center) <= STACK_APPROACH_M
                and side * (actor.xy[across] - layout.center[across]) > 0
                for actor in actors
            ):
                continue
            members = [actor for _, _, actor in sorted(group)[:MAX_STACK_MEMBERS]]
            slots = layout.waiting_slots(side, members, actors, world.navigation)
            if len(slots) >= 2:
                self.stacks[door_id] = Stack(slots, layout.waiting_looks(side, slots), now + 1.5)
                for actor_id in slots:
                    self.staged[door_id, actor_id] = tickets[actor_id]
                break

    def _update_stack(self, door_id, actors, brains, intents, tickets, now, *, interrupted, previous_facing):
        stack = self.stacks[door_id]
        valid = {actor_id: point for actor_id, point in stack.slots.items()
                 if actor_id in intents and tickets[actor_id] is not None
                 and tickets[actor_id] == self.staged.get((door_id, actor_id))}
        if interrupted or len(valid) != len(stack.slots) or now >= stack.deadline or (
                stack.ready_at is not None and now >= stack.ready_at):
            del self.stacks[door_id]
            for actor_id in valid:
                brain, intent = brains[actor_id], intents[actor_id]
                # 대기 위치에서 원래 목적지까지 이어지는 경로를 다시 구한다.
                brain.motion.invalidate()
                brain.motion.speed = 0.0
                intent.direction, intent.open_door = (0, 0), False
            return set(valid)

        ready = True
        for actor_id, point in valid.items():
            actor, brain, intent = actors[actor_id], brains[actor_id], intents[actor_id]
            brain.motion.door_wait, brain.motion.door_stack = door_id, point
            look_x, look_y = stack.looks[actor_id]
            brain.look.point, brain.look.reason = (look_x, look_y), "출입구 주시"
            x, y = actor.xy
            desired = math.atan2(look_y - y, look_x - x)
            angle_error = abs(math.remainder(desired - previous_facing[actor_id], math.tau))
            brain.motion.progress_point, brain.motion.progress_at = actor.xy, now
            brain.motion.speed = 0.0
            intent.open_door, intent.move_straight = False, True
            distance = math.dist(actor.xy, point)
            speed = min(actor.speed, 1.7)
            intent.move_multiplier = speed / actor.speed
            intent.direction = (point[0] - actor.xy[0], point[1] - actor.xy[1]) if distance > 0.06 else (0, 0)
            intent.step_dt = min(1 / 60, distance / speed)
            ready &= distance <= 0.06 and angle_error <= math.radians(5)
        if ready and stack.ready_at is None:
            stack.ready_at = now + 0.25
        return set(valid)

    @staticmethod
    def _clear_step(world, actor, actors, point):
        nav = world.navigation
        if not nav.walkable(point) or not nav.clear(actor.xy, point, clearance=actor.radius):
            return False
        for other in actors:
            if other is actor or other.hp <= 0 or other.position.floor != actor.position.floor:
                continue
            gap = actor.radius + other.radius
            if (math.dist(other.xy, point) < gap - 1e-8
                    or segment_distance(*other.xy, *actor.xy, *point) < min(gap, math.dist(other.xy, actor.xy)) - 1e-8):
                return False
        return True

    @staticmethod
    def _steer(actor, intent, point):
        distance = math.dist(actor.xy, point)
        if distance > 0.06:
            speed = min(actor.speed, 1.7)
            intent.direction = (point[0] - actor.xy[0], point[1] - actor.xy[1])
            intent.step_dt = min(1 / 60, distance / speed)
            intent.move_multiplier = speed / actor.speed
            intent.move_straight = True

    @staticmethod
    def _wait(actor, brain, intent, door_id, now):
        brain.motion.door_wait = door_id
        brain.motion.progress_point, brain.motion.progress_at = actor.xy, now
        intent.direction, intent.open_door = (0, 0), False

    def _step_aside(self, world, actor, actors, intent, layout, side):
        along = actor.xy[layout.along] - layout.center[layout.along]
        setback = layout.half_depth + 2 * actor.radius + 0.2
        slots = (layout.waiting_position(side, flank, 0, actor.radius) for flank in (-1, 1))
        points = [*sorted(slots, key=lambda point: math.dist(actor.xy, point)), layout.point(along, side * setback)]
        target = next((point for point in points if self._clear_step(world, actor, actors, point)), None)
        if target is not None:
            self._steer(actor, intent, target)

    @staticmethod
    def _passage_side(actor, path, layout):
        across, along = layout.across, layout.along
        for start, end in pairwise([actor.xy, *path]):
            offset = start[across] - layout.center[across]
            step = end[across] - start[across]
            if abs(step) < 1e-8 or not 0 <= -offset / step <= 1:
                continue
            crossing = start[along] - offset / step * (end[along] - start[along])
            if abs(crossing - layout.center[along]) <= layout.half_width + actor.radius:
                return -1 if step < 0 else 1
        return -1 if path[0][across] < actor.xy[across] else 1

    def _start_recovery(self, world, actor, actors, brain, intent, door_id, layout, side, now):
        goal = brain.motion.pending_goal or brain.motion.goal
        if goal is None or brain.visible or intent.fire or intent.retreat_fire:
            return False
        along = actor.xy[layout.along] - layout.center[layout.along]
        setback = layout.half_depth + 2 * actor.radius + 0.2
        points = (layout.point(along, side * setback), layout.point(0, side * (setback + 0.5)),
                  *(layout.waiting_position(side, flank, 0, actor.radius) for flank in (-1, 1)))
        target = next((point for point in points if math.dist(actor.xy, point) >= 0.15
                       and self._clear_step(world, actor, actors, point)), None)
        if target is None:
            return False
        self.recoveries[actor.id] = DoorRecovery(door_id, goal, target, math.dist(actor.xy, target), now)
        self._wait(actor, brain, intent, door_id, now)
        self._steer(actor, intent, target)
        return True

    def update(self, world, actors, brains, intents, now, *, previous_facing):
        requests = {}
        started = {}
        tickets = {}
        recovering_doors = set()
        self.recoveries = {key: recovery for key, recovery in self.recoveries.items() if key in intents}
        for actor in actors:
            brain = brains[actor.id]
            brain.motion.door_wait = ""
            brain.motion.door_stack = None
            intent = intents.get(actor.id)
            recovery = self.recoveries.get(actor.id)
            if recovery is not None:
                goal = brain.motion.pending_goal or brain.motion.goal
                if actor.hp <= 0 or brain.visible or intent.fire or intent.retreat_fire or goal != recovery.goal:
                    del self.recoveries[actor.id]
                else:
                    remaining = math.dist(actor.xy, recovery.point)
                    if remaining <= recovery.remaining - PASSAGE_PROGRESS_M:
                        recovery.remaining, recovery.progressed_at = remaining, now
                    self._wait(actor, brain, intent, recovery.door_id, now)
                    if remaining <= 0.06 or now - recovery.progressed_at >= PASSAGE_STALL_S:
                        del self.recoveries[actor.id]
                        brain.motion.invalidate()
                    else:
                        recovering_doors.add(recovery.door_id)
                        self._steer(actor, intent, recovery.point)
                    tickets[actor.id] = None
                    continue
            tickets[actor.id] = self._ticket(actor, brain) if actor.hp > 0 and intent is not None else None
            if actor.hp <= 0 or intent is None or not intent.open_door or not brain.motion.path:
                continue
            approach = world.door_on_ai_path(actor, brain.motion.path[0])
            if approach is None:
                continue
            door_id, distance = approach
            key = door_id, actor.id
            started[key] = self.requested_at.get(key, now)
            requests.setdefault(door_id, []).append((actor, distance))

        self.staged = {key: ticket for key, ticket in self.staged.items() if tickets.get(key[1]) == ticket}
        by_id = {actor.id: actor for actor in actors}
        approaches = {}
        stacking = {actor_id for stack in self.stacks.values() for actor_id in stack.slots}
        if sum(ticket is not None for ticket in tickets.values()) >= 2:
            for actor in actors:
                if tickets[actor.id] is None or actor.id in stacking:
                    continue
                approach = self._stack_approach(world, actor, brains[actor.id].motion.path)
                if approach is not None:
                    door_id, distance = approach
                    approaches.setdefault(door_id, []).append((actor, distance))
                    key = door_id, actor.id
                    if key in self.staged and key not in started:
                        # 집결을 푼 뒤에는 문 앞 경유점으로 돌아오는 구간부터 차례를 지킨다.
                        requests.setdefault(door_id, []).append((actor, distance))
                        started[key] = self.requested_at.get(key, now)
        self.requested_at = started
        staged = {}
        for door_id in sorted(self.stacks.keys() | approaches.keys()):
            if door_id in recovering_doors:
                continue
            crossing = requests.get(door_id, [])
            if door_id not in self.stacks and all(distance > 0 for _, distance in crossing):
                self._start_stack(world, door_id, approaches[door_id], actors, brains, tickets, now)
            stack = self.stacks.get(door_id)
            if stack is not None:
                interrupted = any(actor.id not in stack.slots and (distance == 0 or actor.state == "combat")
                                  for actor, distance in crossing)
                staged[door_id] = self._update_stack(door_id, by_id, brains, intents, tickets, now,
                                                     interrupted=interrupted, previous_facing=previous_facing)
        next_passages, next_yielding = {}, {}
        for door_id, contenders in requests.items():
            staged_actors = staged.get(door_id, set())
            contenders = [(actor, distance) for actor, distance in contenders if actor.id not in staged_actors]
            if door_id in self.stacks or door_id in recovering_doors:
                for actor, _ in contenders:
                    self._wait(actor, brains[actor.id], intents[actor.id], door_id, now)
                continue
            if not contenders:
                continue
            prior = self.passages.get(door_id)
            owners = {actor.id: (actor, distance) for actor, distance in contenders}
            layout = DoorLayout.from_rect(world.geometry.objects[door_id].rect)
            across = layout.across
            if prior and prior.actor_id in owners:
                actor, distance = owners[prior.actor_id]
                progress = prior.side * (actor.xy[across] - layout.center[across])
                if progress >= prior.progress + PASSAGE_PROGRESS_M:
                    prior.progress, prior.progressed_at = progress, now
                if now - prior.progressed_at >= PASSAGE_STALL_S and now >= prior.retry_at:
                    if distance > 0 and len(owners) > 1:
                        # 문 밖에서 정지한 NPC는 다음 요청자에게 차례를 양보한다.
                        self.requested_at[(door_id, prior.actor_id)] = now
                        prior = None
                    else:
                        brain, intent = brains[actor.id], intents[actor.id]
                        if self._start_recovery(world, actor, actors, brain, intent, door_id, layout, -prior.side, now):
                            for other, _ in contenders:
                                if other is not actor:
                                    self._wait(other, brains[other.id], intents[other.id], door_id, now)
                            continue
                        prior.retry_at = now + 0.5
            else:
                prior = None

            actor, distance = min(contenders, key=lambda entry: (
                entry[1] > 0, self.requested_at[door_id, entry[0].id], entry[1], entry[0].id))
            if prior is None or (distance == 0 and owners[prior.actor_id][1] > 0):
                motion = brains[actor.id].motion
                side = self._passage_side(actor, motion.path, layout)
                prior = Passage(actor.id, side, side * (actor.xy[across] - layout.center[across]), now)
            next_passages[door_id] = prior

            owner_brain = brains[prior.actor_id]
            yielded = self.yielding.get(door_id)
            waiter_id = yielded[1] if yielded is not None and yielded[0] == prior.actor_id else owner_brain.motion.blocked_by
            if waiter_id in owners and waiter_id != prior.actor_id:
                waiter = brains[waiter_id]
                owner_goal = owner_brain.motion.goal
                destination_side = owner_goal[across] - layout.center[across] if owner_goal is not None else 0
                crossing_opposite = (waiter.motion.goal is not None
                                     and destination_side * (waiter.motion.goal[across] - layout.center[across]) < 0)
                if destination_side and (crossing_opposite or waiter.motion.yield_point is not None):
                    next_yielding[door_id] = prior.actor_id, waiter_id

            for actor, _ in contenders:
                if actor.id != prior.actor_id:
                    brain, intent = brains[actor.id], intents[actor.id]
                    self._wait(actor, brain, intent, door_id, now)
                    if next_yielding.get(door_id) == (prior.actor_id, actor.id):
                        side = -1 if destination_side < 0 else 1
                        self._step_aside(world, actor, actors, intent, layout, side)
        self.passages = next_passages
        self.yielding = next_yielding
