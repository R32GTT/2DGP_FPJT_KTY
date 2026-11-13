"""UI 프레임 예산 안에서 기존 전략 틱을 그대로 실행하는 재생 제어기."""

from dataclasses import replace
import math
from time import perf_counter

from .simulation import Simulation
from .viewmodel import convoy_markers


class Playback:
    def __init__(self, simulation: Simulation, *, speed: float = 120.0, timer=perf_counter):
        self.simulation = simulation
        self.paused = False
        self.speed = 1.0
        self.set_speed(speed)
        self.desired_time = float(simulation.state.world_time)
        self.skip_target: int | None = None
        self.previous_time = simulation.state.world_time
        self.previous = self.current = convoy_markers(simulation.state)
        self.timer = timer

    def set_speed(self, speed: float) -> None:
        if not isinstance(speed, (int, float)) or not math.isfinite(speed) or speed <= 0:
            raise ValueError("배속은 양수여야 함")
        self.speed = float(speed)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self.cancel_skip()

    def cancel_skip(self) -> None:
        self.skip_target = None
        self.desired_time = float(self.simulation.state.world_time)

    def request_skip(self, seconds: int) -> None:
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("스킵 시간은 양의 정수 초여야 함")
        self.skip_target = (self.skip_target or self.simulation.state.world_time) + seconds

    def refresh(self) -> None:
        # 디버그 개입 뒤에는 이전 위치 보간을 폐기한다.
        self.previous_time = self.simulation.state.world_time
        self.previous = self.current = convoy_markers(self.simulation.state)

    def update(self, elapsed: float, *, budget_s: float = 0.006, max_chunks: int = 4) -> None:
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("프레임 경과 시간 오류")
        if self.skip_target is None and not self.paused:
            self.desired_time += elapsed * self.speed
        target = self.skip_target if self.skip_target is not None else int(self.desired_time + 1e-8)
        started = self.timer()
        for _ in range(max_chunks):
            current_time = self.simulation.state.world_time
            if current_time >= target or self.timer() - started >= budget_s:
                break
            self.previous_time = current_time
            self.previous = self.current
            # 큰 스킵도 사건 순서를 보존하며 최대 30초씩 처리한다.
            self.simulation.advance_to(min(target, current_time + 30))
            self.current = convoy_markers(self.simulation.state)
        if self.skip_target is not None and self.simulation.state.world_time >= self.skip_target:
            self.skip_target = None
            self.desired_time = float(self.simulation.state.world_time)

    def markers(self):
        current_time = self.simulation.state.world_time
        if (self.paused or self.skip_target is not None or current_time - self.previous_time != 1
                or self.desired_time - current_time >= 1):
            return self.current
        alpha = max(0.0, min(1.0, self.desired_time - current_time))
        result = {}
        for key, current in self.current.items():
            previous = self.previous.get(key)
            if (previous is not None and previous.edge is not None and previous.edge == current.edge
                    and previous.phase == current.phase and previous.waiting == current.waiting):
                result[key] = replace(current, x=previous.x + (current.x - previous.x) * alpha,
                                      y=previous.y + (current.y - previous.y) * alpha)
            else:
                result[key] = current
        return result
