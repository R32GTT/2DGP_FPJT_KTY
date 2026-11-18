from collections import deque
import json
import unittest

from prototype.generation import generate_scene
from prototype.movement import move_actor
from prototype.simulation import PlaySimulation
from prototype.world import World


class GeneratedMapTests(unittest.TestCase):
    def test_seed_repeats_geometry_and_other_seeds_change_it(self):
        scene = generate_scene(42)
        self.assertEqual(scene, generate_scene(42))
        self.assertNotEqual(scene["colliders"], generate_scene(43)["colliders"])
        self.assertNotEqual(scene["layers"], generate_scene(43)["layers"])
        self.assertEqual(scene["facilities"], [])
        self.assertEqual(scene["guards"], [])

    def test_saved_generation_settings_rebuild_the_same_map_and_reject_other_versions(self):
        scene = generate_scene(42)
        settings = json.loads(json.dumps(scene["generation"]))
        self.assertEqual(generate_scene(**settings), scene)
        with self.assertRaises(ValueError):
            generate_scene(42, version=settings["version"] + 1)

    def test_spawn_and_sampled_free_space_are_connected(self):
        for seed in (0, 42, -7):
            with self.subTest(seed=seed):
                world = World(generate_scene(seed))
                self.assertTrue(world.navigation.walkable((0, 0)))
                free = {(x, y) for x in range(-30, 31, 2) for y in range(-30, 31, 2)
                        if world.navigation.walkable((x, y))}
                seen, queue = {(0, 0)}, deque([(0, 0)])
                while queue:
                    x, y = queue.popleft()
                    for point in ((x - 2, y), (x + 2, y), (x, y - 2), (x, y + 2)):
                        if point in free and point not in seen and world.navigation.clear((x, y), point):
                            seen.add(point)
                            queue.append(point)
                self.assertEqual(seen, free)

    def test_play_movement_obstacle_collision_and_map_boundary(self):
        simulation = PlaySimulation(generate_scene(42))
        self.assertFalse(simulation.guards)
        for _ in range(60):
            simulation.step((1, 0))
        self.assertAlmostEqual(simulation.player.position.x, 3)
        self.assertAlmostEqual(simulation.player.position.y, 0)
        actor, world = simulation.player, simulation.world
        move_actor(actor, (1, 0), world, 20, speed=3)
        self.assertAlmostEqual(actor.position.x, world.bounds.right - actor.radius)
        obstacle = next(iter(world.geometry.objects.values())).rect
        actor.position.x, actor.position.y = obstacle.x - actor.radius - 0.1, obstacle.y + obstacle.h / 2
        move_actor(actor, (1, 0), world, 1, speed=3)
        self.assertLessEqual(actor.position.x + actor.radius, obstacle.x + 1e-8)
