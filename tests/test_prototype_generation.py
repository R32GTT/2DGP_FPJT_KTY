import json
import math
import unittest
from unittest.mock import patch
from dataclasses import asdict
from collections import Counter
from copy import deepcopy

from shapely import LineString, Polygon, box, union_all

from campaign.persistence import deserialize_campaign, serialize_campaign
from campaign.resources import assert_invariants
from campaign.simulation import Simulation
from prototype.generation import generate_scene
from prototype.generated_campaign import SETUP_PATH, build_generated_state
from prototype.movement import move_actor
from prototype.prefabs import load_catalog
from prototype.road_generation import ROAD_GAP, build_junctions, validate_roads
from prototype.road_visuals import ROAD_STYLES, road_layers
from prototype.simulation import PlaySimulation
from prototype.world import ROOT, World


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

    def test_tree_clusters_offer_real_cover_and_leave_walkable_gaps(self):
        for scene in (self.scene, self.other):
            world = World(scene)
            trees = [collider for collider in world.geometry.objects.values() if collider.kind == 'tree']
            centers = [(tree.rect.x + tree.rect.w / 2, tree.rect.y + tree.rect.h / 2) for tree in trees]
            gaps = [(a, b) for index, a in enumerate(centers) for b in centers[index + 1:]
                    if math.dist(a, b) < 2.7 and world.navigation.walkable(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2))]
            self.assertTrue(gaps, '엄폐 사이로 이동할 수 있는 나무 군집이 필요함')
            center = centers[0]
            start, end = (center[0] - .6, center[1]), (center[0] + .6, center[1])
            for category in ('sight', 'bullet'):
                self.assertEqual(world.geometry.raycast(start, end, category=category)[1], trees[0].id)

    def test_new_map_starts_inside_its_safehouse(self):
        for scene in (self.scene, self.other):
            with self.subTest(seed=scene['generation']['seed']):
                play = PlaySimulation(scene)
                safehouse = next(site for site in play.world.facilities if site['kind'] == 'safehouse')
                self.assertEqual(play.player.xy, tuple(safehouse['position']))
                self.assertEqual(play.player.xy, play.world.safehouse)
                self.assertTrue(play.world.navigation.walkable(play.player.xy))

    def test_blocked_prefab_entry_uses_a_connected_site_instead(self):
        catalog = load_catalog(ROOT)
        definition = next(item for item in catalog.values() if item["kind"] == "hq")
        definition["colliders"].append({"id": "blocked_gate", "rect": [-1, 11, 2, 1]})
        with patch("prototype.generation.load_catalog", return_value=catalog):
            scene = generate_scene(42)
        world = World(scene)
        hq = next(site for site in scene["planned_facilities"] if site["kind"] == "hq")
        gate = tuple(hq["connector"]["position"])
        self.assertTrue(hq["placeholder"])
        self.assertEqual(world.navigation.route(world.safehouse, gate)[-1], gate)

    def test_play_movement_obstacle_collision_and_map_boundary(self):
        simulation = PlaySimulation(self.scene)
        start_x, start_y = simulation.player.xy
        self.assertTrue(simulation.guards)
        for _ in range(60):
            simulation.step((1, 0))
        self.assertAlmostEqual(simulation.player.position.x, start_x + 3)
        self.assertAlmostEqual(simulation.player.position.y, start_y)
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
        segments = [(row, LineString([network["nodes"][key] for key in row["nodes"]])) for row in network["segments"]]
        for index, (segment, line) in enumerate(segments):
            edge = segment["nodes"]
            a, b = line.coords
            ux, uy = (b[0] - a[0]) / line.length, (b[1] - a[1]) / line.length
            for other_segment, other in segments[index + 1:]:
                other_edge = other_segment["nodes"]
                if set(edge) & set(other_edge) or line.intersects(other):
                    continue
                p, q = other.coords
                vx, vy = (q[0] - p[0]) / other.length, (q[1] - p[1]) / other.length
                if abs(ux * vy - uy * vx) >= 0.2:
                    continue
                projections = [(x - a[0]) * ux + (y - a[1]) * uy for x, y in other.coords]
                overlap = min(line.length, max(projections)) - max(0, min(projections))
                if overlap >= min(segment["width"], other_segment["width"]):
                    self.assertGreaterEqual(line.distance(other), (segment["width"] + other_segment["width"]) / 2 + ROAD_GAP)

    def test_road_roles_materials_and_replaceable_art_preserve_geometry(self):
        scene = self.scene
        self.assertEqual({(row["kind"], row["width"], row["material"]) for row in scene["roads"]},
                         {("main", 6, "asphalt"), ("access", 4.5, "asphalt"),
                          ("access", 3, "dirt"), ("service", 3, "dirt")})
        routes = {}
        for road in scene["roads"]:
            routes.setdefault(road["route"], set()).add((road["kind"], road["width"], road["material"]))
        self.assertTrue(all(len(specs) == 1 for specs in routes.values()))
        self.assertEqual({road["material"] for road in scene["roads"] if road["kind"] == "access"}, {"asphalt", "dirt"})
        for site in scene["planned_facilities"]:
            gate = scene["road_network"]["terminals"][site["id"]]
            approach, = [road for road in scene["roads"] if gate in road["nodes"]]
            self.assertEqual({key: approach[key] for key in ("kind", "width", "material")}, site["road"])
            self.assertLessEqual(approach["width"], site["connector"]["width_m"])
        original = list(road_layers(scene["road_network"]))
        junction_layers = {row["id"]: row for row in original if row["style"] == "road_junction"}
        for junction in scene["road_network"]["junctions"]:
            for index, surface in enumerate(junction['surfaces']):
                self.assertEqual(junction_layers[f"junction/{junction['node']}/{index}"]["polygon"], surface["polygon"])
        style = ROAD_STYLES["dirt"]
        old_path = style["image"]
        try:
            style["image"] = "assets/prototype/roads/future_dirt.png"
            replaced = list(road_layers(scene["road_network"]))
            self.assertTrue(any(row["image"] == style["image"] for row in replaced))
            self.assertEqual([row["rect"] for row in original], [row["rect"] for row in replaced])
        finally:
            style["image"] = old_path

    def test_narrow_road_and_vehicle_turn_space_are_checked(self):
        from prototype.geometry import Rect

        network = {"nodes": {"a": [-10, 0], "b": [0, 0], "c": [0, 10]},
                   "edges": [("a", "b"), ("b", "c")], "terminals": {},
                   "segments": [{"nodes": [a, b], "width": 3, "kind": "service", "material": "dirt"}
                                for a, b in (("a", "b"), ("b", "c"))]}
        network["junctions"] = build_junctions(network["nodes"], network["segments"])
        bounds = Rect(-20, -20, 40, 40)
        validate_roads(network, [], bounds, [])
        narrow = deepcopy(network)
        narrow["segments"][0]["width"] = 2.9
        with self.assertRaisesRegex(ValueError, "폭"):
            validate_roads(narrow, [], bounds, [])
        with self.assertRaisesRegex(ValueError, "통행 공간"):
            validate_roads(network, [], bounds, [{"rect": [1, -1, 0.2, 0.2]}])

        network["nodes"]["c"] = [10, 10]
        network["junctions"] = build_junctions(network["nodes"], network["segments"])
        validate_roads(network, [], bounds, [])
        shape = Polygon(network["junctions"][0]["polygon"])
        self.assertTrue(shape.is_valid)
        self.assertGreater(shape.bounds[1], -2.5)

    def test_mixed_junction_preserves_pavement_edge_and_concave_mesh(self):
        nodes = {'center': [0, 0], 'west': [-12, 0], 'east': [12, 0], 'north': [0, -12]}
        segments = [{'nodes': ['center', key], 'width': width, 'material': material, 'kind': kind}
                    for key, width, material, kind in (('west', 6, 'asphalt', 'main'),
                                                      ('east', 6, 'asphalt', 'main'),
                                                      ('north', 3, 'dirt', 'service'))]
        junction, = build_junctions(nodes, segments)
        shape = Polygon(junction['polygon'])
        self.assertLess(shape.area, shape.convex_hull.area)
        paved = []
        for surface in junction['surfaces']:
            points = surface['triangles']
            triangles = [Polygon(points[index:index + 3]) for index in range(0, len(points), 3)]
            mesh = union_all(triangles)
            self.assertAlmostEqual(sum(triangle.area for triangle in triangles), mesh.area)
            if surface['material'] == 'asphalt':
                paved.append(mesh)
        self.assertTrue(box(-7, -3, 7, 3).buffer(1e-6).covers(union_all(paved)))
        self.assertAlmostEqual(shape.area, sum(Polygon(surface['triangles'][index:index + 3]).area
                                              for surface in junction['surfaces']
                                              for index in range(0, len(surface['triangles']), 3)))
        network = {'nodes': nodes, 'segments': segments, 'junctions': [junction]}
        order = [layer['color'] == ROAD_STYLES['asphalt']['color'] for layer in road_layers(network)]
        self.assertEqual(order, sorted(order))

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

    def test_generated_campaign_uses_real_sites_and_delivers_with_the_player_away(self):
        play = PlaySimulation(self.scene)
        state, network = play.campaign.simulation.state, self.scene["road_network"]
        self.assertEqual(state.seed, self.scene["generation"]["seed"])
        self.assertEqual(set(state.nodes), set(network["nodes"]))
        self.assertEqual(set(state.facilities), {site["id"] for site in self.scene["planned_facilities"]
                                               if site["kind"] != "safehouse"})
        self.assertEqual(len(state.roads), len(self.scene["roads"]))
        for road in state.roads.values():
            self.assertAlmostEqual(road.length_m, math.dist(network["nodes"][road.start], network["nodes"][road.end]))
        hq = next(facility for facility in state.facilities.values() if "command" in facility.capabilities)
        operation, = [op for op in state.operations.values() if op.destination_id == hq.id]
        initial = hq.inventory["supplies"]
        amount = state.reservations[operation.id].cargo["supplies"]
        original_player = play.player.xy
        seen_trucks = False
        snapshot = None
        for second in range(1, 121):
            play.time = float(second)
            play.campaign.advance_to(second)
            play._sync_guards()
            seen_trucks |= bool(play.transports.trucks)
            if operation.delivered_at is None:
                self.assertEqual(hq.inventory["supplies"], initial)
            if second == 45:
                self.assertTrue(play.transports.trucks)
                snapshot = deserialize_campaign(serialize_campaign(state))
        self.assertTrue(seen_trucks)
        self.assertIsNotNone(operation.delivered_at)
        self.assertEqual(hq.inventory["supplies"], initial + amount)
        self.assertEqual(play.player.xy, original_player)
        self.assertEqual(sum(row["kind"] == "handed_over" and row["operation_id"] == operation.id
                             for row in state.history), 1)
        Simulation(snapshot).advance_to(120)
        self.assertEqual(asdict(snapshot), asdict(state))
        assert_invariants(state)

    def test_configured_initial_assets_and_shared_campaign_survive_view_rebuild(self):
        world = World(self.scene)
        setup = json.loads(SETUP_PATH.read_text(encoding="utf-8"))
        setup["facilities"]["hq"]["inventory"]["supplies"] = 7
        setup["vehicle_profiles"]["truck"]["capacity"] = 180
        state = build_generated_state(world.data, setup)
        hq = next(facility for facility in state.facilities.values() if "command" in facility.capabilities)
        self.assertEqual(hq.inventory["supplies"], 7)
        self.assertTrue(all(vehicle.capacity == 180 for key, vehicle in state.vehicles.items() if not key.startswith("external/")))
        play = PlaySimulation(self.scene)
        campaign = play.campaign.simulation
        campaign.advance_to(45)
        snapshot = asdict(campaign.state)
        rebuilt = PlaySimulation(self.scene, campaign=campaign)
        self.assertIs(rebuilt.campaign.simulation, campaign)
        self.assertEqual(rebuilt.time, 45)
        self.assertEqual(asdict(campaign.state), snapshot)
