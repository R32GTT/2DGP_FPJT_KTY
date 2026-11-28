from copy import deepcopy
import json
from pathlib import Path
import unittest

from prototype.movement import move_actor
from prototype.simulation import PlaySimulation
from prototype.world import World


SCENE = Path(__file__).resolve().parents[1] / "prototype/scenarios/prefab_test.json"


class FacilityPrefabTests(unittest.TestCase):
    def test_baked_search_matches_runtime_geometry_after_rotation(self):
        source = json.loads(SCENE.read_text(encoding="utf-8"))
        for prefab in ("field_comms_01", "field_hq_01", "field_warehouse_01"):
            for angle in (0, 90):
                with self.subTest(prefab=prefab, rotation=angle):
                    scene = deepcopy(source)
                    scene["navigation"], scene["navigation_edges"] = {}, []
                    scene["prefab_instances"] = [{"id": "site", "prefab": f"prototype/data/prefabs/{prefab}.json",
                                                  "position": [0, 0], "rotation": angle}]
                    baked = World(scene)
                    self.assertIn("baked_search", baked._search_layouts["site"][1])
                    scene["search_layouts"] = {"site": {}}
                    runtime = World(scene)
                    a, b = baked.search_areas[0], runtime.search_areas[0]
                    def normalized(rows):
                        return frozenset(tuple(round(value, 6) for value in point) for point in rows)

                    self.assertEqual(normalized(a.points), normalized(b.points))
                    self.assertEqual({normalized(rows) for rows in a.room_points.values()},
                                     {normalized(rows) for rows in b.room_points.values()})
                    self.assertEqual({normalized(rows) for rows in a.room_corners.values()},
                                     {normalized(rows) for rows in b.room_corners.values()})

    def test_rotated_prefab_keeps_connectors_and_every_room_reachable(self):
        source = json.loads(SCENE.read_text(encoding="utf-8"))
        for angle in (0, 90, 180, 270):
            with self.subTest(rotation=angle):
                scene = deepcopy(source)
                scene["navigation"], scene["navigation_edges"] = {}, []
                scene["prefab_instances"] = [{**source["prefab_instances"][0], "position": [0, 0], "rotation": angle}]
                world = World(scene)
                instance, = world.data["placed_prefabs"]
                vehicle = instance["connectors"]["vehicle"]
                start = world.navigation.nodes[vehicle["node"]]
                self.assertEqual(tuple(vehicle["position"]), start)
                self.assertEqual(sum(abs(value) for value in vehicle["direction"]), 1)
                for key, point in world.navigation.nodes.items():
                    self.assertTrue(world.navigation.route(start, point, allow_doors=True), key)
                self.assertEqual(len(world.search_areas), 1)
                self.assertNotIn("placed_prefabs", world.source)
                self.assertEqual(World(world.source).data, world.data)

    def test_instances_have_independent_doors_and_player_can_enter(self):
        simulation = PlaySimulation(SCENE)
        self.assertEqual(len(simulation.world.data["placed_prefabs"]), 2)
        door = simulation.world.geometry.objects["hq_a/door_yard"]
        other = simulation.world.geometry.objects["hq_b/door_yard"]
        self.assertAlmostEqual(door.rect.w, 1.6)
        self.assertTrue(door.active and other.active)
        move_actor(simulation.player, (0, -1), simulation.world, 2, speed=3)
        self.assertGreater(simulation.player.position.y, door.rect.bottom)
        simulation.use_door()
        self.assertFalse(door.active)
        self.assertTrue(other.active)
        move_actor(simulation.player, (0, -1), simulation.world, 0.5, speed=3)
        self.assertLess(simulation.player.position.y, door.rect.y)
