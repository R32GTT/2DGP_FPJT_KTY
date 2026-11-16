"""좁은 출입구로 향하는 NPC의 이동 순서를 정한다."""

from dataclasses import dataclass
from itertools import pairwise
import math

from .door_layout import DoorLayout


@dataclass
class Passage:
    actor_id: str
    position: tuple[float, float]
    progressed_at: float


@dataclass
class Stack:
    slots: dict[str, tuple[float, float]]
    look_at: tuple[float, float]
    deadline: float
    ready_at: float | None = None


class DoorTraffic:
    def __init__(self):
        self.passages: dict[str, Passage] = {}
        self.requested_at: dict[tuple[str, str], float] = {}
        self.stacks: dict[str, Stack] = {}
        self.staged: dict[tuple[str, str], tuple] = {}

    @staticmethod
    def _ticket(actor, brain):
        if (actor.state in {"alert", "search"} and not brain.visible and brain.goal is not None
                and (brain.path or brain.pending_goal == brain.goal)
                and brain.pending_goal in (None, brain.goal) and brain.indoor_yield is None):
            if brain.investigating_priority and brain.priority_cue.kind == "sound":
                party = ("sound", brain.priority_cue.at)
            elif brain.investigating_priority:
                party = brain.incident or ("cue", brain.priority_cue)
            elif brain.cue and brain.cue[1] == brain.memory.last_sound:
                party = ("sound", brain.memory.sound_at)
            else:
                party = brain.incident
            if party:
                return party, brain.goal, brain.priority_cue
        return None

    @staticmethod
    def _stack_approach(world, actor, path):
        # 문 앞의 짧은 경유점에 아직 닿지 않아도 바로 다음 구간의 문을 찾는다.
        walked = 0.0
        for start, end in pairwise([actor.xy, *path]):
            hit = world.door_on_ai_path_from(start, end, reach=1.8 - walked,
                                            padding=actor.radius + 0.08, clearance=actor.radius)
            if hit is not None:
                return hit[0], walked + hit[1]
            walked += math.dist(start, end)
            if walked >= 1.8:
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
                   for start, end in pairwise([actor.xy, *brains[actor.id].path])):
                continue
            groups.setdefault((party, side), []).append((distance, actor.id, actor))

        for (_, side), group in groups.items():
            if len(group) < 2:
                continue
            slots = layout.waiting_slots(side, [actor for _, _, actor in sorted(group)], actors, world.navigation)
            if len(slots) >= 2:
                self.stacks[door_id] = Stack(slots, layout.center, now + 1.5)
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
                brain.path.clear()
                brain.path_revision, brain.plan_at = -1, 0.0
                brain.indoor_speed, brain.indoor_depart_at = 0.0, None
                intent.direction, intent.open_door = (0, 0), False
            return set(valid)

        ready = True
        for actor_id, point in valid.items():
            actor, brain, intent = actors[actor_id], brains[actor_id], intents[actor_id]
            brain.door_wait, brain.door_stack = door_id, point
            brain.look_point, brain.look_reason = stack.look_at, "출입구 주시"
            x, y = actor.xy
            actor.facing = math.atan2(stack.look_at[1] - y, stack.look_at[0] - x)
            angle_error = abs(math.remainder(actor.facing - previous_facing[actor_id], math.tau))
            brain.progress_point, brain.progress_at = actor.xy, now
            brain.indoor_speed = 0.0
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

    def update(self, world, actors, brains, intents, now, *, previous_facing):
        requests = {}
        started = {}
        tickets = {}
        for actor in actors:
            brain = brains[actor.id]
            brain.door_wait = ""
            brain.door_stack = None
            intent = intents.get(actor.id)
            tickets[actor.id] = self._ticket(actor, brain) if actor.hp > 0 and intent is not None else None
            if actor.hp <= 0 or intent is None or not intent.open_door or not brain.path:
                continue
            approach = world.door_on_ai_path(actor, brain.path[0])
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
                approach = self._stack_approach(world, actor, brains[actor.id].path)
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
            crossing = requests.get(door_id, [])
            if door_id not in self.stacks and all(distance > 0 for _, distance in crossing):
                self._start_stack(world, door_id, approaches[door_id], actors, brains, tickets, now)
            stack = self.stacks.get(door_id)
            if stack is not None:
                interrupted = any(actor.id not in stack.slots and (distance == 0 or actor.state == "combat")
                                  for actor, distance in crossing)
                staged[door_id] = self._update_stack(door_id, by_id, brains, intents, tickets, now,
                                                     interrupted=interrupted, previous_facing=previous_facing)
        next_passages = {}
        for door_id, contenders in requests.items():
            staged_actors = staged.get(door_id, set())
            contenders = [(actor, distance) for actor, distance in contenders if actor.id not in staged_actors]
            if door_id in self.stacks:
                for actor, _ in contenders:
                    brains[actor.id].door_wait = door_id
                    intents[actor.id].direction, intents[actor.id].open_door = (0, 0), False
                    brains[actor.id].progress_point, brains[actor.id].progress_at = actor.xy, now
                continue
            if not contenders:
                continue
            prior = self.passages.get(door_id)
            owners = {actor.id: (actor, distance) for actor, distance in contenders}
            if prior and prior.actor_id in owners:
                actor, distance = owners[prior.actor_id]
                if math.dist(actor.xy, prior.position) >= 0.08:
                    prior.position, prior.progressed_at = actor.xy, now
                if len(owners) > 1 and distance > 0 and now - prior.progressed_at >= 1.4:
                    # 정지한 NPC가 입구 밖에 있으면 다음 요청자에게 차례를 양보한다.
                    self.requested_at[(door_id, prior.actor_id)] = now
                    prior = None
            else:
                prior = None

            actor, distance = min(contenders, key=lambda entry: (
                entry[1] > 0, self.requested_at[door_id, entry[0].id], entry[1], entry[0].id))
            if prior is None or (distance == 0 and owners[prior.actor_id][1] > 0):
                prior = Passage(actor.id, actor.xy, now)
            next_passages[door_id] = prior

            for actor, _ in contenders:
                if actor.id != prior.actor_id:
                    brain, intent = brains[actor.id], intents[actor.id]
                    brain.door_wait = door_id
                    brain.progress_point, brain.progress_at = actor.xy, now
                    intent.direction, intent.open_door = (0, 0), False
        self.passages = next_passages
