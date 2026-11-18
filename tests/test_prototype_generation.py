import json
import unittest

from shapely import box, union_all

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

    def test_spawn_and_free_space_are_connected(self):
        for seed in (42, 43):
            with self.subTest(seed=seed):
                world = World(generate_scene(seed))
                self.assertTrue(world.navigation.walkable((0, 0)))
                obstacles = [collider.rect.expanded(world.navigation.clearance)
                             for collider in world.geometry.objects.values()]
                bounds = world.bounds.expanded(-world.navigation.clearance)
                free = box(bounds.x, bounds.y, bounds.right, bounds.bottom).difference(
                    union_all([box(r.x, r.y, r.right, r.bottom) for r in obstacles]))
                self.assertEqual(free.geom_type, "Polygon")

    def test_play_movement_obstacle_collision_and_map_boundary(self):
        simulation = PlaySimulation(generate_scene(42))
        self.assertFalse(simulation.guards)
        for _ in range(60):
            simulation.step((1, 0))
        self.assertAlmostEqual(simulation.player.position.x, 3)
        self.assertAlmostEqual(simulation.player.position.y, 0)
        actor, world = simulation.player, simulation.world
        actor.position.x = world.bounds.right - 1
        move_actor(actor, (1, 0), world, 1, speed=3)
        self.assertAlmostEqual(actor.position.x, world.bounds.right - actor.radius)
        obstacle = next(iter(world.geometry.objects.values())).rect
        actor.position.x, actor.position.y = obstacle.x - actor.radius - 0.1, obstacle.y + obstacle.h / 2
        move_actor(actor, (1, 0), world, 1, speed=3)
        self.assertLessEqual(actor.position.x + actor.radius, obstacle.x + 1e-8)
