import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from campaign.models import Position
from prototype.actors import Actor
from prototype.ai import Brain, EnemyAI, Intent
from prototype.ai_state import SearchCue
from prototype.ai_motion import turn_indoor_gaze
from prototype.geometry import Collider, Rect, SpatialIndex
from prototype.indoor_search import IndoorSweep, build_search_areas, choose_look, sample_views
from prototype.navigation import Navigation
from prototype.simulation import PlaySimulation


FACILITY_SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/ai_facility.json"
KILLHOUSE_SCENE = FACILITY_SCENE.with_name("ai_killhouse.json")


class IndoorSearchTests(unittest.TestCase):
    def test_equal_viewpoints_allow_stable_individual_choices(self):
        geometry = SpatialIndex()
        navigation = Navigation({}, [], geometry)
        sweep = IndoorSweep(SimpleNamespace(view_coverage={}), (0, 0), 0)
        candidates = [(3, 1), (3, -1)]
        choices = [sweep.observation_point((0, 0), 0, candidates, [(5, 0)], navigation,
                                           revision=1, actor_id=f"guard_{index}") for index in range(12)]
        self.assertEqual(set(choices), set(candidates))
        self.assertEqual(choices, [sweep.observation_point((0, 0), 0, candidates, [(5, 0)], navigation,
                                                          revision=1, actor_id=f"guard_{index}")
                                   for index in range(12)])

    def test_look_groups_samples_across_angle_wrap(self):
        left = [(5 * math.cos(angle), 5 * math.sin(angle))
                for angle in (math.pi - 0.1, -math.pi + 0.1)]
        samples = {point: 1 for point in [*left, (5, 0)]}
        self.assertIn(choose_look((0, 0), 0, SpatialIndex(), samples), left)

    def test_weak_outdoor_noise_waits_for_indoor_sweep_but_gunshot_interrupts(self):
        geometry = SpatialIndex()
        world = SimpleNamespace(data={"search_areas": {"room": [[0, 0, 5.4, 5.4]]}},
                                facilities=[], geometry=geometry, revision=0,
                                navigation=Navigation({}, [], geometry))
        area, = build_search_areas(world)
        guard = Actor("guard", Position(2.7, 2.7))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        sweep = brain.search.task.sweep = IndoorSweep(area, guard.xy, 0)
        original = brain.search.priority_cue = SearchCue("sound", guard.xy, 1)
        brain.memory.last_sound, brain.memory.sound_at = (8, 2.7), 2
        brain.memory.sound_priority = 0.4

        ai.search.update_priority_cue(guard, brain, False, True, None, 2)
        self.assertEqual(brain.search.priority_cue, original)
        self.assertIs(brain.search.task.sweep, sweep)

        brain.memory.gunshot_origin = (8, 2.7)
        ai.search.update_priority_cue(guard, brain, False, True, None, 2)
        self.assertEqual(brain.search.priority_cue.point, (8, 2.7))

        brain.search.priority_cue = original
        brain.memory.gunshot_origin = None
        ai.search.update_priority_cue(guard, brain, False, True, None, 9)
        self.assertEqual(brain.search.priority_cue.point, (8, 2.7))

        brain.search.priority_cue = original
        sweep.checked.update(area.points)
        ai.search.update_priority_cue(guard, brain, False, True, None, 2)
        self.assertEqual(brain.search.priority_cue.point, (8, 2.7))

    def test_fixed_sight_reuses_squad_geometry_but_updates_on_door_change(self):
        geometry = SpatialIndex()
        door = Collider("door", Rect(2, -1, .2, 2), kind="door")
        geometry.add(door)
        sweep = IndoorSweep(None, (0, 0), 0)
        samples = [(3, 0), (3, .5), (3, -.5), (0, 1)]
        with patch.object(geometry, "sight_flags", wraps=geometry.sight_flags) as query:
            self.assertEqual(sweep.fixed_view_count((0, 0), samples, geometry, 1), 1)
            self.assertEqual(sweep.fixed_view_count((0, 0), list(reversed(samples)), geometry, 1), 1)
            self.assertEqual(query.call_count, 1)
            door.active = False
            self.assertEqual(sweep.fixed_view_count((0, 0), samples, geometry, 2), 4)
            self.assertEqual(query.call_count, 2)
        self.assertFalse(sweep.checked)

    def test_sight_candidates_still_require_exact_segment_collision(self):
        geometry = SpatialIndex()
        geometry.add(Collider("wall", Rect(1, 2, .5, .5)))
        origin = (0, 0)
        targets = [(3, 3), (3, 6), (3, 0), (0, 3)]

        self.assertEqual(geometry.sight_flags(origin, targets), [True, False, True, True])
        self.assertIsNone(geometry.raycast(origin, targets[0]))
        self.assertIsNotNone(geometry.raycast(origin, targets[1]))

    def test_indoor_gaze_starts_turning_immediately_and_brakes_before_reversing(self):
        actor = Actor("guard", Position(0, 0))
        brain = Brain([])
        brain.look.point = (0, 2)
        first = turn_indoor_gaze(actor, brain, 0.0, math.radians(540))
        self.assertGreater(first, 0)

        facing = first
        for _ in range(3):
            facing = turn_indoor_gaze(actor, brain, facing, math.radians(540))
        brain.look.point = (0, -2)
        braking = turn_indoor_gaze(actor, brain, facing, math.radians(540))
        self.assertGreater(braking, facing)
        for _ in range(60):
            braking = turn_indoor_gaze(actor, brain, braking, math.radians(540))
        self.assertAlmostEqual(braking, -math.pi / 2)

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
        grid_points = {(x, y) for x in (1, 3, 5, 7) for y in (1, 3, 5)}
        self.assertTrue(grid_points.issubset(area.points))
        self.assertFalse(any(area.room_corners.values()))
        guard = Actor("guard", Position(1, 1))
        ai = EnemyAI(world, [guard], [[]])
        brain = ai.brains[guard.id]
        first, later = (5, 1), (5, 3)
        sweep = brain.search.task.sweep = IndoorSweep(area, first, 0, checked=set(area.points) - {first, later})

        for tick in range(10):
            if tick == 5:
                screen.active = False
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
            if tick == 4:
                self.assertEqual(brain.look.point, first)
            elif tick == 5:
                self.assertEqual(brain.look.point, later)

        self.assertIn(first, sweep.checked)
        self.assertNotIn(later, sweep.checked)
        self.assertIn(later, brain.search.task.scan.exposure)
        ai.search.interrupt_search(guard, brain)
        self.assertFalse(brain.search.task.scan.exposure)
        self.assertIn(first, sweep.checked)

        for tick in range(10, 16):
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
        self.assertNotIn(later, sweep.checked)
        for tick in range(16, 18):
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
        self.assertIn(later, sweep.checked)

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
        brain.search.task.sweep = IndoorSweep(area, goal, 0, checked=set(area.points) - {far, revealed})
        brain.search.task.target, brain.motion.path = goal, [goal]

        ai.search.pace_indoor_movement(guard, brain, Intent(direction=(2, 0)), 0, room_sweep=True)
        ai.search.confirm_view(guard, brain, 0)
        self.assertEqual(brain.look.point, far)
        self.assertIn(revealed, brain.search.task.scan.occluded)

        guard.position = Position(1, 0.4)
        intent = Intent(direction=(2, 0.6))
        ai.search.pace_indoor_movement(guard, brain, intent, 1 / 60, room_sweep=True)
        ai.search.confirm_view(guard, brain, 1 / 60)
        self.assertEqual(brain.look.point, revealed)
        self.assertEqual(intent.direction, (2, 0.6))
        self.assertEqual(brain.motion.path, [goal])
        self.assertNotIn(revealed, brain.search.task.sweep.checked)

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
                while simulation.time < 3 and brain.search.task.sweep is None:
                    simulation.step()

                self.assertIsNotNone(brain.search.task.sweep)
                self.assertIs(brain.search.task.sweep.area, area)
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
        brain.search.task.sweep = IndoorSweep(area, target, 0, checked=set(area.points) - {behind})
        brain.search.task.target = target
        brain.motion.path = [waypoint, target]

        intent = Intent(direction=(0.6, 0))
        ai.search.pace_indoor_movement(guard, brain, intent, 1, room_sweep=True)
        ai.search.confirm_view(guard, brain, 1)

        self.assertEqual(brain.look.point, behind)
        self.assertAlmostEqual(guard.facing, math.pi)
        self.assertEqual(intent.direction, (0.6, 0))

        guard.position = Position(*waypoint)
        brain.motion.path.pop(0)
        intent = Intent(direction=(0, 1.8))
        ai.search.pace_indoor_movement(guard, brain, intent, 1 + 1 / 60, room_sweep=True)
        ai.search.confirm_view(guard, brain, 1 + 1 / 60)

        self.assertEqual(brain.look.point, behind)
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
        brain.search.task.sweep = IndoorSweep(area, target, 0, checked=set(area.points) - {sample})
        brain.search.task.target = target

        for tick in range(5):
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, brain.search.task.sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
        self.assertEqual(brain.look.point, sample)
        self.assertNotIn(sample, brain.search.task.sweep.checked)

        obstacle.active = True
        ai.search.look_at_indoor_samples(guard, brain, 5 / 60, brain.search.task.sweep)
        ai.search.confirm_view(guard, brain, 5 / 60)
        self.assertNotIn(sample, brain.search.task.scan.exposure)
        self.assertIsNone(brain.look.point)

        obstacle.active = False
        for tick in range(6, 12):
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, brain.search.task.sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
        self.assertNotIn(sample, brain.search.task.sweep.checked)

        for tick in range(12, 14):
            ai.search.look_at_indoor_samples(guard, brain, tick / 60, brain.search.task.sweep)
            ai.search.confirm_view(guard, brain, tick / 60)
        self.assertIn(sample, brain.search.task.sweep.checked)
        self.assertIsNone(brain.look.point)

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

        ai.search.look_at_indoor_samples(first, first_brain, 0, sweep)
        ai.search.confirm_view(first, first_brain, 0)
        ai.search.look_at_indoor_samples(second, second_brain, 0, sweep)
        ai.search.confirm_view(second, second_brain, 0)
        original, reserved = first_brain.look.point, second_brain.look.point
        corners = set(sweep.pending_corners(None))
        self.assertIn(original, corners)
        self.assertIn(reserved, corners)
        self.assertNotEqual(original, reserved)
        self.assertFalse(sweep.checked)

        x, y = ((a + b) / 2 for a, b in zip(first.xy, original))
        geometry.add(Collider("occluder", Rect(x - 0.05, y - 0.05, 0.1, 0.1)))
        world.revision += 1
        ai.search.look_at_indoor_samples(first, first_brain, 1 / 60, sweep)
        ai.search.confirm_view(first, first_brain, 1 / 60)

        self.assertIn(first_brain.look.point, corners - {original, reserved})
        self.assertNotIn(original, first_brain.search.task.scan.exposure)
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

    def test_squad_finishes_search_in_facility(self):
        simulation = PlaySimulation(scene=FACILITY_SCENE)
        simulation.player.hp = 0
        area, = simulation.world.search_areas
        simulation.ai.search.start_search_drill(simulation.guards, area.points[len(area.points) // 2])

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
