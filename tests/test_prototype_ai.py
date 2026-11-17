import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from campaign.models import Position
from prototype.actors import Actor
from prototype.ai import EnemyAI, Incident, SearchCue
from prototype.combat import Combat
from prototype.events import EventBus
from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.indoor_search import IndoorSweep, SearchArea
from prototype.movement import move_actor
from prototype.navigation import Navigation
from prototype.simulation import PlaySimulation
from prototype.weapons import WeaponState, load_weapons


DOOR_SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_door_queue.json"
FIELD_SCENE = DOOR_SCENE.with_name("field_proving_ground.json")


def field_approach(count, y=-65, distance=15):
    simulation = PlaySimulation(FIELD_SCENE)
    guards = simulation.guards[:count]
    for other in simulation.guards[count:]:
        other.hp = 0
    simulation.player.position = Position(0, y)
    for index, guard in enumerate(guards):
        guard.position = Position(-distance - 0.47 * (index % 3), y + 0.46 * (index // 3))
        guard.facing = 0
    return simulation, guards


class PrototypeAITests(unittest.TestCase):
    def test_squad_updates_its_stops_when_player_steps_out_of_range(self):
        simulation, guards = field_approach(2)
        with patch.object(simulation.combat, "fire"):
            for _ in range(30):
                simulation.step()
            simulation.player.position.x += 1
            for _ in range(330):
                simulation.step()
        self.assertTrue(all(math.dist(guard.xy, simulation.player.xy) <= 13 for guard in guards))
        self.assertGreater(abs(guards[0].xy[1] - guards[1].xy[1]), 2)

    def test_squad_recovers_spread_after_leaving_a_narrow_corridor(self):
        geometry = SpatialIndex()
        geometry.add(Collider("north", Rect(-2, -2, 3.15, 1.55)))
        geometry.add(Collider("south", Rect(-2, 0.45, 3.15, 1.55)))
        bounds = Rect(-2, -10, 30, 20)
        world = SimpleNamespace(geometry=geometry, bounds=bounds, revision=0,
                                navigation=Navigation({}, [], geometry, bounds=bounds),
                                door_on_ai_path=lambda *_: None)
        spec = load_weapons()["ar_a"]
        guards = [Actor(str(index), Position(-0.47 * index, 0), source_unit="squad", active_weapon="ar_a",
                        loadout={"ar_a": WeaponState.ready(spec)}) for index in range(2)]
        player = Actor("player", Position(15.5, 0), team="player")
        ai = EnemyAI(world, guards, [[], []])
        for frame in range(1, 140):
            intents = ai.update(guards, player, frame / 60)
            for guard in guards:
                intent = intents[guard.id]
                move_actor(guard, intent.direction, world, intent.step_dt, neighbors=guards)
            if frame == 5:
                plan, = ai.approaches.values()
                self.assertFalse(any(plan.orders.values()))
        self.assertGreater(abs(guards[0].xy[1] - guards[1].xy[1]), 1)

    def test_distant_door_does_not_restart_an_investigation_approach(self):
        simulation, guards = field_approach(2, distance=11)
        simulation.player.hp = 0
        simulation.events.emit("noise", 0, simulation.player, radius=15, strength=0.8, reason="gunshot")
        for _ in range(200):
            simulation.step()
        plan, = simulation.ai.approaches.values()
        self.assertTrue(all(order.passed for order in plan.orders.values()))
        door = next(item for item in simulation.world.geometry.objects.values() if item.kind == "door")
        center = (door.rect.x + door.rect.w / 2, door.rect.y + door.rect.h / 2)
        self.assertGreater(math.dist(center, simulation.player.xy), 20)
        self.assertIsNotNone(simulation.world.toggle_door(center))
        previous = [guard.xy[0] for guard in guards]
        for _ in range(20):
            simulation.step()
        self.assertTrue(all(guard.xy[0] > x for guard, x in zip(guards, previous)))

    def test_regroup_provides_positions_for_every_member_of_a_large_squad(self):
        geometry = SpatialIndex()
        bounds = Rect(-30, -30, 60, 60)
        world = SimpleNamespace(geometry=geometry, revision=0, navigation=Navigation({}, [], geometry, bounds=bounds))
        center = (0, 0)
        area = SearchArea("room", (bounds,), (center,), {center: 0}, {0: (center,)}, {0: ()})
        sweep = IndoorSweep(area, center, 0)
        sweep.confirm(center, 0)
        guards = [Actor(str(index), Position(15 + index * 0.5, 0)) for index in range(26)]
        ai = EnemyAI(world, guards, [[] for _ in guards])
        ai.incidents["search"] = Incident(0, 0, center, indoor=sweep)
        for guard in guards:
            ai.brains[guard.id].incident = "search"

        ai._update_regroups(guards, 1)
        self.assertEqual(len(sweep.regroup_slots), len(guards))
        for guard in guards[:-1]:
            guard.position = Position(*sweep.regroup_slots[guard.id])
        ai._update_regroups(guards, 3)
        self.assertIsNone(sweep.regroup_until)
        guards[-1].position = Position(*sweep.regroup_slots[guards[-1].id])
        ai._update_regroups(guards, 4)
        ai._update_regroups(guards, 5)
        self.assertTrue(sweep.regroup_done)

    def test_navigation_rejects_a_clear_goal_outside_the_map(self):
        nav = Navigation({}, [], SpatialIndex(), bounds=Rect(0, 0, 10, 8))
        self.assertEqual(nav.route((9, 4), (11, 4)), [])
        self.assertEqual(nav.route((9, 4), (9.7, 4)), [(9.7, 4)])

    def test_reported_threat_can_be_used_to_check_support_without_direct_sighting(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        guard = Actor("guard", Position(0, 0), active_weapon="ar_a")
        support = Actor("support", Position(1, 0), active_weapon="ar_a")
        spec = load_weapons()["ar_a"]
        for actor in (guard, support):
            actor.loadout["ar_a"] = WeaponState.ready(spec)
        ai = EnemyAI(world, [guard, support], [[], []])
        brain, ally = ai.brains[guard.id], ai.brains[support.id]
        brain.incident = ally.incident = "report"
        brain.role, brain.memory.reported = "intercept", (8, 0)
        ally.memory.visible, ally.action, ally.fired_at = True, "fire", 0.8

        ai._combat_action(guard, brain, [guard, support], brain.memory.reported, 1.0)

        self.assertIsNone(brain.memory.last_seen)
        self.assertEqual(brain.action, "flank")

    def test_visible_squad_spreads_early_and_stops_at_distinct_positions(self):
        for count, y in ((1, -65), (2, -65), (6, -65), (2, -79.65)):
            with self.subTest(count=count, y=y):
                simulation, guards = field_approach(count, y)

                # 사격 피해 없이 지각부터 실제 이동까지 비교한다.
                with patch.object(simulation.combat, "fire"):
                    for _ in range(30):
                        simulation.step()
                    if count == 1:
                        self.assertEqual(simulation.ai.brains[guards[0].id].path, [simulation.player.xy])
                        self.assertEqual(guards[0].xy[1], y)
                        continue

                    plan, = simulation.ai.approaches.values()
                    destinations = [plan.orders[guard.id].destination for guard in guards]
                    self.assertEqual(len(set(destinations)), count)
                    early_width = max(guard.xy[1] for guard in guards) - min(guard.xy[1] for guard in guards)
                    self.assertGreater(early_width, 1.0)
                    self.assertTrue(all(math.dist(guard.xy, point) > 0.5
                                        for guard, point in zip(guards, destinations)))
                    for _ in range(360):
                        simulation.step()

                self.assertTrue(all(math.dist(guard.xy, point) < 0.1
                                    for guard, point in zip(guards, destinations)))

    def test_investigators_spread_before_checking_the_same_clue(self):
        for reason in ("gunshot", "footstep", "seen"):
            with self.subTest(reason=reason):
                simulation, (front, following) = field_approach(2, distance=15 if reason == "seen" else 11)
                player = simulation.player
                clue = player.xy
                with patch.object(simulation.combat, "fire"):
                    if reason == "seen":
                        for _ in range(30):
                            simulation.step()
                        player.position = Position(20, clue[1])
                    else:
                        player.hp = 0
                        simulation.events.emit("noise", 0, player, radius=15, strength=0.8, reason=reason)
                    for _ in range(90):
                        simulation.step()

                    self.assertGreater(abs(front.xy[1] - following.xy[1]), 2.0)
                    for guard in (front, following):
                        brain = simulation.ai.brains[guard.id]
                        self.assertFalse(brain.visible)
                        self.assertIn(guard.state, {"alert", "search"})
                        self.assertNotEqual(brain.goal, clue)
                        self.assertIn(clue, brain.search_points)
                    for _ in range(360):
                        simulation.step()

                for guard in (front, following):
                    brain = simulation.ai.brains[guard.id]
                    if reason != "footstep":
                        self.assertTrue(brain.priority_checked)
                    else:
                        self.assertNotIn(clue, brain.search_points)

    def test_gunshot_stays_in_focus_while_route_bends_away(self):
        simulation = PlaySimulation(DOOR_SCENE)
        guard = simulation.guards[0]
        simulation.player.hp = 0
        cue = (9, 9)
        brain = simulation.ai.brains[guard.id]
        brain.combat_alert_until = 60
        brain.priority_cue = SearchCue("sound", cue, 0)

        for tick in range(1, 31):
            intent = simulation.ai.update([guard], simulation.player, tick / 60)[guard.id]

        self.assertGreater(intent.direction[0], 0)
        self.assertNotEqual(brain.path[0], cue)
        self.assertEqual(brain.look_point, cue)
        self.assertAlmostEqual(guard.facing, math.atan2(cue[1] - guard.xy[1], cue[0] - guard.xy[0]))

    def test_searchers_can_pass_an_occupied_door_in_opposite_directions(self):
        for start, opposite in ((6.2, 7.88), (6.8, 7.48)):
            with self.subTest(start=start, opposite=opposite):
                simulation = PlaySimulation(DOOR_SCENE)
                simulation.player.hp = 0
                front, following = simulation.guards
                front.position, following.position = Position(start, 6), Position(opposite, 6)
                for actor, goal in ((front, (10, 6)), (following, (4, 6))):
                    brain = simulation.ai.brains[actor.id]
                    brain.incident = "door_test"
                    brain.combat_alert_until = 60
                    brain.priority_cue = SearchCue("seen", goal, 0)

                passed = set()
                while simulation.time < 5 and len(passed) < 2:
                    simulation.step()
                    if front.xy[0] >= 9:
                        passed.add(front.id)
                    if following.xy[0] <= 5:
                        passed.add(following.id)

                self.assertEqual(passed, {front.id, following.id})

    def test_investigators_stack_along_the_wall_then_pass(self):
        simulation = PlaySimulation(DOOR_SCENE, stack_drill=True)
        front, following = simulation.guards
        door = simulation.world.geometry.objects["shared_door"]
        while simulation.time < 1 and not simulation.ai.door_traffic.stacks:
            simulation.step()

        stack = simulation.ai.door_traffic.stacks["shared_door"]
        first, second = stack.slots[front.id], stack.slots[following.id]
        self.assertLess(first[0], door.rect.x)
        self.assertAlmostEqual(first[0], second[0])
        self.assertLess(first[1], door.rect.y)
        self.assertGreater(second[1], door.rect.bottom)
        while simulation.time < 1.5 and stack.ready_at is None:
            simulation.step()

        self.assertIsNotNone(stack.ready_at)
        self.assertTrue(door.active)
        self.assertLess(math.dist(front.xy, first), 0.06)
        self.assertLess(math.dist(following.xy, second), 0.06)
        center_y = door.rect.y + door.rect.h / 2
        for actor in simulation.guards:
            look_x, look_y = simulation.ai.brains[actor.id].look_point
            self.assertGreater(look_x, door.rect.right)
            self.assertLess((look_y - center_y) * (actor.xy[1] - center_y), 0)
            expected = math.atan2(look_y - actor.xy[1], look_x - actor.xy[0])
            self.assertLessEqual(abs(math.remainder(actor.facing - expected, math.tau)), math.radians(5))
        self.assertGreater(front.facing, 0)
        self.assertLess(following.facing, 0)

        observations = {}
        while simulation.time < 3 and len(observations) < len(simulation.guards):
            simulation.step()
            for actor in simulation.guards:
                brain = simulation.ai.brains[actor.id]
                if brain.indoor_target is not None and brain.path:
                    observations.setdefault(actor.id, brain.indoor_target)

        self.assertTrue(all(actor.xy[0] > door.rect.right + actor.radius for actor in simulation.guards))
        self.assertEqual(set(observations), {front.id, following.id})
        first_observation, next_observation = observations[front.id], observations[following.id]
        self.assertLess(first_observation[0], simulation.world.data["stack_drill"]["sound"][0])
        self.assertGreater(abs(first_observation[1] - center_y), door.rect.h / 2)
        self.assertGreater(abs(first_observation[1] - next_observation[1]), door.rect.h)
        self.assertFalse(simulation.ai.door_traffic.stacks)
        self.assertFalse(door.active)
        self.assertEqual(simulation.player.hp, 100)

    def test_each_door_drill_layout_assembles_and_passes(self):
        layouts = ((1, 1), (2, 0), (3, 0), (2, 1), (1, 2), (4, 0), (3, 1), (2, 2), (1, 3))
        for index, (left, right) in enumerate(layouts):
            with self.subTest(left=left, right=right):
                simulation = PlaySimulation(DOOR_SCENE, stack_drill=True, stack_variant=index)
                self.assertEqual(len(simulation.guards), left + right)
                stack = None
                while simulation.time < 1.5:
                    simulation.step()
                    stack = simulation.ai.door_traffic.stacks.get("shared_door")
                    if stack is not None and stack.ready_at is not None:
                        break

                self.assertIsNotNone(stack)
                self.assertIsNotNone(stack.ready_at)
                self.assertEqual(sum(point[1] < 6 for point in stack.slots.values()), left)
                self.assertEqual(sum(point[1] > 6 for point in stack.slots.values()), right)
                self.assertEqual(len(set(stack.slots.values())), left + right)
                door = simulation.world.geometry.objects["shared_door"]
                area = simulation.world.search_areas[0]
                self.assertGreaterEqual(len(area.points), 2 * 28)
                corners = set().union(*area.room_corners.values())
                lead = simulation.guards[0]
                first_looks = {}
                internal_looks = set()
                passage_order = []
                at_entry_positions = None
                scanned_while_staging = set()
                moved_during_followup = False
                opened_at = passed_at = None
                all_passed = False
                moving_scan = False
                points_finished_before_area = False
                sweep = None
                while simulation.time < 15:
                    before = {actor.id: actor.xy for actor in simulation.guards}
                    was_open = not door.active
                    simulation.step()
                    if not was_open and not door.active:
                        opened_at = simulation.time
                    for actor in simulation.guards:
                        if actor.xy[0] >= door.rect.right and actor.id not in passage_order:
                            passage_order.append(actor.id)
                        look = simulation.ai.brains[actor.id].look_point
                        if was_open and actor.id not in first_looks and look in area.points:
                            first_looks[actor.id] = look
                            self.assertIsNone(simulation.world.geometry.raycast(before[actor.id], look, category="sight"))
                            if actor is lead:
                                self.assertLess(before[lead.id][0], door.rect.right)
                                self.assertGreater(lead.xy[0], before[lead.id][0])
                    if not all_passed and all(actor.xy[0] > door.rect.right + actor.radius for actor in simulation.guards):
                        all_passed = True
                        passed_at = simulation.time
                    moving_scan |= any(actor.xy != before[actor.id]
                                       and simulation.ai.brains[actor.id].look_reason == "이동 중 공간 확인"
                                       for actor in simulation.guards)
                    sweep = next((incident.indoor for incident in simulation.ai.incidents.values()
                                  if incident.indoor is not None), None)
                    if sweep is not None:
                        points_finished_before_area |= len(sweep.checked) == len(area.points) and not sweep.complete
                        if sweep.entry_ready and at_entry_positions is None:
                            at_entry_positions = before
                        owners = {owner for owner, _ in sweep.corner_checks.values()}
                        pending_inside = set(area.points) - corners - sweep.checked
                        for actor in simulation.guards:
                            brain = simulation.ai.brains[actor.id]
                            look = brain.look_point
                            if not sweep.entry_ready:
                                self.assertNotIn(brain.indoor_target, area.observations)
                                if brain.indoor_staging and actor.xy != before[actor.id] and look in area.points:
                                    scanned_while_staging.add(actor.id)
                            else:
                                moved_during_followup |= (brain.indoor_target is not None and look is not None
                                                          and actor.xy != before[actor.id])
                            if actor.id in owners and pending_inside:
                                self.assertNotIn(look, corners)
                                if look in area.points:
                                    internal_looks.add(actor.id)
                    if all_passed and sweep is not None and sweep.entry_ready and sweep.complete:
                        break

                self.assertTrue(all_passed)
                self.assertIsNotNone(opened_at)
                self.assertLess(passed_at - opened_at, 3.3)
                self.assertIsNotNone(sweep)
                self.assertEqual(sweep.checked, set(sweep.area.points))
                self.assertTrue(sweep.complete)
                if (left, right) == (1, 1):
                    self.assertTrue(points_finished_before_area)
                    self.assertTrue(moved_during_followup)
                self.assertIsNotNone(at_entry_positions)
                self.assertTrue(moving_scan)
                first, second = passage_order[:2]
                self.assertIn(first, first_looks)
                self.assertIn(second, first_looks)
                self.assertIn(first_looks[first], corners)
                self.assertIn(first_looks[second], corners)
                self.assertLess((first_looks[first][1] - 6) * (first_looks[second][1] - 6), 0)
                for actor_id in (first, second):
                    owner, _ = sweep.corner_checks[first_looks[actor_id]]
                    self.assertEqual(owner, actor_id)
                    self.assertIn(actor_id, internal_looks)
                    self.assertIn(actor_id, scanned_while_staging)
                expected_order = [passage_order[rank] for rank in (1, 2, 3, 0) if rank < len(passage_order)]
                self.assertEqual(sorted(at_entry_positions, key=lambda actor_id: at_entry_positions[actor_id][1]),
                                 expected_order)
                for actor_id in passage_order:
                    self.assertLessEqual(math.dist(at_entry_positions[actor_id], sweep.entry_pose(actor_id).position), 0.10)

    def test_solo_door_drill_moves_from_its_first_corner_to_the_room_interior(self):
        simulation = PlaySimulation(DOOR_SCENE, stack_drill=True, stack_variant=9)
        guard, = simulation.guards
        brain = simulation.ai.brains[guard.id]
        area, = simulation.world.search_areas
        corners = set().union(*area.room_corners.values())
        saw_inside = False
        while simulation.time < 15:
            simulation.step()
            sweep = brain.indoor
            if sweep is None:
                continue
            if sweep.corner_checks and set(area.points) - corners - sweep.checked:
                self.assertNotIn(brain.look_point, corners)
                saw_inside |= brain.look_point in area.points
            if sweep.entry_ready and sweep.complete:
                break

        self.assertTrue(sweep.entry_ready)
        self.assertTrue(sweep.complete)
        self.assertTrue(saw_inside)
        self.assertEqual(sweep.checked, set(area.points))
        self.assertFalse(simulation.ai.door_traffic.stacks)

    def test_new_gunshot_releases_stack(self):
        simulation = PlaySimulation(DOOR_SCENE, stack_drill=True)
        while simulation.time < 1 and not simulation.ai.door_traffic.stacks:
            simulation.step()
        self.assertIn("shared_door", simulation.ai.door_traffic.stacks)

        source = Actor("new_sound", Position(3, 6), team="player")
        simulation.events.emit("noise", simulation.time, source,
                               radius=30, strength=0.8, reason="gunshot")
        simulation.step()

        self.assertFalse(simulation.ai.door_traffic.stacks)
        self.assertTrue(simulation.world.geometry.objects["shared_door"].active)
        for actor in simulation.guards:
            brain = simulation.ai.brains[actor.id]
            self.assertIsNone(brain.door_stack)
            self.assertEqual(brain.priority_cue.point, (3, 6))

    def test_gunshot_replaces_a_patrol_route_while_waiting_at_a_door(self):
        simulation = PlaySimulation(DOOR_SCENE)
        simulation.player.hp = 0
        waiter = None
        while waiter is None and simulation.time < 2:
            simulation.step()
            waiter = next((actor for actor in simulation.guards if simulation.ai.brains[actor.id].door_wait), None)
        self.assertIsNotNone(waiter)
        brain = simulation.ai.brains[waiter.id]
        self.assertEqual(waiter.state, "patrol")
        old_goal = brain.goal

        simulation.player.position = Position(4, 4)
        simulation.events.emit("noise", simulation.time, simulation.player,
                               radius=30, strength=0.8, reason="gunshot")
        simulation.step()

        self.assertEqual(brain.priority_cue.kind, "sound")
        self.assertNotEqual(brain.goal, old_goal)
        self.assertFalse(brain.door_wait)
        self.assertEqual(waiter.state, "search")

    def test_peek_keeps_its_direction_and_can_fire_after_reacquiring_sight(self):
        geometry = SpatialIndex()
        geometry.add(Collider("wall", Rect(4, 2.5, 0.2, 1)))
        world = SimpleNamespace(geometry=geometry, revision=0, door_on_ai_path=lambda actor, point: None,
                                navigation=Navigation({}, [], geometry))
        guard = Actor("guard", Position(2, 3), facing=math.pi / 2, state="retreat")
        guard.loadout["ar_a"] = WeaponState.ready(load_weapons()["ar_a"])
        guard.active_weapon = "ar_a"
        player = Actor("player", Position(6, 3), team="player")
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        brain.combat_alert_until = 60
        brain.memory.last_seen, brain.memory.seen_at = player.xy, 0.1
        brain.peek_home = guard.xy
        brain.action, brain.action_goal, brain.action_until = "peek", (2, 4.8), 1.01
        brain.fire_at = 1.2

        initial = ai.update([guard], player, 0.35)[guard.id]
        self.assertEqual(initial.direction, (0, 0))
        self.assertLess(guard.facing, math.pi / 2)
        for tick in range(22, 55):
            move = ai.update([guard], player, tick / 60)[guard.id]
        self.assertEqual(brain.action, "peek")
        self.assertGreater(move.direction[1], 0)
        self.assertEqual(guard.facing, 0)

        guard.position = Position(2, 4.8)
        brain.sense_at = 0
        ai.update([guard], player, 1.02)
        self.assertTrue(brain.visible)
        self.assertEqual(brain.action, "peek")
        self.assertTrue(ai.update([guard], player, 1.2)[guard.id].fire)

        combat = Combat(EventBus())
        combat.prepare([guard, player], 1 / 60)
        self.assertTrue(combat.clear_fire_line(guard, player, world))
        self.assertTrue(combat.fire(guard, world, 1.2))

    def test_cover_recovery_keeps_priority_search_from_pulling_guard_out(self):
        geometry = SpatialIndex()
        geometry.add(Collider("wall", Rect(4, 2.5, 0.2, 1)))
        bounds = Rect(0, 0, 12, 10)
        world = SimpleNamespace(geometry=geometry, bounds=bounds, revision=0,
                                navigation=Navigation({}, [], geometry, bounds=bounds),
                                door_on_ai_path=lambda *_: None)
        home = (2, 3.75)
        guard = Actor("guard", Position(*home), state="retreat", active_weapon="ar_a")
        guard.loadout["ar_a"] = WeaponState.ready(load_weapons()["ar_a"])
        player = Actor("player", Position(6, 3), team="player")
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        brain.action, brain.action_goal = "cover", home
        brain.cover_threat, brain.cover_revision = player.xy, world.revision
        brain.suppression, brain.combat_alert_until = 0.53, 60
        brain.memory.last_seen, brain.memory.seen_at = player.xy, 0
        brain.priority_cue = SearchCue("seen", player.xy, 0)

        def step(frame):
            intent = ai.update([guard], player, frame / 60)[guard.id]
            move_actor(guard, intent.direction, world, intent.step_dt,
                       speed=guard.speed * intent.move_multiplier, allow_slide=not intent.move_straight)
            return intent

        for frame in range(1, 36):
            step(frame)
            self.assertEqual((guard.state, brain.action, guard.xy), ("retreat", "cover", home))
        for frame in range(36, 120):
            intent = step(frame)
            self.assertIn(brain.action, {"cover", "peek"})
            if intent.fire:
                break
        else:
            self.fail("제압에서 회복한 NPC가 피킹 중 시야를 되찾아 사격하지 못함")
        self.assertTrue(brain.visible)
        self.assertEqual(brain.action, "peek")

        brain.suppression = 0.56
        step(frame + 1)
        self.assertEqual(brain.action, "peek_return")
        for frame in range(frame + 2, frame + 60):
            step(frame)
            self.assertEqual(guard.state, "retreat")
            if brain.action == "cover":
                break
        else:
            self.fail("다시 제압된 NPC가 엄폐로 복귀하지 못함")
        recovered_position = guard.xy
        for frame in range(frame + 1, math.ceil(brain.peek_at * 60)):
            step(frame)
            self.assertEqual((guard.state, brain.action, guard.xy), ("retreat", "cover", recovered_position))
