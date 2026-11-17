import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from campaign.models import Position
from prototype.actors import Actor
from prototype.ai import EnemyAI, Intent
from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.indoor_search import IndoorSweep, build_search_areas, sample_views
from prototype.navigation import Navigation
from prototype.simulation import PlaySimulation


FACILITY_SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_facility.json"
KILLHOUSE_SCENE = FACILITY_SCENE.with_name("ai_killhouse.json")


class IndoorSearchTests(unittest.TestCase):
    def test_pending_view_keeps_shared_occlusion_separate_from_confirmation(self):
        geometry = SpatialIndex()
        geometry.add(Collider("partition", Rect(1.9, 3, 3.4, 0.3)))
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 7.2, 7.2]]}},
                                facilities=[], geometry=geometry, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        sample = min(area.points, key=lambda point: math.dist(point, (2.7, 0.9)))
        sweep = IndoorSweep(area, sample, 0)
        left = ((1.0, 6.0), -math.pi / 2, False)
        right = ((6.2, 6.0), -math.pi / 2, False)

        visible, blocked = sweep.pending_view((left, right), geometry)
        self.assertIn(sample, blocked)
        self.assertNotIn(sample, visible)

        visible, blocked = sweep.pending_view((((0.9, 3.0), -math.pi / 2, False), right), geometry)
        self.assertIn(sample, visible)
        self.assertNotIn(sample, blocked)
        self.assertFalse(sweep.checked)

        outside = ((0.9, 3.0), math.pi / 2, False)
        visible, blocked = sweep.pending_view((outside, right), geometry)
        self.assertNotIn(sample, visible)
        self.assertNotIn(sample, blocked)

        visible, blocked = sweep.pending_view(((left[0], math.pi / 2, False), right), geometry)
        self.assertIn(sample, blocked)
        self.assertNotIn(sample, visible)

        sweep.confirm(sample, 1.0)
        visible, blocked = sweep.pending_view((left, right), geometry)
        self.assertIn(sample, sweep.checked)
        self.assertNotIn(sample, blocked)
        self.assertNotIn(sample, visible)

    def test_open_area_samples_are_confirmed_independently_and_survive_interruption(self):
        geometry = SpatialIndex()
        screen = Collider("screen", Rect(2.38, 1.54, 0.3, 0.36))
        geometry.add(screen)
        world = SimpleNamespace(
            data={"search_areas": {"yard": [[0, 0, 8, 6]]},
                  "search_layouts": {"yard": {"sample_spacing": 2, "corner_first": False}}},
            facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry),
        )
        area, = build_search_areas(world)
        self.assertEqual(len(area.points), 12)
        self.assertFalse(any(area.room_corners.values()))
        guard = Actor("guard", Position(1, 1))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        first, later = (5, 1), (5, 3)
        sweep = brain.indoor = IndoorSweep(area, first, 0, checked=set(area.points) - {first, later})

        for tick in range(25):
            if tick == 15:
                screen.active = False
            ai._look_at_indoor_samples(guard, brain, tick / 60, sweep)
            if tick == 14:
                self.assertEqual(brain.look_point, first)
            elif tick == 15:
                self.assertEqual(brain.look_point, later)

        self.assertIn(first, sweep.checked_at)
        self.assertNotIn(later, sweep.checked)
        self.assertIn(later, brain.indoor_exposure)
        ai._interrupt_search(guard, brain)
        self.assertFalse(brain.indoor_exposure)
        self.assertIn(first, sweep.checked_at)

        for tick in range(26, 46):
            ai._look_at_indoor_samples(guard, brain, tick / 60, sweep)
        self.assertNotIn(later, sweep.checked)
        for tick in range(46, 49):
            ai._look_at_indoor_samples(guard, brain, tick / 60, sweep)
        self.assertIn(later, sweep.checked_at)

    def test_moving_guard_prefers_a_newly_visible_sample_while_keeping_its_route(self):
        geometry = SpatialIndex()
        geometry.add(Collider("screen", Rect(2.38, 1.54, 0.3, 0.36)))
        world = SimpleNamespace(
            data={"search_areas": {"yard": [[0, 0, 8, 6]]},
                  "search_layouts": {"yard": {"sample_spacing": 2, "corner_first": False}}},
            facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry),
        )
        area, = build_search_areas(world)
        guard = Actor("guard", Position(1, 1))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        far, revealed, goal = (7, 1), (5, 3), (3, 1)
        brain.indoor = IndoorSweep(area, goal, 0, checked=set(area.points) - {far, revealed})
        brain.indoor_target, brain.path = goal, [goal]

        ai._pace_indoor_movement(guard, brain, Intent(direction=(2, 0)), 0, room_sweep=True)
        self.assertEqual(brain.look_point, far)
        self.assertIn(revealed, brain.indoor_occluded)

        guard.position = Position(1, 0.4)
        intent = Intent(direction=(2, 0.6))
        ai._pace_indoor_movement(guard, brain, intent, 1 / 60, room_sweep=True)
        self.assertEqual(brain.look_point, revealed)
        self.assertEqual(intent.direction, (2, 0.6))
        self.assertEqual(brain.path, [goal])
        self.assertNotIn(revealed, brain.indoor.checked)

    def test_indoor_gunshot_starts_a_sweep_in_normal_play(self):
        for scene in (FACILITY_SCENE, KILLHOUSE_SCENE):
            with self.subTest(scene=scene.name):
                simulation = PlaySimulation(scene)
                guard = simulation.guards[0]
                area = simulation.world.search_areas[0]
                navigation = simulation.world.navigation
                point = min((point for point in area.points if navigation.clear(guard.xy, point)),
                            key=lambda point: math.dist(guard.xy, point))
                source = Actor("test_sound", Position(*point), team="player")
                simulation.player.hp = 0
                simulation.events.emit("noise", simulation.time, source, radius=30, strength=0.8, reason="gunshot")
                brain = simulation.ai.brains[guard.id]
                while simulation.time < 3 and brain.indoor is None:
                    simulation.step()

                self.assertIsNotNone(brain.indoor)
                self.assertIs(brain.indoor.area, area)
                self.assertEqual(guard.state, "search")
                self.assertIsNone(brain.memory.reported)

    def test_patrol_turns_to_its_next_node_while_moving(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(geometry=geometry, revision=0, door_on_ai_path=lambda actor, point: None,
                                navigation=Navigation({}, [], geometry))
        guard = Actor("guard", Position(2, 2), facing=0)
        player = Actor("player", Position(0, 0), hp=0)
        ai = EnemyAI(world, [guard], [[(2, 6)]])

        for tick in range(1, 9):
            intent = ai.update([guard], player, tick / 60)[guard.id]
            self.assertEqual(intent.direction, (0, 4))

        self.assertAlmostEqual(guard.facing, math.pi / 2)

    def test_guard_walks_along_path_while_looking_to_the_side(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 7.2]]}},
                                facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        guard = Actor("guard", Position(0.9, 0.9))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        side = (0.9, 2.7)
        target = (4.5, 0.9)
        brain.indoor = IndoorSweep(area, target, 0, checked=set(area.points) - {side})
        brain.indoor_target = target
        brain.path = [target]
        intent = Intent(direction=(3.6, 0), open_door=True)

        ai._pace_indoor_movement(guard, brain, intent, 1, room_sweep=True)

        self.assertEqual(intent.direction, (3.6, 0))
        self.assertGreater(intent.move_multiplier, 0)
        self.assertTrue(intent.open_door)
        self.assertEqual(brain.look_point, side)
        for tick in range(1, 11):
            ai._pace_indoor_movement(guard, brain, intent, 1 + tick / 60, room_sweep=True)
        self.assertAlmostEqual(guard.facing, math.pi / 2)
        self.assertEqual(intent.direction, (3.6, 0))
        brain.indoor.confirm(side, 1.2)
        self.assertIn(side, brain.indoor.checked)
        guard.position = Position(1.5, 0.9)
        intent.direction = (3, 0)
        ai._pace_indoor_movement(guard, brain, intent, 2, room_sweep=True)
        ai._pace_indoor_movement(guard, brain, intent, 2 + 1 / 60, room_sweep=True)
        self.assertAlmostEqual(guard.facing, math.pi / 2)
        self.assertIsNone(brain.look_point)

    def test_walking_scan_changes_focus_before_reaching_its_waypoint(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 9, 9]]}},
                                facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        guard = Actor("guard", Position(0.9, 4.5))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        target = (8.1, 4.5)
        pending = {(4.5, 0.9), (4.5, 8.1)}
        brain.indoor = IndoorSweep(area, target, 0, checked=set(area.points) - pending)
        brain.indoor_target = target
        brain.path = [target]

        ai._pace_indoor_movement(guard, brain, Intent(direction=(7.2, 0)), 1, room_sweep=True)
        first = brain.look_point
        ai._pace_indoor_movement(guard, brain, Intent(direction=(7.2, 0)), 1 + 1 / 60, room_sweep=True)
        self.assertAlmostEqual(abs(guard.facing), math.radians(18))
        brain.indoor.confirm(first, 1.04)
        self.assertIn(first, brain.indoor.checked)

        guard.position = Position(1.15, 4.5)
        intent = Intent(direction=(6.95, 0))
        self.assertGreater(brain.indoor_walk_look_at, 1.10)
        facing = guard.facing
        ai._pace_indoor_movement(guard, brain, intent, 1.10, room_sweep=True)

        self.assertIn(brain.look_point, pending - {first})
        self.assertGreater(intent.move_multiplier, 0)
        self.assertAlmostEqual(abs(guard.facing - facing), math.radians(9))
        self.assertEqual(brain.path, [target])

    def test_indoor_guard_keeps_looking_at_a_sample_when_its_path_turns(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 5.4]]}},
                                facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        waypoint = (2.7, 0.9)
        target = min(area.points, key=lambda point: math.dist(point, (2.7, 2.7)))
        behind = (0.9, 0.9)
        guard = Actor("guard", Position(2.1, 0.9), facing=math.pi)
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        brain.indoor = IndoorSweep(area, target, 0, checked=set(area.points) - {behind})
        brain.indoor_target = target
        brain.path = [waypoint, target]

        intent = Intent(direction=(0.6, 0))
        ai._pace_indoor_movement(guard, brain, intent, 1, room_sweep=True)

        self.assertEqual(brain.look_point, behind)
        self.assertAlmostEqual(guard.facing, math.pi)
        self.assertEqual(intent.direction, (0.6, 0))

        guard.position = Position(*waypoint)
        brain.path.pop(0)
        intent = Intent(direction=(0, 1.8))
        ai._pace_indoor_movement(guard, brain, intent, 1 + 1 / 60, room_sweep=True)

        self.assertEqual(brain.look_point, behind)
        self.assertAlmostEqual(guard.facing, math.pi)
        self.assertEqual(intent.direction, (0, 1.8))

    def test_walking_focus_requires_uninterrupted_visibility(self):
        geometry = SpatialIndex()
        obstacle = Collider("occluder", Rect(1.8, 0.6, 0.2, 0.6), active=False)
        geometry.add(obstacle)
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 3.6]]}},
                                facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        target = min(area.points, key=lambda point: math.dist(point, (4.5, 0.9)))
        sample = min(area.points, key=lambda point: math.dist(point, (2.7, 0.9)))
        guard = Actor("guard", Position(0.9, 0.9))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        brain.indoor = IndoorSweep(area, target, 0, checked=set(area.points) - {sample})
        brain.indoor_target = target

        for tick in range(18):
            ai._look_at_indoor_samples(guard, brain, tick / 60, brain.indoor)
        self.assertEqual(brain.look_point, sample)
        self.assertNotIn(sample, brain.indoor.checked)

        obstacle.active = True
        ai._look_at_indoor_samples(guard, brain, 18 / 60, brain.indoor)
        self.assertNotIn(sample, brain.indoor_exposure)
        self.assertIsNone(brain.look_point)

        obstacle.active = False
        for tick in range(19, 40):
            ai._look_at_indoor_samples(guard, brain, tick / 60, brain.indoor)
        self.assertEqual(brain.look_point, sample)
        self.assertNotIn(sample, brain.indoor.checked)

        for tick in range(40, 53):
            ai._look_at_indoor_samples(guard, brain, tick / 60, brain.indoor)
        self.assertIn(sample, brain.indoor.checked)
        self.assertIsNone(brain.look_point)

    def test_walkers_choose_distinct_corners_and_switch_when_occluded(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 5.4]]}},
                                facilities=[], geometry=geometry, revision=0, navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        first = Actor("first", Position(2.7, 2.7))
        second = Actor("second", Position(2.7, 3.0))
        ai = EnemyAI(world, [first, second], [[], []])
        sweep = IndoorSweep(area, first.xy, 0)
        first_brain, second_brain = ai.brains[first.id], ai.brains[second.id]

        ai._look_at_indoor_samples(first, first_brain, 0, sweep)
        ai._look_at_indoor_samples(second, second_brain, 0, sweep)
        original, reserved = first_brain.look_point, second_brain.look_point
        corners = set(sweep.pending_corners(None))
        self.assertIn(original, corners)
        self.assertIn(reserved, corners)
        self.assertNotEqual(original, reserved)
        self.assertFalse(sweep.checked)

        x, y = ((a + b) / 2 for a, b in zip(first.xy, original))
        geometry.add(Collider("occluder", Rect(x - 0.05, y - 0.05, 0.1, 0.1)))
        world.revision += 1
        ai._look_at_indoor_samples(first, first_brain, 1 / 60, sweep)

        self.assertIn(first_brain.look_point, corners - {original, reserved})
        self.assertNotIn(original, first_brain.indoor_exposure)
        self.assertNotIn(original, sweep.checked)

    def test_scan_checks_visible_corners_without_ignoring_pillar(self):
        geometry = SpatialIndex()
        geometry.add(Collider("west", Rect(-0.2, 0, 0.2, 7.2)))
        geometry.add(Collider("south", Rect(0, 7.2, 3.6, 0.2)))
        geometry.add(Collider("pillar", Rect(3.8, 0.25, 1.2, 1.3)))
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 9, 3.6], [0, 3.6, 3.6, 3.6]]}},
                                facilities=[], geometry=geometry,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        origin, open_point = (0.9, 0.9), (2.7, 0.9)
        hidden = (6.3, 0.9)
        room = area.rooms[origin]
        self.assertEqual(area.rooms[hidden], room)
        points = area.room_points[room]
        view = dict(sample_views(origin, 0, points, geometry))
        self.assertTrue(view[open_point])
        self.assertFalse(view[hidden])

        far_corner = max(point for point in area.room_points[room] if point[0] == origin[0])
        self.assertTrue(dict(sample_views(origin, math.pi / 2, points, geometry, stationary=True))[far_corner])
        self.assertTrue(dict(sample_views((5.5, 0.9), 0, points, geometry))[hidden])

    def test_clear_view_completes_wide_room_from_one_position(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 12.6, 5.4]]}},
                                facilities=[], geometry=geometry, revision=0,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        world.indoor_search_area = lambda point: area
        guard = Actor("guard", Position(0.9, 0.9), facing=math.pi)
        ai = EnemyAI(world, [guard], [[]])
        ai.start_search_drill([guard], (0.9, 0.9))
        brain = ai.brains[guard.id]
        sweep = ai.incidents["indoor_drill"].indoor

        goal, finished = ai._indoor_sweep(guard, brain, (0.9, 0.9), 0, 1)
        self.assertIsNone(goal)
        self.assertFalse(finished)
        for tick in range(1, 301):
            time = 1 + tick / 60
            if not brain.indoor_scanning(time):
                break
            ai._advance_indoor_look(guard, brain, time)

        self.assertEqual(sweep.checked, set(area.points))
        self.assertEqual(guard.xy, (0.9, 0.9))

    def test_guard_scans_visible_space_before_moving_to_hidden_points(self):
        geometry = SpatialIndex()
        geometry.add(Collider("pillar", Rect(3.8, 0.25, 1.2, 1.3)))
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 9, 3.6], [0, 3.6, 3.6, 3.6]]}},
                                facilities=[], geometry=geometry, revision=0,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        world.indoor_search_area = lambda point: area
        guard = Actor("guard", Position(0.9, 0.9), facing=math.pi)
        ai = EnemyAI(world, [guard], [[]])
        ai.start_search_drill([guard], guard.xy)
        brain = ai.brains[guard.id]

        self.assertEqual(ai._indoor_sweep(guard, brain, guard.xy, 0, 1), (None, False))
        for tick in range(1, 301):
            time = 1 + tick / 60
            if not brain.indoor_scanning(time):
                break
            ai._advance_indoor_look(guard, brain, time)

        self.assertIn((2.7, 0.9), brain.indoor.checked)
        self.assertNotIn((6.3, 0.9), brain.indoor.checked)
        goal, finished = ai._indoor_sweep(guard, brain, guard.xy, 0, time)
        self.assertIsNotNone(goal)
        self.assertFalse(finished)

    def test_unreachable_observation_point_stops_guard_for_a_scan(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 3.6]]}},
                                facilities=[], geometry=geometry, revision=0,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        world.indoor_search_area = lambda point: area
        origin, target = (2.7, 0.9), (4.5, 0.9)
        guard = Actor("guard", Position(*origin))
        ai = EnemyAI(world, [guard], [[]])
        ai.start_search_drill([guard], target)
        brain = ai.brains[guard.id]
        brain.indoor = ai.incidents[brain.incident].indoor
        brain.indoor_target = brain.goal = target
        brain.path, brain.path_revision = [target], world.revision

        geometry.add(Collider("new_obstacle", Rect(4, 0, 1.1, 3.6)))
        world.revision += 1
        intent = Intent()
        ai._move(guard, brain, intent, target, 1, 1)
        self.assertTrue(brain.route_failed)
        self.assertEqual(intent.direction, (0, 0))

        self.assertEqual(ai._indoor_sweep(guard, brain, target, 0, 1 + 1 / 60), (None, False))
        self.assertTrue(brain.indoor_scanning(1 + 1 / 60))
        self.assertEqual(brain.indoor_blocked[target], world.revision)
        for tick in range(1, 181):
            time = 1 + tick / 60
            if not brain.indoor_scanning(time):
                break
            ai._advance_indoor_look(guard, brain, time)

        self.assertEqual(guard.xy, origin)
        self.assertNotIn(target, brain.indoor.checked)
        origin_sample = min(area.points, key=lambda point: math.dist(point, origin))
        self.assertIn(origin_sample, brain.indoor.checked)
        self.assertFalse(brain.indoor_scanning(time))

    def test_two_doorways_preserve_room_boundaries_when_a_door_opens(self):
        geometry = SpatialIndex()
        panels = ((0, 0.7, "wall"), (0.7, 1.8, "door"), (2.5, 4, "wall"),
                  (6.5, 2, "door"), (8.5, 0.5, "wall"))
        for y, height, kind in panels:
            geometry.add(Collider(f"panel-{y}", Rect(5.3, y, 0.2, height), kind))
        world = SimpleNamespace(data={"search_areas": {"suite": [[0, 0, 9, 9]]}},
                                facilities=[], geometry=geometry,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        left, right = (4.5, 0.9), (6.3, 0.9)
        left_room, right_room = area.rooms[left], area.rooms[right]
        self.assertNotEqual(left_room, right_room)
        self.assertFalse(dict(sample_views(left, 0, area.room_points[right_room], geometry))[right])

        geometry.objects["panel-0.7"].active = False
        self.assertNotIn(right, dict(sample_views(left, 0, area.room_points[left_room], geometry)))
        self.assertTrue(dict(sample_views(left, 0, area.room_points[right_room], geometry))[right])

        sweep = IndoorSweep(area, left, 0)
        sweep.confirm(left, 1)
        sweep.confirm(right, 2)
        sweep.reopen_room(right)
        self.assertEqual(sweep.checked, {left})
        self.assertEqual(sweep.checked_at, {left: 1})

    def test_squad_finishes_search_in_facility(self):
        simulation = PlaySimulation(scene=FACILITY_SCENE)
        simulation.player.hp = 0
        area, = simulation.world.search_areas
        simulation.ai.start_search_drill(simulation.guards, area.points[len(area.points) // 2])

        while simulation.time < 90:
            simulation.step()
            sweep = simulation.ai.incidents["indoor_drill"].indoor
            if len(sweep.checked) == len(area.points):
                break

        self.assertEqual(sweep.checked, set(area.points))

    def test_killhouse_drill_starts_from_observation_point_and_completes(self):
        simulation = PlaySimulation(scene=KILLHOUSE_SCENE, search_drill=True)
        drill = simulation.world.data["search_drill"]
        sweep = simulation.ai.incidents["indoor_drill"].indoor

        self.assertEqual(simulation.player.xy, tuple(drill["observer_spawn"]))
        self.assertEqual(sweep.anchor, tuple(drill["last_seen"]))
        expected = set(sweep.area.points)
        while simulation.time < 60 and sweep.checked != expected:
            simulation.step()

        self.assertEqual(sweep.checked, expected)
        self.assertEqual(simulation.player.hp, 100)
