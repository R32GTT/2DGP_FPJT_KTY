"""재사용 가능한 로컬 순찰·관측·마지막 관측 지점 추적. 그리기·입력과 독립."""

from dataclasses import dataclass, field
import math


@dataclass
class Brain:
    patrol: list[tuple[float, float]]
    waypoint: int = 0
    visible: bool = False
    last_seen: tuple | None = None
    memory_until: float = 0.0
    sense_at: float = 0.0
    plan_at: float = 0.0
    path: list = field(default_factory=list)


class PatrolAI:
    def __init__(self, world, actors, patrols):
        self.world = world
        self.brains = {actor.id: Brain([tuple(point) for point in patrol], sense_at=index * 0.07)
                       for index, (actor, patrol) in enumerate(zip(actors, patrols))}

    def update(self, actors, player, time):
        intents, planning_budget = {}, 1
        for actor in actors:
            if actor.hp <= 0:
                continue
            brain = self.brains[actor.id]
            if time >= brain.sense_at:
                brain.sense_at = time + 0.15
                dx, dy = player.position.x - actor.position.x, player.position.y - actor.position.y
                distance = math.hypot(dx, dy)
                within_cone = (distance < 1.5 or (dx * math.cos(actor.facing) + dy * math.sin(actor.facing))
                               >= distance * 0.5)
                brain.visible = distance <= 14 and within_cone and self.world.geometry.raycast(actor.xy, player.xy) is None
                if brain.visible:
                    brain.last_seen, brain.memory_until = player.xy, time + 2.0
            chasing = brain.last_seen is not None and time < brain.memory_until
            actor.state = "chase" if chasing else "patrol"
            goal = brain.last_seen if chasing else brain.patrol[brain.waypoint]
            if not chasing and math.dist(actor.xy, goal) < 0.35:
                brain.waypoint = (brain.waypoint + 1) % len(brain.patrol)
                goal = brain.patrol[brain.waypoint]
                brain.path.clear()
                brain.plan_at = 0
            if time >= brain.plan_at and planning_budget:
                brain.path = self.world.navigation.route(actor.xy, goal)
                brain.plan_at = time + 0.6
                planning_budget -= 1
            while brain.path and math.dist(actor.xy, brain.path[0]) < 0.16:
                brain.path.pop(0)
            if brain.path:
                dx, dy = brain.path[0][0] - actor.position.x, brain.path[0][1] - actor.position.y
                actor.facing = math.atan2(dy, dx)
                intents[actor.id] = (dx, dy)
            else:
                intents[actor.id] = (0, 0)
        return intents
