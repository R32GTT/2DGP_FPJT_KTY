"""관측·추정·기억만 AI 판단에 전달한다. 오디오 파일 재생과 독립적이다."""

from dataclasses import dataclass
import math


@dataclass
class Knowledge:
    visible: bool = False
    last_seen: tuple | None = None
    seen_at: float = -1000.0
    seen_heading: float | None = None
    last_sound: tuple | None = None
    sound_at: float = -1000.0
    sound_priority: float = 0.0
    suspicion: float = 0.0
    reported: tuple | None = None
    reported_at: float = -1000.0


def observe(actor, target, geometry, memory, time):
    memory.visible = False
    if target.hp <= 0:
        return
    dx, dy = target.position.x - actor.position.x, target.position.y - actor.position.y
    distance = math.hypot(dx, dy)
    in_cone = distance < 1.2 or dx * math.cos(actor.facing) + dy * math.sin(actor.facing) >= distance * 0.5
    if distance <= 16 and in_cone and geometry.raycast(actor.xy, target.xy) is None:
        if memory.last_seen is not None and time - memory.seen_at < 0.5:
            vx, vy = target.xy[0] - memory.last_seen[0], target.xy[1] - memory.last_seen[1]
            if math.hypot(vx, vy) > 0.1:
                memory.seen_heading = math.atan2(vy, vx)
        else:
            memory.seen_heading = None
        memory.visible, memory.last_seen, memory.seen_at = True, target.xy, time
        memory.suspicion = 1.0


def hear(actor, memory, event):
    if event.kind != "noise" or event.data.get("team") == actor.team:
        return
    distance = math.dist(actor.xy, event.position)
    radius = event.data["radius"]
    if radius <= 0 or distance > radius:
        return
    strength = event.data.get("strength", 0.4)
    memory.suspicion = min(1.0, memory.suspicion + strength * (1 - distance / radius * 0.4))
    # 가까운 총성도 뒤따른 약한 발소리 하나로 덮어쓰지 않는다.
    priority = strength * (1 - distance / radius * 0.4)
    if event.time - memory.sound_at < 2 and priority < memory.sound_priority * 0.75:
        return
    resolution = 0.75 if distance < 5 else 3.0
    memory.last_sound = tuple(round(value / resolution) * resolution for value in event.position)
    memory.sound_at = event.time
    memory.sound_priority = priority
