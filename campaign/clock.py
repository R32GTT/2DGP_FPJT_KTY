"""정수 초 시간축. 같은 시각은 우선순위·발급 순번으로 처리한다."""

from collections.abc import Callable
import heapq

from .models import CampaignState, Event


CONSUMPTION = 10
TRANSFER = 20
DEMAND = 30
PLANNING = 40


def schedule(state: CampaignState, time: int, kind: str, *, priority: int = PLANNING,
             operation_id: str | None = None) -> Event:
    if type(time) is not int or time < state.world_time:
        raise ValueError("예정 시각은 현재 이후의 정수 초여야 함")
    event = Event(time, priority, state.next_sequence, kind, operation_id)
    state.next_sequence += 1
    heapq.heappush(state.events, event)
    return event


def drain_events(state: CampaignState, handler: Callable[[Event], None]) -> None:
    while state.events and state.events[0].time <= state.world_time:
        handler(heapq.heappop(state.events))


def advance_to(state: CampaignState, target: int, handler: Callable[[Event], None],
               *, tick: Callable[[], None] | None = None) -> None:
    if type(target) is not int or target < state.world_time:
        raise ValueError("목표 시각은 현재 이후의 정수 초여야 함")
    drain_events(state, handler)
    if tick is not None:
        # 일반 진행과 스킵이 같은 1초 이동·사건 순서를 사용한다.
        while state.world_time < target:
            state.world_time += 1
            tick()
            drain_events(state, handler)
    else:
        while state.events and state.events[0].time <= target:
            state.world_time = state.events[0].time
            drain_events(state, handler)
        state.world_time = target
