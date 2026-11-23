"""전투 행동과 엄폐 이탈·복귀를 관리한다."""

import math

from . import ai_config as config


class CombatController:
    def __init__(self, world, brains, cover_finder):
        self.world, self.brains, self.cover_finder = world, brains, cover_finder

    def has_support(self, actor, brain, actors, threat, time):
        return any(other.id != actor.id and other.hp > 0
                   and other.position.floor == actor.position.floor
                   and (ally := self.brains[other.id]).incident == brain.incident
                   and ally.combat.action in {"advance", "fire"} and ally.visible
                   and time - ally.combat.fired_at < 1.5
                   and other.weapon is not None and other.weapon.chambered and not other.weapon.reloading
                   and ally.combat.suppression < 0.55 and math.dist(other.xy, actor.xy) <= 18
                   and math.dist(other.xy, threat) <= other.weapon.spec.range_m
                   for other in actors)

    def record_blocked_shot(self, actor, threat, time, clear_from):
        combat = self.brains[actor.id].combat
        if combat.blocked_fire_at is None:
            combat.blocked_fire_at = time
            return
        if (time - combat.blocked_fire_at < 0.4 or combat.action == "pin"
                or (combat.action == "reposition" and time < combat.action_until)):
            return
        goal = self.cover_finder.peek(actor, threat, clear_from=clear_from)
        if goal is None and combat.action in {"advance", "flank"}:
            combat.blocked_fire_at = time
            return
        combat.action, combat.action_goal = ("reposition", goal) if goal is not None else ("wait", None)
        combat.action_until = time + (math.dist(actor.xy, goal) / actor.speed + 0.5 if goal is not None else 0.5)
        combat.blocked_fire_at = None

    def record_shot(self, actor, time):
        combat = self.brains[actor.id].combat
        combat.blocked_fire_at = None
        burst_size = 2 if combat.suppression >= 0.3 or combat.action == "peek" else 4
        combat.burst = (combat.burst + 1) % burst_size
        delay = 0.20 if combat.burst else 0.58
        combat.fire_at = time + delay * (1 + combat.suppression * 2)

    def cover_exposed(self, brain, threat):
        combat = brain.combat
        home = combat.peek_home or combat.action_goal
        return home is not None and (
            combat.cover_threat != threat or combat.cover_revision != self.world.revision
        ) and self.world.geometry.raycast(threat, home, category="bullet") is None

    def choose(self, actor, brain, actors, threat, time):
        combat = brain.combat
        weapon = actor.weapon
        empty = weapon is None or (not weapon.chambered and weapon.stage < 2 and weapon.reserve <= 0)
        urgent = empty or actor.hp < 20 or actor.hurt_until - time > 1.75 or combat.suppression >= 0.55
        route_failed = combat.action_goal is not None and combat.action_goal == brain.motion.goal and brain.motion.route_failed
        safe_action = combat.action in {"cover", "retreat", "pin"}
        needs_reload = weapon is not None and (weapon.reloading or not weapon.chambered)
        if combat.action in {"reposition", "retreat"} and combat.action_goal is not None and math.dist(actor.xy, combat.action_goal) <= 0.15:
            combat.action_until = time
        if combat.action == "cover" and combat.action_goal is not None and not route_failed:
            if self.cover_exposed(brain, threat):
                route_failed = True
            else:
                combat.cover_threat = threat
                combat.cover_revision = self.world.revision
                if (math.dist(actor.xy, combat.action_goal) > 0.3 or (needs_reload and not empty) or urgent
                        or combat.suppression >= config.PEEK_SUPPRESSION_LIMIT or time < combat.peek_at):
                    return

        elif (time < combat.action_until and not route_failed
                and (combat.action != "flank" or (math.dist(actor.xy, combat.action_goal) > 0.35
                                                and self.has_support(actor, brain, actors, threat, time)))
                and (not urgent or safe_action) and (not needs_reload or safe_action or combat.action == "reload")):
            return
        rejected = combat.action_goal if route_failed else None
        combat.action_goal = None
        if urgent or needs_reload:
            occupied = [ally.combat.action_goal for other in actors if other.id != actor.id and other.hp > 0
                        and other.team == actor.team and other.position.floor == actor.position.floor
                        and (ally := self.brains[other.id]).combat.action in {"cover", "retreat"}
                        and ally.combat.action_goal is not None and other.state == "retreat"]
            if rejected is not None:
                occupied.append(rejected)
            cover = self.cover_finder.choose(actor, threat, occupied)
            if cover is not None:
                combat.action, combat.action_goal = "cover", cover
                combat.cover_threat = threat
                combat.cover_revision = self.world.revision
            elif not urgent:
                combat.action = "reload"
            else:
                retreat = self.cover_finder.withdraw(actor, threat, occupied)
                if retreat is None and (empty or actor.hp < 20):
                    retreat = max((point for point in brain.patrol
                                   if point != rejected and math.dist(point, threat) > math.dist(actor.xy, threat)),
                                  key=lambda point: math.dist(point, threat), default=None)
                combat.action, combat.action_goal = ("retreat", retreat) if retreat else ("pin", None)
            combat.action_until = time + 1.65
            return

        distance = math.dist(actor.xy, threat)
        combat.action, combat.action_until = "fire", time + 1.3
        if combat.formation is not None:
            anchor, destination = combat.formation
            if (math.dist(anchor, threat) <= 1.5 and self.world.navigation.walkable(destination)
                    and not brain.motion.failed(destination, self.world.revision)):
                if math.dist(actor.xy, destination) > 0.12:
                    combat.action, combat.action_goal = "advance", destination
                return
            combat.formation = None
        if distance > config.ADVANCE_DISTANCE:
            combat.action, combat.action_goal = "advance", threat
            return
        if distance <= 3:
            return
        if not self.has_support(actor, brain, actors, threat, time):
            return
        dx, dy = threat[0] - actor.position.x, threat[1] - actor.position.y
        nav = self.world.navigation
        for side in ((1, -1) if sum(actor.id.encode("utf-8")) % 2 else (-1, 1)):
            for lateral in (4, 6):
                point = (actor.position.x + (dx - side * dy * lateral) / distance,
                         actor.position.y + (dy + side * dx * lateral) / distance)
                if (point != rejected and nav.walkable(point)
                        and nav.clear(actor.xy, point, ignore_doors=True)
                        and self.world.geometry.raycast(point, threat, category="bullet") is None):
                    combat.action, combat.action_goal, combat.action_until = "flank", point, time + 3.2
                    return

    def peek(self, actor, brain, threat, time):
        combat = brain.combat
        if threat is not None and combat.action in {"cover", "peek", "peek_return"} and self.cover_exposed(brain, threat):
            combat.peek_home = None
            combat.action = "cover"
            return False
        if combat.peek_home is not None:
            if combat.action == "peek" and brain.visible and not combat.peek_contact:
                combat.peek_contact = True
                combat.action_until = max(combat.action_until, max(combat.fire_at, time) + 0.2)
            if combat.action == "peek" and (time >= combat.action_until or combat.suppression >= 0.55
                                          or actor.hp < 20
                                          or actor.weapon is None or not actor.weapon.chambered
                                          or (brain.motion.goal == combat.action_goal and brain.motion.route_failed)):
                combat.action, combat.action_goal = "peek_return", combat.peek_home
            if (combat.action == "peek_return" and brain.motion.failed(combat.peek_home, self.world.revision)):
                combat.peek_home = None
                return False
            if combat.action == "peek_return" and math.dist(actor.xy, combat.peek_home) <= 0.3:
                combat.action, combat.action_goal = "cover", combat.peek_home
                combat.peek_home = None
                combat.peek_at = time + config.PEEK_RECOVERY_SECONDS
                return False
            return True
        weapon = actor.weapon
        if (threat is None or combat.action != "cover" or combat.action_goal is None
                or math.dist(actor.xy, combat.action_goal) > 0.3 or time < combat.peek_at
                or combat.suppression >= config.PEEK_SUPPRESSION_LIMIT or actor.hp < 20
                or weapon is None or not weapon.chambered or weapon.reloading):
            return False
        point = self.cover_finder.peek(actor, threat)
        if point is None:
            return False
        combat.peek_home = combat.action_goal
        combat.peek_contact = brain.visible
        combat.peek_departed = False
        combat.action, combat.action_goal, combat.action_until = "peek", point, time + 1.65
        return True

    def continue_cover(self, actor, brain, actors, threat, time):
        if self.peek(actor, brain, threat, time):
            return True
        self.choose(actor, brain, actors, threat, time)
        return brain.combat.action in {"cover", "retreat", "pin", "reload"}
