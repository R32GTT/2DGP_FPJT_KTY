"""이동 요청을 경로와 실제 이동 의도로 변환한다."""

import math

from . import ai_config as config


def face(actor, point):
    dx, dy = point[0] - actor.position.x, point[1] - actor.position.y
    if math.hypot(dx, dy) > 0.05:
        actor.facing = math.atan2(dy, dx)

def turn_toward(current, desired, dt, *, rate=config.SEARCH_TURN_RADIANS_PER_S):
    difference = math.remainder(desired - current, math.tau)
    step = rate * dt
    return current + max(-step, min(step, difference))

def turn_indoor_gaze(actor, brain, previous, rate):
    point = brain.look.point
    if point is None:
        brain.look.turn_rate = 0.0
        return previous
    dx, dy = point[0] - actor.xy[0], point[1] - actor.xy[1]
    if math.hypot(dx, dy) < 0.15:
        brain.look.turn_rate = 0.0
        return previous
    error = math.remainder(math.atan2(dy, dx) - previous, math.tau)
    desired = math.copysign(min(rate, math.sqrt(2 * config.INDOOR_SWEEP_TURN_ACCEL * abs(error))), error)
    acceleration = config.INDOOR_SWEEP_TURN_ACCEL / 60
    change = max(-acceleration, min(acceleration, desired - brain.look.turn_rate))
    velocity = brain.look.turn_rate + change
    step = velocity / 60
    if abs(error) < 1e-6 or (step * error > 0 and abs(step) >= abs(error)):
        brain.look.turn_rate = 0.0
        return previous + error
    brain.look.turn_rate = velocity
    return previous + step


class MotionController:
    def __init__(self, world):
        self.world = world

    def move(self, actor, brain, intent, request, time, budget):
        motion = brain.motion
        goal = request.goal
        motion.pending_goal = None
        if goal is None:
            motion.cancel()
            return budget
        nav = self.world.navigation
        tolerance = 0.045 if request.precise else 0.9 if actor.state == "combat" else 0.15
        changed = motion.goal is None or math.dist(motion.goal, goal) > tolerance
        invalid = motion.path_revision != self.world.revision
        relocated = motion.route_origin is not None and math.dist(actor.xy, motion.route_origin) > 2
        if relocated:
            motion.path.clear()
            motion.path_revision = -1
            invalid = True
        motion.route_origin = actor.xy
        if invalid and not relocated and motion.path:
            points = [actor.xy, *motion.path]
            if all(nav.clear(start, end, ignore_doors=True) for start, end in zip(points, points[1:])):
                motion.path_revision = self.world.revision
                invalid = False
        if motion.path and motion.progress_point is not None and not changed:
            if math.dist(actor.xy, motion.progress_point) >= 0.18:
                motion.progress_at, motion.progress_point = time, actor.xy
            elif time - motion.progress_at >= 0.9:
                motion.path.clear()
                motion.path_revision = -1
                invalid = True
        if budget and (changed or invalid) and time >= motion.plan_at:
            options = nav.nearby_goals(goal, True) if request.approximate else [goal]
            motion.path = []
            for point in options:
                motion.path = nav.route(actor.xy, point, allow_doors=True)
                if motion.path:
                    break
            motion.goal, motion.path_revision, motion.plan_at = goal, self.world.revision, time + 0.2
            motion.route_failed = not motion.path
            motion.progress_at, motion.progress_point = time, actor.xy
            budget -= 1
        elif changed or invalid:
            motion.pending_goal = goal
        if changed and motion.goal != goal and actor.state != "combat":
            return budget
        if invalid and motion.path_revision != self.world.revision:
            return budget
        while motion.path and math.dist(actor.xy, motion.path[0]) < 0.045:
            motion.path.pop(0)
        if (motion.blocked_by and len(motion.path) > 1 and math.dist(actor.xy, motion.path[0]) <= 0.75
                and nav.clear(actor.xy, motion.path[1], ignore_doors=True)):
            # 아군이 밟고 있는 가까운 경유점은 다음 구간이 열려 있으면 건너뛴다.
            motion.path.pop(0)
        if not motion.path:
            return budget
        point = motion.path[0]
        if request.face:
            face(actor, point)
            intent.face_path = True
        intent.direction = point[0] - actor.position.x, point[1] - actor.position.y
        intent.step_dt = min(intent.step_dt, math.hypot(*intent.direction) / (actor.speed * intent.move_multiplier))
        intent.open_door = True
        return budget

    @staticmethod
    def record_step(brain, blocker, time):
        brain.motion.blocked_by = blocker.id if blocker is not None else ""
