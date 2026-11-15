"""유한 이력의 게임 이벤트. 소음 판정·화면 효과·향후 오디오가 함께 사용한다."""

from collections import Counter, deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class GameEvent:
    kind: str
    time: float
    source: str
    position: tuple[float, float]
    data: dict = field(default_factory=dict)


class EventBus:
    def __init__(self):
        self.pending = []
        self.history = deque(maxlen=128)
        self.counts = Counter()

    def emit(self, kind, time, actor, **data):
        event = GameEvent(kind, time, actor.id, actor.xy, {"team": actor.team, **data})
        self.pending.append(event)
        self.history.append(event)
        self.counts[kind] += 1
        return event

    def drain(self):
        events, self.pending = self.pending, []
        return events
