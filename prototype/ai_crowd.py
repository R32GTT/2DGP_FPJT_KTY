"""주변 개체만 ORCA에 전달한다. 월드 좌표는 각 병사의 로컬 좌표로 변환한다."""

from collections import defaultdict
import math

from navground import core

from .geometry import Rect


class CrowdMotion:
    def __init__(self, world):
        self.world = world
        self.previous, self.blockers = {}, {}
        self.neighbors = {}
        self.behavior = core.Behavior.make_type("ORCA")
        self.behavior.kinematics = core.Kinematics.make_type("Omni")
        self.behavior.horizon = 0.8

    def apply(self, actors, intents, time, brains, controlled=()):
        cell_size, reach, dt = 4.0, 4.0, 1 / 60
        grid = defaultdict(list)
        living = [actor for actor in actors if actor.hp > 0]
        velocities = {}
        for actor in living:
            before, stamp = self.previous.get(actor.id, (actor.xy, time))
            velocities[actor.id] = tuple((a - b) / max(time - stamp, dt) for a, b in zip(actor.xy, before))
            grid[actor.position.floor, math.floor(actor.xy[0] / cell_size), math.floor(actor.xy[1] / cell_size)].append(actor)
        self.blockers.clear()
        self.neighbors.clear()
        moving = {key for key, intent in intents.items() if any(intent.direction)}
        for actor in living:
            intent = intents.get(actor.id)
            if intent is None or not any(intent.direction):
                continue
            x, y = actor.xy
            cx, cy = math.floor(x / cell_size), math.floor(y / cell_size)
            nearby = [other for i in range(cx - 1, cx + 2) for j in range(cy - 1, cy + 2)
                      for other in grid.get((actor.position.floor, i, j), ())
                      if other is not actor and math.dist(actor.xy, other.xy) < reach]
            self.neighbors[actor.id] = nearby
            motion = brains[actor.id].motion
            # 문 통과와 명시적 양보는 통행 조정기가 정한 경로를 그대로 실행한다.
            if (actor.id in controlled or motion.door_wait or motion.door_stack is not None or motion.yield_point is not None
                    or brains[actor.id].search.task.staging):
                continue
            if not nearby:
                continue
            length = math.hypot(*intent.direction)
            dx, dy = (value / length for value in intent.direction)
            if not all(self.world.navigation.clear(actor.xy, (x + dx * forward - dy * side,
                                                               y + dy * forward + dx * side))
                       for forward in (0, min(0.8, length)) for side in (-0.75, 0.75)):
                continue
            behavior = self.behavior
            reload_scale = actor.weapon.spec.reload_move_multiplier if actor.weapon and actor.weapon.reloading else 1
            speed = min(actor.speed * intent.move_multiplier * reload_scale,
                        length / max(intent.step_dt, 1e-8))
            behavior.position, behavior.orientation = (0, 0), 0
            behavior.velocity, behavior.radius = velocities[actor.id], actor.radius
            behavior.kinematics.max_speed = behavior.optimal_speed = speed
            behavior.target = core.Target(direction=intent.direction, speed=speed)
            state = behavior.environment_state
            neighbors, obstacles = [], []
            for index, other in enumerate(nearby):
                position = (other.xy[0] - x, other.xy[1] - y)
                if other.id in moving:
                    neighbors.append(core.Neighbor(position, other.radius, velocities[other.id], index + 1))
                else:
                    obstacles.append(core.Disc(position, other.radius))
            state.neighbors, state.static_obstacles = neighbors, obstacles
            lines = []
            for collider in self.world.geometry.query(Rect(x - reach, y - reach, reach * 2, reach * 2)):
                rect = collider.rect
                points = [(rect.x - x, rect.y - y), (rect.right - x, rect.y - y),
                          (rect.right - x, rect.bottom - y), (rect.x - x, rect.bottom - y)]
                lines.extend(core.LineSegment(a, b) for a, b in zip(points, points[1:] + points[:1]))
            state.line_obstacles = lines
            velocity = behavior.compute_cmd(intent.step_dt, frame=core.Frame.absolute).velocity
            if math.hypot(*velocity) < 0.05 and speed > 0.1:
                self.blockers[actor.id] = min(nearby, key=lambda other: math.dist(actor.xy, other.xy))
            intent.direction = tuple(float(value) for value in velocity)
            intent.move_multiplier = math.hypot(*velocity) / (actor.speed * reload_scale)
        self.previous = {actor.id: (actor.xy, time) for actor in living}
