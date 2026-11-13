"""관측·추정·기억만 AI 판단에 전달한다. 오디오 파일 재생과 독립적이다."""

from dataclasses import dataclass
import math


@dataclass
class Knowledge:
    visible: bool = False
    last_seen: tuple | None = None
    seen_at: float = -1000.0
    last_sound: tuple | None = None
    sound_at: float = -1000.0
    suspicion: float = 0.0


def observe(actor, target, geometry, memory, time):
    memory.visible = False
    if target.hp <= 0:
        return
    dx, dy = target.position.x - actor.position.x, target.position.y - actor.position.y
    distance = math.hypot(dx, dy)
    in_cone = distance < 1.2 or dx * math.cos(actor.facing) + dy * math.sin(actor.facing) >= distance * 0.5
    if distance <= 16 and in_cone and geometry.raycast(actor.xy, target.xy) is None:
        memory.visible, memory.last_seen, memory.seen_at = True, target.xy, time
        memory.suspicion = 1.0


def hear(actor, memory, event):
    if event.kind != "noise" or event.data.get("team") == actor.team:
        return
    distance = math.dist(actor.xy, event.position)
    radius = event.data["radius"]
    if distance > min(radius, 30):
        return
    # 청각 정보는 직접 목격이 아니다. 먼 소리일수록 조사 위치를 거칠게 한다.
    resolution = 0.75 if distance < 5 else 3.0
    memory.last_sound = tuple(round(value / resolution) * resolution for value in event.position)
    memory.sound_at = event.time
    memory.suspicion = min(1.0, memory.suspicion + event.data.get("strength", 0.4) * (1 - distance / max(radius, 1) * 0.5))
