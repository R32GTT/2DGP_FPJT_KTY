import json
import math
import unittest
from collections import Counter
from copy import deepcopy

from shapely import LineString, box, union_all

from prototype.generation import generate_scene
from prototype.movement import move_actor
from prototype.road_generation import ROAD_SEPARATION, ROAD_WIDTH, validate_roads
from prototype.simulation import PlaySimulation
from prototype.world import World


class GeneratedMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scene, cls.other = generate_scene(42), generate_scene(43)

    def test_seed_repeats_geometry_and_other_seeds_change_it(self):
        scene = self.scene
        self.assertEqual(scene, generate_scene(42))
        self.assertNotEqual(scene["colliders"], self.other["colliders"])
        self.assertNotEqual(scene["road_network"], self.other["road_network"])
        self.assertEqual(scene["facilities"], [])
        self.assertEqual(scene["guards"], [])

    def test_saved_generation_settings_rebuild_the_same_map_and_reject_other_versions(self):
        scene = self.scene
        settings = json.loads(json.dumps(scene["generation"]))
        self.assertEqual(generate_scene(**settings), scene)
        with self.assertRaises(ValueError):
            generate_scene(42, version=settings["version"] + 1)
        changed = deepcopy(settings)
        path = next(iter(changed["prefab_versions"]))
        changed["prefab_versions"][path] += 1
        with self.assertRaises(ValueError):
            generate_scene(**changed)

    def test_spawn_and_free_space_are_connected(self):
        for scene in (self.scene, self.other):
            with self.subTest(seed=scene["generation"]["seed"]):
                world = World(scene)
                self.assertTrue(world.navigation.walkable((0, 0)))
                obstacles = [collider.rect.expanded(world.navigation.clearance)
                             for collider in world.geometry.objects.values() if collider.kind != "door"]
                bounds = world.bounds.expanded(-world.navigation.clearance)
                free = box(bounds.x, bounds.y, bounds.right, bounds.bottom).difference(
                    union_all([box(r.x, r.y, r.right, r.bottom) for r in obstacles]))
                self.assertEqual(free.geom_type, "Polygon")

    def test_play_movement_obstacle_collision_and_map_boundary(self):
        simulation = PlaySimulation(self.scene)
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

    def test_region_size_and_importance_select_facility_candidates(self):
        counts = Counter(site["kind"] for site in self.scene["planned_facilities"])
        self.assertEqual(counts, {"safehouse": 1, "hq": 1, "comms": 1, "warehouse": 1})
        larger = generate_scene(42, region_size="medium", importance="core")
        counts = Counter(site["kind"] for site in larger["planned_facilities"])
        self.assertEqual((counts["hq"], counts["warehouse"], counts["garrison"], counts["sam"]), (1, 2, 1, 1))
        self.assertIn(counts["comms"], (1, 2))
        warehouse = next(site for site in self.scene["planned_facilities"] if site["kind"] == "warehouse")
        selected = next(site for site in larger["planned_facilities"] if site["kind"] == "warehouse")
        self.assertGreater(selected["rect"][2] * selected["rect"][3], warehouse["rect"][2] * warehouse["rect"][3])
        self.assertIn("/core/", selected["variant_id"])

    def test_roads_reject_disconnection_wrong_entry_direction_and_blocked_vehicle_space(self):
        scene = self.scene
        network, sites = scene["road_network"], scene["planned_facilities"]
        bounds = World(scene).bounds
        self.assertGreaterEqual(len(network["edges"]), len(network["nodes"]))
        broken = deepcopy(network)
        gate = broken["terminals"][sites[0]["id"]]
        broken["edges"] = [edge for edge in broken["edges"] if gate not in edge]
        with self.assertRaises(ValueError):
            validate_roads(broken, sites, bounds, [])
        reversed_sites = deepcopy(sites)
        reversed_sites[0]["connector"]["direction"] = [-v for v in sites[0]["connector"]["direction"]]
        with self.assertRaises(ValueError):
            validate_roads(network, reversed_sites, bounds, [])
        a, b = (network["nodes"][key] for key in network["edges"][0])
        x, y = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        with self.assertRaises(ValueError):
            validate_roads(network, sites, bounds, [{"rect": [x - 0.5, y - 0.5, 1, 1]}])

    def test_independent_parallel_roads_keep_their_distance(self):
        network = self.scene["road_network"]
        segments = [(edge, LineString([network["nodes"][key] for key in edge])) for edge in network["edges"]]
        for index, (edge, line) in enumerate(segments):
            a, b = line.coords
            ux, uy = (b[0] - a[0]) / line.length, (b[1] - a[1]) / line.length
            for other_edge, other in segments[index + 1:]:
                if set(edge) & set(other_edge) or line.intersects(other):
                    continue
                p, q = other.coords
                vx, vy = (q[0] - p[0]) / other.length, (q[1] - p[1]) / other.length
                if abs(ux * vy - uy * vx) >= 0.2:
                    continue
                projections = [(x - a[0]) * ux + (y - a[1]) * uy for x, y in other.coords]
                overlap = min(line.length, max(projections)) - max(0, min(projections))
                if overlap >= ROAD_WIDTH:
                    self.assertGreaterEqual(line.distance(other), ROAD_SEPARATION)

    def test_generated_road_connects_real_hq_and_player_can_walk_inside(self):
        simulation = PlaySimulation(self.scene)
        world, actor = simulation.world, simulation.player
        instance, = world.data["placed_prefabs"]
        site = next(site for site in self.scene["planned_facilities"] if site["id"] == instance["id"])
        self.assertFalse(site["placeholder"])
        self.assertEqual(tuple(site["rect"]), tuple(instance["rect"]))
        self.assertEqual(world.data["road_network"], self.scene["road_network"])
        road_node = self.scene["road_network"]["terminals"][site["id"]]
        vehicle_node = instance["connectors"]["vehicle"]["node"]
        self.assertEqual(world.navigation.nodes[road_node], world.navigation.nodes[vehicle_node])
        self.assertIn(vehicle_node, world.navigation.edges[road_node])
        goal = world.navigation.nodes[instance["terminal"]]
        route = world.navigation.route(actor.xy, goal, allow_doors=True)
        self.assertTrue(route)
        for point in route:
            for _ in range(math.ceil(math.dist(actor.xy, point) / 0.3) + 10):
                distance = math.dist(actor.xy, point)
                if distance < 0.02:
                    break
                world.open_for_ai(actor, point)
                direction = point[0] - actor.position.x, point[1] - actor.position.y
                move_actor(actor, direction, world, min(0.3, distance) / 3, speed=3)
            self.assertLess(math.dist(actor.xy, point), 0.02)
        self.assertEqual(world.indoor_search_area(actor.xy).id, instance["id"])
        self.assertTrue(any(not collider.active for collider in world.geometry.objects.values()
                            if collider.kind == "door" and collider.id.startswith(instance["id"] + "/")))
