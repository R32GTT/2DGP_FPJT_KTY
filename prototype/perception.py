"""관측·추정·기억만 AI 판단에 전달한다. 오디오 파일 재생과 독립적이다."""

from dataclasses import dataclass
import math


SOUND_MEMORY_SECONDS = 14
GUNSHOT_REPLAN_DISTANCE = 4.0
SIGHT_RANGE = 16.0
SIGHT_RETENTION_MARGIN = 0.25
NOTICE_THRESHOLD = 0.30
INSPECT_THRESHOLD = 0.70
VISION_HOLD_SECONDS = 0.7
VISION_DECAY_PER_SECOND = 0.25


@dataclass
class Knowledge:
    visible: bool = False
    last_seen: tuple | None = None
    seen_at: float = -1000.0
    seen_heading: float | None = None
    seen_motion_at: float = -1000.0
    last_sound: tuple | None = None
    sound_at: float = -1000.0
    sound_heard_at: float = -1000.0
    gunshot_origin: tuple | None = None
    sound_priority: float = 0.0
    suspicion: float = 0.0
    reported: tuple | None = None
    reported_at: float = -1000.0
    report_source: str = ""
    recognition: float = 0.0
    glimpse: tuple | None = None
    glimpse_at: float = -1000.0
    vision_at: float | None = None
    exposed: bool = False


def sight_sample(actor, target, geometry, memory, *, reach=SIGHT_RANGE):
    if target.hp <= 0 or actor.position.floor != target.position.floor:
        return
    dx, dy = target.position.x - actor.position.x, target.position.y - actor.position.y
    distance = math.hypot(dx, dy)
    sight_range = reach + (SIGHT_RETENTION_MARGIN if memory.exposed or memory.visible else 0.0)
    alignment = (dx * math.cos(actor.facing) + dy * math.sin(actor.facing)) / distance if distance else 1.0
    if distance <= sight_range and alignment >= 0.5 and geometry.raycast(actor.xy, target.xy) is None:
        return distance, alignment


def update_vision(target, memory, time, sample, *, alert=False, reacquire=False, motion=1.0, light=1.0):
    previous = memory.vision_at if memory.vision_at is not None else time
    memory.vision_at = time
    elapsed = max(0.0, time - previous)
    memory.suspicion = max(0.0, memory.suspicion - 0.06 * elapsed)
    if light <= 0:
        sample = None
    was_exposed = memory.exposed and elapsed <= 0.25
    memory.exposed = sample is not None
    memory.visible = False
    if sample is None or not was_exposed:
        decay_time = max(0.0, time - max(previous, memory.glimpse_at + VISION_HOLD_SECONDS))
        memory.recognition = max(0.0, memory.recognition - decay_time * VISION_DECAY_PER_SECOND)
        if not memory.recognition:
            memory.glimpse = None
    if sample is None:
        return

    distance, alignment = sample
    memory.glimpse, memory.glimpse_at = target.xy, time
    if reacquire:
        memory.recognition = 1.0
    elif was_exposed:
        # 첫 표본은 노출 시작만 기록한다. 두 관측 사이의 노출만 누적한다.
        distance_factor = max(0.26, 1.0 - distance / 22)
        angle_factor = 0.4 + 0.6 * max(0.0, (alignment - 0.5) * 2)
        rate = 1.05 * distance_factor * angle_factor
        if distance <= 2.0:
            rate = max(rate, 3.4 * angle_factor)
        rate *= motion * light * (1.35 if alert else 1.0)
        memory.recognition = min(1.0, memory.recognition + elapsed * rate)
    if memory.recognition < 1.0:
        return
    if memory.last_seen is not None and time - memory.seen_at < 0.5:
        vx, vy = target.xy[0] - memory.last_seen[0], target.xy[1] - memory.last_seen[1]
        if math.hypot(vx, vy) > 0.06:
            memory.seen_heading = math.atan2(vy, vx)
            memory.seen_motion_at = time
    if time - memory.seen_motion_at > 1.5:
        memory.seen_heading = None
    memory.visible, memory.last_seen, memory.seen_at = True, target.xy, time


def observe(actor, target, geometry, memory, time, **factors):
    update_vision(target, memory, time, sight_sample(actor, target, geometry, memory), **factors)


def _estimate_sound(position, distance, navigation):
    # 추정 오차가 벽을 건너면 오차를 줄이고, 가능한 가장 가까운 지점을 고른다.
    resolution = 0.5 if distance < 6 else 1.0 if distance < 16 else 1.5
    rounded = tuple(round(value / resolution) * resolution for value in position)
    x, y = position
    candidates = [rounded, (rounded[0], y), (x, rounded[1]), position]
    for offset in (0.3, 0.6):
        candidates.extend(((x + offset, y), (x - offset, y), (x, y + offset), (x, y - offset)))
    for point in candidates:
        if navigation.walkable(point) and navigation.clear(position, point, clearance=0):
            return point
    return None


def hear(actor, memory, event, navigation, *, checked_sound_at):
    if event.kind != "noise" or event.data.get("team") == actor.team:
        return
    distance = math.dist(actor.xy, event.position)
    radius = event.data["radius"]
    if radius <= 0 or distance > radius:
        return "out_of_range"
    priority = event.data.get("strength", 0.4) * (1 - distance / radius * 0.4)
    memory.suspicion = min(1.0, memory.suspicion + priority)
    gunshot = event.data.get("reason") == "gunshot"
    recent_gunshot = memory.gunshot_origin is not None and event.time - memory.sound_heard_at < SOUND_MEMORY_SECONDS
    if recent_gunshot:
        if not gunshot and memory.sound_at > checked_sound_at and priority < memory.sound_priority:
            return "lower_priority"
        # 비교 기준은 첫 총성의 위치다. 같은 구역의 총성은 조사 목표를 그대로 둔다.
        nearby = (math.dist(event.position, memory.gunshot_origin) <= GUNSHOT_REPLAN_DISTANCE
                  and navigation.clear(memory.gunshot_origin, event.position, clearance=0))
        if nearby:
            if not gunshot:
                return "lower_priority"
            memory.sound_heard_at = event.time
            if memory.sound_at <= checked_sound_at:
                memory.sound_at = event.time
                return "renewed"
            return "same_area"
    if (not gunshot and not recent_gunshot and event.time - memory.sound_at < 2
            and priority < memory.sound_priority * 0.75):
        return "lower_priority"
    point = _estimate_sound(event.position, distance, navigation)
    if point is None:
        return "no_estimate"
    memory.last_sound = point
    memory.sound_at = memory.sound_heard_at = event.time
    memory.sound_priority = priority
    memory.gunshot_origin = event.position if gunshot else None
    return "new"
