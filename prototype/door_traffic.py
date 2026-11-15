"""좁은 출입구로 향하는 NPC의 이동 순서를 정한다."""

from dataclasses import dataclass
import math


@dataclass
class Passage:
    actor_id: str
    position: tuple[float, float]
    progressed_at: float


class DoorTraffic:
    def __init__(self):
        self.passages: dict[str, Passage] = {}
        self.requested_at: dict[tuple[str, str], float] = {}

    def update(self, world, actors, brains, intents, now):
        requests = {}
        started = {}
        for actor in actors:
            brain = brains[actor.id]
            brain.door_wait = ""
            intent = intents.get(actor.id)
            if actor.hp <= 0 or intent is None or not intent.open_door or not brain.path:
                continue
            approach = world.door_on_ai_path(actor, brain.path[0])
            if approach is None:
                continue
            door_id, distance = approach
            key = door_id, actor.id
            started[key] = self.requested_at.get(key, now)
            requests.setdefault(door_id, []).append((actor, distance))

        self.requested_at = started
        next_passages = {}
        for door_id, contenders in requests.items():
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
