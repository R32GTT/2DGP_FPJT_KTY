"""화면 전용 흔들림·경계 비네팅 상태. 물리·조준·판정 좌표를 변경하지 않는다."""

import math


class Feedback:
    def __init__(self):
        self.clock = 0.0
        self.shake_left = 0.0
        self.shake_duration = 0.1
        self.shake_power = 0.0
        self.alpha = 0.0

    def receive(self, events):
        for event in events:
            if event.source != "player":
                continue
            profile = {"weapon_fired": (3.0, 0.08), "damaged": (8.0, 0.18),
                       "reload_step_started": (1.4, 0.10)}.get(event.kind)
            if event.kind == "reload_step_started":
                profile = ({"remove_magazine": (1.2, 0.08), "insert_magazine": (1.7, 0.10),
                            "bolt_catch": (2.3, 0.10)}[event.data["stage"]])
            if profile:
                self.shake_power = min(9.0, max(self.shake_power, profile[0]))
                self.shake_duration = self.shake_left = profile[1]

    def update(self, dt, danger):
        self.clock += dt
        self.shake_left = max(0, self.shake_left - dt)
        if self.shake_left == 0:
            self.shake_power = 0.0
        target = min(0.45, max(0, danger) * 0.45)
        rate = 4.0 if target > self.alpha else 1.0
        self.alpha += (target - self.alpha) * min(1, dt * rate)

    @property
    def offset(self):
        power = self.shake_power * self.shake_left / max(0.001, self.shake_duration)
        return math.sin(self.clock * 105) * power, math.sin(self.clock * 137) * power * 0.6
